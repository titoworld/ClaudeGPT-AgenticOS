"""Login throttling: exponential lockout per client and globally.

After ``max_failures`` consecutive failures a key is locked for
``min(base_delay * 2 ** (failures - max_failures), max_delay)``: 5 s, 10 s, 20 s...
up to 15 minutes. Two keys are counted on every failure:

- the client (``ip:<IPv4>`` or ``ip:<IPv6 /64>``, since one IPv6 host usually
  owns a whole /64), and
- ``global``, with a higher threshold (4x by default), because an attacker can
  rotate addresses and there is only one account to guess.

A success resets both. Counters restart after ``reset_after`` without failures.
State lives in the database, so restarting the server does not lift a lockout
(``agentic-os reset-throttle`` does).

A login from a known device (:mod:`agentic_os.security.devices`) uses only
``device:<token hash>``, with the per-client policy: anyone can keep the global
key locked, but not a device they do not hold, so the owner is never locked out
of a browser already used to log in. Its success resets only that key.
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final, Protocol

from agentic_os.config import Settings
from agentic_os.storage.models import ThrottleState, as_utc

GLOBAL_KEY: Final = "global"
UNKNOWN_CLIENT_KEY: Final = "ip:unknown"
DEVICE_KEY_PREFIX: Final = "device:"


class ThrottleStore(Protocol):
    """Throttle persistence (implemented by ``storage.SqliteStore``)."""

    async def get_throttle(self, key: str) -> ThrottleState | None: ...

    async def record_throttle_failure(
        self,
        key: str,
        now: datetime,
        *,
        reset_after: timedelta,
        lock_for: Callable[[int], timedelta],
    ) -> ThrottleState: ...

    async def reset_throttle(self, *keys: str) -> None: ...

    async def purge_throttle(self, now: datetime, *, idle: timedelta) -> int: ...


def client_key(ip: str | None) -> str:
    """Throttle key of a client address: the IPv4 address, the IPv4 behind an
    IPv4-mapped IPv6 address, or the /64 network of an IPv6 address."""
    if not ip:
        return UNKNOWN_CLIENT_KEY
    try:
        address = ipaddress.ip_address(ip.strip())
    except ValueError:
        return UNKNOWN_CLIENT_KEY
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return f"ip:{address.ipv4_mapped}"
        return f"ip:{ipaddress.IPv6Network((int(address), 64), strict=False)}"
    return f"ip:{address}"


def device_key(device: str) -> str:
    """Throttle key of a known device (``device``: the hash of its token)."""
    return f"{DEVICE_KEY_PREFIX}{device}"


class LoginThrottle:
    """Pure lockout policy plus persistence through a :class:`ThrottleStore`.

    Server flow: ``retry_after`` before checking credentials (answer 429 with
    ``Retry-After`` if > 0), ``record_failure`` on a wrong password or code,
    ``record_success`` after a successful login. Every method takes ``device``, the
    token hash of a known device presented by the client, or ``None``."""

    def __init__(
        self,
        store: ThrottleStore,
        *,
        max_failures: int = 5,
        global_max_failures: int | None = None,
        base_delay: timedelta = timedelta(seconds=5),
        max_delay: timedelta = timedelta(minutes=15),
        reset_after: timedelta = timedelta(hours=24),
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be >= 1")
        self._store = store
        self.max_failures = max_failures
        self.global_max_failures = (
            global_max_failures if global_max_failures is not None else 4 * max_failures
        )
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.reset_after = reset_after

    @classmethod
    def from_settings(cls, store: ThrottleStore, settings: Settings) -> LoginThrottle:
        return cls(store, max_failures=settings.login_max_failures)

    def lock_duration(self, failures: int, threshold: int) -> timedelta:
        """Lock applied after the ``failures``-th consecutive failure."""
        if failures < threshold:
            return timedelta(0)
        exponent = min(failures - threshold, 32)
        return min(self.base_delay * (1 << exponent), self.max_delay)

    def _client_lock(self, failures: int) -> timedelta:
        return self.lock_duration(failures, self.max_failures)

    def _global_lock(self, failures: int) -> timedelta:
        return self.lock_duration(failures, self.global_max_failures)

    @staticmethod
    def _wait(now: datetime, *states: ThrottleState | None) -> int:
        remaining = max(
            (
                (state.locked_until - now).total_seconds()
                for state in states
                if state is not None and state.locked_until is not None
            ),
            default=0.0,
        )
        return math.ceil(remaining) if remaining > 0 else 0

    async def retry_after(self, ip: str | None, now: datetime, *, device: str | None = None) -> int:
        """Seconds the client must wait before trying again (0: allowed)."""
        now = as_utc(now)
        if device is not None:
            return self._wait(now, await self._store.get_throttle(device_key(device)))
        client = await self._store.get_throttle(client_key(ip))
        overall = await self._store.get_throttle(GLOBAL_KEY)
        return self._wait(now, client, overall)

    async def record_failure(
        self, ip: str | None, now: datetime, *, device: str | None = None
    ) -> int:
        """Count a failed login; returns the resulting wait in seconds (0: none)."""
        now = as_utc(now)
        if device is not None:
            own = await self._store.record_throttle_failure(
                device_key(device), now, reset_after=self.reset_after, lock_for=self._client_lock
            )
            return self._wait(now, own)
        client = await self._store.record_throttle_failure(
            client_key(ip), now, reset_after=self.reset_after, lock_for=self._client_lock
        )
        overall = await self._store.record_throttle_failure(
            GLOBAL_KEY, now, reset_after=self.reset_after, lock_for=self._global_lock
        )
        return self._wait(now, client, overall)

    async def record_success(self, ip: str | None, *, device: str | None = None) -> None:
        if device is not None:
            await self._store.reset_throttle(device_key(device))
        else:
            await self._store.reset_throttle(client_key(ip), GLOBAL_KEY)

    async def purge(self, now: datetime) -> int:
        """Delete stale, unlocked counters (call periodically)."""
        return await self._store.purge_throttle(as_utc(now), idle=self.reset_after)
