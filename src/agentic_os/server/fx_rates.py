"""Background refresh of the ECB USD -> EUR rate (only while ``fx.mode`` is ``auto``).

The last rate fetched is stored in the database, so a restart does not need the
network and a failed fetch keeps the previous value (the manual rate takes over when
it is older than ``storage.models.FX_MAX_AGE``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final

from agentic_os import fx
from agentic_os.fx import FxError, FxRate
from agentic_os.storage import SqliteStore

logger = logging.getLogger(__name__)

REFRESH_INTERVAL: Final = timedelta(hours=12)
"""A stored rate older than this is fetched again."""
CHECK_INTERVAL_SECONDS: Final = 3600.0
"""How often the refresher wakes up (so a failed fetch is retried within the hour)."""

FxFetcher = Callable[[], Awaitable[FxRate]]


class FxRefresher:
    """Keeps the stored ECB rate fresh: at startup, whenever it is older than
    :data:`REFRESH_INTERVAL` and when :meth:`poke` is called (the owner switched to
    ``auto``)."""

    def __init__(
        self,
        store: SqliteStore,
        clock: Callable[[], datetime],
        *,
        fetcher: FxFetcher | None = None,
        check_interval_seconds: float = CHECK_INTERVAL_SECONDS,
    ) -> None:
        self._store = store
        self._clock = clock
        self._fetcher = fetcher
        self._check_interval = check_interval_seconds
        self._wake = asyncio.Event()

    def poke(self) -> None:
        """Check now instead of at the next interval."""
        self._wake.set()

    async def refresh(self, *, force: bool = False) -> FxRate | None:
        """Fetch and store the ECB rate if ``fx.mode`` is ``auto`` and the stored one is
        stale (or ``force``). Returns the new rate, or ``None`` if nothing was fetched.
        A failed fetch is logged and the previous rate kept."""
        settings = await self._store.get_runtime_settings()
        if settings.fx.mode != "auto":
            return None
        now = self._clock()
        stored = await self._store.get_ecb_rate()
        if not force and stored is not None and now - stored.fetched_at < REFRESH_INTERVAL:
            return None
        fetch = self._fetcher or fx.fetch_ecb_rate
        try:
            rate = await fetch()
        except FxError as exc:
            logger.warning("Could not refresh the ECB exchange rate: %s", exc)
            return None
        await self._store.put_ecb_rate(rate, now)
        logger.info("ECB exchange rate %s: %.6f EUR per USD", rate.as_of, rate.eur_per_usd)
        return rate

    async def run(self) -> None:
        """Refresh loop for the application's lifetime (cancel it to stop)."""
        while True:
            self._wake.clear()
            try:
                await self.refresh()
            except Exception:
                logger.exception("Exchange rate refresh failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._check_interval)
