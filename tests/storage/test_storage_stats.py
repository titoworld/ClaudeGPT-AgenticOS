from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from agentic_os.domain import AgentName, ProviderMode, Purpose, SavingKind, Usage
from agentic_os.fx import FxRate
from agentic_os.orchestrator.store import JsonValue, NewMessage, SavingRecord, UsageRecord
from agentic_os.storage import FxSettings, RuntimeSettings, SqliteStore
from agentic_os.storage.stats import month_bounds, percentile, window

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def store(tmp_path: Path, clock: FakeClock) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "db.sqlite3", clock=clock) as store:
        yield store


def at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 9, day, hour, 0, 0, tzinfo=UTC)


async def usage(
    store: SqliteStore,
    agent: AgentName,
    *,
    tokens: Usage,
    latency_ms: int,
    ttft_ms: int | None = None,
    ok: bool = True,
    mode: ProviderMode = "api",
    purpose: Purpose = "answer",
) -> None:
    await store.record_usage(
        UsageRecord(
            conversation_id=None,
            turn_id=None,
            agent=agent,
            provider_mode=mode,
            model="m",
            purpose=purpose,
            usage=tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            ok=ok,
            error=None if ok else "timeout",
        )
    )


async def saving(store: SqliteStore, kind: SavingKind, tokens: int) -> None:
    await store.record_saving(SavingRecord(None, None, kind, tokens))


async def turn(
    store: SqliteStore,
    conversation_id: int,
    mode: str,
    *,
    options: Mapping[str, JsonValue] | None = None,
    revisions: Mapping[int, tuple[int | None, int | None]] | None = None,
    synthesis: bool = True,
    consensus: Mapping[str, JsonValue] | None = None,
) -> None:
    meta: dict[str, JsonValue] = {"mode": mode, "target": "claude"}
    if options is not None:
        meta["options"] = dict(options)
    question_id = await store.add_message(
        NewMessage(conversation_id=conversation_id, kind="question", content="Q", meta=meta)
    )
    for round_, (claude, chatgpt) in (revisions or {}).items():
        scores: tuple[tuple[AgentName, int | None], ...] = (
            ("claude", claude),
            ("chatgpt", chatgpt),
        )
        for agent, agreement in scores:
            if agreement is None:
                continue  # that agent failed in this round
            await store.add_message(
                NewMessage(
                    conversation_id=conversation_id,
                    kind="revision",
                    content="R",
                    turn_id=question_id,
                    agent=agent,
                    round=round_,
                    meta={"agreement": agreement, "critique": "c", "unchanged": False},
                )
            )
    if mode == "debate" and synthesis:
        await store.add_message(
            NewMessage(
                conversation_id=conversation_id,
                kind="synthesis",
                content="S",
                turn_id=question_id,
                agent="claude",
                final=True,
                meta={"consensus": dict(consensus)} if consensus is not None else {},
            )
        )


