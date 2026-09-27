"""SQLite implementation of the orchestrator :class:`~agentic_os.orchestrator.store.Store`
plus the persistence needed by the web layer and the security primitives."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Final, Self, cast

from agentic_os.domain import AGENTS, MessageKind
from agentic_os.fx import FxRate
from agentic_os.orchestrator.store import (
    CachedTurn,
    History,
    JsonValue,
    NewMessage,
    SavingRecord,
    StoredMessage,
    UsageRecord,
)
from agentic_os.storage.db import Database, Tx
from agentic_os.storage.models import (
    TURN_MODES,
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
    parse_ts,
    utc_now,
)
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

_MESSAGE_COLUMNS: Final = (
    "id, conversation_id, turn_id, kind, agent, round, final, content, meta, created_at"
)
_SUMMARY_SELECT: Final = """
    SELECT c.id, c.title, c.summary, c.created_at, c.updated_at, c.last_mode,
           (SELECT COUNT(*) FROM messages AS m WHERE m.conversation_id = c.id) AS message_count
    FROM conversations AS c
"""


class ConversationNotFoundError(LookupError):
    """The conversation does not exist (never created or deleted)."""

    def __init__(self, conversation_id: int) -> None:
        super().__init__(f"La conversa {conversation_id} no existeix.")
        self.conversation_id = conversation_id


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
    conversation browsing, runtime settings, the owner account, sessions, login
    throttling and stats. Open with :meth:`open` and always :meth:`close` (or use
    ``async with``). All methods are safe to call concurrently from many tasks.
    Datetimes returned are aware UTC; naive datetimes passed in are taken as UTC.
    """

    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._db = db
        self._clock = clock

    @classmethod
    async def open(cls, path: Path, *, clock: Callable[[], datetime] = utc_now) -> SqliteStore:
        """Open or create the database file at ``path`` (the parent directory is
        created with mode 0700, the file with 0600) and apply pending migrations.

        ``clock`` timestamps new rows (conversations, messages, usage, savings,
        cache entries); tests inject a fixed clock."""
        return cls(await Database.open(path), clock=clock)

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
        ``meta["mode"]``. Any other message must reference a question of the same
        conversation. Every message bumps the conversation's ``updated_at``.
        Raises :class:`ConversationNotFoundError` or :class:`ValueError`.
        """
        is_question = message.kind == "question"
        if is_question and message.turn_id is not None:
            raise ValueError("a question starts its own turn: turn_id must be None")
        if not is_question and message.turn_id is None:
            raise ValueError(f"a {message.kind} message needs the turn_id of its question")
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
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO savings (ts, conversation_id, turn_id, kind, tokens, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._now(),
                    record.conversation_id,
                    record.turn_id,
                    record.kind,
                    record.tokens_saved,
                    record.detail,
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
        self, limit: int = 50, before: int | None = None
    ) -> list[ConversationSummary]:
        """Conversations by most recent activity (``updated_at``), newest first.

        ``before`` is the id of the last conversation of the previous page: only
        conversations after it in this order are returned (none if it no longer
        exists). ``limit`` must be 1..:data:`MAX_LIST_LIMIT`."""
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise ValueError(f"«limit» ha de ser un enter entre 1 i {MAX_LIST_LIMIT}.")
        async with self._db.transaction(write=False) as tx:
            if before is None:
                rows = await tx.fetchall(
                    f"{_SUMMARY_SELECT} ORDER BY c.updated_at DESC, c.id DESC LIMIT ?", (limit,)
                )
            else:
                cursor = await tx.fetchone(
                    "SELECT updated_at FROM conversations WHERE id = ?", (before,)
                )
                if cursor is None:
                    return []
                rows = await tx.fetchall(
                    f"{_SUMMARY_SELECT} WHERE (c.updated_at, c.id) < (?, ?) "
                    "ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
                    (cursor["updated_at"], before, limit),
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
        from it (usage and savings rows are kept). Returns ``False`` if missing."""
        async with self._db.transaction() as tx:
            await tx.execute("DELETE FROM turn_cache WHERE conversation_id = ?", (conversation_id,))
            deleted = await tx.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return deleted > 0

    # ------------------------------------------------------------------
    # Runtime settings
    # ------------------------------------------------------------------

    async def get_runtime_settings(self) -> RuntimeSettings:
        """Stored settings, or the defaults if never saved (or no longer valid).
        Keys added by newer versions take their defaults on older stored values."""
        async with self._db.transaction(write=False) as tx:
            return await _read_runtime_settings(tx)

    async def put_runtime_settings(self, settings: RuntimeSettings) -> None:
        async with self._db.transaction() as tx:
            await _put_setting(tx, _RUNTIME_SETTINGS_KEY, settings.to_wire())

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
        sessions revoked."""
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

    async def update_password_hash(self, password_hash: str) -> None:
        """Replace the stored hash (e.g. after a successful login that needed a rehash)."""
        async with self._db.transaction() as tx:
            await tx.execute(
                "UPDATE owner SET password_hash = ?, updated_at = ? WHERE id = 1",
                (password_hash, self._now()),
            )

    async def consume_totp_step(self, step: int) -> bool:
        """Atomically record ``step`` as the last accepted TOTP step if it is newer
        than the stored one. ``False`` means the code was already used (replay) or
        there is no owner: reject the login."""
        async with self._db.transaction() as tx:
            updated = await tx.execute(
                "UPDATE owner SET totp_last_step = ? WHERE id = 1 AND totp_last_step < ?",
                (step, step),
            )
        return updated == 1

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
        created = format_ts(created_at)
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO sessions (token_hash, created_at, last_seen_at, expires_at, ip, "
                "user_agent) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    token_hash,
                    created,
                    created,
                    format_ts(expires_at),
                    ip[:_MAX_IP_LENGTH] if ip else None,
                    user_agent[:_MAX_USER_AGENT_LENGTH] if user_agent else None,
                ),
            )

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
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO devices (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
                (token_hash, format_ts(created_at), format_ts(expires_at)),
            )

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


async def _read_runtime_settings(tx: Tx) -> RuntimeSettings:
    stored = await _get_setting(tx, _RUNTIME_SETTINGS_KEY)
    if stored is None:
        return RuntimeSettings()
    try:
        return RuntimeSettings.from_wire(stored)
    except ValueError:
        logger.warning("Stored runtime settings are invalid; using the defaults")
        return RuntimeSettings()


async def _read_ecb_rate(tx: Tx) -> StoredFxRate | None:
    stored = await _get_setting(tx, _ECB_RATE_KEY)
    if stored is None:
        return None
    try:
        return StoredFxRate.from_json(stored)
    except ValueError:
        logger.warning("The stored ECB exchange rate is invalid; ignoring it")
        return None


__all__ = ["DEFAULT_TITLE", "MAX_LIST_LIMIT", "ConversationNotFoundError", "SqliteStore"]
