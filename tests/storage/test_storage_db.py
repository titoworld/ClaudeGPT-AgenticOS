import asyncio
import stat
from pathlib import Path

import pytest

from agentic_os.storage import SqliteStore
from agentic_os.storage.db import MIGRATIONS, SCHEMA_VERSION, Database, SchemaVersionError, Tx


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


async def test_open_creates_private_dir_and_file_with_pragmas(tmp_path: Path) -> None:
    path = tmp_path / "data" / "db.sqlite3"
    db = await Database.open(path)
    try:
        assert _mode(path.parent) == 0o700
        assert _mode(path) == 0o600
        async with db.transaction(write=False) as tx:
            journal = await tx.fetchone("PRAGMA journal_mode")
            foreign_keys = await tx.fetchone("PRAGMA foreign_keys")
            synchronous = await tx.fetchone("PRAGMA synchronous")
            busy = await tx.fetchone("PRAGMA busy_timeout")
        assert journal is not None and journal[0] == "wal"
        assert foreign_keys is not None and foreign_keys[0] == 1
        assert synchronous is not None and synchronous[0] == 1  # NORMAL
        assert busy is not None and busy[0] == 5000
        assert await db.schema_version() == SCHEMA_VERSION == len(MIGRATIONS)
    finally:
        await db.close()
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        assert _mode(wal) == 0o600


async def test_existing_file_permissions_are_tightened(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    path.touch(mode=0o644)
    path.chmod(0o644)
    db = await Database.open(path)
    await db.close()
    assert _mode(path) == 0o600


async def test_migrations_are_idempotent_and_keep_data(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    async with await SqliteStore.open(path) as store:
        conversation_id = await store.create_conversation("Hola")
    for _ in range(2):
        async with await SqliteStore.open(path) as store:
            assert await store.conversation_exists(conversation_id)
    db = await Database.open(path)
    try:
        assert await db.schema_version() == SCHEMA_VERSION
        async with db.transaction(write=False) as tx:
            tables = {
                row[0]
                for row in await tx.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        assert {
            "owner",
            "sessions",
            "login_throttle",
            "settings",
            "conversations",
            "messages",
            "usage",
            "savings",
            "turn_cache",
            "devices",
        } <= tables
    finally:
        await db.close()


async def test_version_1_databases_are_migrated(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    db = await Database.open(path)
    async with db.transaction() as tx:  # back to a version 1 database with data
        await tx.execute("DROP TABLE devices")
        await tx.execute("PRAGMA user_version = 1")
        await tx.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")
    await db.close()

    async def tables(tx: Tx) -> set[str]:
        rows = await tx.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}

    db = await Database.open(path)
    try:
        assert await db.schema_version() == SCHEMA_VERSION == 2
        async with db.transaction(write=False) as tx:
            assert "devices" in await tables(tx)
            row = await tx.fetchone("SELECT value FROM settings WHERE key = 'a'")
        assert row is not None and row[0] == "1"
    finally:
        await db.close()


async def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    db = await Database.open(path)
    async with db.transaction() as tx:
        await tx.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    await db.close()
    with pytest.raises(SchemaVersionError, match="més nova"):
        await Database.open(path)


async def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "db.sqlite3")
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction() as tx:
                await tx.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")
                raise RuntimeError("boom")
        async with db.transaction(write=False) as tx:
            assert await tx.fetchone("SELECT * FROM settings") is None
    finally:
        await db.close()


async def test_cancelled_transaction_rolls_back_and_releases_the_connection(
    tmp_path: Path,
) -> None:
    db = await Database.open(tmp_path / "db.sqlite3")
    inserted = asyncio.Event()

    async def writer() -> None:
        async with db.transaction() as tx:
            await tx.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")
            inserted.set()
            await asyncio.sleep(3600)

    try:
        task = asyncio.create_task(writer())
        await inserted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with asyncio.timeout(5):
            async with db.transaction() as tx:
                assert await tx.fetchone("SELECT * FROM settings") is None
                await tx.execute("INSERT INTO settings (key, value) VALUES ('b', '2')")
        async with db.transaction(write=False) as tx:
            rows = await tx.fetchall("SELECT key FROM settings")
        assert [row[0] for row in rows] == ["b"]
    finally:
        await db.close()


async def test_close_is_idempotent_and_blocks_further_use(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "db.sqlite3")
    await db.close()
    await db.close()
    with pytest.raises(RuntimeError):
        async with db.transaction():
            pass
