"""SQLite implementation of the orchestrator :class:`~agentic_os.orchestrator.store.Store`
plus the persistence needed by the web layer and the security primitives.

Attachments (docs/adr/0009-adjunts.md): the rows live in the database and the files next
to it (:mod:`agentic_os.storage.files`). Every change to the files (placing an upload,
a thumbnail, deleting and sweeping) holds one lock, and the database transactions are
taken inside it, so a sweep never removes the file of a row being added."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import sqlite3
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Final, Self, cast

from agentic_os.attachments import ORPHAN_TTL
from agentic_os.domain import AGENTS, MessageKind
from agentic_os.fx import FxRate
from agentic_os.orchestrator.events import TurnOutcome
from agentic_os.orchestrator.store import (
    AttachmentNotFoundError,
    CachedTurn,
    History,
    JsonValue,
    NewMessage,
    SavingRecord,
    StoredMessage,
    UsageRecord,
)
from agentic_os.pricing import normalize_model
from agentic_os.providers.base import Attachment, AttachmentKind
from agentic_os.storage.db import Database, Tx
from agentic_os.storage.files import AttachmentFiles, IncomingFile
from agentic_os.storage.models import (
    ATTACHMENT_KINDS,
    TURN_MODES,
    AttachmentRecord,
    ConversationDetail,
    ConversationSummary,
    DeviceRecord,
    OwnerRecord,
    RuntimeSettings,
    SessionRecord,
    StoredFxRate,
    ThrottleState,
    as_utc,
    effective_fx,
    format_ts,
    is_revision,
    parse_ts,
    utc_now,
)
from agentic_os.storage.search import FOLD_FUNCTION, LIKE_ESCAPE, search_pattern
from agentic_os.storage.stats import MonthSpend, Stats, compute_month_spend, compute_stats

logger = logging.getLogger(__name__)

DEFAULT_TITLE: Final = "Conversa nova"
MAX_TITLE_LENGTH: Final = 200
MAX_LIST_LIMIT: Final = 200
_MAX_IP_LENGTH: Final = 64
_MAX_USER_AGENT_LENGTH: Final = 256
_RUNTIME_SETTINGS_KEY: Final = "runtime"
_ECB_RATE_KEY: Final = "fx_ecb"
_CACHE_FORMAT: Final = 1
_MESSAGE_KINDS: Final[tuple[MessageKind, ...]] = ("question", "answer", "revision", "synthesis")
_VERIFIED_OWNER: Final = (
    "WHERE id = 1 AND totp_last_step < ? AND password_hash = ? AND totp_secret = ?"
)
"""Condition of :meth:`SqliteStore.complete_login`: a newer TOTP step, on the owner
whose credentials were verified."""

_MESSAGE_COLUMNS: Final = (
    "id, conversation_id, turn_id, kind, agent, round, final, content, meta, created_at"
)
_SUMMARY_SELECT: Final = """
    SELECT c.id, c.title, c.summary, c.created_at, c.updated_at, c.last_mode,
           (SELECT COUNT(*) FROM messages AS m WHERE m.conversation_id = c.id) AS message_count
    FROM conversations AS c
