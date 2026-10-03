# 0008. Token accounting and declined attempts

- Status: Proposed
- Date: 2026-09-28

## Context

The validation of the audit of 28 September 2026 (points 7 and 8) showed two accounting errors of a different nature.

**Point 7. The token counts left the cache out.** The amounts of money were right (they are computed by category), but the counts and the ratios were not.

- `Usage.total_tokens` was `input + output`, and `input_tokens` is only the input that does not come from the cache: the four adapters normalize it that way.
- The consumers used it as "the turn's tokens":
  - the saving of a turn cache hit (`CachedTurn.tokens`);
  - the consensus stop;
  - a turn's total in the interface;
  - the dashboard's savings ratio.
- In a call with context (3 input tokens, 100 output tokens, 10,000 cache reads and 20,000 cache writes), it counted 103 tokens instead of 30,103. The consensus stop counted 618 tokens instead of 180,618.
- Cache writes, billed at 1.25 times the input, did not show anywhere: `stats.daily` did not carry them.
- The tokens saved did not square with their value (103 tokens valued at 0.132515 USD), and the ratio mixed definitions: it showed 84% when the real one was 1.8%.

**Point 8. Anthropic's fallbacks were charged at the price of the final model.** The amounts were wrong.

- With server-side fallbacks, a model that declines passes the request on to another one, and the answer arrives in a single call.
- `billed_usage` added the declined attempts to the same `Usage`, and the engine priced it with the model that answered.
- From a Fable 5.1 (10/50 USD per million tokens) to an Opus 4.8 (5/25), 0.25075 USD was recorded instead of 0.370625 (−32%), under Opus 4.8's name. A refusal after a fallback came to 0.055 USD instead of 0.11 (−50%).
- Anthropic's rule (the "Refusals and fallback" guide, section "Billing and rate limits", read on 28 September 2026): each attempt is billed at the rates of the model that ran it; `usage.iterations` is the record of what is billed per attempt; the top-level `usage` only describes the attempt that produced the message, and the tokens of different models are never added up in the same field.

## Decision

### Processed tokens

- `Usage.processed_tokens = input + cache_read + cache_write + output`: everything the call has processed and that has been billed. The reasoning (`reasoning_tokens`) is already part of the output at Anthropic, OpenAI and Codex, and it is not added again.
- `Usage.total_tokens` goes away. No consumer needed "uncached input plus output".
- Processed tokens are used in:
  - `CachedTurn.tokens` and the turn cache's saving (see "Value of a cache hit");
  - the consensus stop: the turn's average pair of revisions, in processed tokens;
  - `is_billed`, which decides whether a failed call was billed;
  - the client (`web/`): a turn's total, `consumed()` and the dashboard's ratio, with the same definition in the numerator and the denominator, computed with `processedTokens(usage)` from `web/src/lib/costs.ts`.
- `Usage` does not change in the protocol: the client computes the processed tokens.
- Each row of `stats.daily` carries `cache_write_tokens`. The per-agent totals already carried it.
- Compaction and `UNCHANGED` were already estimates of input and output tokens from the text. They do not change.
- `CACHE_KEY_VERSION` goes up to 4. The earlier entries counted `input + output` and hid the declined attempts inside the usage of the served message, so a hit would have given the saving with the old figure, and the fallback valued at the price of the final model.
- The savings rows stored before this change keep the old definition (`input + output`, in the turn cache and in the consensus stop). They are not migrated, because the rows do not say how much cache there was.

### Declined attempts of a fallback

- The providers' contract (`providers/base.py`):
  - `DeclinedAttempt(model, usage)` is an attempt that its model declined before another model took the request.
  - `GenerationResult.declined` is the billed attempts that other models declined before `model` served the answer, in order. Without a fallback, it is empty.
  - `ProviderError`, and so `RefusalError`, also carries `declined`: a refusal, or an output limit used up without any text, after a fallback.
  - `usage` is always the usage of the attempt that produced the result or the error, at the rates of its `model`. The tokens of different models are never added up.
