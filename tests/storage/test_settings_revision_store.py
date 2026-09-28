"""Revision of the stored runtime settings (ADR 0006): it is 0 until the settings are
first saved and at least 1 afterwards, every write adds 1, a save based on another
revision writes nothing (the read, the comparison and the write are one transaction)
and it survives reopening the database."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agentic_os.storage import RuntimeSettings, SettingsConflictError, SqliteStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "db.sqlite3") as store:
        yield store


async def write_raw(store: SqliteStore, value: object) -> None:
    """Store ``value`` as the settings JSON, as an older version (or a hand edit) left it."""
    async with store._db.transaction() as tx:
        await tx.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (json.dumps(value),),
        )


async def read_raw(store: SqliteStore) -> object:
    async with store._db.transaction(write=False) as tx:
        row = await tx.fetchone("SELECT value FROM settings WHERE key = 'runtime'")
    assert row is not None
    value: object = json.loads(str(row["value"]))
    return value


def test_the_revision_is_part_of_the_settings() -> None:
    assert RuntimeSettings().revision == 0
    assert RuntimeSettings().to_wire()["revision"] == 0
    assert RuntimeSettings.from_wire({}).revision == 0  # the wire default; see the store
    settings = RuntimeSettings.from_wire({"revision": 12, "default_mode": "solo"})
    assert settings == RuntimeSettings(default_mode="solo", revision=12)
    assert RuntimeSettings.from_wire(settings.to_wire()) == settings


@pytest.mark.parametrize("revision", [-1, 1.5, 2.0, "3", True, None, [1], {"revision": 1}])
def test_the_revision_is_an_integer_from_0(revision: object) -> None:
    message = r"^«revision» ha de ser un enter igual o més gran que 0\.$"
    with pytest.raises(ValueError, match=message):
        RuntimeSettings.from_wire({"revision": revision})
    with pytest.raises(ValueError, match=message):
        RuntimeSettings(revision=revision)  # type: ignore[arg-type]


async def test_a_new_database_is_at_revision_0_and_every_write_adds_1(
    store: SqliteStore,
) -> None:
    assert await store.get_runtime_settings() == RuntimeSettings()
    first = await store.put_runtime_settings(RuntimeSettings(default_mode="solo"), base_revision=0)
    assert first == RuntimeSettings(default_mode="solo", revision=1)
    assert await store.get_runtime_settings() == first
    second = await store.put_runtime_settings(
        RuntimeSettings(default_mode="duel", revision=1), base_revision=1
    )
    assert second == RuntimeSettings(default_mode="duel", revision=2)
    # Without a base revision (tools and tests) the write is unconditional, and it moves
    # the revision on too: the revision the settings carry is ignored.
    third = await store.put_runtime_settings(RuntimeSettings(default_mode="solo", revision=40))
    assert third == RuntimeSettings(default_mode="solo", revision=3)
    assert await store.get_runtime_settings() == third
    assert await read_raw(store) == third.to_wire()


async def test_a_save_based_on_another_revision_writes_nothing(store: SqliteStore) -> None:
    saved = await store.put_runtime_settings(RuntimeSettings(use_cache=False), base_revision=0)
    for base in (0, 2, 10**30):
        with pytest.raises(SettingsConflictError) as caught:
            await store.put_runtime_settings(
                RuntimeSettings(default_mode="solo"), base_revision=base
            )
        assert caught.value.current == saved
    assert await store.get_runtime_settings() == saved


async def test_concurrent_saves_on_one_revision_let_exactly_one_win(store: SqliteStore) -> None:
    candidates = [RuntimeSettings(compaction_threshold_tokens=1000 + i) for i in range(8)]
    results = await asyncio.gather(
        *(store.put_runtime_settings(c, base_revision=0) for c in candidates),
        return_exceptions=True,
    )
    [winner] = [result for result in results if isinstance(result, RuntimeSettings)]
    conflicts = [result for result in results if isinstance(result, SettingsConflictError)]
    assert len(conflicts) == len(candidates) - 1
    assert winner.revision == 1
    assert all(conflict.current == winner for conflict in conflicts)
    assert await store.get_runtime_settings() == winner


async def test_two_connections_cannot_both_save_on_one_revision(tmp_path: Path) -> None:
    """Like the server and a tool in another process: the comparison runs under
    SQLite's write lock, so the second save sees the first one."""
    path = tmp_path / "db.sqlite3"
    async with await SqliteStore.open(path) as first, await SqliteStore.open(path) as second:
        results = await asyncio.gather(
            first.put_runtime_settings(RuntimeSettings(default_mode="solo"), base_revision=0),
            second.put_runtime_settings(RuntimeSettings(default_mode="duel"), base_revision=0),
            return_exceptions=True,
        )
        [winner] = [result for result in results if isinstance(result, RuntimeSettings)]
        [conflict] = [result for result in results if isinstance(result, SettingsConflictError)]
        assert winner.revision == 1
        assert conflict.current == winner
        assert await first.get_runtime_settings() == winner
        assert await second.get_runtime_settings() == winner


