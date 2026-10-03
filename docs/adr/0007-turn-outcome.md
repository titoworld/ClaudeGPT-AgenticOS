# 0007. Turn outcome

- Status: Proposed
- Date: 2026-09-28

## Context

The validation of the audit of 28 September 2026 (points 9 and 14, and new issue N10) showed that the final state of a turn only existed as an ephemeral WebSocket event. It was not stored anywhere, and on reloading the conversation the client had to guess it from the stored messages.

- **Point 9. The cost of a late failure was lost on reload.** In a duel, a billed call that failed after the other agent had stored its answer did not end up in any message: a refusal from Claude's API with usage, or a billed empty answer from any provider. The usage of the calls without a message (`unstored_usage`) was written to the final messages when they were stored, and messages are not written again. Live, the turn cost 0.029316 USD; reloaded, 0.003116. It only affected the reloaded turn: the global statistics come from the usage table and were correct. It was already listed as a limitation in PROTOCOL.md.
- **Point 14. A cancelled duel was rebuilt as completed.** If an agent had stored its answer and then a cancellation, a shutdown or a restart came, the stored data were identical to those of a completed duel with a failed agent. The reason for an agent's failure was lost too.
- **N10. The cost of a failed turn did not show in the turn.** Neither live (`turn.failed` carried no usage) nor on reload, even though it counted in the statistics.

## Decision

### The outcome is stored in the question

- The question is the turn's record (its id is the `turn_id`). The engine creates it with `meta.outcome = null`.
- When the turn ends, the engine decides how it ended, only once, and writes it to the question **before** emitting the final event. The outcome is:
  - `status`: `completed`, `failed` or `cancelled`.
  - `error` (`{kind, message}`): only if it is `failed`, the same one `turn.failed` carries.
  - `failures`: the turn's call failures (those of `stream.failed`), in the order they happened, with `{agent, kind, message, round}`. It can be empty.
  - `usage`: the turn's total. It includes every billed call: the compaction summaries, the failed calls, the attempts declined before a fallback ([ADR 0008](0008-token-accounting.md)) and the calls that stored no message. It is the same value the final event carries.
  - `savings`: the same as in `turn.completed`. A failed or cancelled turn records no savings, as before, and carries zeros. The exception is a turn that is cancelled when it was already storing its savings, just before ending, with all its messages stored: the savings rows are written whole (in a task of their own, like the outcome), and the outcome carries them and is written afterwards. That way it matches what the dashboard counts.
  - `consensus`: that of a completed debate; `null` in the other cases.
  - `final_message_ids`: the final messages stored. In a cancelled duel, the answer that had already been stored.
  - `cached`: whether the turn was served from the turn cache.
- The store's contract (`orchestrator/store.py`) has `set_turn_outcome(question_message_id, outcome)`:
  - SQLite writes it with `json_set(meta, '$.outcome', json(?))` (JSON1, which the statistics already use). It stays a JSON object, without touching the other keys or the conversation's `updated_at`. No schema migration is needed.
  - An id that is not a question, or that no longer exists because the conversation was deleted, changes nothing.
  - `InMemoryStore` does the same.
- The engine's paths:
  - `completed`: when the turn finishes, also when it is served from the cache.
  - `failed`: on every path that emits `turn.failed`, an internal error included. A `CancelledError` that does not come from any cancellation (a call raises it on its own, because of an error of a provider or of a library) is an internal error: live, `turn.failed` arrives, and the stored outcome says the same.
  - `cancelled`: when the turn is cancelled (the owner stops it, the conversation is deleted or the server shuts down).
- Cancellation:
  - The engine first stops the turn's calls. Then it decides the outcome and writes it in a task of its own, which it awaits with `asyncio.shield`. Finally, it raises the `CancelledError` again, which always propagates.
  - A turn is cancelled only once. The web layer does not cancel a turn that is already stopping again: neither on a second `turn.cancel` (the owner pressing "Stop" again) nor when the server shuts down. Besides, if the consumer of `Engine.run` is cancelled again while the turn is stopping, the engine does not interrupt it: it waits for the turn to end (for the calls to stop, which on a CLI can take a few seconds, and for the outcome to be written) and then lets the `CancelledError` go on. That way, `turn.cancelled` always arrives after the outcome has been stored, with the same `usage`, and no write of the turn reaches a database that the server has already closed.
  - The write is still protected with `asyncio.shield`: if the turn's task were cancelled again while the outcome is being written, it would stop waiting, but the write would finish anyway.
  - If the cancellation arrives while an outcome that was already decided (`completed` or `failed`) is being written, that one is kept: an outcome is never overwritten.
