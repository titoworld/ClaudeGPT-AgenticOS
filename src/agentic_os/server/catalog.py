"""Model catalog (``GET /api/models``) and effective price table (``GET /api/pricing``).

Model lists come live from each provider (which caches them too) and are cached here
for :data:`MODELS_TTL_SECONDS`. A request waits at most :data:`MODELS_WAIT_SECONDS`;
a slow listing keeps running in the background (bounded by a hard timeout) and the
last known list, or only the default model, is answered meanwhile with ``live: false``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from agentic_os.config import Settings
from agentic_os.domain import AGENTS, AgentName, ProviderMode
from agentic_os.fx import FxRate
from agentic_os.pricing import DEFAULT_PRICES, ModelPrice, normalize_model
from agentic_os.providers.base import ModelInfo, Provider, ProviderStatus
from agentic_os.server.tasks import cancel_and_wait
from agentic_os.storage import RuntimeSettings

logger = logging.getLogger(__name__)

MODELS_WAIT_SECONDS: Final = 6.0
"""How long a request waits for the providers' model lists."""
MODELS_HARD_TIMEOUT_SECONDS: Final = 60.0
"""A listing still running after this long is abandoned."""
MODELS_TTL_SECONDS: Final = 600.0
FALLBACK_TTL_SECONDS: Final = 60.0
"""A fallback (not live) list is retried sooner."""

FAST_MODEL_DEFAULTS: Final[Mapping[tuple[AgentName, ProviderMode], str]] = {
    ("claude", "cli"): "haiku",
    ("claude", "api"): "claude-haiku-4-5",
    ("chatgpt", "cli"): "gpt-6-luna",
    ("chatgpt", "api"): "gpt-6-luna",
}
"""Model of the cheap internal calls (summaries) of each provider when neither the
dashboard nor ``AOS_*_FAST_MODEL`` choose one. Demo providers use ``fake-<agent>-mini``."""

Wire = dict[str, object]


def default_fast_model(agent: AgentName, mode: ProviderMode, settings: Settings) -> str:
    """The provider's own summary model: ``AOS_*_FAST_MODEL`` or the built-in default."""
    if mode == "fake":
        return f"fake-{agent}-mini"
    configured = settings.claude_fast_model if agent == "claude" else settings.chatgpt_fast_model
    return configured or FAST_MODEL_DEFAULTS[(agent, mode)]


def effective_models(
    agent: AgentName,
    mode: ProviderMode,
    provider_model: str,
    runtime: RuntimeSettings,
    settings: Settings,
) -> tuple[str, str]:
    """``(default_model, fast_model)`` of an agent: the owner's choice from the
    dashboard, else the provider's (``provider_model`` is the one its status reports)."""
    default = runtime.models.get(agent) or provider_model
    fast = runtime.fast_models.get(agent) or default_fast_model(agent, mode, settings)
    return default, fast


@dataclass(frozen=True, slots=True)
class ModelListing:
    models: tuple[ModelInfo, ...]
    live: bool
    """False when the provider answered with its static fallback list."""


def _accepts_refresh(method: Callable[..., object]) -> bool:
    try:
        return "refresh" in inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False


async def _list_models(provider: Provider, refresh: bool) -> Sequence[ModelInfo]:
    """``provider.list_models()``, bypassing the provider's own cache on ``refresh``
    when the implementation supports it (``list_models(*, refresh=True)``)."""
    if refresh and _accepts_refresh(provider.list_models):
        method = cast(Callable[..., Awaitable[Sequence[ModelInfo]]], provider.list_models)
        return await method(refresh=True)
    return await provider.list_models()


