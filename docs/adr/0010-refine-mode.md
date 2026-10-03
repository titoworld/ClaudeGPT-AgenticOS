# 0010. Refine mode: a document the two AIs improve until you stop it

- Status: Proposed
- Date: 2026-10-02

## Context

The owner asked for "an option for them to keep debating to perfect a project, but without oversizing the result, that is, an 'infinite' debate until I click the stop button and it stops at the next round or the one after, with the final result", for developments that aim for perfection.

The Council mode (`debate`) is not suited to this as it is:

- It has at most 4 rounds and stops by itself when the two models agree: it is designed to answer a question, not to polish a deliverable.
- Each model rewrites its own answer. There is no single document that improves round after round.
- Nothing curbs the growth. When two models review each other without limits, the text gets longer: each one adds what it thinks is missing, and the result ends up oversized.

## Decision

A fourth turn mode, `refine` (Refine in the interface). The two AIs work on **a single document**.

1. **Round 0:** both answer the brief, as in a Council turn.
2. **Round 1:** the editor (Claude by default) merges the two answers into version 1.
3. **Round 2 onwards:** both review the current version, and the editor writes the next one, applying only the justified changes.

The turn ends when the owner stops it, when neither AI finds anything to change, or when it reaches a limit. The last version is the turn's final answer, and the one the following turns see.

### Against oversizing

- **A closed brief:** every prompt quotes the brief again. A change must say which defect it fixes or which requirement of the brief it meets, and the prompts reject additions the brief does not ask for.
- **Word limit:** the one the owner sets or, if they set none, 1.2 times the word count of version 1 (at least 300).
  - Every prompt states the current length and the limit.
  - The engine checks the limit without any model: a version that goes over it gets one attempt to shorten it and, if it still goes over, the round is discarded and the current version is kept.
  - Version 1 has no previous version to keep. If the merge goes over the owner's limit, it also gets one attempt to shorten it, but if it still goes over (or if it is the copy of an answer, because nobody could merge them), it stays as version 1 anyway, and it is up to the edits of the following rounds to make it fit.
  - Each version is written whole in a single reply of the model, which has at most 16,000 output tokens, reasoning included: a version that does not fit is cut off and not accepted. In practice, the document cannot go beyond about 10,000 words of English prose, and fewer in Catalan or in code.
- **At most 5 changes per round:** 5 proposals per review and 5 changes per edit. Each review must look for defects first, and then for something to remove or simplify.
- **Against oscillation:** every prompt carries the changelog of the previous rounds (the last 30 lines), and undoing a change must be justified.
- **Visibility:** the owner sees each version, what changed from the previous one, and the words and the cost of each round.

### Stopping it

- **"Stop after this round"** (`turn.stop`): the current round finishes (reviews and edit), and the turn closes with the last version.
- **"Stop now"** (`turn.cancel`): the running calls are cancelled. The last complete version is stored anyway as the final answer, without any call, so that nothing already paid for is lost.
- **It stops by itself:**
  - when neither AI finds anything to change two rounds in a row (always);
  - when both give it the threshold (90 by default) or more, without proposing any defect, two rounds in a row (this can be turned off to make it "infinite").
- **Safety limits, always:** a maximum number of rounds (12 by default, up to 50) and a budget in euros (€3 by default; in subscription mode the value at API prices counts, so as not to use up the quota).

When one of the two AIs fails, the other one continues alone. If both fail when there is already a version, that version is the final answer. A Refine turn is never stored in the turn cache.

## Alternatives considered

- **Alternating editors** (a different AI writes each round): more independent, but each editor undoes part of what the other one did, and the document oscillates. A single editor is kept, and the other AI contributes its reviews.
- **Free rounds, without a word limit:** the document grows round after round, which is what the owner wants to avoid.
- **A single AI that reviews itself:** it has no second opinion, and it loses the point of a council.
- **Extending the current Council mode with more rounds:** each AI would keep rewriting its own answer instead of improving a single document, and the synthesis would only come at the end.

## Consequences

- The protocol has a new mode (`refine`) with its options, the `turn.stop` message and the `turn.stopping` and `refine.round` events ([PROTOCOL.md](../PROTOCOL.md)). The messages use the kinds that already exist (`answer`, `revision`, `synthesis`), with `meta.refine`, so the messages table does not change. Only a Refine turn has rounds to finish: a `turn.stop` for another mode is rejected, and that turn is stopped with `turn.cancel`.
- Refine cannot be the default mode in the settings: a turn that lasts until you stop it only starts when the owner chooses it.
- The budget is given in euros, but the engine counts in dollars. The server converts it with the rate the app shows euros with (the ECB's or the manual one), so the turn stops when what the interface shows it has spent reaches the budget.
- The conversations table stores the Refine mode as the last mode in a new column (`last_turn_mode`, migration 6), with the values of the old one, which is kept but no longer used. The old column has a `CHECK` constraint that cannot be modified, and rebuilding the table would drag the messages along.
- The cost of a Refine turn depends on its rounds: a round is three calls (two reviews and an edit, plus a fourth if the new version has to be shortened). The limits on rounds and euros and the automatic stop keep it bounded. The interface shows its cost round by round.
