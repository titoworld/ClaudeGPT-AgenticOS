"""Model catalog (live lists, timeouts, cache), price table and ECB rate refresher."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode
from agentic_os.fx import FxError, FxRate
from agentic_os.pricing import DEFAULT_PRICES, ModelPrice
from agentic_os.providers.base import ModelInfo, Provider, ProviderStatus
from agentic_os.providers.factory import build_provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.server.catalog import (
    ModelCatalog,
    effective_models,
    pricing_to_wire,
)
from agentic_os.server.fx_rates import FxRefresher
from agentic_os.storage import FxSettings, RuntimeSettings, SqliteStore, StoredFxRate

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
ECB = FxRate(eur_per_usd=0.8547, as_of=date(2026, 9, 25), source="ecb")


def settings(**overrides: Any) -> Settings:
    return Settings(**{"_env_file": None, **overrides})


class Ticker:
    """Fake monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ListingProvider(FakeProvider):
    """Fake provider whose model list can be slow, fail, or be a fallback list."""

    def __init__(self, agent: AgentName, *, live: bool = True) -> None:
        super().__init__(agent, chunk_delay=0)
        self.gate: asyncio.Event | None = None
        self.fail = False
        self.live = live
        self.calls: list[bool] = []

    @property
    def models_live(self) -> bool:
        return self.live

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        self.calls.append(refresh)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("boom")
        return await super().list_models()


async def snapshot(
    catalog: ModelCatalog,
    runtime: RuntimeSettings,
    statuses: list[ProviderStatus],
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    return catalog.to_wire(runtime, statuses, await catalog.listings(refresh=refresh))


def statuses(**models: str) -> list[ProviderStatus]:
    return [
        ProviderStatus(agent, "fake", True, models.get(agent, f"fake-{agent}"), "Demo")
        for agent in ("claude", "chatgpt")
    ]


# -- default models ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "mode", "expected"),
    [
        ("claude", "cli", "haiku"),
        ("claude", "api", "claude-haiku-4-5"),
        ("chatgpt", "cli", "gpt-6-luna"),
        ("chatgpt", "api", "gpt-6-luna"),
        ("claude", "fake", "fake-claude-mini"),
        ("chatgpt", "fake", "fake-chatgpt-mini"),
    ],
)
async def test_default_fast_models(agent: AgentName, mode: ProviderMode, expected: str) -> None:
    provider = build_provider(agent, mode, settings())
    try:
        assert provider.fast_model == expected
    finally:
        await provider.aclose()


async def test_effective_models_prefer_the_dashboard_then_the_environment() -> None:
    env = settings(claude_fast_model="claude-haiku-4-5-20251001")
    claude = build_provider("claude", "cli", env)
    chatgpt = build_provider("chatgpt", "api", env)
    try:
        assert effective_models("claude", claude, "opus", RuntimeSettings()) == (
            "opus",
            "claude-haiku-4-5-20251001",
        )
        runtime = RuntimeSettings(
            models={"claude": "sonnet", "chatgpt": None},
            fast_models={"claude": "haiku", "chatgpt": None},
        )
        assert effective_models("claude", claude, "opus", runtime) == ("sonnet", "haiku")
        assert effective_models("chatgpt", chatgpt, "gpt-6-astra", runtime) == (
            "gpt-6-astra",
            "gpt-6-luna",
        )
    finally:
        await claude.aclose()
        await chatgpt.aclose()


# -- catalog -----------------------------------------------------------------------------


async def test_catalog_of_live_lists_is_cached() -> None:
    ticker = Ticker()
    providers: dict[AgentName, ListingProvider] = {
        "claude": ListingProvider("claude"),
        "chatgpt": ListingProvider("chatgpt"),
    }
    catalog = ModelCatalog(providers, settings(), ttl_seconds=600, monotonic=ticker)
    runtime = RuntimeSettings(models={"claude": "my-new-model", "chatgpt": None})
    wire = await snapshot(catalog, runtime, statuses())
    assert wire["claude"] == {
        "mode": "fake",
        "default_model": "my-new-model",
        "fast_model": "fake-claude-mini",
        "models": [m.to_wire() for m in await FakeProvider("claude").list_models()],
        "live": True,
    }
    chatgpt = wire["chatgpt"]
    assert isinstance(chatgpt, dict)
    assert chatgpt["default_model"] == "fake-chatgpt"

    await snapshot(catalog, runtime, statuses())
    assert providers["claude"].calls == [False]  # cached
    ticker.now += 601
    await snapshot(catalog, runtime, statuses())
    assert providers["claude"].calls == [False, False]  # expired
    await snapshot(catalog, runtime, statuses(), refresh=True)
    assert providers["claude"].calls == [False, False, True]  # the provider's cache too
    await catalog.aclose()