class ModelCatalog:
    """Concurrent, timeout-bounded and cached model lists of every provider."""

    def __init__(
        self,
        providers: Mapping[AgentName, Provider],
        settings: Settings,
        *,
        wait_seconds: float = MODELS_WAIT_SECONDS,
        hard_timeout_seconds: float = MODELS_HARD_TIMEOUT_SECONDS,
        ttl_seconds: float = MODELS_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = {agent: providers[agent] for agent in AGENTS if agent in providers}
        self._settings = settings
        self._wait = wait_seconds
        self._hard_timeout = hard_timeout_seconds
        self._ttl = ttl_seconds
        self._monotonic = monotonic
        self._cache: dict[AgentName, tuple[float, ModelListing]] = {}
        """Last successful listing and when it expires (kept after expiring, as the
        last known list)."""
        self._pending: dict[AgentName, asyncio.Task[ModelListing | None]] = {}

    async def listings(self, *, refresh: bool = False) -> dict[AgentName, ModelListing | None]:
        """Model list of every provider; ``None`` when there is none yet."""
        now = self._monotonic()
        result: dict[AgentName, ModelListing | None] = {}
        tasks: dict[AgentName, asyncio.Task[ModelListing | None]] = {}
        for agent in self._providers:
            cached = self._cache.get(agent)
            if not refresh and cached is not None and now < cached[0]:
                result[agent] = cached[1]
            else:
                tasks[agent] = self._start(agent, refresh)
        if tasks:
            await asyncio.wait(tasks.values(), timeout=self._wait)
        for agent, task in tasks.items():
            listing = task.result() if task.done() and not task.cancelled() else None
            if listing is None:
                last = self._cache.get(agent)
                listing = ModelListing(last[1].models, live=False) if last else None
            result[agent] = listing
        return {agent: result[agent] for agent in self._providers}

    def to_wire(
        self,
        runtime: RuntimeSettings,
        statuses: Sequence[ProviderStatus],
        listings: Mapping[AgentName, ModelListing | None],
    ) -> Wire:
        """``ModelCatalog`` of PROTOCOL.md from :meth:`listings` and the providers'
        statuses (whose ``model`` is each provider's default)."""
        status_models = {status.agent: status.model for status in statuses}
        wire: Wire = {}
        for agent, provider in self._providers.items():
            listing = listings.get(agent)
            provider_model = status_models.get(agent, "")
            if not provider_model and listing is not None:
                provider_model = next((m.id for m in listing.models if m.is_default), "")
            default, fast = effective_models(
                agent, provider.mode, provider_model, runtime, self._settings
            )
            if listing is None:
                models: tuple[ModelInfo, ...] = (
                    (ModelInfo(id=default, label=default, is_default=True),) if default else ()
                )
                listing = ModelListing(models, live=False)
            wire[agent] = {
                "mode": provider.mode,
                "default_model": default,
                "fast_model": fast,
                "models": [model.to_wire() for model in listing.models],
                "live": listing.live,
            }
        return wire

    async def aclose(self) -> None:
        await cancel_and_wait(list(self._pending.values()))
        self._pending.clear()

    def _start(self, agent: AgentName, refresh: bool) -> asyncio.Task[ModelListing | None]:
        task = self._pending.get(agent)
        if task is None:
            task = asyncio.create_task(self._fetch(agent, refresh), name=f"models-{agent}")
            self._pending[agent] = task

            def forget(done: asyncio.Task[ModelListing | None]) -> None:
                if self._pending.get(agent) is done:
                    del self._pending[agent]

            task.add_done_callback(forget)
        return task

    async def _fetch(self, agent: AgentName, refresh: bool) -> ModelListing | None:
        provider = self._providers[agent]
        try:
            models = await asyncio.wait_for(_list_models(provider, refresh), self._hard_timeout)
        except TimeoutError:
            logger.warning("Listing the models of %s timed out", agent)
            return None
        except Exception:
            logger.warning("Listing the models of %s failed", agent, exc_info=True)
            return None
        if not models:
            return None
        live = getattr(provider, "models_live", True)
        listing = ModelListing(tuple(models), live=live if isinstance(live, bool) else True)
        ttl = self._ttl if listing.live else min(self._ttl, FALLBACK_TTL_SECONDS)
        self._cache[agent] = (self._monotonic() + ttl, listing)
        return listing


def pricing_to_wire(fx: FxRate, overrides: Mapping[str, ModelPrice]) -> Wire:
    """``Pricing`` of PROTOCOL.md: the default prices with the owner's over them (an
    owner price replaces the default of the same normalized model id)."""
    table: dict[str, tuple[str, ModelPrice, str]] = {
        normalize_model(model): (model, price, "default") for model, price in DEFAULT_PRICES.items()
    }
    for model, price in overrides.items():
        table[normalize_model(model)] = (model, price, "custom")
    prices = [
        {"model": model, **price.to_wire(), "source": source}
        for model, price, source in sorted(table.values(), key=lambda entry: entry[0])
    ]
    return {"fx": fx.to_wire(), "prices": prices}
