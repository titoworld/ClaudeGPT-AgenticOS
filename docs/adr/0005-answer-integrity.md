# 0005. Answer integrity

- Status: Proposed
- Date: 2026-09-28

## Context

The validation of the audit of 28 September 2026 (points 10 and 17, and new issues N1, N2, N3, N4 and N14) showed that the system could not tell a complete answer from a truncated one or from a refusal. The common cause was threefold.

**The providers' contract could not say so.** `GenerationResult` only carried text and usage. Because of that:

- An answer cut off by the output limit was stored as complete and entered the turn cache for 7 days. It happened with OpenAI's `response.incomplete`, with Anthropic's `stop_reason: "max_tokens"` and with a stream of Claude's API that ended without `message_stop`.
- An OpenAI refusal was shown as an "empty answer". If there was partial text before it, that fragment was stored as the answer.
- A connection drop in the middle of a stream of Claude's API came out as an internal error, without a retry.
- Codex, when it retries a dropped stream after having emitted text, generates the whole answer again in a new item. The result was a duplicated answer.

**The output budget did not mean the same thing everywhere.**

- On OpenAI's API, `max_output_tokens=8000` includes the reasoning. If the reasoning used it up, an "empty answer" came out with 8,000 tokens billed.
- Claude's API silently raised it to 16,000 when there was thinking.
- The CLIs did not get it.

**The revision parser took tags written as content for structure.** An answer that explains `<answer>` in code or in XML was cut off mid-sentence. Besides, "UNCHANGED (my answer already covers it)" replaced the whole answer with the note.

The review of the first version of this decision added three things:

- Claude's CLI 2.1.283, tested locally against a mock API, sends the resume request that follows `max_tokens` about 10 ms after the end of the answer. The provider killed it too late, and with SIGTERM: the resume always went out (6 out of 6 attempts) and its cost was not counted.
- The same CLI, after a refusal, asks again on its own. The provider joined the refused fragment to the new answer and stored them as a complete answer, which also entered the cache.
- The parser's first rule for closing tags stored extra tags and text when the model wrote a heading between sections ("Here is my revised answer:") or a sign-off at the end, and it lost the new answer in two cases that used to work.

## Decision

### Contract (`providers/base.py`)

- `GenerationResult` has `truncated` (bool) and `finish_reason`. The values of `finish_reason` are `"max_tokens"`, `"content_filter"`, `"incomplete"`, `"interrupted"` or a value of the provider's own. A truncated answer is a useful partial answer, never a complete one.
- `RefusalError(ProviderError)` is shared by all the providers: kind `invalid`, not retryable. It carries the billed usage (`usage`), the model, the category if there is one, and the text of the refusal, cleaned up and shortened. The text emitted before a refusal never becomes an answer.
- Any `ProviderError` can carry `usage` and `model`. That way, a call that fails but has been billed keeps its cost; for example, one that uses up the output limit without writing anything.
- `GenerationRequest.max_output_tokens` is the call's maximum of **billed** output tokens, reasoning included. The adapters send it as it is and never raise it.
- `GenerationRequest.reasoning` (`"default"` or `"off"`) is the reasoning policy, and it is separate from the budget.
- Explicit budgets in the engine (`EngineConfig`):
  - answers, revisions and syntheses: 16,000 billed tokens, with the default reasoning;
  - compaction summaries: 2,000, with reasoning `"off"`.

### Engine (`orchestrator/`)

- A truncated answer is stored with `meta.truncated: true` and `meta.finish_reason`, and `stream.completed` carries `truncated: true` and the same `finish_reason`. An `UNCHANGED` revision with a note carries `unchanged_note` in `stream.completed` and in `meta`. That way, the live view and the reloaded one say the same thing. A turn with any truncated message **never enters** the turn cache.
- In a debate, a truncated answer still serves for the revisions and the synthesis, but the prompt marks it as incomplete. The agent that wrote it is asked to complete it instead of answering `UNCHANGED`. A degraded synthesis keeps the mark of the answer it reuses.
- A truncated answer without any text fails with a message that says that the output limit was used up. The billed usage is recorded anyway.
- A truncated compaction summary is not used, because it would replace the old messages forever. Its cost is recorded and the other provider is tried.
- A refusal is reported with its own message (not "empty answer"), and its billed usage is recorded as before (`failed_call_usage`).
- `CACHE_KEY_VERSION` goes up to 3. The earlier entries may contain truncated, duplicated or damaged answers.

