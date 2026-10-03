# Architecture

> A personal AI council: Claude and ChatGPT answer, critique each other and synthesize a better answer, spending as few tokens as possible. A single user, self-hosted on a VPS.

## Overview

```
Browser (Svelte 5 + three.js)
   │  HTTPS / HTTP/3 · one persistent WebSocket connection
   ▼
Caddy (automatic TLS, security headers, compression)
   │  internal Docker network
   ▼
Python app (FastAPI + uvicorn/uvloop)
   ├── server/       REST API, WebSocket, session, security headers (CSP), the frontend's static files
   ├── security/     argon2id password, TOTP, sessions, known devices, login attempt limits
   ├── orchestrator/ turn engine: solo · duel · debate · refine, compaction,
   │                 cache, accounting
   ├── providers/    Claude and ChatGPT, each in cli · api · fake mode
   ├── storage/      SQLite (WAL): conversations, messages, attachments, usage, savings, cache,
   │                 sessions; the attached files next to it, addressed by their content
   ├── attachments   attachments: limits, type from the content, text and pages of PDFs (in a separate process)
   ├── i18n          the language in force and the server's texts in English, Spanish and Catalan (locales/)
   └── pricing · fx  prices per model (USD/MTok) and the ECB's USD→EUR rate
        │
        ├── official Claude Code CLI  (Pro/Max subscription, OAuth)   ─┐ "cli" mode
        ├── official Codex CLI        (ChatGPT subscription, OAuth)   ─┘
        └── Anthropic / OpenAI SDKs   (API keys)                         "api" mode
```

## Modules and contracts

| Module | Contract | Responsibility |
| --- | --- | --- |
| `domain.py` | shared types | `AgentName`, `TurnMode`, `Usage`, the options of a debate and of Refine |
| `providers/base.py` | `Provider`, `Attachment` | Turns a `GenerationRequest` (with its attachments) into a stream of `TextDelta` + a `GenerationResult` |
| `orchestrator/store.py` | `Store` | The persistence the engine needs (implemented by `storage`), including the outcome of each turn and the question's attachments |
| `orchestrator/events.py` | events, `TurnOutcome` | The server → client messages of a turn ([PROTOCOL.md](PROTOCOL.md)) and how it ended |
| `orchestrator/types.py` | `TurnRequest`, `EngineConfig` | The engine's input |
| `config.py` | `Settings` | The process's configuration (`AOS_*` variables) |
| `pricing.py` | `ModelPrice`, `estimate_cost_usd` | Default and custom prices; the cost of each call |
| `fx.py` | `FxRate` | The ECB's daily exchange rate, with a manual fallback value |
| `attachments.py` | limits, `PdfReader` | Attachment limits, a file's type from its content, an image's dimensions, a clean name, estimated tokens, and reading PDFs in a separate process: the text and the analysis of each page |
| `pdf_facts.py` | `PdfPage`, `PdfCheck` | What the reader found on each page of a PDF and the warnings that follow from it; Claude's check, read strictly |
| `orchestrator/pdf_check.py` | `check_pdf` | Claude's check of the text of a PDF that ChatGPT with the subscription reads |
| `orchestrator/refine.py` | `parse_review`, `parse_edit` | The Refine mode: reads the reviews and the editor's versions strictly, also while they stream in |
| `i18n.py` | `t`, `lazy`, `number` | The language in force and the server's texts in it, from the catalogs of `locales/` ([Internationalization](#internationalization)) |

## Turn modes

- **Solo:** a single agent answers. The cheapest.
- **Duel:** both answer in parallel and are shown side by side.
- **Council** (`debate`):
  1. *Initial answers* in parallel.
  2. *Review rounds* (up to 2 by default): each agent gets the question, its own answer and the other's, and returns a short critique, its improved answer (or `UNCHANGED`, with an optional short note, if it needs no change) and an agreement score from 0 to 100.
  3. *Stop on consensus:* if both reach the threshold (85 by default), no more rounds are run.
  4. *Synthesis:* the synthesizer agent combines the two final answers and the points of disagreement into the definitive answer.
