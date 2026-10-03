"""Unit tests of how a turn's savings are valued (TurnAccounting) and of the cache key."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agentic_os import i18n
from agentic_os.domain import TurnOptions, Usage
from agentic_os.orchestrator.accounting import TurnAccounting, failed_call_usage
from agentic_os.orchestrator.cache import context_fingerprint, normalize_question, turn_cache_key
from agentic_os.orchestrator.memory import build_context
from agentic_os.pricing import ModelPrice, estimate_cost_usd
from agentic_os.providers.base import ProviderError

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


CHEAP = ModelPrice(1.0, 2.0, 0.1, 1.25)


def test_kept_answers_are_valued_at_the_output_price_of_their_model() -> None:
    accounting = TurnAccounting()
    accounting.add_call(CACHED_CALL, "answer")
    accounting.add_unchanged("x" * 4000, OPUS_55)  # 1000 tokens Opus did not write
    accounting.add_unchanged("x" * 400, CHEAP)  # 100 tokens the cheap model did not write
    savings = accounting.savings()
    # Before: both at the turn's blended price per billed token (cache reads included).
    assert savings.cost_usd == pytest.approx((1000 * OPUS_55.output + 100 * CHEAP.output) / 1e6)
    (record,) = accounting.saving_records(1, 2)
    assert record.kind == "unchanged" and record.cost_usd == pytest.approx(savings.cost_usd)


def test_an_unpriced_kept_answer_takes_the_rate_of_the_priced_ones() -> None:
    accounting = TurnAccounting()
    accounting.add_unchanged("x" * 400, OPUS_55)
    accounting.add_unchanged("x" * 400, None)
    assert accounting.savings().cost_usd == pytest.approx(200 * OPUS_55.output / 1e6)
    unpriced = TurnAccounting()
    unpriced.add_call(CACHED_CALL, "answer")  # a price is known, but not the keeper's
    unpriced.add_unchanged("x" * 400, None)
    (record,) = unpriced.saving_records(1, 2)
    assert record.cost_usd is None


def test_compaction_is_valued_at_the_input_price_of_the_calls_that_carried_it() -> None:
    accounting = TurnAccounting()
    accounting.add_call(CACHED_CALL, "answer")
    accounting.compaction_per_request = 2000
    accounting.add_context_request(OPUS_55)
    accounting.add_context_request(CHEAP)
    savings = accounting.savings()
    assert savings.compaction == 4000
    # Uncached input price of each call's model (before: the blended average).
    assert savings.cost_usd == pytest.approx(2000 * (OPUS_55.input + CHEAP.input) / 1e6)
    # A call with no known price counts at the average input price of the priced ones.
    accounting.add_context_request(None)
    average = (OPUS_55.input + CHEAP.input) / 2
    assert accounting.savings().cost_usd == pytest.approx(3 * 2000 * average / 1e6)

    unpriced = TurnAccounting()
    unpriced.compaction_per_request = 2000
    unpriced.add_context_request(None)
    assert unpriced.savings().cost_usd is None


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
    # Tokens: 2 rounds x a pair of the average revision, in processed tokens (input,
    # cache reads and writes, output: before, only input + output, 680 tokens).
    assert savings.early_stop == round(2 * 2 * (20_305 + 10_105 + 100) / 3)
    revision_cost = (first.cost_usd or 0.0) + (second.cost_usd or 0.0)
    assert savings.cost_usd == pytest.approx(2 * 2 * revision_cost / 2)
    (record,) = accounting.saving_records(1, 2)
    assert record.kind == "early_stop" and record.cost_usd == pytest.approx(savings.cost_usd)


def test_early_stop_without_priced_revisions_has_no_value() -> None:
    accounting = TurnAccounting()
    accounting.add_call(priced(Usage(input_tokens=300, output_tokens=100)), "answer")
    accounting.add_call(Usage(input_tokens=200, output_tokens=0), "revision")  # no price
    accounting.add_early_stop(1)
    (record,) = accounting.saving_records(1, 2)
    assert record.kind == "early_stop" and record.cost_usd is None
    # The only kind with saved tokens has no price, so the turn's saving has no value
    # (not 0 €), even though the answer call was priced.
    assert accounting.savings().cost_usd is None


def test_saving_details_are_written_in_the_turns_language() -> None:
    accounting = TurnAccounting()
    accounting.add_call(CACHED_CALL, "answer")
    accounting.add_call(CACHED_CALL, "revision")
    accounting.add_early_stop(2)
    for _ in range(2):
        accounting.add_unchanged("x" * 400, OPUS_55)
        accounting.add_context_request(OPUS_55)
    accounting.compaction_per_request = 12_345
    accounting.cache = 10
    with i18n.use("en"):
        english = {record.kind: record.detail for record in accounting.saving_records(1, 2)}
    catalan = {record.kind: record.detail for record in accounting.saving_records(1, 2)}
    assert english == {
        "cache": "Answer served from the cache",
        "compaction": "Tokens saved per call: 12,345; calls: 2",
        "early_stop": "Rounds skipped by consensus: 2",
        "unchanged": "Answers not rewritten: 2",
    }
    assert catalan == {
        "cache": "Resposta servida des de la memòria cau",
        "compaction": "12.345 tokens menys en 2 crides",
        "early_stop": "2 rondes omeses per consens",
        "unchanged": "2 respostes sense reescriure",
    }


def test_saving_records_carry_their_own_value() -> None:
    accounting = TurnAccounting()
    accounting.add_call(CACHED_CALL, "answer")
    accounting.add_call(CACHED_CALL, "revision")
    accounting.add_early_stop(1)
    accounting.add_unchanged("x" * 400, OPUS_55)
    accounting.compaction_per_request = 50
    accounting.add_context_request(OPUS_55)
    records = {r.kind: r for r in accounting.saving_records(1, 2)}
    assert set(records) == {"early_stop", "unchanged", "compaction"}
    assert records["unchanged"].cost_usd == pytest.approx(100 * OPUS_55.output / 1e6)
    assert records["compaction"].cost_usd == pytest.approx(50 * OPUS_55.input / 1e6)
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


def test_calls_without_a_message_count_in_the_usage_only() -> None:
    accounting = TurnAccounting()
    accounting.add_call(priced(Usage(input_tokens=100, output_tokens=100)), "answer")
    summary = priced(Usage(input_tokens=500, output_tokens=50))
    empty = priced(Usage(input_tokens=1000, output_tokens=8000))
    accounting.add_spent(summary)
    accounting.add_unstored(empty)
    accounting.add_unchanged("x" * 400, OPUS_55)
    assert accounting.usage.processed_tokens == 9750
    # Only the call stored on no message is unstored (the summary is on the question).
    assert accounting.unstored == empty
    assert accounting.savings().cost_usd == pytest.approx(100 * OPUS_55.output / 1e6)


def test_a_failed_call_keeps_the_usage_it_says_it_billed() -> None:
    class Billed(Exception):
        """Any exception that says what it billed (read defensively: it may be malformed)."""

        def __init__(self, usage: object, model: object) -> None:
            super().__init__("declined")
            self.usage = usage
            self.model = model

    billed = Usage(input_tokens=5000, output_tokens=300, cache_read_tokens=100)
    model, usage = failed_call_usage(Billed(billed, "opus"), "asked", PRICES)
    assert model == "opus"
    assert usage == replace(billed, cost_usd=estimate_cost_usd("opus", billed, PRICES))
    # Nothing billed, no usage, or malformed attributes: an empty, unpriced usage.
    assert failed_call_usage(Billed(Usage(), "opus"), "asked", PRICES) == ("opus", Usage())
    assert failed_call_usage(Billed({"input_tokens": 9}, 7), "asked") == ("asked", Usage())
    plain = ProviderError("down", kind="unavailable")
    assert failed_call_usage(plain, "asked", PRICES) == ("asked", Usage())
    assert failed_call_usage(None, "", PRICES) == ("", Usage())
    # An unknown model is recorded with its tokens and no cost.
    _, unknown = failed_call_usage(Billed(billed, "mystery"), "asked", PRICES)
    assert unknown == billed and unknown.cost_usd is None


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


def test_a_provider_error_can_carry_its_billed_usage() -> None:
    billed = Usage(input_tokens=10, output_tokens=16_000, reasoning_tokens=16_000)
    error = ProviderError("límit", kind="invalid", usage=billed, model="opus")
    model, usage = failed_call_usage(error, "asked", PRICES)
    assert model == "opus"
    assert usage == replace(billed, cost_usd=estimate_cost_usd("opus", billed, PRICES))
