"""Provider status for ``GET /api/providers`` and the WebSocket ``hello``.

Statuses are gathered concurrently and waited for at most a short timeout. A slow
check is not cancelled: it keeps running in the background (providers cache their
own status) and the last known status, or a placeholder, is answered meanwhile.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Final

from agentic_os.domain import AGENTS, AgentName
from agentic_os.providers.base import Provider, ProviderStatus
from agentic_os.server.tasks import cancel_and_wait
from agentic_os.storage import format_ts

logger = logging.getLogger(__name__)

STATUS_WAIT_SECONDS: Final = 3.0
"""How long a request waits for fresh statuses."""
STATUS_HARD_TIMEOUT_SECONDS: Final = 30.0
"""A status check still running after this long is abandoned."""

Wire = dict[str, object]


def status_to_wire(status: ProviderStatus) -> Wire:
    """``ProviderStatus`` of PROTOCOL.md."""
    return {
        "agent": status.agent,
        "mode": status.mode,
        "available": status.available,
        "model": status.model,
        "detail": status.detail,
        "limits": [
            {
                "window": limit.window,
                "used_percent": limit.used_percent,
                "resets_at": format_ts(limit.resets_at) if limit.resets_at else None,
                "status": limit.status,
            }
            for limit in status.limits
        ],
    }


def _placeholder(agent: AgentName, provider: Provider, detail: str) -> ProviderStatus:
    return ProviderStatus(agent=agent, mode=provider.mode, available=False, model="", detail=detail)


class ProviderMonitor:
    """Concurrent, timeout-bounded status checks of every provider."""

    def __init__(
        self,
        providers: Mapping[AgentName, Provider],
        *,
        wait_seconds: float = STATUS_WAIT_SECONDS,
        hard_timeout_seconds: float = STATUS_HARD_TIMEOUT_SECONDS,
    ) -> None:
        self._providers = {agent: providers[agent] for agent in AGENTS if agent in providers}
        self._wait = wait_seconds
        self._hard_timeout = hard_timeout_seconds
        self._last: dict[AgentName, ProviderStatus] = {}
        self._pending: dict[AgentName, asyncio.Task[ProviderStatus]] = {}

    def refresh(self) -> None:
        """Start a check of every provider without waiting (e.g. at startup)."""
        for agent in self._providers:
            self._check(agent)

    async def statuses(self, wait_seconds: float | None = None) -> list[ProviderStatus]:
        """Current status of every provider, in agent order."""
        tasks = {agent: self._check(agent) for agent in self._providers}
        if tasks:
            timeout = self._wait if wait_seconds is None else wait_seconds
            await asyncio.wait(tasks.values(), timeout=timeout)
        result: list[ProviderStatus] = []
        for agent, task in tasks.items():
            if task.done() and not task.cancelled():
                result.append(task.result())
            else:
                result.append(
                    self._last.get(agent)
                    or _placeholder(agent, self._providers[agent], "S'està comprovant l'estat…")
                )
        return result

    async def aclose(self) -> None:
        await cancel_and_wait(list(self._pending.values()))
        self._pending.clear()

    def _check(self, agent: AgentName) -> asyncio.Task[ProviderStatus]:
        task = self._pending.get(agent)
        if task is None:
            task = asyncio.create_task(self._fetch(agent), name=f"status-{agent}")
            self._pending[agent] = task

            def forget(done: asyncio.Task[ProviderStatus]) -> None:
                if self._pending.get(agent) is done:
                    del self._pending[agent]

            task.add_done_callback(forget)
        return task

    async def _fetch(self, agent: AgentName) -> ProviderStatus:
        provider = self._providers[agent]
        try:
            status = await asyncio.wait_for(provider.status(), self._hard_timeout)
        except TimeoutError:
            logger.warning("Status check of %s timed out", agent)
            status = _placeholder(agent, provider, "El proveïdor no respon.")
        except Exception:
            logger.warning("Status check of %s failed", agent, exc_info=True)
            status = _placeholder(agent, provider, "No s'ha pogut consultar l'estat del proveïdor.")
        self._last[agent] = status
        return status