- **Refine** (`refine`, [ADR 0010](adr/0010-refine-mode.md)): both AIs improve **a single document** (a plan, a text, a design, code) round after round, until the owner stops them. It only starts when the owner picks it: it cannot be the default mode.
  1. *Initial answers* (round 0) in parallel, as in a debate.
  2. *Merge* (round 1): the editor (Claude by default) merges the two answers into version 1.
  3. *Improvement rounds* (from round 2 on): both review the current version. Each review proposes at most 5 changes (fixing a defect, gaining clarity, simplifying, or meeting a requirement of the brief) and scores from 0 to 100 how well the version answers the brief; or else it says `UNCHANGED`, if it would change nothing. A review that does not follow this format (it proposes no change with its kind and does not say `UNCHANGED`) fails, and that agent does not count in the round. Then the editor writes the next version, with at most 5 changes and one changelog line per change. If no review proposes anything, there is no edit.
  4. *Without oversizing it:* every prompt quotes the brief again, and a change must say which defect it fixes or which requirement it meets: additions the brief does not ask for are rejected, and every review must also look for what can be removed or simplified. The prompts carry the length of the version, the word limit and the last 30 lines of the changelog, because undoing an earlier change has to be justified. The limit is the owner's or, if they set none, 1.2 times the words of version 1 (300 at least). The engine checks it without any model: a version over the limit gets one attempt to shorten itself and, if it is still over, the round is discarded and the current version is kept. Version 1, which has no previous one, also gets one attempt to shorten itself when the merge goes over the owner's limit, but if it is still over (or if it is a copy of one of the answers) it stays anyway, and it is the following edits that must make it fit. An answer without the complete version is not accepted either, nor is a version identical to the current one. All of them are stored anyway, for the owner to see. The editor writes each version whole in a single reply, which has at most 16,000 output tokens, reasoning included: a version that does not fit is cut off and not accepted. That is why the document cannot go beyond about 10,000 words of English prose (fewer in Catalan or in code), even if the word limit allows more.
  5. *Stopping:* the turn ends with the current version, which is the final answer and the one that later turns see:
     - when the owner stops it, at the end of the round (`turn.stop`) or right away (`turn.cancel`: the calls in progress are cancelled, but the current version is still stored as the final answer, without any call);
     - when neither of the two finds anything to change for 2 rounds in a row (always);
     - when both give it the threshold (90 by default) or more, without pointing out any defect, for 2 rounds in a row (this can be turned off, to make it "infinite");
     - at the maximum number of rounds (12 by default, from 2 to 50), the merge included, or when it has spent the budget (€3 by default, from €0.10 to €100; in subscription mode it counts the value at API prices, so as not to use up the quota). The engine counts in dollars, so the server passes it the budget converted at the rate the app uses to show euros. Calls of models without a price do not count;
     - when both fail in a round. If only one fails, the other carries on alone.

  A Refine turn never uses the turn cache.

## Token savings

All token counts use the **processed tokens** of a call: input, cache reads and writes, and output (reasoning is already part of the output). That way, the tokens saved and their value in money add up, and the dashboard's ratio has the same definition in its numerator and its denominator ([ADR 0008](adr/0008-token-accounting.md)). The savings rows stored before this change keep the old definition, uncached input plus output, for the turn cache and for the stop on consensus.