"""
_ATTACHMENT_COLUMNS: Final = (
    "id, sha256, kind, mime, name, size, pages, width, height, has_thumbnail, created_at, "
    "length(text) AS text_chars"
)
_UNLINKED: Final = (
    "NOT EXISTS (SELECT 1 FROM message_attachments AS l WHERE l.attachment_id = a.id)"
)
"""Condition on ``attachments AS a``: never sent in a turn (or no longer)."""


class ConversationNotFoundError(LookupError):
    """The conversation does not exist (never created or deleted)."""

    def __init__(self, conversation_id: int) -> None:
        super().__init__(f"La conversa {conversation_id} no existeix.")
        self.conversation_id = conversation_id


class AttachmentInUseError(Exception):
    """The attachment was sent in a turn: it stays while its conversation exists."""

    def __init__(self, attachment_id: int) -> None:
        super().__init__(f"L'adjunt {attachment_id} ja forma part d'una conversa.")
        self.attachment_id = attachment_id


class SettingsConflictError(Exception):
    """A save of the runtime settings based on a revision that is no longer the stored
    one: another tab or device saved first. Nothing was written."""

    def __init__(self, current: RuntimeSettings) -> None:
        super().__init__(f"The runtime settings are at revision {current.revision}")
        self.current = current
        """The stored settings, as ``GET /api/settings`` returns them."""


def _dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _choice[T: str](value: object, choices: tuple[T, ...]) -> T | None:
    for choice in choices:
        if value == choice:
            return choice
    return None


def _clean_title(title: str) -> str:
    return " ".join(title.split())


def _row_to_message(row: sqlite3.Row) -> StoredMessage:
    kind = _choice(row["kind"], _MESSAGE_KINDS)
    if kind is None:  # pragma: no cover - guarded by a CHECK constraint
        raise ValueError(f"unknown message kind {row['kind']!r}")
    meta = json.loads(str(row["meta"]))
    return StoredMessage(
        id=cast(int, row["id"]),
        conversation_id=cast(int, row["conversation_id"]),
        turn_id=cast(int, row["turn_id"]),
        kind=kind,
        content=str(row["content"]),
        agent=_choice(row["agent"], AGENTS),
        round=cast(int, row["round"]),
        final=bool(row["final"]),
        meta=cast(dict[str, JsonValue], meta) if isinstance(meta, dict) else {},
        created_at=parse_ts(str(row["created_at"])),
    )


def _row_to_summary(row: sqlite3.Row) -> ConversationSummary:
    return ConversationSummary(
        id=cast(int, row["id"]),
        title=str(row["title"]),
        created_at=parse_ts(str(row["created_at"])),
        updated_at=parse_ts(str(row["updated_at"])),
        last_mode=_choice(row["last_mode"], TURN_MODES),
        message_count=cast(int, row["message_count"]),
    )


def _attachment_kind(value: object) -> AttachmentKind:
    kind = _choice(value, ATTACHMENT_KINDS)
    if kind is None:  # pragma: no cover - guarded by a CHECK constraint
        raise ValueError(f"unknown attachment kind {value!r}")
    return kind


def _row_to_attachment(row: sqlite3.Row) -> AttachmentRecord:
    return AttachmentRecord(
        id=cast(int, row["id"]),
        sha256=str(row["sha256"]),
        kind=_attachment_kind(row["kind"]),
        mime=str(row["mime"]),
        name=str(row["name"]),
        size=cast(int, row["size"]),
        pages=cast(int | None, row["pages"]),
        width=cast(int | None, row["width"]),
        height=cast(int | None, row["height"]),
        text_chars=cast(int | None, row["text_chars"]),
        has_thumbnail=bool(row["has_thumbnail"]),
        created_at=parse_ts(str(row["created_at"])),
    )


def _json_ids(ids: Collection[int]) -> str:
    """A list of ids as one parameter, for ``json_each(?)`` (no limit on how many)."""
    return json.dumps(list(ids))


def _row_to_session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        token_hash=str(row["token_hash"]),
        created_at=parse_ts(str(row["created_at"])),
        last_seen_at=parse_ts(str(row["last_seen_at"])),
        expires_at=parse_ts(str(row["expires_at"])),
        ip=cast(str | None, row["ip"]),
        user_agent=cast(str | None, row["user_agent"]),
    )


def _row_to_throttle(row: sqlite3.Row) -> ThrottleState:
    locked_until = row["locked_until"]
    return ThrottleState(
        key=str(row["key"]),
        failures=cast(int, row["failures"]),
        locked_until=parse_ts(str(locked_until)) if locked_until is not None else None,
        last_failure_at=parse_ts(str(row["last_failure_at"])),
    )


def _new_message_to_json(message: NewMessage) -> dict[str, JsonValue]:
    return {
        "conversation_id": message.conversation_id,
        "kind": message.kind,
        "content": message.content,
        "turn_id": message.turn_id,
        "agent": message.agent,
        "round": message.round,
        "final": message.final,
        "meta": dict(message.meta),
    }


def _new_message_from_json(data: object) -> NewMessage:
    if not isinstance(data, Mapping):
        raise ValueError("invalid cached message")
    kind = _choice(data["kind"], _MESSAGE_KINDS)
    agent = data.get("agent")
    meta = data.get("meta", {})
    turn_id = data.get("turn_id")
    conversation_id = data["conversation_id"]
    round_ = data.get("round", 0)
    if (
        kind is None
        or (agent is not None and _choice(agent, AGENTS) is None)
        or not isinstance(data["content"], str)
        or not isinstance(meta, dict)
        or not isinstance(conversation_id, int)
        or not isinstance(round_, int)
        or (turn_id is not None and not isinstance(turn_id, int))
    ):
        raise ValueError("invalid cached message")
    return NewMessage(
        conversation_id=conversation_id,
        kind=kind,
        content=data["content"],
        turn_id=turn_id,
        agent=_choice(agent, AGENTS),
        round=round_,
        final=bool(data.get("final", False)),
        meta=cast(dict[str, JsonValue], meta),
    )


def _cached_turn_from_json(raw: str) -> CachedTurn:
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("format") != _CACHE_FORMAT:
        raise ValueError("unknown cache format")
    mode = _choice(data.get("mode"), TURN_MODES)
    tokens = data.get("tokens")
    messages = data.get("messages")
    if mode is None or not isinstance(tokens, int) or not isinstance(messages, list):
        raise ValueError("invalid cached turn")
    return CachedTurn(
        mode=mode,
        messages=tuple(_new_message_from_json(m) for m in messages),
        tokens=tokens,
    )


class SqliteStore:
    """Persistence for the whole application on one SQLite database.

    Implements :class:`agentic_os.orchestrator.store.Store` (engine side) and adds
    conversation browsing, attachments, runtime settings, the owner account, sessions,
    login throttling and stats. Open with :meth:`open` and always :meth:`close` (or use
    ``async with``). All methods are safe to call concurrently from many tasks.
    Datetimes returned are aware UTC; naive datetimes passed in are taken as UTC.
    """

    def __init__(
        self,
        db: Database,
        *,
        attachments_dir: Path,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._clock = clock
        self._files = AttachmentFiles(attachments_dir.absolute())
        self._files_lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        path: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
        attachments_dir: Path | None = None,
    ) -> SqliteStore:
        """Open or create the database file at ``path`` (the parent directory is
        created with mode 0700, the file with 0600) and apply pending migrations.

        ``clock`` timestamps new rows (conversations, messages, usage, savings,
        cache entries, attachments); tests inject a fixed clock. The attachments'
        files go to ``attachments_dir``, by default ``attachments`` next to the
        database (created when the first file arrives)."""
        directory = attachments_dir if attachments_dir is not None else path.parent / "attachments"
        return cls(await Database.open(path), clock=clock, attachments_dir=directory)

    async def close(self) -> None:
        await self._db.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def _now(self) -> str:
        return format_ts(self._clock())

    # ------------------------------------------------------------------
    # Engine contract (orchestrator.store.Store)
    # ------------------------------------------------------------------

    async def create_conversation(self, title: str) -> int:
        """Create a conversation; the title is whitespace-normalized and truncated."""
        clean = _clean_title(title)[:MAX_TITLE_LENGTH] or DEFAULT_TITLE
        now = self._now()
        async with self._db.transaction() as tx:
            return await tx.insert(
                "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
                (clean, now, now),
            )

    async def discard_conversation(self, conversation_id: int) -> bool:
        """Delete a conversation that has no messages: one a turn created and then could
        not store its question in. ``False``, and nothing changes, if it has any message
        or does not exist."""
        async with self._db.transaction() as tx:
            deleted = await tx.execute(
                "DELETE FROM conversations WHERE id = ? AND NOT EXISTS "
                "(SELECT 1 FROM messages WHERE conversation_id = ?)",
                (conversation_id, conversation_id),
            )
        return deleted > 0

    async def conversation_exists(self, conversation_id: int) -> bool:
        async with self._db.transaction(write=False) as tx:
            row = await tx.fetchone("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,))
        return row is not None

    async def get_history(self, conversation_id: int) -> History:
        """Summary plus the final messages newer than the summary, oldest first.
        Raises :class:`ConversationNotFoundError`."""
        async with self._db.transaction(write=False) as tx:
            conversation = await tx.fetchone(
                "SELECT summary, summary_upto_id FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)
            upto = int(conversation["summary_upto_id"])
            rows = await tx.fetchall(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages "
                "WHERE conversation_id = ? AND final = 1 AND id > ? ORDER BY id",
                (conversation_id, upto),
            )
        return History(
            summary=conversation["summary"],
            summary_upto_id=upto,
            messages=tuple(_row_to_message(row) for row in rows),
        )

    async def add_message(self, message: NewMessage) -> int:
        """Store a message and return its id.

        A question starts a turn: it must have ``turn_id=None`` and its turn id
        becomes its own id; it also sets the conversation's ``last_mode`` from
        ``meta["mode"]``, and its ``attachments`` are linked in the same transaction
        (as :meth:`link_attachments` does), so with one that does not exist nothing is
        stored. Any other message must reference a question of the same conversation.
        Every message bumps the conversation's ``updated_at``. Raises
        :class:`ConversationNotFoundError`, :class:`AttachmentNotFoundError` or
        :class:`ValueError`.
        """
        is_question = message.kind == "question"
        if is_question and message.turn_id is not None:
            raise ValueError("a question starts its own turn: turn_id must be None")
        if not is_question and message.turn_id is None:
            raise ValueError(f"a {message.kind} message needs the turn_id of its question")
        if message.attachments and not is_question:
            raise ValueError("only a question takes attachments")
        _check_unique(message.attachments)
        if message.round < 0:
            raise ValueError("round must be >= 0")
        meta_json = _dump_json(dict(message.meta))
        mode = _choice(message.meta.get("mode"), TURN_MODES) if is_question else None
        now = self._now()
        async with self._db.transaction() as tx:
            updated = await tx.execute(
                "UPDATE conversations SET updated_at = ?, last_mode = COALESCE(?, last_mode) "
                "WHERE id = ?",
                (now, mode, message.conversation_id),
            )
            if updated == 0:
                raise ConversationNotFoundError(message.conversation_id)
            if not is_question:
                question = await tx.fetchone(
                    "SELECT 1 FROM messages "
                    "WHERE id = ? AND conversation_id = ? AND kind = 'question'",
                    (message.turn_id, message.conversation_id),
                )
                if question is None:
                    raise ValueError(
                        f"turn {message.turn_id} is not a question of conversation "
                        f"{message.conversation_id}"
                    )
            message_id = await tx.insert(
                "INSERT INTO messages (conversation_id, turn_id, kind, agent, round, final, "
                "content, meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.conversation_id,
                    message.turn_id if message.turn_id is not None else 0,
                    message.kind,
                    message.agent,
                    message.round,
                    int(message.final),
                    message.content,
                    meta_json,
                    now,
                ),
            )
            if is_question:
                await tx.execute("UPDATE messages SET turn_id = id WHERE id = ?", (message_id,))
                if message.attachments:
                    await _link(tx, message_id, message.attachments)
        return message_id

    async def set_summary(self, conversation_id: int, summary: str, upto_message_id: int) -> None:
        """Replace the compaction summary, which covers messages with id <=
        ``upto_message_id``. Raises :class:`ConversationNotFoundError`."""
        async with self._db.transaction() as tx:
            updated = await tx.execute(
                "UPDATE conversations SET summary = ?, summary_upto_id = ? WHERE id = ?",
                (summary, upto_message_id, conversation_id),
            )
        if updated == 0:
            raise ConversationNotFoundError(conversation_id)

    async def set_turn_outcome(self, question_message_id: int, outcome: TurnOutcome) -> None:
        """Write how a turn ended into its question's meta (``$.outcome``, a JSON object:
        the other keys and the conversation's ``updated_at`` are kept). An id that is not
        a question, or no longer exists (a deleted conversation), changes nothing."""
        async with self._db.transaction() as tx:
            await tx.execute(
                "UPDATE messages SET meta = json_set(meta, '$.outcome', json(?)) "
                "WHERE id = ? AND kind = 'question'",
                (_dump_json(outcome.to_wire()), question_message_id),
            )

    async def record_usage(self, record: UsageRecord) -> None:
        usage = record.usage
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO usage (ts, conversation_id, turn_id, agent, provider_mode, model, "
                "purpose, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
                "reasoning_tokens, cost_usd, latency_ms, ttft_ms, ok, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._now(),
                    record.conversation_id,
                    record.turn_id,
                    record.agent,
                    record.provider_mode,
                    record.model,
                    record.purpose,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read_tokens,
                    usage.cache_write_tokens,
                    usage.reasoning_tokens,
                    usage.cost_usd,
                    record.latency_ms,
                    record.ttft_ms,
                    int(record.ok),
                    record.error,
                ),
            )

    async def record_saving(self, record: SavingRecord) -> None:
        """Store a saving with its value (``cost_usd``), which, like the tokens, is
        kept when the conversation is deleted."""
        cost = record.cost_usd
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO savings (ts, conversation_id, turn_id, kind, tokens, detail, "
                "cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self._now(),
                    record.conversation_id,
                    record.turn_id,
                    record.kind,
                    record.tokens_saved,
                    record.detail,
                    cost if cost is not None and math.isfinite(cost) else None,
                ),
            )

    async def cache_get(self, key: str, now: datetime) -> CachedTurn | None:
        """The cached turn for ``key`` unless missing or expired at ``now``. An entry
        that cannot be decoded is deleted and reported as a miss."""
        async with self._db.transaction(write=False) as tx:
            row = await tx.fetchone(
                "SELECT value FROM turn_cache WHERE key = ? AND expires_at > ?",
                (key, format_ts(now)),
            )
        if row is None:
            return None
        try:
            return _cached_turn_from_json(str(row["value"]))
        except (ValueError, KeyError, TypeError):
            logger.warning("Discarding undecodable turn cache entry")
            async with self._db.transaction() as tx:
                await tx.execute("DELETE FROM turn_cache WHERE key = ?", (key,))
            return None

    async def cache_put(self, key: str, value: CachedTurn, expires_at: datetime) -> None:
        """Insert or replace the entry for ``key``. It is also removed when the
        conversation its messages came from is deleted."""
        payload = _dump_json(
            {
                "format": _CACHE_FORMAT,
                "mode": value.mode,
                "tokens": value.tokens,
                "messages": [_new_message_to_json(m) for m in value.messages],
            }
        )
        origin = value.messages[0].conversation_id if value.messages else None
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO turn_cache (key, value, conversation_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
                "conversation_id = excluded.conversation_id, created_at = excluded.created_at, "
                "expires_at = excluded.expires_at",
                (key, payload, origin, self._now(), format_ts(expires_at)),
            )

    async def purge_expired_cache(self, now: datetime) -> int:
        """Delete cache entries expired at ``now``; returns how many."""
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM turn_cache WHERE expires_at <= ?", (format_ts(now),)
            )

    # ------------------------------------------------------------------
    # Conversations (web layer)
    # ------------------------------------------------------------------

    async def list_conversations(
        self, limit: int = 50, before: int | None = None, query: str | None = None
    ) -> list[ConversationSummary]:
        """Conversations by most recent activity (``updated_at``), newest first.

        ``before`` is the id of the last conversation of the previous page: only
        conversations after it in this order are returned (none if it no longer
        exists). ``limit`` must be 1..:data:`MAX_LIST_LIMIT`. ``query`` keeps only
        the titles that contain it, without telling case or accents apart
        (:mod:`agentic_os.storage.search`); a blank one is no search. Raises
        :class:`ValueError` (Catalan) for a ``limit`` out of range or a ``query``
        longer than :data:`~agentic_os.storage.search.MAX_SEARCH_LENGTH`."""
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise ValueError(f"«limit» ha de ser un enter entre 1 i {MAX_LIST_LIMIT}.")
        pattern = search_pattern(query)
        conditions: list[str] = []
        params: list[object] = []
        async with self._db.transaction(write=False) as tx:
            if before is not None:
                cursor = await tx.fetchone(
                    "SELECT updated_at FROM conversations WHERE id = ?", (before,)
                )
                if cursor is None:
                    return []
                conditions.append("(c.updated_at, c.id) < (?, ?)")
                params += [cursor["updated_at"], before]
            if pattern is not None:
                conditions.append(f"{FOLD_FUNCTION}(c.title) LIKE ? ESCAPE '{LIKE_ESCAPE}'")
                params.append(pattern)
            where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
            rows = await tx.fetchall(
                f"{_SUMMARY_SELECT} {where}ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
                (*params, limit),
            )
        return [_row_to_summary(row) for row in rows]

    async def get_conversation(self, conversation_id: int) -> ConversationDetail | None:
        """The conversation with its summary and every message (oldest first)."""
        async with self._db.transaction(write=False) as tx:
            row = await tx.fetchone(f"{_SUMMARY_SELECT} WHERE c.id = ?", (conversation_id,))
            if row is None:
                return None
            messages = await tx.fetchall(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            )
        return ConversationDetail(
            conversation=_row_to_summary(row),
            summary=row["summary"],
            messages=tuple(_row_to_message(m) for m in messages),
        )

    async def rename_conversation(
        self, conversation_id: int, title: str
    ) -> ConversationSummary | None:
        """Rename (``updated_at`` is kept). Returns ``None`` if the conversation does
        not exist; raises :class:`ValueError` (Catalan) for an empty or long title."""
        clean = _clean_title(title)
        if not clean:
            raise ValueError("El títol no pot estar buit.")
        if len(clean) > MAX_TITLE_LENGTH:
            raise ValueError(f"El títol no pot tenir més de {MAX_TITLE_LENGTH} caràcters.")
        async with self._db.transaction() as tx:
            updated = await tx.execute(
                "UPDATE conversations SET title = ? WHERE id = ?", (clean, conversation_id)
            )
            if updated == 0:
                return None
            row = await tx.fetchone(f"{_SUMMARY_SELECT} WHERE c.id = ?", (conversation_id,))
        return _row_to_summary(row) if row is not None else None

    async def delete_conversation(self, conversation_id: int) -> bool:
        """Delete the conversation, its messages and the cached answers that came
        from it (usage and savings rows are kept), and the attachments sent in it that
        no other conversation uses, with their files. Returns ``False`` if missing."""
        async with self._files_lock:
            async with self._db.transaction() as tx:
                linked = [
                    int(row[0])
                    for row in await tx.fetchall(
                        "SELECT DISTINCT l.attachment_id FROM message_attachments AS l "
                        "JOIN messages AS m ON m.id = l.message_id WHERE m.conversation_id = ?",
                        (conversation_id,),
                    )
                ]
                await tx.execute(
                    "DELETE FROM turn_cache WHERE conversation_id = ?", (conversation_id,)
                )
                deleted = await tx.execute(
                    "DELETE FROM conversations WHERE id = ?", (conversation_id,)
                )
                removed = await _delete_unlinked(tx, linked) if linked else []
                unused = await _unused_contents(tx, {sha for _, sha in removed})
            if removed:
                await self._remove_files(unused, [attachment_id for attachment_id, _ in removed])
        return deleted > 0

    # ------------------------------------------------------------------
    # Attachments (docs/adr/0009-adjunts.md)
    # ------------------------------------------------------------------

    def content_path(self, sha256: str) -> Path:
        """Absolute path of the stored file with this content."""
        return self._files.content_path(sha256)

    def thumbnail_path(self, attachment_id: int) -> Path:
        """Absolute path of an attachment's thumbnail (it exists if ``has_thumbnail``)."""
        return self._files.thumbnail_path(attachment_id)

    def new_upload(self) -> IncomingFile:
        """A private temporary file for an upload being received (blocking, but only
        creates it): :meth:`add_attachment` stores it once finished, and
        :meth:`IncomingFile.discard` deletes it otherwise."""
        return self._files.incoming()

    async def add_attachment(
        self,
        upload: IncomingFile,
        *,
        kind: AttachmentKind,
        mime: str,
        name: str,
        pages: int | None = None,
        width: int | None = None,
        height: int | None = None,
        text: str | None = None,
    ) -> AttachmentRecord:
        """Store a finished upload (:meth:`IncomingFile.finish`), already checked, and
        its row. Its file is moved to its content-addressed path, or deleted if that
        content is already stored. It is an orphan until a turn links it."""
        if not upload.sha256:
            raise ValueError("the upload is not finished")
        now = self._now()
        async with self._files_lock:
            await asyncio.to_thread(self._files.place, upload)
            # If the row cannot be written, the file stays until the sweep, as after
            # a crash: nothing may remove a file another row could be adding.
            async with self._db.transaction() as tx:
                attachment_id = await tx.insert(
                    "INSERT INTO attachments (sha256, kind, mime, name, size, pages, width, "
                    "height, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        upload.sha256,
                        kind,
                        mime,
                        name,
                        upload.size,
                        pages,
                        width,
                        height,
                        text,
                        now,
                    ),
                )
        return AttachmentRecord(
            id=attachment_id,
            sha256=upload.sha256,
            kind=kind,
            mime=mime,
            name=name,
            size=upload.size,
            pages=pages,
            width=width,
            height=height,
            text_chars=len(text) if text is not None else None,
            has_thumbnail=False,
            created_at=parse_ts(now),
        )

    async def get_attachment(self, attachment_id: int) -> AttachmentRecord | None:
        async with self._db.transaction(write=False) as tx:
            row = await tx.fetchone(
                f"SELECT {_ATTACHMENT_COLUMNS} FROM attachments WHERE id = ?", (attachment_id,)
            )
        return _row_to_attachment(row) if row is not None else None

    async def get_attachments(self, ids: Sequence[int]) -> list[Attachment]:
        """The attachments with these ids, in the given order, in mode ``"full"``, with
        their text (engine contract). Raises :class:`AttachmentNotFoundError` with the
        first id that does not exist, or whose file is gone."""
        if not ids:
            return []
        async with self._db.transaction(write=False) as tx:
            rows = await tx.fetchall(
                f"SELECT {_ATTACHMENT_COLUMNS}, text FROM attachments "
                "WHERE id IN (SELECT value FROM json_each(?))",
                (_json_ids(ids),),
            )
        found = {cast(int, row["id"]): row for row in rows}
        attachments: list[Attachment] = []
        for attachment_id in ids:
            row = found.get(attachment_id)
            if row is None:
                raise AttachmentNotFoundError(attachment_id)
            record = _row_to_attachment(row)
            path = self._files.content_path(record.sha256)
            if not path.is_file():
                logger.warning("The file of attachment %d is missing", attachment_id)
                raise AttachmentNotFoundError(attachment_id)
            attachments.append(
                Attachment(
                    kind=record.kind,
                    name=record.name,
                    mime=record.mime,
                    sha256=record.sha256,
                    size=record.size,
                    path=path,
                    pages=record.pages,
                    width=record.width,
                    height=record.height,
                    text=cast(str | None, row["text"]),
                    mode="full",
                    created_at=record.created_at,
                    has_thumbnail=record.has_thumbnail,
                )
            )
        return attachments

    async def link_attachments(self, message_id: int, ids: Sequence[int]) -> None:
        """Link attachments to a question, in the given order (their positions): from
        then on they are kept while the conversation exists. Raises :class:`ValueError`
        if the message is not a question, already has attachments or an id repeats,
        and :class:`AttachmentNotFoundError` with the first id that does not exist. (The
        engine links a question's attachments as it stores it: ``NewMessage.attachments``.)"""
        _check_unique(ids)
        async with self._db.transaction() as tx:
            question = await tx.fetchone(
                "SELECT 1 FROM messages WHERE id = ? AND kind = 'question'", (message_id,)
            )
            if question is None:
                raise ValueError(f"message {message_id} is not a question")
            if await tx.fetchone(
                "SELECT 1 FROM message_attachments WHERE message_id = ?", (message_id,)
            ):
                raise ValueError(f"message {message_id} already has attachments")
            await _link(tx, message_id, ids)

    async def delete_attachment(self, attachment_id: int) -> bool:
        """Delete an attachment never sent in a turn, its thumbnail and, if no other
        upload has the same content, its file. ``False`` if it does not exist; raises
        :class:`AttachmentInUseError` if a question has it."""
        async with self._files_lock:
            async with self._db.transaction() as tx:
                row = await tx.fetchone(
                    "SELECT sha256 FROM attachments WHERE id = ?", (attachment_id,)
                )
                if row is None:
                    return False
                if await tx.fetchone(
                    "SELECT 1 FROM message_attachments WHERE attachment_id = ?", (attachment_id,)
                ):
                    raise AttachmentInUseError(attachment_id)
                await tx.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
                unused = await _unused_contents(tx, {str(row["sha256"])})
            await self._remove_files(unused, [attachment_id])
        return True

    async def set_thumbnail(self, attachment_id: int, data: bytes) -> bool:
        """Store (or replace) the thumbnail of an attachment, already checked
        (:func:`agentic_os.attachments.thumbnail_type`). ``False`` if the attachment
        does not exist."""
        async with self._files_lock:
            async with self._db.transaction(write=False) as tx:
                exists = await tx.fetchone(
                    "SELECT 1 FROM attachments WHERE id = ?", (attachment_id,)
                )
            if exists is None:
                return False
            # Under the lock no one deletes the attachment meanwhile.
            await asyncio.to_thread(self._files.write_thumbnail, attachment_id, data)
            async with self._db.transaction() as tx:
                await tx.execute(
                    "UPDATE attachments SET has_thumbnail = 1 WHERE id = ?", (attachment_id,)
                )
        return True

    async def purge_attachments(self, now: datetime) -> int:
        """Delete the attachments never sent in a turn that were uploaded
        :data:`~agentic_os.attachments.ORPHAN_TTL` or more before ``now``, with their
        thumbnails and the files no other row uses; then sweep the files that no row
        uses and are older than an hour (leftovers of a crash or of a failed upload).
        Returns how many attachments were deleted."""
        cutoff = format_ts(now - ORPHAN_TTL)
        async with self._files_lock:
            async with self._db.transaction() as tx:
                rows = await tx.fetchall(
                    f"SELECT a.id, a.sha256 FROM attachments AS a "
                    f"WHERE a.created_at <= ? AND {_UNLINKED}",
                    (cutoff,),
                )
                removed = [(int(row[0]), str(row[1])) for row in rows]
                if removed:
                    await tx.execute(
                        "DELETE FROM attachments WHERE id IN (SELECT value FROM json_each(?))",
                        (_json_ids([attachment_id for attachment_id, _ in removed]),),
                    )
                unused = await _unused_contents(tx, {sha for _, sha in removed})
                shas = await tx.fetchall("SELECT DISTINCT sha256 FROM attachments")
                stored = {str(row[0]) for row in shas}
                ids = {int(row[0]) for row in await tx.fetchall("SELECT id FROM attachments")}
            await self._remove_files(unused, [attachment_id for attachment_id, _ in removed])
            swept = await asyncio.to_thread(self._files.sweep, stored=stored, attachment_ids=ids)
        if removed or swept:
            logger.info(
                "Deleted %d attachments never sent and %d unused files", len(removed), swept
            )
        return len(removed)

    async def _remove_files(self, contents: Collection[str], thumbnails: Collection[int]) -> None:
        """Delete these content files and thumbnails (their rows are gone). A failure is
        logged: the sweep deletes them later."""

        def remove() -> None:
            for sha256 in contents:
                self._files.remove_content(sha256)
            for attachment_id in thumbnails:
                self._files.remove_thumbnail(attachment_id)

        try:
            await asyncio.to_thread(remove)
        except OSError:
            logger.exception("Could not delete the files of deleted attachments")

    # ------------------------------------------------------------------
    # Runtime settings
    # ------------------------------------------------------------------

    async def get_runtime_settings(self) -> RuntimeSettings:
        """Stored settings with their revision, or the defaults if never saved (revision
        0) or no longer valid (with the stored revision). Saved settings are always at
        revision 1 or more, also the ones saved before revisions existed. Keys added by
        newer versions take their defaults on older stored values."""
        async with self._db.transaction(write=False) as tx:
            return await _read_runtime_settings(tx)

    async def put_runtime_settings(
        self, settings: RuntimeSettings, *, base_revision: int | None = None
    ) -> RuntimeSettings:
        """Store ``settings`` as the next revision and return them as stored.

        The stored revision is always the previous one plus 1 (``settings.revision`` is
        ignored), so it only grows. With ``base_revision`` (``PUT /api/settings``) this
        is a compare-and-swap: the read, the comparison and the write are one write
        transaction (``BEGIN IMMEDIATE``, which also excludes other processes), and if
        the stored revision is not ``base_revision`` nothing is written and
        :class:`SettingsConflictError` carries the stored settings. Without it the write
        is unconditional (tools and tests)."""
        async with self._db.transaction() as tx:
            current = await _read_runtime_settings(tx)
            if base_revision is not None and base_revision != current.revision:
                raise SettingsConflictError(current)
            stored = dataclasses.replace(settings, revision=current.revision + 1)
            await _put_setting(tx, _RUNTIME_SETTINGS_KEY, stored.to_wire())
        return stored

    # ------------------------------------------------------------------
    # Exchange rate
    # ------------------------------------------------------------------

    async def get_ecb_rate(self) -> StoredFxRate | None:
        """The last ECB rate stored by :meth:`put_ecb_rate`, if any."""
        async with self._db.transaction(write=False) as tx:
            return await _read_ecb_rate(tx)

    async def put_ecb_rate(self, rate: FxRate, fetched_at: datetime) -> None:
        stored = StoredFxRate(rate=rate, fetched_at=as_utc(fetched_at))
        async with self._db.transaction() as tx:
            await _put_setting(tx, _ECB_RATE_KEY, stored.to_json())

    async def current_fx(self, now: datetime) -> FxRate:
        """The rate costs are shown with (see :func:`~agentic_os.storage.models.effective_fx`)."""
        async with self._db.transaction(write=False) as tx:
            return effective_fx(await _read_runtime_settings(tx), await _read_ecb_rate(tx), now)

    # ------------------------------------------------------------------
    # Owner account
    # ------------------------------------------------------------------

    async def get_owner(self) -> OwnerRecord | None:
        """The owner, or ``None`` before ``agentic-os init`` (setup required)."""
        async with self._db.transaction(write=False) as tx:
            row = await tx.fetchone(
                "SELECT password_hash, totp_secret, totp_last_step, created_at, updated_at "
                "FROM owner WHERE id = 1"
            )
        if row is None:
            return None
        return OwnerRecord(
            password_hash=str(row["password_hash"]),
            totp_secret=str(row["totp_secret"]),
            totp_last_step=int(row["totp_last_step"]),
            created_at=parse_ts(str(row["created_at"])),
            updated_at=parse_ts(str(row["updated_at"])),
        )

    async def set_owner(self, *, password_hash: str, totp_secret: str, totp_last_step: int) -> int:
        """Create or replace the owner and, atomically, revoke every session, forget
        every known device and clear the login throttling. Returns the number of
        sessions revoked.

        A login in flight in another process (``agentic-os init`` runs next to the
        server) cannot outlive this: it checked the credentials against the previous
        owner, and :meth:`complete_login` only writes if the owner is still that one.
        So no session or device made with the old credentials survives, and a rehash
        of the old password never replaces the new one."""
        now = self._now()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO owner (id, password_hash, totp_secret, totp_last_step, created_at, "
                "updated_at) VALUES (1, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                "password_hash = excluded.password_hash, totp_secret = excluded.totp_secret, "
                "totp_last_step = excluded.totp_last_step, updated_at = excluded.updated_at",
                (password_hash, totp_secret, totp_last_step, now, now),
            )
            await tx.execute("DELETE FROM devices")
            await tx.execute("DELETE FROM login_throttle")
            return await tx.execute("DELETE FROM sessions")

    async def consume_totp_step(self, step: int) -> bool:
        """Atomically record ``step`` as the last accepted TOTP step if it is newer
        than the stored one. ``False`` means the code was already used (replay) or
        there is no owner. The login uses :meth:`complete_login` instead, which also
        checks that the owner did not change."""
        async with self._db.transaction() as tx:
            updated = await tx.execute(
                "UPDATE owner SET totp_last_step = ? WHERE id = 1 AND totp_last_step < ?",
                (step, step),
            )
        return updated == 1

    async def complete_login(
        self,
        verified: OwnerRecord,
        *,
        step: int,
        session: SessionRecord,
        device: DeviceRecord,
        password_hash: str | None = None,
        ended_session: str | None = None,
        forgotten_device: str | None = None,
    ) -> bool:
        """Finish a login in one write transaction, only if the owner is still
        ``verified``, the snapshot whose password and TOTP secret the credentials were
        checked against, and ``step`` is newer than the last accepted TOTP step.

        Then, all or nothing: record ``step`` (replay protection), store
        ``password_hash`` if given (a rehash, computed before), end the session
        ``ended_session`` presented with the login (session fixation), forget the
        device ``forgotten_device`` presented, and store the new ``session`` and
        ``device`` (token hashes only). ``False``, with nothing written, if the owner
        changed meanwhile (``agentic-os init`` in another process), the code was
        already used or there is no owner: reject the login."""
        verified_owner = (step, verified.password_hash, verified.totp_secret)
        if password_hash is None:
            sql = "UPDATE owner SET totp_last_step = ? " + _VERIFIED_OWNER
            params: tuple[object, ...] = (step, *verified_owner)
        else:
            sql = "UPDATE owner SET totp_last_step = ?, password_hash = ?, updated_at = ? "
            sql += _VERIFIED_OWNER
            params = (step, password_hash, self._now(), *verified_owner)
        async with self._db.transaction() as tx:
            if await tx.execute(sql, params) != 1:
                return False
            if ended_session is not None:
                await tx.execute("DELETE FROM sessions WHERE token_hash = ?", (ended_session,))
            if forgotten_device is not None:
                await tx.execute("DELETE FROM devices WHERE token_hash = ?", (forgotten_device,))
            await _insert_session(tx, session)
            await _insert_device(tx, device)
        return True

    # ------------------------------------------------------------------
    # Sessions (only SHA-256 hashes of the tokens are stored)
    # ------------------------------------------------------------------

    async def create_session(
        self,
        token_hash: str,
        *,
        created_at: datetime,
        expires_at: datetime,
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        session = SessionRecord(
            token_hash=token_hash,
            created_at=created_at,
            last_seen_at=created_at,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
        )
        async with self._db.transaction() as tx:
            await _insert_session(tx, session)

    async def get_session(self, token_hash: str) -> SessionRecord | None:
        async with self._db.transaction(write=False) as tx:
            row = await tx.fetchone(
                "SELECT token_hash, created_at, last_seen_at, expires_at, ip, user_agent "
                "FROM sessions WHERE token_hash = ?",
                (token_hash,),
            )
        return _row_to_session(row) if row is not None else None

    async def touch_session(self, token_hash: str, now: datetime) -> None:
        async with self._db.transaction() as tx:
            await tx.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                (format_ts(now), token_hash),
            )

    async def delete_session(self, token_hash: str) -> bool:
        async with self._db.transaction() as tx:
            deleted = await tx.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        return deleted > 0

    async def delete_all_sessions(self) -> int:
        async with self._db.transaction() as tx:
            return await tx.execute("DELETE FROM sessions")

    async def purge_expired_sessions(self, now: datetime, idle_timeout: timedelta) -> int:
        """Delete sessions past their absolute expiry or idle for ``idle_timeout``."""
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM sessions WHERE expires_at <= ? OR last_seen_at <= ?",
                (format_ts(now), format_ts(now - idle_timeout)),
            )

    # ------------------------------------------------------------------
    # Known devices (only SHA-256 hashes of the device tokens are stored)
    # ------------------------------------------------------------------

    async def create_device(
        self, token_hash: str, *, created_at: datetime, expires_at: datetime
    ) -> None:
        device = DeviceRecord(token_hash=token_hash, created_at=created_at, expires_at=expires_at)
        async with self._db.transaction() as tx:
            await _insert_device(tx, device)

    async def get_device(self, token_hash: str) -> DeviceRecord | None:
        async with self._db.transaction(write=False) as tx:
            row = await tx.fetchone(
                "SELECT token_hash, created_at, expires_at FROM devices WHERE token_hash = ?",
                (token_hash,),
            )
        if row is None:
            return None
        return DeviceRecord(
            token_hash=str(row["token_hash"]),
            created_at=parse_ts(str(row["created_at"])),
            expires_at=parse_ts(str(row["expires_at"])),
        )

    async def delete_device(self, token_hash: str) -> bool:
        async with self._db.transaction() as tx:
            deleted = await tx.execute("DELETE FROM devices WHERE token_hash = ?", (token_hash,))
        return deleted > 0

    async def delete_all_devices(self) -> int:
        async with self._db.transaction() as tx:
            return await tx.execute("DELETE FROM devices")

    async def purge_expired_devices(self, now: datetime) -> int:
        async with self._db.transaction() as tx:
            return await tx.execute("DELETE FROM devices WHERE expires_at <= ?", (format_ts(now),))

    # ------------------------------------------------------------------
    # Login throttling
    # ------------------------------------------------------------------

    async def get_throttle(self, key: str) -> ThrottleState | None:
        async with self._db.transaction(write=False) as tx:
            row = await tx.fetchone(
                "SELECT key, failures, locked_until, last_failure_at FROM login_throttle "
                "WHERE key = ?",
                (key,),
            )
        return _row_to_throttle(row) if row is not None else None

    async def record_throttle_failure(
        self,
        key: str,
        now: datetime,
        *,
        reset_after: timedelta,
        lock_for: Callable[[int], timedelta],
    ) -> ThrottleState:
        """Atomically count one more failure for ``key`` and lock it for
        ``lock_for(failures)``. The count restarts at 1 when the previous failure is
        older than ``reset_after``."""
        now = as_utc(now)
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT failures, last_failure_at FROM login_throttle WHERE key = ?", (key,)
            )
            failures = 1
            if row is not None and now - parse_ts(str(row["last_failure_at"])) <= reset_after:
                failures = int(row["failures"]) + 1
            lock = lock_for(failures)
            locked_until = now + lock if lock > timedelta(0) else None
            await tx.execute(
                "INSERT INTO login_throttle (key, failures, locked_until, last_failure_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (key) DO UPDATE SET "
                "failures = excluded.failures, locked_until = excluded.locked_until, "
                "last_failure_at = excluded.last_failure_at",
                (
                    key,
                    failures,
                    format_ts(locked_until) if locked_until is not None else None,
                    format_ts(now),
                ),
            )
        return ThrottleState(
            key=key,
            failures=failures,
            locked_until=parse_ts(format_ts(locked_until)) if locked_until else None,
            last_failure_at=parse_ts(format_ts(now)),
        )

    async def reset_throttle(self, *keys: str) -> None:
        async with self._db.transaction() as tx:
            for key in keys:
                await tx.execute("DELETE FROM login_throttle WHERE key = ?", (key,))

    async def clear_throttle(self) -> int:
        """Delete every throttle counter and lock (``agentic-os reset-throttle``);
        returns how many there were."""
        async with self._db.transaction() as tx:
            return await tx.execute("DELETE FROM login_throttle")

    async def purge_throttle(self, now: datetime, *, idle: timedelta) -> int:
        """Delete throttle rows whose last failure is older than ``idle`` and that
        are not locked any more."""
        now_ts = format_ts(now)
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM login_throttle WHERE last_failure_at <= ? "
                "AND (locked_until IS NULL OR locked_until <= ?)",
                (format_ts(now - idle), now_ts),
            )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def stats(self, days: int, now: datetime) -> Stats:
        """``Stats`` of PROTOCOL.md for the ``days`` (1-365) UTC days ending on
        ``now``'s date; raises :class:`ValueError` (Catalan) for other values.
        See :mod:`agentic_os.storage.stats` for the exact definitions."""
        async with self._db.transaction(write=False) as tx:
            settings = await _read_runtime_settings(tx)
            fx = effective_fx(settings, await _read_ecb_rate(tx), now)
            return await compute_stats(
                tx,
                days,
                now,
                fx=fx,
                budgets_eur=settings.budgets_eur,
                plans_eur=settings.plans_eur,
            )

    async def month_spend(self, now: datetime) -> MonthSpend:
        """``MonthSpend`` of PROTOCOL.md for ``now``'s UTC calendar month."""
        async with self._db.transaction(write=False) as tx:
            settings = await _read_runtime_settings(tx)
            fx = effective_fx(settings, await _read_ecb_rate(tx), now)
            return await compute_month_spend(tx, now, fx, settings.budgets_eur, settings.plans_eur)