async def test_stats_aggregation(store: SqliteStore, clock: FakeClock) -> None:
    conversation_id = await store.create_conversation("Stats")

    clock.now = at(24)  # outside a 3-day window ending on the 27th
    await usage(store, "claude", tokens=Usage(input_tokens=9999), latency_ms=1)
    await saving(store, "compaction", 999)
    await turn(store, conversation_id, "solo")

    clock.now = at(25)
    await usage(
        store,
        "claude",
        tokens=Usage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            reasoning_tokens=2,
            cost_usd=0.01,
        ),
        latency_ms=1000,
        ttft_ms=200,
    )
    await saving(store, "cache", 1000)
    await turn(store, conversation_id, "solo")

    clock.now = at(26)
    await usage(
        store, "chatgpt", tokens=Usage(input_tokens=200, output_tokens=100), latency_ms=2000
    )
    await usage(store, "chatgpt", tokens=Usage(), latency_ms=500, ok=False)
    await turn(store, conversation_id, "duel")

    clock.now = at(27, 11)
    await usage(
        store,
        "claude",
        tokens=Usage(input_tokens=300, output_tokens=150, cost_usd=0.02),
        latency_ms=3000,
        ttft_ms=400,
    )
    await saving(store, "early_stop", 300)
    await saving(store, "unchanged", 50)
    # Reached with the default threshold (85) after one round.
    await turn(store, conversation_id, "debate", revisions={1: (90, 88)})
    # Threshold 95 from the question options: the last round (2) misses it.
    await turn(
        store,
        conversation_id,
        "debate",
        options={"debate": {"rounds": 2, "consensus_threshold": 95, "synthesizer": "claude"}},
        revisions={1: (90, 92), 2: (96, 94)},
    )
    # Unfinished debate (no synthesis): counted as a turn, not as a debate.
    await turn(store, conversation_id, "debate", revisions={1: (99, 99)}, synthesis=False)
    # Debate without revision rounds.
    await turn(store, conversation_id, "debate")
    # One agent failed in the last round: no consensus.
    await turn(store, conversation_id, "debate", revisions={1: (80, 80), 2: (95, None)})

    stats = await store.stats(3, NOW)

    assert stats["days"] == 3
    assert stats["totals"] == {
        "calls": 4,
        "errors": 1,
        "cost_usd": 0.03,
        "by_agent": {
            "claude": {
                "input_tokens": 400,
                "output_tokens": 200,
                "cache_read_tokens": 10,
                "cache_write_tokens": 5,
                "reasoning_tokens": 2,
                "cost_usd": 0.03,
                "calls": 2,
            },
            "chatgpt": {
                "input_tokens": 200,
                "output_tokens": 100,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
                "cost_usd": None,
                "calls": 2,
            },
        },
    }
    assert stats["savings"] == {
        "cache": 1000,
        "compaction": 0,
        "early_stop": 300,
        "unchanged": 50,
        "total": 1350,
        "cost_usd": None,
    }
    assert stats["daily"] == [
        {
            "date": "2026-09-25",
            "agent": "claude",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "cache_write_tokens": 5,
            "cost_usd": 0.01,
        },
        {
            "date": "2026-09-25",
            "agent": "chatgpt",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.0,
        },
        {
            "date": "2026-09-26",
            "agent": "claude",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.0,
        },
        {
            "date": "2026-09-26",
            "agent": "chatgpt",
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.0,
        },
        {
            "date": "2026-09-27",
            "agent": "claude",
            "input_tokens": 300,
            "output_tokens": 150,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.02,
        },
        {
            "date": "2026-09-27",
            "agent": "chatgpt",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.0,
        },
    ]
    assert len(stats["savings_daily"]) == 3 * 4
    assert {
        (entry["date"], entry["kind"]): entry["tokens"]
        for entry in stats["savings_daily"]
        if entry["tokens"]
    } == {
        ("2026-09-25", "cache"): 1000,
        ("2026-09-27", "early_stop"): 300,
        ("2026-09-27", "unchanged"): 50,
    }
    assert [entry["kind"] for entry in stats["savings_daily"][:4]] == [
        "cache",
        "compaction",
        "early_stop",
        "unchanged",
    ]
    assert stats["latency"] == {
        "claude": {"p50_ms": 1000, "p95_ms": 3000, "ttft_p50_ms": 200},
        "chatgpt": {"p50_ms": 2000, "p95_ms": 2000, "ttft_p50_ms": None},
    }
    assert stats["turns"] == {"solo": 1, "duel": 1, "debate": 5}
    assert stats["consensus"] == {"debates": 4, "reached": 1, "avg_rounds": 1.25}
    assert stats["costs"] == {
        "fx": {"eur_per_usd": 0.86, "as_of": None, "source": "manual"},
        "by_agent": {
            "claude": {"api_usd": 0.03, "equivalent_usd": 0.0, "unpriced_calls": 0},
            "chatgpt": {"api_usd": 0.0, "equivalent_usd": 0.0, "unpriced_calls": 1},
        },
    }


async def test_daily_usage_has_every_kind_of_processed_token(
    store: SqliteStore, clock: FakeClock
) -> None:
    # A call with context, as the Claude CLI/API reports it: the processed tokens are
    # 3 + 100 + 10 000 + 20 000 (reasoning is part of the output).
    case = Usage(3, 100, 10_000, 20_000, reasoning_tokens=50, cost_usd=0.13)
    clock.now = at(27)
    await usage(store, "claude", tokens=case, latency_ms=10)
    await usage(store, "claude", tokens=case, latency_ms=10)
    stats = await store.stats(1, NOW)
    (claude,) = [d for d in stats["daily"] if d["agent"] == "claude"]
    # Before: the day had no cache writes at all (the dashboard could not count them).
    assert claude["cache_write_tokens"] == 40_000
    processed = (
        claude["input_tokens"]
        + claude["output_tokens"]
        + claude["cache_read_tokens"]
        + claude["cache_write_tokens"]
    )
    assert processed == 2 * case.processed_tokens == 60_206
    totals = stats["totals"]["by_agent"]["claude"]
    assert totals["cache_write_tokens"] == claude["cache_write_tokens"]


