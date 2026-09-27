from datetime import date

import pytest

from agentic_os.domain import Usage
from agentic_os.fx import FxError, parse_ecb_daily
from agentic_os.pricing import ModelPrice, estimate_cost_usd, find_price, normalize_model

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


def test_longest_prefix_wins() -> None:
    assert find_price("claude-opus-5-5-20260901") == ModelPrice(4.0, 20.0, 0.20, 5.0)
    opus5 = find_price("claude-opus-5")
    assert opus5 is not None and opus5.input == 5.0


def test_overrides_take_precedence_and_add_new_models() -> None:
    overrides = {"gpt-7": ModelPrice(1.0, 2.0, 0.1, 1.25), "gpt-6-sol": ModelPrice(9, 9, 9, 9)}
    assert find_price("gpt-7-mini", overrides) == overrides["gpt-7"]
    assert find_price("gpt-6-sol", overrides) == overrides["gpt-6-sol"]
    assert find_price("unknown-model", overrides) is None


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
