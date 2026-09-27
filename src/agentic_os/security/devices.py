"""Known owner devices: the login throttling exemption (OWASP "device cookie").

After a successful login the browser also gets a long-lived random device token in
an HttpOnly cookie that survives logout. A login that presents a valid one is
throttled only by that device's own failure counter, never by the per-address or
the global one (see :mod:`agentic_os.security.throttle`), so an anonymous client
that keeps those locked cannot lock the owner out of a browser already used to log
in. Unknown clients stay throttled as before.

Like sessions, only the SHA-256 of the token is stored. Every successful login
replaces the presented device token with a new one; ``agentic-os init`` and
``agentic-os reset-sessions`` forget every device.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from typing import Final, Protocol

from agentic_os.security.sessions import hash_token, is_well_formed, new_token
from agentic_os.storage.models import DeviceRecord, as_utc

DEVICE_MAX_AGE: Final = timedelta(days=365)


class DeviceStore(Protocol):
    """Device persistence (implemented by ``storage.SqliteStore``)."""

    async def create_device(
        self, token_hash: str, *, created_at: datetime, expires_at: datetime
    ) -> None: ...

    async def get_device(self, token_hash: str) -> DeviceRecord | None: ...

    async def delete_device(self, token_hash: str) -> bool: ...

    async def delete_all_devices(self) -> int: ...

    async def purge_expired_devices(self, now: datetime) -> int: ...


class DeviceManager:
    """Issues, recognizes and forgets device tokens on top of a :class:`DeviceStore`."""

    def __init__(self, store: DeviceStore, *, max_age: timedelta = DEVICE_MAX_AGE) -> None:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        self._store = store
        self.max_age = max_age
        """Lifetime; use it as the cookie ``Max-Age``."""

    async def create(self, now: datetime) -> str:
        """Register a new device and return its raw token for the cookie."""
        now = as_utc(now)
        token = new_token()
        await self._store.create_device(
            hash_token(token), created_at=now, expires_at=now + self.max_age
        )
        return token

    async def check(self, token: str | None, now: datetime) -> str | None:
        """The stored hash of a known, unexpired device ``token``, or ``None``.
        Read-only (expired devices are removed by :meth:`purge_expired`)."""
        if not is_well_formed(token):
            return None
        token_hash = hash_token(token)
        record = await self._store.get_device(token_hash)
        if record is None or not hmac.compare_digest(record.token_hash, token_hash):
            return None
        if as_utc(now) >= record.expires_at:
            return None
        return token_hash

    async def revoke(self, token: str | None) -> None:
        """Forget the device of ``token``; unknown tokens are ignored."""
        if is_well_formed(token):
            await self._store.delete_device(hash_token(token))

    async def revoke_all(self) -> int:
        """Forget every device; returns how many there were."""
        return await self._store.delete_all_devices()

    async def purge_expired(self, now: datetime) -> int:
        """Delete expired devices (call periodically)."""
        return await self._store.purge_expired_devices(as_utc(now))