async def test_consensus_stored_by_the_engine_takes_precedence(store: SqliteStore) -> None:
    conversation_id = await store.create_conversation("Consens")
    # The stored outcome wins over what the revisions alone would suggest.
    await turn(
        store,
        conversation_id,
        "debate",
        revisions={1: (10, 10)},
        consensus={"reached": True, "round": 1, "scores": {"claude": 90, "chatgpt": 90}},
    )
    await turn(
        store,
        conversation_id,
        "debate",
        revisions={1: (99, 99), 2: (99, 99)},
        consensus={"reached": False, "round": 2, "scores": {}},
    )
    # Malformed stored consensus: derived from the revisions instead.
    await turn(
        store, conversation_id, "debate", revisions={1: (90, 90)}, consensus={"reached": True}
    )
    stats = await store.stats(1, NOW)
    assert stats["consensus"] == {"debates": 3, "reached": 2, "avg_rounds": 1.33}


async def test_costs_by_mode_and_month_spend(store: SqliteStore, clock: FakeClock) -> None:
    async def call(
        agent: AgentName, mode: ProviderMode, cost: float | None, *, ok: bool = True
    ) -> None:
        await usage(
            store, agent, tokens=Usage(10, 5, cost_usd=cost), latency_ms=10, ok=ok, mode=mode
        )

    clock.now = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)  # last month, inside 30 days
    await call("claude", "api", 1.0)
    clock.now = at(2)
    await call("claude", "api", 2.0)
    await call("claude", "fake", 0.5)  # a priced demo model counts as equivalent
    await call("claude", "fake", None)  # demo calls without a price are not "unpriced"
    await call("chatgpt", "cli", 3.0)
    await call("chatgpt", "cli", None)  # a model without a known price
    await call("chatgpt", "cli", None, ok=False)  # failed calls are not counted
    await store.put_runtime_settings(
        RuntimeSettings(
            budgets_eur={"claude": 10.0, "chatgpt": None},
            plans_eur={"claude": 0.0, "chatgpt": 20.0},
        )
    )
    await store.put_ecb_rate(FxRate(0.85, date(2026, 9, 25), "ecb"), NOW - timedelta(days=1))

    stats = await store.stats(30, NOW)
    ecb = {"eur_per_usd": 0.85, "as_of": "2026-09-25", "source": "ecb"}
    assert stats["costs"] == {
        "fx": ecb,
        "by_agent": {
            "claude": {"api_usd": 3.0, "equivalent_usd": 0.5, "unpriced_calls": 0},
            "chatgpt": {"api_usd": 0.0, "equivalent_usd": 3.0, "unpriced_calls": 1},
        },
    }
    assert [(d["date"], d["cost_usd"]) for d in stats["daily"] if d["cost_usd"]] == [
        ("2026-08-31", 1.0),
        ("2026-09-02", 2.5),
        ("2026-09-02", 3.0),
    ]
    month = {
        "month": "2026-09",
        "fx": ecb,
        "by_agent": {
            "claude": {
                "api_usd": 2.0,
                "equivalent_usd": 0.5,
                "unpriced_calls": 0,
                "budget_eur": 10.0,
                "budget_used": 0.17,  # 2 $ x 0.85 = 1.70 € of 10 €
                "plan_eur": 0.0,
                "plan_value": None,  # no meaningful ratio for a 0 € plan
            },
            "chatgpt": {
                "api_usd": 0.0,
                "equivalent_usd": 3.0,
                "unpriced_calls": 1,
                "budget_eur": None,
                "budget_used": None,
                "plan_eur": 20.0,
                "plan_value": 0.1275,  # 3 $ x 0.85 = 2.55 € of a 20 € plan
            },
        },
    }
    assert stats["month"] == month
    assert await store.month_spend(NOW) == month
    # The month does not depend on the selected range.
    week = await store.stats(7, NOW)
    assert week["costs"]["by_agent"]["claude"]["api_usd"] == 0.0
    assert week["month"] == month

    # Manual mode: the owner's rate.
    await store.put_runtime_settings(
        RuntimeSettings(
            fx=FxSettings(mode="manual", eur_per_usd=1.0),
            budgets_eur={"claude": 10.0, "chatgpt": None},
        )
    )
    spend = await store.month_spend(NOW)
    assert spend["fx"] == {"eur_per_usd": 1.0, "as_of": None, "source": "manual"}
    assert spend["by_agent"]["claude"]["budget_used"] == 0.2
    assert spend["by_agent"]["chatgpt"]["plan_value"] is None