### Providers

- **OpenAI's API:**
  - `response.refusal.delta`/`done`, or a `refusal` part in `response.output`, raise `RefusalError`, even if there was text before.
  - `response.incomplete` gives a truncated result, with the reason from `incomplete_details` (`max_output_tokens` becomes `"max_tokens"`). Without any text, it is an error with the billed usage.
  - With reasoning `"off"`, the lowest effort the model accepts is used: `none` on GPT-6 Sol and Luna, `minimal` on the first GPT-5 models and `low` on the rest.
- **Claude's API:**
  - `max_tokens` is the exact budget. The minimum of 16,000 is removed.
  - If the budget does not reach the minimum thinking budget (1,024), the model answers without thinking.
  - With reasoning `"off"`, thinking is disabled.
  - `stop_reason: "max_tokens"` gives a truncated result.
  - An httpx2 transport error in the middle of a stream, or a stream without the final event, is a retryable `unavailable` error: "Claude's answer was interrupted".
- **Claude's CLI:**
  - The budget reaches the CLI as `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. It is a value the provider computes, like `DISABLE_AUTOUPDATER`, and it is never inherited from the app's environment: the closed list of variables is not widened.
  - The budget and the reasoning are part of the key of the pool of warm processes.
  - With reasoning `"off"`, `--thinking disabled` is added.
  - CLI 2.1.283 does not settle for two kinds of answer: about 10 ms after the end, it sends a new request with a message of its own, which bills the whole context again:
    - after `stop_reason: "max_tokens"`, a resume ("Output token limit hit. Resume directly…"), up to 3 times; the `result` only carries the text of the last one;
    - after `stop_reason: "refusal"`, a second attempt ("Your response above was stopped by a safety classifier…").
  - That is why, as soon as the provider reads a `message_delta` with one of these two reasons, it kills the process group with SIGKILL, before reading anything else and before handing anything over to the engine. SIGTERM is no good: the CLI shuts down in an orderly way and, in the meantime, sends the request.
  - With `max_tokens`, the call returns the text as a truncated result, with the usage of that request.
  - With a refusal, the call raises `RefusalError` with the usage of that request, the model and the category from `stop_details`. A `result` with `is_error` and `stop_reason: "refusal"` is a refusal too, with the usage of the CLI's whole turn.
- **Codex (app-server 0.157.1):**
  - The text is kept per item, and the final text only includes the items that are not commentary and have received `item/completed`.
  - An `error` with `willRetry` after text has been emitted stops the call with "ChatGPT's answer was interrupted". This error is retryable: the engine only retries it if it has not shown anything yet.
  - The protocol has no field for the output limit. The call stops the turn (`turn/interrupt`) when the estimated visible text (characters / 4) goes over the budget, and it returns a truncated result with the usage Codex reports. This stop is told apart from an `interrupted` status the call did not ask for.
- **Fake:** it respects the budget (it cuts off and marks the answer) and, for the tests, it can cut off or refuse by purpose.
- **The fake CLI of the tests of Claude's CLI:** it reproduces the order of events of the real CLI on `max_tokens` and on a refusal, shuts down in an orderly way on SIGTERM like the real one, and notes whether it gets to continue on its own. The tests check that it never does.

### Revision parser (`orchestrator/sections.py`)

- A tag inside code is text: blocks with ```` ``` ```` or `~~~` fences, with any indentation, and inline code spans closed on the same line.
- A code block that never closes was not code. From the first tag that appears in it, the text is held back until the block closes (it was code) or the answer ends (it is read again as normal text). Code without tags is shown at once.
- The opening tag of the section that is already open is text. So is a closing tag of a section that is not open.
- A closing tag of the open section closes it if what follows, not counting whitespace, is the opening of a section or the end of the answer. After `</answer>`, a lone agreement line, like "Agreement: 80", also closes it.
- When other text follows the closing tag, the decision waits until the next tag:
  - the same closing tag: the first one was text (a critique or an answer that mentions it), and the section continues;
  - the opening of a section or the end of the answer: it was the real closing, and the text in between is discarded. It is a heading like "Here is my revised answer:" or a sign-off. If the answer has no `<answer>`, this text is the answer without tags, as before.
  - After `</answer>`, the wait lasts at most 300 characters (not counting whitespace). A longer text is part of the answer, and so is the tag.
