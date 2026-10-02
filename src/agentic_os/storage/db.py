"""SQLite access layer: one aiosqlite connection plus forward-only schema migrations.

The attachments' bytes are not in the database: :mod:`agentic_os.storage.files` keeps
them next to it, and the ``attachments`` table describes them (with the facts of a PDF's
pages); ``pdf_checks`` keeps Claude's check of a PDF's text, by content.

The connection also has the SQL functions the queries use: ``aos_fold`` (the
case- and accent-insensitive form of a text that conversation searches compare, see
:mod:`agentic_os.storage.search`).

Every operation runs inside :meth:`Database.transaction`, which holds an
``asyncio.Lock`` for its whole duration. aiosqlite executes statements in FIFO
order on its worker thread, so when a task is cancelled mid-transaction the
``ROLLBACK`` queued by the cancellation handler always runs before any statement
of the next transaction: the shared connection is never left half-committed.

The schema version lives in ``PRAGMA user_version``; migration ``n`` (1-based) of
:data:`MIGRATIONS` upgrades the database from version ``n - 1`` to ``n``. Never edit
a released migration: append a new one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

import aiosqlite

from agentic_os.storage.search import FOLD_FUNCTION, sql_fold

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_MS: Final = 5000

SqlParams = Sequence[object]

_V1: Final[tuple[str, ...]] = (
    """
    CREATE TABLE owner (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        password_hash TEXT NOT NULL,
        totp_secret TEXT NOT NULL,
        totp_last_step INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        ip TEXT,
        user_agent TEXT
    ) WITHOUT ROWID
    """,
    "CREATE INDEX sessions_expires_at ON sessions (expires_at)",
    """
    CREATE TABLE login_throttle (
        key TEXT PRIMARY KEY,
        failures INTEGER NOT NULL,
        locked_until TEXT,
        last_failure_at TEXT NOT NULL
    ) WITHOUT ROWID
    """,
    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID",
    """
    CREATE TABLE conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        summary TEXT,
        summary_upto_id INTEGER NOT NULL DEFAULT 0,
        last_mode TEXT CHECK (last_mode IN ('solo', 'duel', 'debate')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX conversations_updated ON conversations (updated_at, id)",
    """
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
        turn_id INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('question', 'answer', 'revision', 'synthesis')),
        agent TEXT CHECK (agent IN ('claude', 'chatgpt')),
        round INTEGER NOT NULL DEFAULT 0 CHECK (round >= 0),
        final INTEGER NOT NULL DEFAULT 0 CHECK (final IN (0, 1)),
        content TEXT NOT NULL,
        meta TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX messages_conversation ON messages (conversation_id, final, id)",
    "CREATE INDEX messages_turn ON messages (turn_id, kind)",
    "CREATE INDEX messages_kind_created ON messages (kind, created_at)",
    # usage and savings keep their rows when a conversation is deleted (no FK):
    # they are the cost history.
    """
    CREATE TABLE usage (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        conversation_id INTEGER,
        turn_id INTEGER,
        agent TEXT NOT NULL,
        provider_mode TEXT NOT NULL,
        model TEXT NOT NULL,
        purpose TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL,
        latency_ms INTEGER NOT NULL,
        ttft_ms INTEGER,
        ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
        error TEXT
    )
    """,
    "CREATE INDEX usage_ts ON usage (ts)",
    """
    CREATE TABLE savings (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        conversation_id INTEGER,
        turn_id INTEGER,
        kind TEXT NOT NULL CHECK (kind IN ('cache', 'compaction', 'early_stop', 'unchanged')),
        tokens INTEGER NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX savings_ts ON savings (ts)",
    # conversation_id: origin of the cached answer, so deleting the conversation
    # also forgets its cached content.
    """
    CREATE TABLE turn_cache (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        conversation_id INTEGER,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX turn_cache_expires_at ON turn_cache (expires_at)",
    "CREATE INDEX turn_cache_conversation ON turn_cache (conversation_id)",
)

_V2: Final[tuple[str, ...]] = (
    # Known owner devices (security/devices.py): SHA-256 of the long-lived device
    # cookie. A login that presents one is throttled per device only.
    """
    CREATE TABLE devices (
        token_hash TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    ) WITHOUT ROWID
    """,
    "CREATE INDEX devices_expires_at ON devices (expires_at)",
)

_V3: Final[tuple[str, ...]] = (
    # Value in USD of each saving (SavingRecord.cost_usd), kept with the savings
    # history: it used to be read only from the messages' meta, which deleting a
    # conversation removes. NULL when it could not be priced.
    "ALTER TABLE savings ADD COLUMN cost_usd REAL",
    "CREATE INDEX savings_turn ON savings (turn_id)",
    # Backfill: until now the value only existed as meta.savings.cost_usd of the last
    # final message of each turn (the turn's total). It goes to the first saving row
    # of the turn, so the stats keep it even after the conversation is deleted.
    """
    UPDATE savings SET cost_usd = (
        SELECT CASE
            WHEN json_type(m.meta, '$.savings.cost_usd') IN ('integer', 'real')
            THEN json_extract(m.meta, '$.savings.cost_usd')
        END
        FROM messages AS m
        WHERE m.turn_id = savings.turn_id AND m.final = 1 AND m.kind != 'question'
          AND CASE WHEN json_valid(m.meta) THEN json_type(m.meta, '$.savings') END = 'object'
        ORDER BY m.id DESC
        LIMIT 1
    )
    WHERE id IN (SELECT MIN(id) FROM savings WHERE turn_id IS NOT NULL GROUP BY turn_id)
    """,
)

_V4: Final[tuple[str, ...]] = (
    # Files attached to questions (docs/adr/0009-adjunts.md). The bytes live outside
    # the database, content-addressed by sha256 (storage/files.py); AUTOINCREMENT so a
    # deleted attachment's id never names another file. `text`: a text file's content
    # or a PDF's extracted text (NULL without one).
    """
    CREATE TABLE attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
        kind TEXT NOT NULL CHECK (kind IN ('image', 'pdf', 'text')),
        mime TEXT NOT NULL,
        name TEXT NOT NULL,
        size INTEGER NOT NULL CHECK (size > 0),
        pages INTEGER CHECK (pages > 0),
        width INTEGER CHECK (width > 0),
        height INTEGER CHECK (height > 0),
        text TEXT,
        has_thumbnail INTEGER NOT NULL DEFAULT 0 CHECK (has_thumbnail IN (0, 1)),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX attachments_sha256 ON attachments (sha256)",
    "CREATE INDEX attachments_created_at ON attachments (created_at)",
    # The attachments of each question, in order. Deleting the message (its
    # conversation) removes the links; an attachment still linked cannot be deleted.
    """
    CREATE TABLE message_attachments (
        message_id INTEGER NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
        attachment_id INTEGER NOT NULL REFERENCES attachments (id),
        position INTEGER NOT NULL CHECK (position >= 0),
        PRIMARY KEY (message_id, position),
        UNIQUE (message_id, attachment_id)
    ) WITHOUT ROWID
    """,
    "CREATE INDEX message_attachments_attachment ON message_attachments (attachment_id)",
)

_V5: Final[tuple[str, ...]] = (
    # What the PDF reader found on each page of a PDF (P7b of docs/adr/0009-adjunts.md):
    # a JSON list of pdf_facts.PdfPage. NULL for images, text files and the PDFs that
    # were not analysed (uploaded before, or the reader could not): those are read as
    # before, unchecked.
    "ALTER TABLE attachments ADD COLUMN pdf_pages TEXT",
    # Claude's check of the text extracted from a PDF (pdf_facts.PdfCheck as JSON), by
    # content and check version: every later turn and conversation with the same file
    # reuses it. It goes when no attachment has the content any more.
    """
    CREATE TABLE pdf_checks (
        sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
        version INTEGER NOT NULL,
        model TEXT NOT NULL,
        result TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (sha256, version)
    )
    """,
)

_V6: Final[tuple[str, ...]] = (
    # The refine mode (docs/adr/0010-mode-perfecciona.md) is a turn mode that the CHECK of
    # conversations.last_mode refuses. SQLite cannot alter a CHECK in place, and rebuilding
    # the table would delete its messages (dropping it runs their ON DELETE CASCADE, and
    # foreign keys cannot be turned off inside the migration's transaction), so the last
    # mode moves to a new column with the old values. last_mode stays, no longer used.
    "ALTER TABLE conversations ADD COLUMN last_turn_mode TEXT "
    "CHECK (last_turn_mode IN ('solo', 'duel', 'debate', 'refine'))",
    "UPDATE conversations SET last_turn_mode = last_mode",
)

MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (_V1, _V2, _V3, _V4, _V5, _V6)
"""Statements of each schema version, oldest first. Append only."""

SCHEMA_VERSION: Final = len(MIGRATIONS)


class SchemaVersionError(RuntimeError):
    """The database was created by a newer version of the application."""


class Tx:
    """Statement helpers bound to the connection of an open transaction."""

    __slots__ = ("_conn",)

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def execute(self, sql: str, params: SqlParams = ()) -> int:
        """Run a statement and return the number of rows it changed."""
        async with self._conn.execute(sql, params) as cursor:
            return cursor.rowcount

    async def insert(self, sql: str, params: SqlParams = ()) -> int:
        """Run an INSERT and return the new rowid."""
        row = await self._conn.execute_insert(sql, params)
        if row is None:  # pragma: no cover - last_insert_rowid() always returns a row
            raise RuntimeError("INSERT did not report a rowid")
        return int(row[0])

    async def fetchall(self, sql: str, params: SqlParams = ()) -> list[sqlite3.Row]:
        return list(await self._conn.execute_fetchall(sql, params))

    async def fetchone(self, sql: str, params: SqlParams = ()) -> sqlite3.Row | None:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None

    async def user_version(self) -> int:
        row = await self.fetchone("PRAGMA user_version")
        return int(row[0]) if row else 0


def _prepare_path(path: Path) -> None:
    """Create the data directory (0700) and the database file (0600) before SQLite
    does, so the file never exists with looser permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        try:
            if candidate.stat().st_mode & 0o077:
                candidate.chmod(0o600)
        except FileNotFoundError:
            continue


class Database:
    """A single SQLite connection (WAL, foreign keys on) shared by the whole process."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn: aiosqlite.Connection | None = conn
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> Database:
        """Open (creating if needed) the database at ``path`` and migrate it to
        :data:`SCHEMA_VERSION`. Raises :class:`SchemaVersionError` if it is newer."""
        await asyncio.to_thread(_prepare_path, path)
        conn = await aiosqlite.connect(
            path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000, cached_statements=256
        )
        try:
            conn.row_factory = sqlite3.Row
            rows = list(await conn.execute_fetchall("PRAGMA journal_mode = WAL"))
            if not rows or str(rows[0][0]).lower() != "wal":
                logger.warning("SQLite could not enable WAL mode for %s", path)
            await conn.execute_fetchall(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            await conn.execute_fetchall("PRAGMA foreign_keys = ON")
            await conn.execute_fetchall("PRAGMA synchronous = NORMAL")
            await conn.create_function(FOLD_FUNCTION, 1, sql_fold, deterministic=True)
            db = cls(conn)
            await db._migrate()
        except BaseException:
            await conn.close()
            raise
        return db

    async def close(self) -> None:
        """Wait for the running transaction, optimize and close. Idempotent."""
        async with self._lock:
            conn, self._conn = self._conn, None
            if conn is None:
                return
            try:
                with contextlib.suppress(sqlite3.Error):
                    await conn.execute_fetchall("PRAGMA optimize")
            finally:
                await conn.close()

    @asynccontextmanager
    async def transaction(self, *, write: bool = True) -> AsyncIterator[Tx]:
        """Run the block in one transaction (``BEGIN IMMEDIATE`` for writes, a
        deferred read snapshot otherwise). Commits on success; rolls back on any
        exception, including cancellation, and re-raises it."""
        async with self._lock:
            conn = self._conn
            if conn is None:
                raise RuntimeError("database is closed")
            try:
                await conn.execute_fetchall("BEGIN IMMEDIATE" if write else "BEGIN")
                yield Tx(conn)
                await conn.execute_fetchall("COMMIT")
            except BaseException:
                # Queued after every statement of this transaction; "no transaction
                # is active" (BEGIN failed or COMMIT already ran) is harmless.
                with contextlib.suppress(sqlite3.Error):
                    await conn.execute_fetchall("ROLLBACK")
                raise

    async def schema_version(self) -> int:
        async with self.transaction(write=False) as tx:
            return await tx.user_version()

    async def _migrate(self) -> None:
        current = await self.schema_version()
        if current > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"La base de dades té la versió d'esquema {current}, més nova que la "
                f"{SCHEMA_VERSION} que coneix aquesta versió de l'aplicació. Actualitza-la."
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            async with self.transaction() as tx:
                # Re-check under the write lock: another process may have migrated.
                if await tx.user_version() >= version:
                    continue
                for statement in MIGRATIONS[version - 1]:
                    await tx.execute(statement)
                await tx.execute(f"PRAGMA user_version = {version}")
            logger.info("Database schema migrated to version %d", version)
