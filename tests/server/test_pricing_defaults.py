"""Rows of ``GET /api/pricing`` (ADR 0006): each one carries the normalized id the
server matches models on (``key``) and, when it is an owner price that replaces a
default one, that default price (``default``), so the dashboard can show the base
price, restore it and pre-fill a new row without normalizing ids on its own."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentic_os.config import Settings
from agentic_os.domain import AgentName
from agentic_os.fx import FxRate
from agentic_os.pricing import DEFAULT_PRICES, ModelPrice, normalize_model
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.server.app import create_app
from agentic_os.server.catalog import pricing_to_wire
from agentic_os.server.deps import AppState

ORIGIN = "https://aos.example"
T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
ECB = FxRate(eur_per_usd=0.8547, as_of=date(2026, 9, 25), source="ecb")
CUSTOM = ModelPrice(4.0, 20.0, 0.4, 5.0)


def rows(overrides: dict[str, ModelPrice]) -> list[dict[str, Any]]:
    prices = pricing_to_wire(ECB, overrides)["prices"]
    assert isinstance(prices, list)
    return prices


def by_model(overrides: dict[str, ModelPrice]) -> dict[str, dict[str, Any]]:
    return {row["model"]: row for row in rows(overrides)}


def test_every_row_carries_the_normalized_id_it_is_matched_on() -> None:
    overrides = {"Anthropic/Claude-Opus-5-20260101": CUSTOM, "openai/gpt-7-nova[1m]": CUSTOM}
    table = rows(overrides)
    assert len(table) == len(DEFAULT_PRICES) + 1  # the Opus 5 price replaces the default
    for row in table:
        assert row["key"] == normalize_model(row["model"])
    keys = [row["key"] for row in table]
    assert len(set(keys)) == len(keys)
    assert by_model(overrides)["openai/gpt-7-nova[1m]"]["key"] == "gpt-7-nova"


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5", "anthropic/claude-opus-5", "Claude-Opus-5-20260101", "claude-opus-5[1m]"],
)
def test_an_owner_price_that_replaces_a_default_carries_the_default(model: str) -> None:
    table = by_model({model: CUSTOM})
    assert table[model] == {
        "model": model,
        "key": "claude-opus-5",
        **CUSTOM.to_wire(),
        "source": "custom",
        "default": DEFAULT_PRICES["claude-opus-5"].to_wire(),
    }
    assert [row for row in table.values() if row["key"] == "claude-opus-5"] == [table[model]]
    assert len(table) == len(DEFAULT_PRICES)


def test_rows_that_replace_no_default_have_a_null_default() -> None:
    # A new model, and a longer id that only has a default for its prefix: both are
    # added next to the default rows, which stay as they are.
    table = by_model({"gpt-7-nova": CUSTOM, "claude-opus-5-9": CUSTOM})
    assert len(table) == len(DEFAULT_PRICES) + 2
    for model in ("gpt-7-nova", "claude-opus-5-9"):
        assert table[model] == {
            "model": model,
            "key": model,
            **CUSTOM.to_wire(),
            "source": "custom",
            "default": None,
        }
    for model, price in DEFAULT_PRICES.items():
        assert table[model] == {
            "model": model,
            "key": normalize_model(model),
            **price.to_wire(),
            "source": "default",
            "default": None,
        }


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
        "claude_mode": "fake",
        "chatgpt_mode": "fake",
    }
    providers: dict[AgentName, Provider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    app = create_app(Settings(**values), providers=providers, clock=lambda: T0)
    async with app.router.lifespan_context(app):
        state = app.state.aos
        assert isinstance(state, AppState)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            token = await state.sessions.create(T0, ip=None, user_agent=None)
            client.cookies.set(state.cookie_name, token)
            yield client


async def pricing_rows(client: httpx.AsyncClient, key: str) -> list[dict[str, Any]]:
    response = await client.get("/api/pricing")
    assert response.status_code == 200
    return [row for row in response.json()["prices"] if row["key"] == key]


async def test_saving_an_owner_price_keeps_the_default_price_in_the_row(
    client: httpx.AsyncClient,
) -> None:
    default = DEFAULT_PRICES["claude-opus-5"].to_wire()
    assert await pricing_rows(client, "claude-opus-5") == [
        {
            "model": "claude-opus-5",
            "key": "claude-opus-5",
            **default,
            "source": "default",
            "default": None,
        }
    ]
    current = (await client.get("/api/settings")).json()
    response = await client.put(
        "/api/settings",
        json={**current, "prices": {"anthropic/claude-opus-5": CUSTOM.to_wire()}},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 200
    assert await pricing_rows(client, "claude-opus-5") == [
        {
            "model": "anthropic/claude-opus-5",
            "key": "claude-opus-5",
            **CUSTOM.to_wire(),
            "source": "custom",
            "default": default,
        }
    ]