- An answer is `UNCHANGED` when its first non-empty line is the marker, which can be wrapped in `*`, `_`, backticks or quotes, and end in `.` or `!`. On its own, it is accepted in any mix of upper and lower case. To carry a note, it must be written in capitals, as the prompt asks, and the note must go on the same line, after a dash, a colon, a parenthesis or a bracket, or after a full stop or an exclamation mark and a space, and have at most 200 characters. The note is stored in `meta.unchanged_note`, and the content is the previous answer. Anything else is a normal answer: "Unchanged: the ECB keeps its rate at 2%" is an answer, and so is "UNCHANGED" followed by a paragraph.
- The parser only holds back the text it does not yet know where to place. Any way of splitting the stream gives the same result, and what is shown live is what is stored. Tests with random splits check it.

## Alternatives considered

- **`max(limit, 16000)` when there is reasoning:** it was the fix the audit proposed. The owner discarded it because it mixes the billed budget with what the model needs to think. The billed budget, the reasoning policy and the visible text are separate concepts, and counting fragments of text is never billing.
- **Treating every truncated answer as an error:** it would throw away useful text that has already been paid for. A marked partial answer is more useful than none.
- **Retrying a truncated answer automatically:** it could cost twice as much, without any guarantee that the answer fits. The owner can ask again.
- **Codex: letting it retry and keeping the last item:** the partial text has already been sent to the browser and, in a revision, the parser has already read it. It cannot be taken back.
- **Codex: only documenting that there is no limit:** with the subscription, a runaway answer uses up the 5-hour quota. An approximate stop is better than none.
- **Parser: the answer goes up to the last `</answer>`:** text would have to be held back without limit after every mention in prose, and the live text would stall. The rule adopted waits for at most 300 characters.
- **Parser: a closing tag followed by text is always text (the first version of this decision):** a heading between sections or a sign-off at the end ended up stored inside the critique or the answer, tag and all.
- **Parser: an `UNCHANGED` note in a paragraph of its own:** "UNCHANGED" followed by a correction in another paragraph lost the correction, which became the note.
- **Claude's CLI: SIGTERM, and waiting for the CLI to quit:** the CLI shuts down in an orderly way in about 20 ms and, in the meantime, sends the resume request. It happened in every test.
- **Claude's CLI: turning the resumes off:** version 2.1.283 has no option or variable to do it. The maximum of 3 resumes is fixed.

## Consequences

- A truncated answer shows as incomplete, with the reason, live and on reload, and it is not served again from the cache.
- Refusals have their own message, and their cost is recorded. The partial text from before a refusal is never stored.
- The budgets are the same for every provider, and both APIs respect them exactly. On Claude's CLI, the limit applies to each request the CLI makes.
- Known limitations:
  - Codex's stop is approximate: it does not count the reasoning, and the model can generate a few more tokens before the interruption arrives. The usage of an interrupted request may go unreported.
  - Stopping Claude's CLI is a race: the new request goes out about 10 ms after the end of the answer. The SIGKILL arrives first in every test done with the real CLI and a local mock API (no resume in 15 truncated answers, and no second attempt in 24 refusals; before, they always went out). If a new version of the CLI were faster, a request could go out anyway, and its cost would not be counted.
  - A tag written in prose and followed by the closing of its section is still ambiguous. A critique that mentions `</critique>` and goes on up to `<answer>` without closing again loses the text after the mention. A truncated answer that mentions `</answer>` in its last 300 characters loses the text after the mention.
  - A tag inside a code block stops the live text until the block closes.
- `unchanged_note` and `truncated`/`finish_reason` are part of the protocol, in `meta` and in `stream.completed` ([PROTOCOL.md](../PROTOCOL.md)). The interface shows them.
- This decision is a proposal until the owner accepts it.
