# 0006. Settings revisions

- Status: Proposed
- Date: 2026-09-28

## Context

The validation of the audit of 28 September 2026 (points 11 and 21, and new issues N5, N11 and N13) showed two things. The dashboard's settings could be lost without any warning, and the price table did not know the base price of a model with a custom price.

**The client did not know which version of the settings it was editing.**

- `PUT /api/settings` replaces all the settings, and the missing keys take their default value.
- The SPA read the settings only once, on entering. If loading them failed, it swallowed the error and went on with the built-in settings. The settings drawer copied them when it opened, and did not read them again.
- Because of that, saving after a failed load, or with the drawer opened before the settings arrived, replaced prices, budgets, models and exchange rates with the built-in values (point 11). The first question could also go out with the built-in mode.
- A drawer open in one tab or on one device silently undid what had been saved in another (N5).
- Saving any setting reset the composer to the default values, even if they had not changed (N11).

**The price table lost the base price.** `GET /api/pricing` returns the effective table, where a custom price replaces the default row of the same model. The interface only looked for the base price among the `default` rows. After saving a custom price, the model showed as "added", "Restore" became "Remove" and "Add" filled in zeros (point 21).

**The interface and the server did not normalize ids in the same way.** The table compared ids with `trim` and lowercase, and the server with `normalize_model`, which also removes the provider prefix, the context and the date. Adding `anthropic/claude-opus-5` created a new "added" row at zero, which the server applied to the whole family (N13).

## Decision

### Settings revision

- `RuntimeSettings` has `revision`, an integer ≥ 0 that counts the saves. It is 0 in a database where the settings have never been saved, and each successful save raises it by 1.
- Saved settings are always at revision 1 or more. Settings saved by an earlier version, without a revision, count as revision 1. That way, revision 0 is only that of the built-in settings, the ones a client has while it has not yet read the server's. A save based on these settings gets `409` if there are saved settings already, also before the first save with this version.
- The revision is stored inside the same JSON as the settings (the `runtime` row of the `settings` table). No schema migration is needed: the row is read and written whole, and an old JSON without `revision` is read as revision 1. It survives a restart.
- The revision never goes back:
  - Every write raises it, also the ones that do not compare (`put_runtime_settings` without `base_revision`, which the tools and the tests use). The stored revision is always the previous one + 1, whatever revision the settings being written carry.
  - If the stored settings are no longer valid and are read as the built-in ones, they keep the stored revision. A stored revision that is not valid, or that is 0, can only come from a hand edit, and it counts as 1.
  - That way, a client based on an old revision can never match the current one again.
- `GET /api/settings` returns the revision with the rest of the settings.

### Saving with a comparison (`PUT /api/settings`)

- The body carries all the settings and `revision`, which is the revision of the settings the client read and has edited. It is required.
- The order of the checks:
  1. The body must be a JSON object; otherwise, `422`.
  2. If `revision` is missing: `422` with `"revision" is required (the revision of the settings the change is based on). Reload the page.` It is what a tab that still has an old version of the SPA gets.
  3. If `revision` is not an integer ≥ 0: `422` with `"revision" must be an integer greater than or equal to 0.`
  4. The other fields are validated as before (`422`).
  5. If the revision is not the current one: `409` with `{"detail": "The settings have changed in another tab or on another device. Review them and save them again.", "settings": <the current settings>}`. Nothing is saved.
  6. If it is the current one, the settings are saved with the revision + 1, and the response (`200`) is the saved settings.
- The comparison and the write are a single *compare-and-swap*. The settings are read, compared and written within the same write transaction (`BEGIN IMMEDIATE`). The connection's lock excludes the process's other tasks, and SQLite's write lock excludes the other processes. Of two requests based on the same revision, one wins and the other gets `409`.
- The missing keys, apart from `revision`, still take their default value. The client must always send all the settings.

### Price table (`GET /api/pricing`)

- Each row carries `key`: the normalized id (`normalize_model`) the server compares models by. Two rows never have the same `key`.
- Each row carries `default`:
  - on a `custom` row that replaces a default price with the same `key`, that default price (`{input, output, cache_read, cache_write}`);
  - `null` on all the others, also on a custom price for a longer id that only shares its prefix with a default price.
