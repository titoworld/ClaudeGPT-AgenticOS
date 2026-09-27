from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_os.domain import AgentName, SavingKind, Usage
from agentic_os.orchestrator.store import JsonValue, NewMessage, SavingRecord, UsageRecord
from agentic_os.storage import SqliteStore
from agentic_os.storage.stats import percentile, window

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
) -> None:
    await store.record_usage(
        UsageRecord(
            conversation_id=None,
            turn_id=None,
            agent=agent,
            provider_mode="api",
            model="m",
            purpose="answer",
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
    }
    assert stats["daily"] == [
        {
            "date": "2026-09-25",
            "agent": "claude",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
        },
        {
            "date": "2026-09-25",
            "agent": "chatgpt",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
        },
        {
            "date": "2026-09-26",
            "agent": "claude",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
        },
        {
            "date": "2026-09-26",
            "agent": "chatgpt",
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_read_tokens": 0,
        },
        {
            "date": "2026-09-27",
            "agent": "claude",
            "input_tokens": 300,
            "output_tokens": 150,
            "cache_read_tokens": 0,
        },
        {
            "date": "2026-09-27",
            "agent": "chatgpt",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
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