- If the write fails, it is logged and the turn does not fail. The question stays `null`.

### Events

- `stream.failed` carries an optional `usage`: what the failed call billed, with the cost (a refusal, an empty answer, the output limit used up without any text). It is not there if nothing is known to have been billed.
- `turn.failed` and `turn.cancelled` carry `usage`: the turn's total, the same value as `outcome.usage`. It is zero if the turn fails before any call.
- `turn.cancelled` is emitted by the web layer when the turn's task ends, which does not happen until the outcome has been stored (see "Cancellation"). The engine hands it the outcome with a function, the `on_outcome` of `Engine.run`, which it calls as soon as it has decided the outcome.

### Rebuilding in the client (`web/`)

- If `outcome` is there, it is used: the status, the total (without adding `compaction_usage` or `unstored_usage` again), the failures on the agents' cards with their reason, and a notice if the turn was cancelled.
- If `outcome` is `null`, the turn did not finish (a crash or a restart): "This turn was not completed".
- If the key is not there (turns stored before this decision), the earlier rebuild is kept.
- The final messages still carry `unstored_usage` for the rebuilds without `outcome`. The total of a new turn, however, is `outcome.usage`.

## Alternatives considered

- **Writing `unstored_usage` again to the last final message when a late failure arrives:** it fixes point 9, but neither point 14 (a cancelled duel and a completed one would still look the same) nor N10 (a failed turn has no final message to write it to).
- **A new `turns` table:** it would be cleaner to query, but it would need a schema migration and a change to the conversations API. The question is already the turn's record, it travels with the conversation, and its metadata are already read with JSON1.
- **Deriving the status from the `usage` table:** the rows do not say whether the turn was cancelled nor why it failed, and they are kept even if the conversation is deleted.
- **Writing the outcome after the final event:** a client that reloaded the conversation at once could read `null` for a finished turn. It is written before, and the cost is a short write before `turn.completed`.
- **Having the engine emit `turn.cancelled`:** once cancelled, the engine cannot emit anything else without swallowing the cancellation. That is why the outcome reaches the web layer through `on_outcome`.
- **Letting a second "Stop", or the server's shutdown, interrupt a turn that is already stopping:** the server would answer a few seconds earlier, but `turn.cancelled` would go out without the cost and before the outcome was stored (a reload right afterwards would show the turn as not completed), and the write of the outcome could find the database already closed.

## Consequences

- A reloaded turn shows the same as it did live: the status, the total and the failures. In the audit's case, the duel costs 0.029316 USD live, reloaded and in the usage table.
- A turn that did not finish (a crash, a restart) is told apart from an old one: `null` instead of a missing key.
- `turn.failed` and `turn.cancelled` show the turn's cost live.
- Each turn makes one more write to SQLite (a small `UPDATE`) before the final event.
- In the window of a few milliseconds in which the outcome is written, a cancellation can make `turn.cancelled` show live while the stored outcome is `completed`. All the turn's messages had already been stored.
- A second "Stop", or the server shutting down while a turn is stopping, does not bring `turn.cancelled` forward: it arrives when the turn has finished stopping. When shutting down, the server waits for the turns that are stopping, for as long as each provider takes to stop a call (a few seconds at most on a CLI, which first gets `SIGTERM` and then `SIGKILL`).
- The internal contracts and the protocol change: `Store.set_turn_outcome`, `TurnOutcome` and `TurnFailure` (`orchestrator/events.py`), the `on_outcome` of `Engine.run` and of `TurnRunner`, and `usage` in `stream.failed`, `turn.failed` and `turn.cancelled` ([PROTOCOL.md](../PROTOCOL.md) and `web/src/lib/protocol.ts`).
- This decision is a proposal until the owner accepts it.