- The adapter of Claude's API reads `usage.iterations`:
  - Each `message` entry is a declined attempt, paired in order with the `fallback` block that gives the category and the model that declined. The `fallback_message` entry is the attempt that served.
  - Only billed attempts count: those with output, or declined before the output in a category that Anthropic bills anyway.
  - The model of each attempt is that of its entry. If it is not there, the `from` of its block; if that is not there either, the model the previous attempt had passed the request to; and otherwise, the model that was asked for.
- The engine records each declined attempt as a billed call without a message: a usage row with its model and its cost, `ok = False` and the error `<model> declined the request and passed it on to another model.`
  - It counts towards the turn's total (`outcome.usage`, [ADR 0007](0007-turn-outcome.md)) and towards `unstored_usage`.
  - If it carried the compacted context, it also counts towards the compaction saving.
  - The compaction summaries do the same.
- The served message keeps the usage of its attempt (`meta.usage` and `stream.completed.usage`) and keeps the declined attempts in `meta.declined`, `[{model, usage}]`.
- In a refusal after a fallback, `RefusalError.usage` is that of the attempt that refused, which may bill nothing, and `declined`, those that came before. `stream.failed.usage` is the cost of the failed call; the declined attempts count towards the turn's total.

### Value of a cache hit

- A hit is worth the whole original turn: the calls of each message and the attempts declined before each of them (`meta.declined`), each at the current rates of its model.
- The tokens saved are the processed tokens of these same calls. That way, the saving and its value come from the same data, and they square.
- The failed calls of the original turn that were retried do not count, as before: they are not part of the answer that is replayed.

## Alternatives considered

- **Keeping `total_tokens` next to `processed_tokens`:** no consumer needed it, and having two similar definitions invited mixing them up again.
- **Adding `processed_tokens` to the protocol's `Usage`:** it is derived from the other fields. The client computes it with a function.
- **Migrating the old savings rows:** the rows do not say how much cache there was, so any figure would be made up.
- **Not raising `CACHE_KEY_VERSION`, and documenting that the old entries keep the old figure:** during the 7 days the entries live, hits would come out with 103 tokens, and fallbacks valued at the price of the final model.
- **Adding the tokens of the declined attempts to the `usage`, and putting the right cost aside:** a `Usage` with the tokens of two models cannot be priced again (the value of a hit is computed at the current prices), and it goes against Anthropic's rule.
- **A single usage row per call, with the cost of all the attempts:** it would break the rule that a row is a call of a single model, which the per-model statistics depend on.
- **Valuing a hit with the served message alone:** the saving of a turn with a fallback would be worth less than what the turn cost.

## Consequences

- The tokens of the turn, of the dashboard and of the savings are the processed tokens: 30,103 instead of 103 in the audit's case. The savings ratio is consistent.
- The amounts of a turn with a fallback are the ones Anthropic bills: 0.370625 USD in the audit's case, with a Fable 5.1 row (0.240125, `ok = False`) and an Opus 4.8 one (0.1305). A refusal after a fallback is worth 0.11 USD, at Fable 5.1's rates.
- `stats.totals.errors` also counts the declined attempts.
- The turn cache entries from before version 4 are no longer used, and they expire on their own within 7 days. The first time a question is repeated, the model will be called again.
- The savings rows from before this change keep the old definition. A dashboard window that mixes rows of both kinds adds them up as they are.
- The internal contracts and the protocol change: `Usage.processed_tokens` (`domain.py`), `DeclinedAttempt` and `declined` (`providers/base.py`), `CachedTurn.tokens` (`orchestrator/store.py`), `meta.declined`, and `cache_write_tokens` in `stats.daily` ([PROTOCOL.md](../PROTOCOL.md) and `web/src/lib/protocol.ts`).
- This decision is a proposal until the owner accepts it.