async def test_the_revision_survives_reopening_the_database(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    async with await SqliteStore.open(path) as store:
        await store.put_runtime_settings(RuntimeSettings(default_mode="solo"), base_revision=0)
        await store.put_runtime_settings(RuntimeSettings(default_mode="duel"), base_revision=1)
    async with await SqliteStore.open(path) as store:
        assert await store.get_runtime_settings() == RuntimeSettings(
            default_mode="duel", revision=2
        )
        with pytest.raises(SettingsConflictError):
            await store.put_runtime_settings(RuntimeSettings(), base_revision=1)
        saved = await store.put_runtime_settings(RuntimeSettings(), base_revision=2)
        assert saved.revision == 3


async def test_settings_saved_before_revisions_existed_are_at_revision_1(
    store: SqliteStore,
) -> None:
    """They were saved at least once: only settings never saved are at revision 0, so
    an edit of the built-in defaults (revision 0) cannot overwrite them."""
    await write_raw(store, {"default_mode": "solo", "use_cache": False})
    loaded = await store.get_runtime_settings()
    assert loaded == RuntimeSettings(default_mode="solo", use_cache=False, revision=1)
    with pytest.raises(SettingsConflictError) as conflict:
        await store.put_runtime_settings(RuntimeSettings(), base_revision=0)
    assert conflict.value.current == loaded
    assert await store.get_runtime_settings() == loaded
    saved = await store.put_runtime_settings(RuntimeSettings(default_mode="duel"), base_revision=1)
    assert saved == RuntimeSettings(default_mode="duel", revision=2)
    assert await read_raw(store) == saved.to_wire()


async def test_settings_that_no_longer_validate_keep_their_revision(store: SqliteStore) -> None:
    """They load as the defaults, but the revision never goes back: a tab based on an
    older revision can never match it again."""
    await write_raw(store, {"revision": 5, "default_mode": "trio"})
    assert await store.get_runtime_settings() == RuntimeSettings(revision=5)
    with pytest.raises(SettingsConflictError):
        await store.put_runtime_settings(RuntimeSettings(), base_revision=0)
    saved = await store.put_runtime_settings(RuntimeSettings(default_mode="solo"), base_revision=5)
    assert saved.revision == 6


@pytest.mark.parametrize("revision", [-3, "7", 1.5, True, None, 0])
async def test_a_stored_revision_that_is_not_valid_counts_as_1(
    store: SqliteStore, revision: object
) -> None:
    # Only a hand edit can leave one (0 included: every save stores at least 1). The
    # settings themselves are kept, and they were saved, so they are not at revision 0.
    await write_raw(store, {"revision": revision, "default_mode": "solo"})
    assert await store.get_runtime_settings() == RuntimeSettings(default_mode="solo", revision=1)
    with pytest.raises(SettingsConflictError):
        await store.put_runtime_settings(RuntimeSettings(), base_revision=0)
    saved = await store.put_runtime_settings(RuntimeSettings(default_mode="duel"), base_revision=1)
    assert saved.revision == 2


@pytest.mark.parametrize("value", [[1, 2], "solo", 7])
async def test_stored_settings_that_are_not_an_object_load_as_the_defaults_at_revision_1(
    store: SqliteStore, value: object
) -> None:
    await write_raw(store, value)
    assert await store.get_runtime_settings() == RuntimeSettings(revision=1)
    with pytest.raises(SettingsConflictError):
        await store.put_runtime_settings(RuntimeSettings(), base_revision=0)