async def test_savings_value_comes_from_the_turns_final_messages(
    store: SqliteStore, clock: FakeClock
) -> None:
    conversation_id = await store.create_conversation("Estalvi")

    async def final_answers(*costs: float | None) -> None:
        question = await store.add_message(
            NewMessage(conversation_id, "question", "Q", final=True, meta={"mode": "duel"})
        )
        for index, cost in enumerate(costs):
            savings: dict[str, JsonValue] = {"cache": 0, "total": 10, "cost_usd": cost}
            await store.add_message(
                NewMessage(
                    conversation_id,
                    "answer",
                    "A",
                    turn_id=question,
                    agent="claude" if index == 0 else "chatgpt",
                    final=True,
                    meta={"savings": savings},
                )
            )

    clock.now = at(20)
    await final_answers(5.0)  # outside a 1-day window
    clock.now = at(27, 9)
    await final_answers(0.001, 0.003)  # the last final message has the turn's total
    await final_answers(None)  # no priced call: no value
    await final_answers(0.002)
    stats = await store.stats(1, NOW)
    assert stats["savings"]["cost_usd"] == 0.005
    assert (await store.stats(30, NOW))["savings"]["cost_usd"] == 5.005


async def test_savings_value_is_kept_with_the_savings_history(
    store: SqliteStore, clock: FakeClock
) -> None:
    """The value of each saving is stored with it (like its tokens): deleting the
    conversation no longer drops it, and the final messages' meta, which carries the
    same total, is not counted twice."""
    conversation_id = await store.create_conversation("Estalvi")
    clock.now = at(27, 9)
    question = await store.add_message(
        NewMessage(conversation_id, "question", "Q", final=True, meta={"mode": "solo"})
    )
    savings: dict[str, JsonValue] = {"compaction": 3000, "total": 3000, "cost_usd": 0.012}
    await store.add_message(
        NewMessage(
            conversation_id,
            "answer",
            "A",
            turn_id=question,
            agent="claude",
            final=True,
            meta={"savings": savings},
        )
    )
    records: tuple[tuple[SavingKind, int, float], ...] = (
        ("compaction", 3000, 0.01),
        ("unchanged", 50, 0.002),
    )
    for kind, tokens, cost in records:
        await store.record_saving(
            SavingRecord(conversation_id, question, kind, tokens, cost_usd=cost)
        )
    before = (await store.stats(1, NOW))["savings"]
    assert before["cost_usd"] == 0.012
    assert await store.delete_conversation(conversation_id)
    after = (await store.stats(1, NOW))["savings"]
    assert after == before
    assert after["total"] == 3050


async def test_savings_value_mixes_recorded_values_and_older_turns(
    store: SqliteStore, clock: FakeClock
) -> None:
    conversation_id = await store.create_conversation("Estalvi")
    clock.now = at(27, 9)

    async def older_turn(cost: float | None) -> int:
        """A turn stored before savings rows had a value: only the meta has it."""
        question = await store.add_message(
            NewMessage(conversation_id, "question", "Q", final=True, meta={"mode": "solo"})
        )
        savings: dict[str, JsonValue] = {"total": 10, "cost_usd": cost}
        await store.add_message(
            NewMessage(
                conversation_id,
                "answer",
                "A",
                turn_id=question,
                agent="claude",
                final=True,
                meta={"savings": savings},
            )
        )
        await store.record_saving(SavingRecord(conversation_id, question, "unchanged", 10))
        return question

    await older_turn(0.25)
    await store.record_saving(SavingRecord(None, None, "cache", 100, cost_usd=0.5))
    await store.record_saving(SavingRecord(None, None, "early_stop", 100))  # unpriced
    await store.record_saving(SavingRecord(None, None, "compaction", 1, cost_usd=float("nan")))
    assert (await store.stats(1, NOW))["savings"]["cost_usd"] == 0.75