def _check_unique(ids: Sequence[int]) -> None:
    if len(set(ids)) != len(ids):
        raise ValueError("an attachment cannot be linked twice to a message")


async def _link(tx: Tx, message_id: int, ids: Sequence[int]) -> None:
    """Link attachments to a question in ``tx``, in order (their positions). Raises
    :class:`AttachmentNotFoundError` with the first id that does not exist: the caller's
    transaction is then rolled back, so nothing is linked."""
    rows = await tx.fetchall(
        "SELECT id FROM attachments WHERE id IN (SELECT value FROM json_each(?))",
        (_json_ids(ids),),
    )
    existing = {int(row[0]) for row in rows}
    for attachment_id in ids:
        if attachment_id not in existing:
            raise AttachmentNotFoundError(attachment_id)
    for position, attachment_id in enumerate(ids):
        await tx.execute(
            "INSERT INTO message_attachments (message_id, attachment_id, position) "
            "VALUES (?, ?, ?)",
            (message_id, attachment_id, position),
        )


async def _delete_unlinked(tx: Tx, ids: Collection[int]) -> list[tuple[int, str]]:
    """Delete, among ``ids``, the attachments no question has; returns their ids and
    contents."""
    rows = await tx.fetchall(
        f"SELECT a.id, a.sha256 FROM attachments AS a "
        f"WHERE a.id IN (SELECT value FROM json_each(?)) AND {_UNLINKED}",
        (_json_ids(ids),),
    )
    removed = [(int(row[0]), str(row[1])) for row in rows]
    if removed:
        await tx.execute(
            "DELETE FROM attachments WHERE id IN (SELECT value FROM json_each(?))",
            (_json_ids([attachment_id for attachment_id, _ in removed]),),
        )
    return removed