| Technique | How it works | How it is measured |
| --- | --- | --- |
| Custom system prompt | The CLIs run with a short system prompt instead of the coding agent's: Claude's without any tool, and Codex's without the tools that can be turned off ([Security](#security-single-user)) | – |
| Minimal context in debates | The reviews only see the question and the last two answers, not the whole transcript | – |
| Canonical history | Only the question and the final answer (the synthesis) go into the conversation's history, not the intermediate rounds | – |
| Compaction | When the history goes over the threshold, the old messages are summarized with the fast model and the latest ones are kept | (tokens of the original history − tokens of the compacted context) × the turn's billed calls that carry the context (the answers and the synthesis) |
| Stop on consensus | The remaining rounds are skipped | average processed tokens of a round × rounds skipped |
| `UNCHANGED` | An agent that agrees does not rewrite its answer (at most it adds a short note) | length of the answer not rewritten |
| Answer cache | An identical question (same mode, agents, models and context) is answered without calling any model. If it is not known which model will answer (the provider's status is slow, fails or says it is not available), the turn neither reads nor writes the cache. A Refine turn never uses it | processed tokens of the original turn, including the attempts declined before a fallback; the value, each call at its model's current rates |
| Provider cache | Stable prefixes (system prompt first) so that Anthropic and OpenAI reuse the computation | `cache_read_tokens` |

## Providers

Each agent has three modes, chosen with `AOS_CLAUDE_MODE` and `AOS_CHATGPT_MODE`:

- **`cli`** (default): runs the official CLI (`claude`, `codex`) with the prompt on standard input, in an empty directory and with a minimal environment: Claude without any tool, and Codex in read-only mode, without the tools that can be turned off ([Security](#security-single-user)). Authenticated with your subscription (OAuth) once on the VPS.
- **`api`**: the official SDK with an API key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) and *prompt caching*.
- **`fake`**: deterministic answers for tests and to try the interface without spending anything.

## Attachments

The owner can attach images (PNG, JPEG, GIF, WebP), PDFs and text files to a question, at most 5 and 20 MB in all ([ADR 0009](adr/0009-attachments.md); the limits and routes are in [PROTOCOL.md](PROTOCOL.md#attachments)).

- **Upload:** each file is uploaded on its own (`PUT /api/attachments`), before the question is sent, and `turn.start` carries their `id`s. The type comes from the content, never from the name: images and PDFs by their signature, text by being UTF-8 with an allowed extension. Everything else, SVG included, is rejected. The server reads an image's dimensions from its headers, without decoding it, and the browser already scales large images down before uploading them.
- **PDF:** pypdf reads it in a separate process (isolated Python, without the server's environment, 60 s, 512 MiB of address space, without writing files or creating processes), which counts its pages and extracts its text, one block per page. In the same pass it analyzes each page with pypdf's operator visitor: where its text is, its letters and broken characters, whether it draws images, and the text it shows without it being visible (invisible, smaller than 1 point, or outside the visible area). The card's warnings come from there (`pdf_notes`: pages without text, unreadable, or with possible hidden text). The server's process never analyzes a PDF, and if the container runs out of memory, the kernel kills the reader first (the upload fails), not a CLI in the middle of a turn nor the server.
- **Storage:** each file is stored once, addressed by its `sha256`, in `<data_dir>/attachments` (directories 0700, files 0600), next to the database and inside the same backups. The database stores their description and text (the content of a text file, the text extracted from a PDF and the analysis of its pages), which questions carry them, and Claude's check of each PDF, by its content. An attachment that is not sent is deleted after 24 h; deleting a conversation deletes the ones only it used; every hour, a sweep deletes the files that no row uses. A PDF's check is deleted when no attachment uses its file.
- **Delivery:** the engine loads them when the turn starts (one that does not exist makes the turn fail) and stores the question tied to them, in a single transaction, with their description in `meta.attachments`. The answers and the synthesis get every attachment whole; the reviews get the images and the text whole, and the PDFs as `pdf_in_revisions` says (by default, the extracted text, which costs far fewer tokens; a PDF without text, like a scanned one, goes whole). Later turns only see a reference to them. Each provider sends them in its own blocks (images and documents to Claude; `input_image` and `input_file` to the OpenAI API); Codex gets images by their path (that of a link with the type's extension, which is where Codex infers the type from) and PDFs as extracted text, page by page and checked by Claude (see the next point). The models must treat the content of attachments as data, never as instructions: a file's text goes through `neutralize_tags` and sits between an opening line with its name and a closing line, both with a code the file cannot contain (`prompt_format.enclosed`), so it cannot pass itself off as part of the prompt.
- **PDFs for ChatGPT with the subscription:** Codex cannot open PDFs and reads their extracted text. So that this text does not mislead it (a scanned page has none, a font without a character map makes it unreadable, and a PDF can carry text that is not visible), Claude checks it against the document ([ADR 0009](adr/0009-attachments.md)):
  1. When the first ChatGPT call of a turn needs an analyzed PDF, the engine starts the check (`orchestrator/pdf_check.py`), at most 2 PDFs at a time (in a duel or a debate, while Claude answers); every ChatGPT call of the turn (answers, reviews and synthesis) waits for the same task, for at most 5 minutes. The demo Claude (`AOS_CLAUDE_MODE=fake`) checks nothing: it answers that every page is correct without reading any, so with it the PDF is read unchecked, as without Claude.
  2. If the check is already stored (by the file's content and the check's version), it is reused without any call. If not, Claude gets the whole PDF, the text extracted from each page between two lines with a code that ChatGPT never sees, and the analysis's warnings, and writes only the pages that differ, one JSON line per page: without text, unreadable, incomplete, with text that is not visible, or a description of what the figures show. There are at most 3 calls per PDF, each one from the page where the previous one stopped; a malformed or cut-off line ends the reading, and only the pages before it count.
  3. ChatGPT reads the PDF page by page (`prompt_format.pdf_view`): the extracted text where it is correct, and Claude's reading, marked as such, where it is not; for a page where Claude finds text that is not visible, ChatGPT only gets the visible text and the warning. The pages nobody has checked (without Claude, after an error, a refusal or a timeout, the ones left outside the calls, or a PDF that was not analyzed) reach it as they were extracted, with any text that is not visible they may have: they say they have not been checked and, if the analysis finds them suspicious, that they may have text that is not visible. Every line the view adds to mark the text (its first and last lines, the page lines, and the lines before Claude's additions and descriptions) carries a code of its own that only ChatGPT sees (neither the file's, which Claude sees in the reviews, nor the check's), so neither the PDF nor Claude can forge any of them.
  4. Both models get the warning about the pages with possible hidden text, and the reviews and the synthesis know which pages ChatGPT read through Claude (the text Claude read on them or its description of the figures): if they match, it is a single reading.
  5. The check is stored when it is complete or when the calls have run out; never after an error, a refusal, a call that makes no progress, a timeout or a cancellation (the next turn tries again). A turn where ChatGPT read a PDF that the next turn would check again, or that nobody could check (without Claude, or with the demo one), does not go into the turn cache. The calls are billed with the purpose `check` and count towards the turn's total. The interface shows the check's state (`pdf.check`) and the badge on ChatGPT's answers (`meta.pdf_reading`).
- **Thumbnails:** the browser makes them (images with a `canvas`, the first page of a PDF with PDF.js) and uploads them, so that they show on every device.

## Models, costs and limits

- **Models:** each agent has a default model and a fast one (for summaries), both configurable from the interface. The list is fetched live from the provider (Anthropic's and OpenAI's model APIs, Codex's `model/list`; in the Claude CLI, the aliases `opus`, `sonnet`, `haiku` and `fable`, which always point to the latest version). Any model id is accepted too, to use a new model the same day it comes out.
- **Cost:** the engine computes the cost of each call with the price table (USD per million tokens, editable). In API mode it is the real cost; in subscription mode it is the *equivalent value* at API prices. The interface shows it in euros at the ECB rate.
- **Claude API fallbacks:** when a model declines and another one answers, each attempt is billed at the rates of the model that ran it, as Anthropic does. The declined attempt is a call billed separately, with its model and its cost, that counts towards the turn's total; the answer keeps only the usage of the attempt that answered. Tokens of different models are never added together ([ADR 0008](adr/0008-token-accounting.md)).
- **Percentage used:** in subscription mode, the 5-hour and weekly windows that Anthropic and OpenAI report; in API mode, the monthly budget in euros; and, if you enter the plan's price, how much value you have got out of it this month.

## Turn outcome

How each turn ends (completed, failed or cancelled) is decided once and stored on the question (`meta.outcome`) before the final event ([ADR 0007](adr/0007-turn-outcome.md)). It carries the state, the error, the failures of each call, the turn's total (all the billed calls, including compaction, PDF checks, failed calls and declined attempts), the savings, the consensus and the final messages. So a reloaded turn shows the same as it did live; the global statistics were already correct, because they come from the usage table.

- The question is created with the outcome open (`null`). If the server crashes or restarts in the middle of a turn, it stays that way, and the interface shows the turn as not completed.
- A cancelled turn stores its outcome before the cancellation goes on, in a task of its own protected with `asyncio.shield`. A turn is cancelled only once: a second "Stop", or the server shutting down, does not interrupt a turn that is already stopping, and `turn.cancelled` arrives after the stored outcome, with the same total. When shutting down, the server waits for these turns to finish before closing the database.
- A turn cancelled just as it was storing its savings (with all its messages already stored) finishes writing them, and the outcome carries them, like the rows the dashboard counts. Any other cancelled or failed turn records none.
- `turn.failed` and `turn.cancelled` carry the turn's total, like `turn.completed`, and `stream.failed` carries the cost of the failed call when it is known.
- A Refine turn also says why it ended (`stop_reason`: the owner stopped it, neither of the two finds anything to change, it converged, the rounds or the budget ran out, or both models failed). If it is cancelled when it already has a version, it first stores that version as the final answer, without any call and with the same protection, so that nothing already paid for is lost.

## Answer integrity

An answer can be complete, cut off or a refusal, and the system never confuses them ([ADR 0005](adr/0005-answer-integrity.md)).

- **Cut-off answer:** a useful partial answer, but never a complete one. It is stored with `truncated` and the reason (`finish_reason`), and the interface shows it as incomplete. The turn does not go into the cache. In a debate it keeps feeding the reviews and the synthesis, but the prompt marks it as incomplete. If there is no text at all, the call fails saying that the output limit ran out, and the cost is recorded anyway. A cut-off compaction summary is never used.
- **Refusal:** an error of its own (`RefusalError`), not retryable, with its message and its cost. The text emitted before the refusal is never stored. Faced with a refusal, the Claude CLI asks again on its own, once; the provider stops it before that (see the table below).
- **Interrupted stream:** a retryable error, never a complete answer. It happens when the connection drops mid-stream, when the stream ends without the final event, or when Codex retries an answer that was already being shown. The engine only retries it if it has not shown anything yet.
- **Reviews:** the parser treats the tags written inside code, and the opening tags of the same section, as text. A code block that is never closed was not code. If a closing tag is followed by text, the next tag decides: if the same tag appears again, the first one was text; if another section starts or the answer ends, it closed the section, and the text in between (a heading, a greeting) is discarded. `UNCHANGED` on its own keeps the previous answer; in capitals, it can also be followed by a short note on the same line, which is stored separately.

### Output budget

`max_output_tokens` is the maximum number of **billed** output tokens of a call, reasoning included. No adapter raises it. Reasoning is chosen separately, with `reasoning`:

- answers, reviews and syntheses: 16,000 tokens, with the default reasoning;
- summaries: 2,000 tokens, with reasoning `off`;
- PDF checks: 2,000 tokens, plus 1,200 for each page of the call without text, unreadable or with possible hidden text (Claude transcribes it whole; of the last one, all the visible text) and 100 for each other page, at most 32,000, with reasoning `off`.

Each provider applies it like this:

| Provider | Output limit | Reasoning `off` |
| --- | --- | --- |
| Claude API | Exact `max_tokens`. If the minimum thinking budget (1,024) does not fit, it does not think. `stop_reason: "max_tokens"` gives a cut-off answer. | Thinking disabled |
| OpenAI API | Exact `max_output_tokens`. `response.incomplete` gives a cut-off answer. | The lowest effort the model accepts: `none` on GPT-6 Sol and Luna, `minimal` on the first GPT-5 models and `low` on the rest |
| Claude CLI | `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, computed by the provider, is part of the key of the warm processes. The CLI applies it to every request it makes. When an answer stops on `max_tokens`, CLI 2.1.283 resumes it on its own (up to 3 times) with a new request about 10 ms later; after a refusal, it asks again once. Each of these requests would bill the whole context again. That is why the provider kills the process group with SIGKILL as soon as it reads that `stop_reason`, and ends the call with the usage of that request: a cut-off answer or a refusal. SIGTERM is not enough, because the CLI stops in an orderly way and sends the request anyway. Verified with the real CLI against a local mock API. | `--thinking disabled` |
| Codex (app-server 0.157.1) | The protocol has no field for the limit. The call stops the turn (`turn/interrupt`) when the estimated visible text (characters / 4) goes over the budget. It is approximate: it does not count reasoning, and the usage is the one Codex reports. | `low`, the lowest level of the catalog |

## Security (single user)

- Only Caddy is reachable from outside (80/443); the app listens on the internal network. Request bodies have a maximum of 1 MiB (4 KiB for logging in, the only route read without a session): Caddy passes the body on to the app as it arrives, without buffering it in memory, and the app answers 408 and closes the connection if the body has not arrived whole within 15 s (Caddy cuts off at 30 s), so a slow upload does not hold a connection for long; WebSockets are not subject to this limit. The upload of an attachment, which is only read with a session, has a limit of its own: 20 MB and 120 s (Caddy cuts off at 150 s).
- Login with a password (argon2id) **and** a TOTP code; exponential lockout after failed attempts. A browser that has already logged in (known-device cookie) is only locked out by its own errors; `agentic-os reset-throttle` lifts every lockout. A login ends in a single write transaction, conditional on the owner the credentials were checked against: if `agentic-os init` changes the owner in the meantime, the attempt fails, it leaves no session or device behind, and the old password can never replace the new one.
- Server-side sessions (only their hash is stored), a `__Host-` cookie that is HttpOnly, Secure and SameSite=Strict, and idle and absolute expiry. Only the owner's actions count as activity: the requests the client makes on its own (with `X-AOS-Background: 1`), WebSocket reconnections, *pings* and resubscriptions check the session without extending it, so an open tab that is not being used does not keep it alive. Since the cookie is HttpOnly, only the server can end the session: the client only takes the logout as done when the server confirms it. Until then, the page stays locked locally, without any session data in memory, and when it is reloaded it tries the logout again before anything else.
- Input is validated at the boundaries: identifiers must fit in SQLite and text must be valid UTF-8. Otherwise, the answer is a `422` (or an `invalid` error on the WebSocket) in the client's language, never an internal error or an internal Python message.
- `Origin` check on every request that changes state, and on the WebSocket.
- Strict CSP (`script-src 'self'`), HSTS, `frame-ancestors 'none'`; the models' Markdown is sanitized with DOMPurify.
- Attachments: the type comes from the content, and SVG is rejected. No uploaded file is served as HTML: images are shown with their type, PDFs and text are downloaded (`Content-Disposition: attachment`), and all of them carry `nosniff` and a CSP `default-src 'none'; sandbox`. PDFs are only read by a restricted process, and files are stored under a name that comes from their hash, never from the name the browser gives. A PDF's text that is not visible does not reach ChatGPT with the subscription on the pages Claude has checked; the ones nobody has checked reach it as they were extracted. Both models get a warning about the pages where the server's analysis suspects such text, which is a heuristic, not a guarantee. The file's name travels in the query string of the upload's URL, but no log (neither the app's access log nor Caddy's) stores the query string of any URL, nor the text of searches.
- The CLIs run without a *shell* and without access to the app's secrets, with a time limit, and the whole process group is killed on cancellation. Claude's has no tool. Codex 0.157.1 still offers ChatGPT a code tool in a child process (an isolated V8 environment, without files or network) and subagent tools: the app only lets one subagent run at a time (`agents.max_threads=1`), immediately interrupts the turns that belong to no call in progress, stops a call that uses them more than 3 times and, once no call is in progress, restarts a Codex process that has opened any ([ADR 0002](adr/0002-subscriptions-via-official-clis.md)). Codex's state and logs, which contain the prompts, live in a private tmpfs, and the logs are deleted every time Codex starts. That is why `agentic-os doctor` starts its own Codex with a temporary state directory, which it deletes when done: it never shares the one of the running app. `claude --version` and `codex --version` also run with each CLI's closed list of environment variables.
- Non-root containers (the app runs as user 10001 and Caddy as 10002; only `caddy-init` runs as root, for a few seconds, without network and with only the *capabilities* that `chown` needs, to hand Caddy's volumes over to its user), `no-new-privileges`, no effective *capabilities*, and memory, CPU and process limits.

## Latency and connection

- A single persistent WebSocket connection (no *handshake* per request), with *ping/pong* and automatic reconnection.
- Turns keep running on the server if the connection drops; on reconnecting, the client recovers the pending events (`turn.subscribe`). When a conversation is deleted, the server forgets its turns too: nothing can be recovered from them any more.
- The Codex process recovers by itself. Each request has a time limit that includes writing it, so that a stuck process that stops reading its input cannot block the others. When a call is released, a process that does not answer is restarted, and after consecutive failures the restarts are spaced out, up to 30 s apart. If a call gets no answer when it starts, a cheap request tells a stuck process, which is restarted at once, from a busy one: the busy one keeps serving the other calls and is restarted when it becomes free.
- Both AIs work in parallel; the text arrives by *streaming*.
- uvloop + httptools, HTTP/3 in Caddy, hashed static files with a long cache, and three.js loaded lazily so that the interface appears instantly.

## Internationalization

The interface and every text the server writes for people are in English, Spanish and Catalan ([ADR 0011](adr/0011-internationalization.md)).

- **The interface's language** is the owner's choice (a picker on the login screen and in the settings, kept in this browser), else the browser's first language among the three (`navigator.languages`), else English. Its texts live in the catalogs of `web/src/lib/i18n/areas/`, one module per area: English is the source, and TypeScript checks that the three languages have the same keys and parameters. Numbers, amounts and dates use `Intl` with `en-GB`, `es-ES` or `ca-ES`.
- **The server's texts** (errors, reasons, the agents' status, the models' descriptions, the command line) have a key and three translations in `src/agentic_os/locales/`, and `i18n.t()` gives them in the language in force: an HTTP request's `Accept-Language`, which the web app sets to its own language; a WebSocket connection's `?lang=`, since a browser cannot set headers on a WebSocket (the turns the connection starts inherit it, and so does what they store); on the command line, the system locale (`LC_ALL`, `LC_MESSAGES`, `LANG`); otherwise, English. When the owner changes the language, the app reconnects and fetches the agents' status and the models again.
- **Lazy texts:** no cache shared by every client keeps a translated text. It keeps a `lazy()` text, its key and parameters, and the text is made in the language of whoever reads it (the agents' status, the models' descriptions).
- **Codes on the wire:** a stored text keeps the language it was written in. Where the client must know what a text says, not only show it, the wire also carries a code: `reason_code` for refine rounds and versions, and `attachment_id` for the error about an attachment that no longer exists. For turns stored before these codes, the client recognizes the Catalan texts.
- **The models:** the prompts are in English and ask the models to answer in the language of the user's message; the interface's language does not decide the language of the answers. The demo answers (`fake`) are written in the turn's language.