async def test_latency_counts_only_the_calls_that_write_a_message(store: SqliteStore) -> None:
    """An agent's response time is that of its answers, revisions and syntheses: a
    history summary (short) and Claude's check of a PDF for ChatGPT (a long
    transcription) would skew it, so they count only in tokens and costs."""
    calls: tuple[tuple[Purpose, int], ...] = (
        ("answer", 1000),
        ("revision", 2000),
        ("synthesis", 3000),
    )
    for purpose, ms in calls:
        await usage(
            store, "claude", tokens=Usage(10, 5), latency_ms=ms, ttft_ms=100, purpose=purpose
        )
    await usage(store, "claude", tokens=Usage(10, 5), latency_ms=50, ttft_ms=5, purpose="summary")
    await usage(store, "claude", tokens=Usage(10, 5), latency_ms=240_000, purpose="check")

    stats = await store.stats(1, NOW)

    assert stats["latency"]["claude"] == {"p50_ms": 2000, "p95_ms": 3000, "ttft_p50_ms": 100}
    assert stats["totals"]["by_agent"]["claude"]["calls"] == 5


async def test_unpriced_savings_have_no_value(store: SqliteStore) -> None:
    await store.record_saving(SavingRecord(None, None, "compaction", 100))
    savings = (await store.stats(1, NOW))["savings"]
    assert savings["compaction"] == 100
    assert savings["cost_usd"] is None


def test_month_bounds() -> None:
    assert month_bounds(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == (
        "2026-12",
        "2026-12-01T00:00:00.000Z",
        "2027-01-01T00:00:00.000Z",
    )
    assert month_bounds(datetime(2026, 2, 1, tzinfo=UTC))[2] == "2026-03-01T00:00:00.000Z"


async def test_empty_stats(store: SqliteStore) -> None:
    stats = await store.stats(30, NOW)
    assert stats["totals"]["calls"] == 0
    assert stats["totals"]["cost_usd"] == 0
    assert stats["totals"]["by_agent"]["claude"]["cost_usd"] is None
    assert len(stats["daily"]) == 60
    assert stats["daily"][0]["date"] == "2026-08-29"
    assert stats["daily"][-1]["date"] == "2026-09-27"
    assert stats["latency"]["chatgpt"] == {"p50_ms": None, "p95_ms": None, "ttft_p50_ms": None}
    assert stats["turns"] == {"solo": 0, "duel": 0, "debate": 0}
    assert stats["consensus"] == {"debates": 0, "reached": 0, "avg_rounds": None}
    assert stats["savings"]["cost_usd"] is None
    zero = {"api_usd": 0.0, "equivalent_usd": 0.0, "unpriced_calls": 0}
    assert stats["costs"]["by_agent"] == {"claude": zero, "chatgpt": zero}
    assert stats["month"]["month"] == "2026-09"
    assert stats["month"]["by_agent"]["claude"] == {
        **zero,
        "budget_eur": None,
        "budget_used": None,
        "plan_eur": None,
        "plan_value": None,
    }


@pytest.mark.parametrize("days", [0, 366])
async def test_days_out_of_range(store: SqliteStore, days: int) -> None:
    with pytest.raises(ValueError, match="days"):
        await store.stats(days, NOW)


def test_window_bounds() -> None:
    dates, start, end = window(2, datetime(2026, 1, 1, 0, 30, tzinfo=UTC))
    assert dates == ["2025-12-31", "2026-01-01"]
    assert start == "2025-12-31T00:00:00.000Z"
    assert end == "2026-01-02T00:00:00.000Z"


def test_percentile_nearest_rank() -> None:
    assert percentile([], 50) is None
    assert percentile([7], 95) == 7
    assert percentile([4, 1, 3, 2], 50) == 2
    assert percentile(list(range(1, 101)), 95) == 95
    assert percentile(list(range(1, 101)), 100) == 100