async def _unused_contents(tx: Tx, contents: Collection[str]) -> list[str]:
    """The contents (SHA-256) among ``contents`` that no attachment row has any more."""
    if not contents:
        return []
    rows = await tx.fetchall(
        "SELECT value FROM json_each(?) WHERE value NOT IN (SELECT sha256 FROM attachments)",
        (json.dumps(sorted(contents)),),
    )
    return [str(row[0]) for row in rows]


async def _insert_session(tx: Tx, session: SessionRecord) -> None:
    await tx.execute(
        "INSERT INTO sessions (token_hash, created_at, last_seen_at, expires_at, ip, "
        "user_agent) VALUES (?, ?, ?, ?, ?, ?)",
        (
            session.token_hash,
            format_ts(session.created_at),
            format_ts(session.last_seen_at),
            format_ts(session.expires_at),
            session.ip[:_MAX_IP_LENGTH] if session.ip else None,
            session.user_agent[:_MAX_USER_AGENT_LENGTH] if session.user_agent else None,
        ),
    )


async def _insert_device(tx: Tx, device: DeviceRecord) -> None:
    await tx.execute(
        "INSERT INTO devices (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
        (device.token_hash, format_ts(device.created_at), format_ts(device.expires_at)),
    )


async def _get_setting(tx: Tx, key: str) -> object | None:
    row = await tx.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return None
    try:
        value: object = json.loads(str(row["value"]))
    except ValueError:
        return None
    return value


