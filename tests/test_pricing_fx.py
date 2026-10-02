import json
from datetime import date
from pathlib import Path

import pytest

from agentic_os.domain import Usage
from agentic_os.fx import FxError, parse_ecb_daily
from agentic_os.pricing import (
    DEFAULT_PRICES,
    ModelPrice,
    estimate_cost_usd,
    find_price,
    lookup_price,
    normalize_model,
    price_table,
)

ECB_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time='2026-09-25'>
    <Cube currency='USD' rate='1.1650'/><Cube currency='JPY' rate='171.30'/>
  </Cube></Cube>
</gesmes:Envelope>"""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("claude-opus-5-5-20260901", "claude-opus-5-5"),
        ("anthropic.claude-opus-5", "claude-opus-5"),
        ("claude-sonnet-5[1m]", "claude-sonnet-5"),
        ("GPT-6-Sol", "gpt-6-sol"),
        ("openai/gpt-6-luna", "gpt-6-luna"),
    ],
)
def test_normalize_model(raw: str, expected: str) -> None:
    assert normalize_model(raw) == expected


MODEL_IDS = Path(__file__).parent / "fixtures" / "model_ids.json"
"""Raw model ids and their normalized keys, shared with the web tests, which check the
TypeScript port of ``normalize_model`` against the same file (ADR 0006)."""


def _shared_vectors() -> list[tuple[str, str]]:
    data = json.loads(MODEL_IDS.read_text(encoding="utf-8"))
    return [(vector["id"], vector["key"]) for vector in data["vectors"]]


@pytest.mark.parametrize(("raw", "key"), _shared_vectors())
def test_normalize_model_matches_the_vectors_shared_with_the_web(raw: str, key: str) -> None:
    assert normalize_model(raw) == key


def test_longest_prefix_wins() -> None:
    assert find_price("claude-opus-5-5-20260901") == ModelPrice(4.0, 20.0, 0.20, 5.0)
    opus5 = find_price("claude-opus-5")
    assert opus5 is not None and opus5.input == 5.0


def test_overrides_take_precedence_and_add_new_models() -> None:
    overrides = {"gpt-7": ModelPrice(1.0, 2.0, 0.1, 1.25), "gpt-6-sol": ModelPrice(9, 9, 9, 9)}
    assert find_price("gpt-7-mini", overrides) == overrides["gpt-7"]
    assert find_price("gpt-6-sol", overrides) == overrides["gpt-6-sol"]
    assert find_price("unknown-model", overrides) is None


def test_an_owner_price_on_a_family_keeps_the_more_specific_defaults() -> None:
    family = ModelPrice(1.0, 1.0, 0.1, 1.25)
    # Same normalized id as the default claude-opus-5 row (the pricing page replaces it).
    for key in ("claude-opus-5", "Claude-Opus-5-20260101"):
        overrides = {key: family}
        assert (
            find_price("claude-opus-5-5-20260901", overrides) == DEFAULT_PRICES["claude-opus-5-5"]
        )
        assert find_price("claude-opus-5-5[1m]", overrides) == DEFAULT_PRICES["claude-opus-5-5"]
        assert find_price("claude-opus-5", overrides) == family
        assert find_price("claude-opus-5-20260101", overrides) == family
    fable = {"claude-fable-5": family}
    assert find_price("claude-fable-5-1", fable) == DEFAULT_PRICES["claude-fable-5-1"]
    # A longer owner id still beats a shorter default prefix.
    longer = {"claude-opus-5-5-fast": family}
    assert find_price("claude-opus-5-5-fast-20261001", longer) == family
    assert find_price("claude-opus-5-5", longer) == DEFAULT_PRICES["claude-opus-5-5"]


def test_price_table_is_what_the_engine_charges() -> None:
    overrides = {
        "Claude-Opus-5-20260101": ModelPrice(1.0, 1.0, 0.1, 1.25),
        "gpt-7": ModelPrice(1.0, 2.0, 0.1, 1.25),
    }
    table = price_table(overrides)
    assert table["claude-opus-5"].source == "custom"
    assert table["claude-opus-5"].model == "Claude-Opus-5-20260101"
    assert table["claude-opus-5-5"].source == "default"
    assert table["gpt-7"].source == "custom"
    assert len(table) == len(DEFAULT_PRICES) + 1
    for entry in table.values():
        assert find_price(entry.model, overrides) == entry.price
    found = lookup_price("gpt-7-mini", table)
    assert found is not None and found.model == "gpt-7"
    assert lookup_price("", table) is None


@pytest.mark.parametrize("key", ["openai/", "x/[1m]", "a/-latest", "anthropic/anthropic.", " "])
def test_keys_that_normalize_to_nothing_price_nothing(key: str) -> None:
    free = {key: ModelPrice(0.0, 0.0, 0.0, 0.0)}
    assert normalize_model(key) == ""
    assert find_price("gpt-6-sol", free) == DEFAULT_PRICES["gpt-6-sol"]
    assert find_price("claude-opus-5-5", free) == DEFAULT_PRICES["claude-opus-5-5"]
    assert find_price("mystery-model", free) is None
    assert "" not in price_table(free)
    assert len(price_table(free)) == len(DEFAULT_PRICES)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-opus-4-0", ModelPrice(15.0, 75.0, 1.5, 18.75)),
        ("claude-opus-4-20250514", ModelPrice(15.0, 75.0, 1.5, 18.75)),
        ("claude-opus-4-1-20250805", ModelPrice(15.0, 75.0, 1.5, 18.75)),
        ("claude-opus-4-5-20251101", ModelPrice(5.0, 25.0, 0.5, 6.25)),
        ("claude-opus-4-6", ModelPrice(5.0, 25.0, 0.5, 6.25)),
        ("claude-opus-4-7", ModelPrice(5.0, 25.0, 0.5, 6.25)),
        ("claude-opus-4-8", ModelPrice(5.0, 25.0, 0.5, 6.25)),
        ("claude-mythos-5-1", ModelPrice(10.0, 50.0, 0.25, 12.5)),
        ("anthropic.claude-mythos-5-1", ModelPrice(10.0, 50.0, 0.25, 12.5)),
        ("claude-mythos-5", ModelPrice(10.0, 50.0, 1.0, 12.5)),
        ("claude-fable-5-1", ModelPrice(10.0, 50.0, 0.25, 12.5)),
        ("claude-sonnet-4-6", ModelPrice(3.0, 15.0, 0.3, 3.75)),
        ("claude-haiku-4-5-20251001", ModelPrice(1.0, 5.0, 0.1, 1.25)),
    ],
)
def test_default_prices_of_served_models(model: str, expected: ModelPrice) -> None:
    price = find_price(model)
    assert price is not None
    assert price.to_wire() == pytest.approx(expected.to_wire())


def test_estimate_cost_uses_every_usage_component() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=2_000_000,
        cache_write_tokens=400_000,
    )
    # opus-5 ($/MTok): 1M input x 5 + 0.1M output x 25 + 2M cache read x 0.5 + 0.4M write x 6.25
    assert estimate_cost_usd("claude-opus-5", usage) == pytest.approx(5 + 2.5 + 1 + 2.5)
    assert estimate_cost_usd("mystery", usage) is None


def test_price_from_wire_validates() -> None:
    assert ModelPrice.from_wire(
        {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 1.25}
    ) == ModelPrice(1, 2, 0.1, 1.25)
    with pytest.raises(ValueError, match="output"):
        ModelPrice.from_wire({"input": 1, "output": -2, "cache_read": 0, "cache_write": 0})
    with pytest.raises(ValueError, match="input"):
        ModelPrice.from_wire({"input": True, "output": 2, "cache_read": 0, "cache_write": 0})
    # A valid JSON integer too large for a float is a ValueError (a 422), not a crash.
    with pytest.raises(ValueError, match="cache_write"):
        ModelPrice.from_wire({"input": 1, "output": 2, "cache_read": 0, "cache_write": 10**400})


def test_parse_ecb_daily() -> None:
    rate = parse_ecb_daily(ECB_SAMPLE)
    assert rate.source == "ecb"
    assert rate.as_of == date(2026, 9, 25)
    assert rate.eur_per_usd == pytest.approx(1 / 1.165, rel=1e-6)


@pytest.mark.parametrize(
    "document",
    ["<Cube/>", "<Cube time='2026-09-25'><Cube currency='USD' rate='42'/></Cube>"],
)
def test_parse_ecb_daily_rejects_bad_documents(document: str) -> None:
    with pytest.raises(FxError):
        parse_ecb_daily(document)