- That way, the interface can show the base price, restore it, and fill in a new row with the known price, without having to know the default table.
- The table is still exactly the one the engine uses to compute the costs.

### Shared normalization

- `tests/fixtures/model_ids.json` holds raw ids and the `key` that `normalize_model` gives them. Pytest checks `normalize_model` against these vectors, and vitest checks, against the same vectors, the TypeScript port the interface uses. If either side changes, the tests fail.
- New vectors are added to the file. The existing ones are not changed unless both implementations change at the same time.

### Client (`web/`)

- The app knows whether the settings are loading, are ready, or could not be loaded. No question is sent and nothing is saved until they are ready. If loading fails, the error shows, the owner can retry, and the app retries on its own. A request for the settings or the prices that does not answer within 15 seconds counts as failed, so that a stuck connection does not leave the app waiting until the page is reloaded.
- The composer's default values only apply to the fields the owner has not touched. After a save, only the ones that have changed apply.
- The settings drawer reads the settings and the prices again every time it opens, and it sends the revision when saving. On a `409`, it fills in the form again with the server's settings and warns that they have changed somewhere else.
- The price table uses `key` and `default`:
  - it shows the base price;
  - "Restore the default price" puts the default row back;
  - "Add" fills in the known price for the `key`;
  - an id with the same `key` as an existing row edits that row, and the table says why (for example, that the server treats `anthropic/claude-opus-5` as `claude-opus-5`).

## Alternatives considered

- **`ETag` and `If-Match` (412 Precondition Failed):** it is HTTP's standard mechanism, but the 412 response does not carry the current settings, and another request would be needed to get them. The client would also have to handle headers besides the JSON body. A field in the body is simpler, and it is visible in the settings themselves.
- **Saving only the changed fields (a `PATCH` that merges):** it would not erase what was not touched, but of two edits of the same field, one would still be lost without a warning. Besides, some fields depend on each other (two price keys cannot be the same normalized model), and an automatic merge could save settings that no client has seen.
- **A timestamp (`updated_at`) instead of a counter:** two saves within the same millisecond, or a clock that goes back, would make it ambiguous. A counter is exact.
- **A new column or table for the revision:** it would need a schema migration with no advantage, because the settings are already read and written whole within a single transaction.
- **An optional revision (without one, the `PUT` replaces the settings as before):** a tab with an old version of the SPA, or a client that forgot the revision, would still undo changes silently. With a required revision, an old SPA gets a `422` that asks it to reload the page.
- **Only reading the settings again when the drawer opens, without checking anything on the server:** it does not protect two drawers open at the same time.
- **Prices: a separate route with the default prices, or a client that knows them:** it would duplicate the table and the replacement rule in the client. `default` on the row is the minimum the interface needs.
- **Prices: also returning the default rows that were replaced:** the table would no longer be exactly the one the engine uses, and the guarantee that the price shown is the price charged would be lost.

## Consequences

- A tab or a device can no longer silently undo what was saved on another. It gets a `409`, sees the current settings and must save again. There is no automatic merge: the owner makes the changes needed again.
- An old SPA, cached from before the update, cannot save until it is reloaded (`422`, with a message that says so).
- The internal saves without a comparison (tools and tests) also invalidate the open edits.
- The revision counts saves, but it is not a history: it does not let you recover earlier versions.
- If the app goes back to an earlier version and the settings are saved there, the revision is lost, because that version does not know about it. On updating again, it starts over at 1, and a tab left open since before could match a new revision again. All three things must happen together, so it is very unlikely.
- The normalization of ids exists in two places, in Python and in TypeScript. The shared vectors keep them the same, but a change to `normalize_model` must be made in both places and in the vectors.
- `revision`, `key`, `default` and the `409` are part of the protocol ([PROTOCOL.md](../PROTOCOL.md)). The client's types are in `web/src/lib/protocol.ts`.
- This decision is a proposal until the owner accepts it.