async def test_a_slow_provider_answers_with_the_default_then_the_last_known_list() -> None:
    ticker = Ticker()
    slow = ListingProvider("claude")
    slow.gate = asyncio.Event()
    providers: dict[AgentName, Provider] = {"claude": slow, "chatgpt": ListingProvider("chatgpt")}
    catalog = ModelCatalog(
        providers, settings(), wait_seconds=0.05, ttl_seconds=600, monotonic=ticker
    )

    wire = await snapshot(catalog, RuntimeSettings(), statuses(claude="opus"))
    assert wire["claude"] == {
        "mode": "fake",
        "default_model": "opus",
        "fast_model": "fake-claude-mini",
        "models": [ModelInfo(id="opus", label="opus", is_default=True).to_wire()],
        "live": False,
    }
    chatgpt = wire["chatgpt"]
    assert isinstance(chatgpt, dict)
    assert chatgpt["live"] is True

    # The listing goes on in the background and fills the cache.
    slow.gate.set()
    await asyncio.sleep(0.01)
    wire = await snapshot(catalog, RuntimeSettings(), statuses())
    claude = wire["claude"]
    assert isinstance(claude, dict)
    assert claude["live"] is True
    assert [m["id"] for m in claude["models"]] == ["fake-claude", "fake-claude-mini"]
    assert slow.calls == [False]  # one call for both requests

    # Expired and slow again: the last known list, flagged as not live.
    slow.gate = asyncio.Event()
    ticker.now += 601
    wire = await snapshot(catalog, RuntimeSettings(), statuses())
    claude = wire["claude"]
    assert isinstance(claude, dict)
    assert claude["live"] is False
    assert [m["id"] for m in claude["models"]] == ["fake-claude", "fake-claude-mini"]
    await catalog.aclose()  # cancels the pending listing


async def test_failures_and_fallback_lists_are_not_live() -> None:
    failing = ListingProvider("claude")
    failing.fail = True
    fallback = ListingProvider("chatgpt", live=False)
    catalog = ModelCatalog({"claude": failing, "chatgpt": fallback}, settings())
    wire = await snapshot(catalog, RuntimeSettings(), [])
    claude, chatgpt = wire["claude"], wire["chatgpt"]
    assert isinstance(claude, dict)
    assert isinstance(chatgpt, dict)
    # Without a status, the default is the list's own default (none here: failed).
    assert claude["models"] == []
    assert claude["default_model"] == ""
    assert claude["live"] is False
    assert chatgpt["live"] is False
    assert chatgpt["default_model"] == "fake-chatgpt"  # the list's default entry
    await catalog.aclose()


# -- prices ------------------------------------------------------------------------------


def test_pricing_merges_the_owner_prices() -> None:
    custom = ModelPrice(4.0, 20.0, 0.4, 5.0)
    wire = pricing_to_wire(
        ECB,
        {"Claude-Opus-5-20260101": custom, "gpt-7-nova": ModelPrice(3.0, 12.0, 0.3, 0.0)},
    )
    assert wire["fx"] == {"eur_per_usd": 0.8547, "as_of": "2026-09-25", "source": "ecb"}
    prices = wire["prices"]
    assert isinstance(prices, list)
    models = [entry["model"] for entry in prices]
    assert models == sorted(models)
    assert len(prices) == len(DEFAULT_PRICES) + 1  # the Opus 5 override replaces the default
    by_model = {entry["model"]: entry for entry in prices}
    assert "claude-opus-5" not in by_model
    assert by_model["Claude-Opus-5-20260101"] == {
        "model": "Claude-Opus-5-20260101",
        **custom.to_wire(),
        "source": "custom",
    }
    assert by_model["gpt-7-nova"]["source"] == "custom"
    assert by_model["gpt-6-luna"] == {
        "model": "gpt-6-luna",
        "input": 0.10,
        "output": 0.50,
        "cache_read": 0.01,
        "cache_write": 0.125,
        "source": "default",
    }


# -- exchange rate -----------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Fetcher:
    def __init__(self, *rates: FxRate | FxError) -> None:
        self.results = list(rates)
        self.calls = 0

    async def __call__(self) -> FxRate:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, FxError):
            raise result
        return result


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "db.sqlite3") as store:
        yield store


async def test_refresher_fetches_when_missing_or_stale(store: SqliteStore) -> None:
    clock = Clock()
    newer = FxRate(eur_per_usd=0.85, as_of=date(2026, 9, 26), source="ecb")
    fetcher = Fetcher(ECB, FxError("503"), newer)
    refresher = FxRefresher(store, clock, fetcher=fetcher)

    assert await refresher.refresh() == ECB
    assert await store.get_ecb_rate() == StoredFxRate(ECB, T0)
    assert await refresher.refresh() is None  # fresh: no download
    assert fetcher.calls == 1

    clock.now += timedelta(hours=12)
    assert await refresher.refresh() is None  # the download fails: the old rate stays
    assert await store.get_ecb_rate() == StoredFxRate(ECB, T0)
    assert await store.current_fx(clock.now) == ECB

    assert await refresher.refresh(force=True) == newer
    assert await store.get_ecb_rate() == StoredFxRate(newer, clock.now)
    assert fetcher.calls == 3


async def test_refresher_does_nothing_in_manual_mode(store: SqliteStore) -> None:
    await store.put_runtime_settings(RuntimeSettings(fx=FxSettings(mode="manual")))
    fetcher = Fetcher(ECB)
    refresher = FxRefresher(store, Clock(), fetcher=fetcher)
    assert await refresher.refresh(force=True) is None
    assert fetcher.calls == 0
    assert await store.get_ecb_rate() is None


async def test_refresher_loop_wakes_up_when_poked(store: SqliteStore) -> None:
    await store.put_runtime_settings(RuntimeSettings(fx=FxSettings(mode="manual")))
    fetcher = Fetcher(ECB)
    refresher = FxRefresher(store, Clock(), fetcher=fetcher, check_interval_seconds=3600)
    task = asyncio.create_task(refresher.run())
    try:
        await asyncio.sleep(0.05)
        assert fetcher.calls == 0
        await store.put_runtime_settings(RuntimeSettings())
        refresher.poke()
        for _ in range(100):
            if await store.get_ecb_rate() is not None:
                break
            await asyncio.sleep(0.01)
        assert await store.get_ecb_rate() == StoredFxRate(ECB, T0)
        assert fetcher.calls == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
