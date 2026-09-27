"""Server-side login sessions.

The browser keeps a random 256-bit token (``secrets.token_urlsafe(32)``) in an
HttpOnly cookie; the database stores only its SHA-256, so a leaked database does
not reveal usable cookies. A session ends after ``session_idle_hours`` without
activity or ``session_max_days`` after login, whichever comes first. Activity is
written at most once per :data:`TOUCH_INTERVAL` to avoid a write per request.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Final, Protocol

from agentic_os.config import Settings
from agentic_os.storage.models import SessionRecord, as_utc

TOKEN_BYTES: Final = 32
TOUCH_INTERVAL: Final = timedelta(minutes=1)
_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9_-]{16,128}")


class SessionStore(Protocol):
    """Session persistence (implemented by ``storage.SqliteStore``)."""

    async def create_session(
        self,
        token_hash: str,
        *,
        created_at: datetime,
        expires_at: datetime,
        ip: str | None,
        user_agent: str | None,
    ) -> None: ...

    async def get_session(self, token_hash: str) -> SessionRecord | None: ...

    async def touch_session(self, token_hash: str, now: datetime) -> None: ...

    async def delete_session(self, token_hash: str) -> bool: ...

    async def delete_all_sessions(self) -> int: ...

    async def purge_expired_sessions(self, now: datetime, idle_timeout: timedelta) -> int: ...


def new_token() -> str:
    """A fresh URL-safe session token (256 bits of entropy)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Hex SHA-256 of a token. A fast hash is right here: the token is random and
    high-entropy, so it cannot be brute-forced like a password."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class SessionManager:
    """Creates, validates and revokes sessions on top of a :class:`SessionStore`."""

    def __init__(
        self,
        store: SessionStore,
        *,
        idle_timeout: timedelta,
        max_age: timedelta,
        touch_interval: timedelta = TOUCH_INTERVAL,
    ) -> None:
        if idle_timeout <= timedelta(0) or max_age <= timedelta(0):
            raise ValueError("session timeouts must be positive")
        self._store = store
        self.idle_timeout = idle_timeout
        self.max_age = max_age
        """Absolute lifetime; use it as the cookie ``Max-Age``."""
        self._touch_interval = touch_interval

    @classmethod
    def from_settings(cls, store: SessionStore, settings: Settings) -> SessionManager:
        return cls(
            store,
            idle_timeout=timedelta(hours=settings.session_idle_hours),
            max_age=timedelta(days=settings.session_max_days),
        )

    async def create(self, now: datetime, *, ip: str | None, user_agent: str | None) -> str:
        """Start a session and return the raw token for the cookie (never stored)."""
        now = as_utc(now)
        token = new_token()
        await self._store.create_session(
            hash_token(token),
            created_at=now,
            expires_at=now + self.max_age,
            ip=ip,
            user_agent=user_agent,
        )
        return token

    async def validate(self, token: str | None, now: datetime) -> SessionRecord | None:
        """The live session for ``token``, or ``None`` (missing, malformed, idle or
        expired; expired sessions are deleted). Refreshes ``last_seen_at`` at most
        once per touch interval."""
        if not token or not _TOKEN_RE.fullmatch(token):
            return None
        now = as_utc(now)
        token_hash = hash_token(token)
        record = await self._store.get_session(token_hash)
        if record is None or not hmac.compare_digest(record.token_hash, token_hash):
            return None
        if now >= record.expires_at or now >= record.last_seen_at + self.idle_timeout:
            await self._store.delete_session(token_hash)
            return None
        if now - record.last_seen_at >= self._touch_interval:
            await self._store.touch_session(token_hash, now)
            record = replace(record, last_seen_at=now)
        return record

    async def revoke(self, token: str | None) -> None:
        """End the session of ``token`` (logout); unknown tokens are ignored."""
        if token and _TOKEN_RE.fullmatch(token):
            await self._store.delete_session(hash_token(token))

    async def revoke_all(self) -> int:
        """End every session; returns how many there were."""
        return await self._store.delete_all_sessions()

    async def purge_expired(self, now: datetime) -> int:
        """Delete idle and expired sessions (call periodically)."""
        return await self._store.purge_expired_sessions(as_utc(now), self.idle_timeout)
