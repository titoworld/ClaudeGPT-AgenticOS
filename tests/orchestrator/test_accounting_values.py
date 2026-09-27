"""Unit tests of how a turn's savings are valued (TurnAccounting) and of the cache key."""

from __future__ import annotations

import pytest

from agentic_os.domain import TurnOptions, Usage
from agentic_os.orchestrator.accounting import TurnAccounting
from agentic_os.orchestrator.cache import context_fingerprint, normalize_question, turn_cache_key
from agentic_os.orchestrator.memory import build_context
from agentic_os.pricing import ModelPrice, estimate_cost_usd

OPUS_55 = ModelPrice(4.0, 20.0, 0.20, 5.0)
PRICES = {"opus": OPUS_55}


def priced(usage: Usage) -> Usage:
    cost = estimate_cost_usd("opus", usage, PRICES)
    assert cost is not None
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cost_usd=cost,
    )


# A call as the Claude CLI/API reports it: tiny uncached input, big cache reads/writes.
CACHED_CALL = priced(
    Usage(input_tokens=3, output_tokens=600, cache_read_tokens=8000, cache_write_tokens=1500)
)


def test_average_price_counts_cache_reads_and_writes() -> None:
    accounting = TurnAccounting()
    accounting.add_call(CACHED_CALL, "answer")
    accounting.add_unchanged("x" * 4000)  # 1000 tokens
    accounting.compaction_per_request = 2000
    accounting.add_context_request()
    savings = accounting.savings()
    cost = CACHED_CALL.cost_usd
    assert savings.cost_usd is not None and cost is not None
    billed = 3 + 600 + 8000 + 1500
    assert savings.cost_usd == pytest.approx(3000 * cost / billed)
    # Never above the model's highest price per token (before: ~35 $/MTok here).
    assert savings.cost_usd / savings.total * 1e6 <= OPUS_55.output


def test_early_stop_is_valued_at_the_average_revision_cost() -> None:
    accounting = TurnAccounting()
    accounting.add_call(priced(Usage(input_tokens=100, output_tokens=100)), "answer")
    first = priced(Usage(input_tokens=5, output_tokens=300, cache_read_tokens=20_000))
    second = priced(Usage(input_tokens=5, output_tokens=100, cache_write_tokens=10_000))
    accounting.add_call(first, "revision")
    accounting.add_call(second, "revision")
    accounting.add_call(Usage(input_tokens=50, output_tokens=50), "revision")  # no price
    accounting.add_early_stop(2)
    savings = accounting.savings()
    # Tokens: 2 rounds x a pair of the average revision (input + output).
    assert savings.early_stop == round(2 * 2 * (305 + 105 + 100) / 3)
    revision_cost = (first.cost_usd or 0.0) + (second.cost_usd or 0.0)
    assert savings.cost_usd == pytest.approx(2 * 2 * revision_cost / 2)
    (record,) = accounting.saving_records(1, 2)
    assert record.kind == "early_stop" and record.cost_usd == pytest.approx(savings.cost_usd)


def test_early_stop_without_priced_revisions_uses_the_average_price() -> None:
    accounting = TurnAccounting()
    accounting.add_call(priced(Usage(input_tokens=300, output_tokens=100)), "answer")
    accounting.add_call(Usage(input_tokens=200, output_tokens=0), "revision")  # no price
    accounting.add_early_stop(1)
    rate = (accounting.usage.cost_usd or 0.0) / 400
    assert accounting.savings().cost_usd == pytest.approx(400 * rate)


def test_saving_records_carry_their_own_value() -> None:
    accounting = TurnAccounting()
    accounting.add_call(CACHED_CALL, "answer")
    accounting.add_call(CACHED_CALL, "revision")
    accounting.add_early_stop(1)
    accounting.add_unchanged("x" * 400)
    accounting.compaction_per_request = 50
    accounting.add_context_request()
    records = {r.kind: r for r in accounting.saving_records(1, 2)}
    assert set(records) == {"early_stop", "unchanged", "compaction"}
    rate = 2 * (CACHED_CALL.cost_usd or 0.0) / (2 * 10_103)
    assert records["unchanged"].cost_usd == pytest.approx(100 * rate)
    assert records["compaction"].cost_usd == pytest.approx(50 * rate)
    assert records["early_stop"].cost_usd == pytest.approx(2 * (CACHED_CALL.cost_usd or 0.0))
    total = sum(r.cost_usd or 0.0 for r in records.values())
    assert accounting.savings().cost_usd == pytest.approx(total)

    unpriced = TurnAccounting()
    unpriced.add_call(Usage(input_tokens=10, output_tokens=10), "answer")
    unpriced.add_unchanged("x" * 40)
    (only,) = unpriced.saving_records(1, 2)
    assert only.cost_usd is None and unpriced.savings().cost_usd is None

    cached = TurnAccounting()
    cached.cache, cached.cache_cost = 1234, 0.25
    (record,) = cached.saving_records(1, 2)
    assert (record.kind, record.cost_usd) == ("cache", 0.25)


def test_spent_calls_count_in_the_usage_only() -> None:
    accounting = TurnAccounting()
    accounting.add_call(priced(Usage(input_tokens=100, output_tokens=100)), "answer")
    empty = priced(Usage(input_tokens=1000, output_tokens=8000))
    accounting.add_spent(empty)
    accounting.add_unchanged("x" * 400)
    assert accounting.usage.total_tokens == 9200
    assert accounting.turn_usage.total_tokens == 200
    # The average price is still the one of the calls that produced messages.
    rate = (accounting.turn_usage.cost_usd or 0.0) / 200
    assert accounting.savings().cost_usd == pytest.approx(100 * rate)


def test_cache_key_keeps_newlines_and_indentation() -> None:
    fingerprint = context_fingerprint(build_context(None, []))

    def key(question: str) -> str:
        return turn_cache_key(
            mode="solo",
            target="claude",
            options=TurnOptions(),
            question=question,
            context_fingerprint=fingerprint,
            identities={"claude": "fake:fake-claude"},
        )

    outside = "Per què falla?\n```python\nif x:\n    print(1)\nprint(2)\n```"
    inside = "Per què falla?\n```python\nif x:\n    print(1)\n    print(2)\n```"
    assert key(outside) != key(inside)
    assert key("- a\n- b") != key("- a - b")
    assert key(outside) == key("\n  " + outside.replace("\n", "\r\n") + "  \n")
    assert normalize_question("a\r\n  b\rc") == "a\n  b\nc"