async def _put_setting(tx: Tx, key: str, value: JsonValue) -> None:
    await tx.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, _dump_json(value)),
    )


def _legacy_prices(stored: Mapping[str, object]) -> Mapping[str, object]:
    """``stored`` without the owner prices that older versions accepted and are now
    refused: ids that normalize to nothing (they were never applied) and repeated
    ids of one model (only the last one was applied, and it is the one kept). So the
    rest of the owner's settings survive the stricter validation."""
    prices = stored.get("prices")
    if not isinstance(prices, Mapping):
        return stored
    kept: dict[str, tuple[str, object]] = {}
    for model, price in prices.items():
        if not isinstance(model, str):
            return stored  # not written by this application: refused as a whole
        normalized = normalize_model(model)
        if normalized:
            kept.pop(normalized, None)
            kept[normalized] = (model, price)
    if len(kept) == len(prices):
        return stored
    logger.warning(
        "Ignoring %d stored owner prices that name no model or repeat one",
        len(prices) - len(kept),
    )
    return {**stored, "prices": dict(kept.values())}


async def _read_runtime_settings(tx: Tx) -> RuntimeSettings:
    stored = await _get_setting(tx, _RUNTIME_SETTINGS_KEY)
    if stored is None:
        return RuntimeSettings()
    # Settings that were saved are at least at revision 1, so an edit of the built-in
    # defaults (revision 0: settings never loaded) can never overwrite them. That covers
    # settings saved before revisions existed (no "revision") and the ones only a hand
    # edit can leave (not an object, or a revision that is not valid or is 0).
    if not isinstance(stored, Mapping):
        logger.warning("Stored runtime settings are invalid; using the defaults")
        return RuntimeSettings(revision=1)
    # The revision never goes back, not even when the rest no longer validates and
    # loads as the defaults: an edit based on an older revision must never match it
    # again.
    value = stored.get("revision")
    if is_revision(value) and value >= 1:
        revision = value
    else:
        if value is not None:
            logger.warning("The stored runtime settings have an invalid revision; using 1")
        revision = 1
    try:
        return RuntimeSettings.from_wire({**_legacy_prices(stored), "revision": revision})
    except ValueError:
        logger.warning("Stored runtime settings are invalid; using the defaults")
        return RuntimeSettings(revision=revision)


async def _read_ecb_rate(tx: Tx) -> StoredFxRate | None:
    stored = await _get_setting(tx, _ECB_RATE_KEY)
    if stored is None:
        return None
    try:
        return StoredFxRate.from_json(stored)
    except ValueError:
        logger.warning("The stored ECB exchange rate is invalid; ignoring it")
        return None


__all__ = [
    "DEFAULT_TITLE",
    "MAX_LIST_LIMIT",
    "AttachmentInUseError",
    "ConversationNotFoundError",
    "SettingsConflictError",
    "SqliteStore",
]
