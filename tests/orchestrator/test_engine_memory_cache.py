"""Compaction, turn cache and usage recording through the engine."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

from agentic_os.domain import AgentName, TurnMode, TurnOptions, Usage
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.events import (
    PhaseChanged,
    ServerEvent,
    StreamCompleted,
    StreamDelta,
    StreamStarted,
    TurnCompleted,
    TurnStarted,
)
from agentic_os.orchestrator.memory import build_context, compaction_cut, context_from_history
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.types import EngineConfig, TurnRequest
from agentic_os.providers.fake import FakeProvider

NO_CACHE = TurnOptions(use_cache=False)


async def collect(events: AsyncIterator[ServerEvent]) -> list[ServerEvent]:
    return [event async for event in events]


def of_type[E](events: Sequence[ServerEvent], kind: type[E]) -> list[E]:
    return [event for event in events if isinstance(event, kind)]


def completed(events: Sequence[ServerEvent]) -> TurnCompleted:
    last = events[-1]
    assert isinstance(last, TurnCompleted), last
    return last


async def seed_conversation(engine: Engine, turns: int) -> int:
    """A conversation with ``turns`` solo turns of long questions."""
    conversation_id: int | None = None
    for i in range(turns):
        events = await collect(
            engine.run(
                TurnRequest(
                    f"seed{i}",
                    f"Pregunta {i}: " + "detall " * 60,
                    "solo",
                    conversation_id=conversation_id,
                    options=NO_CACHE,
                )
            )
        )
        conversation_id = of_type(events, TurnStarted)[0].conversation_id
    assert conversation_id is not None
    return conversation_id


# -- compaction -------------------------------------------------------------------------


async def test_compaction_summarizes_old_messages_and_reports_savings(
    fakes: dict[AgentName, FakeProvider], store: InMemoryStore
) -> None:
    engine = Engine(fakes, store, EngineConfig(keep_recent_messages=2))
    conversation_id = await seed_conversation(engine, 3)
    before = context_from_history(await store.get_history(conversation_id))
    cut = compaction_cut(before, threshold=100, keep_recent=2)
    assert cut == 4

    events = await collect(
        engine.run(
            TurnRequest("r", "I ara?", "duel", conversation_id=conversation_id),
            compaction_threshold_tokens=100,
        )
    )
    assert events[0] == PhaseChanged("r", "compaction", 0)
    assert isinstance(events[1], TurnStarted)

    conversation = store.conversations[conversation_id]
    assert conversation.summary is not None and "Pregunta 0" in conversation.summary
    assert conversation.summary_upto_id == before.messages[cut - 1].id

    summary_request = fakes["claude"].requests[-2]
    assert summary_request.purpose == "summary" and summary_request.fast
    assert len(summary_request.history) == 4
    summary_usage = next(u for u in store.usage if u.purpose == "summary")
    assert (summary_usage.agent, summary_usage.ok, summary_usage.turn_id) == ("claude", True, None)

    for agent in ("claude", "chatgpt"):
        answer_request = fakes[agent].requests[-1]
        assert answer_request.context_summary == conversation.summary
        assert [t.content for t in answer_request.history] == [
            m.content for m in before.messages[cut:]
        ]

    after = build_context(conversation.summary, before.messages[cut:])
    done = completed(events)
    assert done.savings.compaction == (before.tokens - after.tokens) * 2
    assert ("compaction", done.savings.compaction) in [
        (s.kind, s.tokens_saved) for s in store.savings
    ]
    answers = sum((e.usage for e in of_type(events, StreamCompleted)), Usage())
    assert done.usage == answers + summary_usage.usage


async def test_debate_compaction_counts_every_request_with_context(
    fakes: dict[AgentName, FakeProvider], store: InMemoryStore
) -> None:
    engine = Engine(fakes, store, EngineConfig(keep_recent_messages=2))
    conversation_id = await seed_conversation(engine, 3)
    before = context_from_history(await store.get_history(conversation_id))
    events = await collect(
        engine.run(
            TurnRequest("r", "Debat", "debate", conversation_id=conversation_id),
            compaction_threshold_tokens=100,
        )
    )
    summary = store.conversations[conversation_id].summary
    after = build_context(summary, before.messages[4:])
    # Two initial answers and the synthesis carry the context; revisions do not.
    assert completed(events).savings.compaction == (before.tokens - after.tokens) * 3


async def test_compaction_falls_back_to_chatgpt(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, fail={"summary"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, EngineConfig(keep_recent_messages=2))
    conversation_id = await seed_conversation(engine, 3)
    await collect(
        engine.run(
            TurnRequest("r", "Q", "solo", conversation_id=conversation_id),
            compaction_threshold_tokens=100,
        )
    )
    summaries = [(u.agent, u.ok) for u in store.usage if u.purpose == "summary"]
    assert summaries == [("claude", False), ("chatgpt", True)]
    assert store.conversations[conversation_id].summary is not None


async def test_failed_compaction_keeps_the_full_context(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, fail={"summary"})
        for agent in ("claude", "chatgpt")
    }
    engine = Engine(providers, store, EngineConfig(keep_recent_messages=2))
    conversation_id = await seed_conversation(engine, 3)
    events = await collect(
        engine.run(
            TurnRequest("r", "Q", "solo", conversation_id=conversation_id),
            compaction_threshold_tokens=100,
        )
    )
    assert completed(events).savings.compaction == 0
    assert store.conversations[conversation_id].summary is None
    assert len(providers["claude"].requests[-1].history) == 6


async def test_no_compaction_below_the_threshold(
    engine: Engine, store: InMemoryStore, fakes: dict[AgentName, FakeProvider]
) -> None:
    conversation_id = await seed_conversation(engine, 3)
    events = await collect(
        engine.run(TurnRequest("r", "Q", "solo", conversation_id=conversation_id))
    )
    assert not [e for e in events if isinstance(e, PhaseChanged) and e.phase == "compaction"]
    assert all(r.purpose != "summary" for r in fakes["claude"].requests)


# -- turn cache ---------------------------------------------------------------------------


async def test_cache_hit_replays_without_calling_models(
    engine: Engine, store: InMemoryStore, fakes: dict[AgentName, FakeProvider]
) -> None:
    first = await collect(engine.run(TurnRequest("r1", "Què és uv?\r\nI pip?", "solo")))
    calls = len(fakes["claude"].requests)
    second = await collect(engine.run(TurnRequest("r2", "  Què és uv?\nI pip?\n", "solo")))
    assert len(fakes["claude"].requests) == calls

    kinds = [type(e).__name__ for e in second]
    assert kinds == [
        "TurnStarted",
        "PhaseChanged",
        "StreamStarted",
        "StreamDelta",
        "StreamCompleted",
        "TurnCompleted",
    ]
    original = next(m for m in store.messages if m.kind == "answer")
    replayed = [m for m in store.messages if m.kind == "answer"][-1]
    assert replayed.id != original.id and replayed.content == original.content
    assert replayed.meta["cached"] is True and replayed.final
    assert replayed.meta["model"] == original.meta["model"]
    assert of_type(second, StreamDelta)[0].text == original.content
    assert of_type(second, StreamCompleted)[0].usage == Usage()

    done, first_done = completed(second), completed(first)
    assert done.cached and done.final_message_ids == (replayed.id,)
    assert done.savings.cache == first_done.usage.processed_tokens
    assert done.usage == Usage()
    assert [(s.kind, s.tokens_saved) for s in store.savings] == [
        ("cache", first_done.usage.processed_tokens)
    ]


async def test_debate_replay_restores_rounds_and_consensus(
    engine: Engine, store: InMemoryStore
) -> None:
    first = await collect(engine.run(TurnRequest("r1", "Debat", "debate")))
    second = await collect(engine.run(TurnRequest("r2", "Debat", "debate")))
    done = completed(second)
    assert done.cached and done.consensus == completed(first).consensus
    assert [(e.phase, e.round) for e in of_type(second, PhaseChanged)] == [
        (e.phase, e.round) for e in of_type(first, PhaseChanged)
    ]
    streams = of_type(second, StreamStarted)
    # Replayed in storage (completion) order, so only compare the set of streams.
    assert sorted((s.agent, s.kind, s.round) for s in streams) == sorted(
        (s.agent, s.kind, s.round) for s in of_type(first, StreamStarted)
    )
    revision = next(s for s in streams if s.kind == "revision")
    sections = [
        e.section for e in of_type(second, StreamDelta) if e.stream_id == revision.stream_id
    ]
    assert sections == ["critique", "answer"]
    agreements = [e.agreement for e in of_type(second, StreamCompleted) if e.agreement is not None]
    assert sorted(agreements) == [72, 72, 90, 90]
    synthesis = next(m for m in store.messages if m.id in done.final_message_ids)
    assert synthesis.kind == "synthesis" and synthesis.turn_id == done.turn_id


async def test_cache_is_bypassed_when_disabled(
    engine: Engine, fakes: dict[AgentName, FakeProvider]
) -> None:
    await collect(engine.run(TurnRequest("r1", "Hola", "solo")))
    second = await collect(engine.run(TurnRequest("r2", "Hola", "solo", options=NO_CACHE)))
    assert not completed(second).cached
    assert len(fakes["claude"].requests) == 2


async def test_cache_misses_on_other_mode_or_context(engine: Engine) -> None:
    first = await collect(engine.run(TurnRequest("r1", "Hola", "solo")))
    conversation_id = completed(first).conversation_id
    duel = await collect(engine.run(TurnRequest("r2", "Hola", "duel")))
    assert not completed(duel).cached
    # Same question inside the conversation: the context is different now.
    again = await collect(
        engine.run(TurnRequest("r3", "Hola", "solo", conversation_id=conversation_id))
    )
    assert not completed(again).cached


async def test_degraded_turns_are_not_cached(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, fail={"answer"}),
    }
    engine = Engine(providers, store)
    for request_id in ("r1", "r2"):
        events = await collect(engine.run(TurnRequest(request_id, "Hola", "debate")))
        assert not completed(events).cached
    assert store.cache == {}


async def test_cache_entries_expire(fakes: dict[AgentName, FakeProvider]) -> None:
    now = [datetime(2026, 9, 27, tzinfo=UTC)]
    store = InMemoryStore()
    engine = Engine(fakes, store, EngineConfig(cache_ttl_seconds=60), clock=lambda: now[0])
    await collect(engine.run(TurnRequest("r1", "Hola", "solo")))
    ((_, expires_at),) = store.cache.values()
    assert expires_at == now[0] + timedelta(seconds=60)
    now[0] += timedelta(seconds=30)
    assert completed(await collect(engine.run(TurnRequest("r2", "Hola", "solo")))).cached
    now[0] += timedelta(seconds=31)
    assert not completed(await collect(engine.run(TurnRequest("r3", "Hola", "solo")))).cached


# -- usage ----------------------------------------------------------------------------------


async def test_every_call_is_recorded(engine: Engine, store: InMemoryStore) -> None:
    modes: tuple[TurnMode, ...] = ("solo", "duel", "debate")
    for mode in modes:
        events = await collect(engine.run(TurnRequest(mode, f"Pregunta {mode}", mode)))
        done = completed(events)
        records = [u for u in store.usage if u.turn_id == done.turn_id]
        assert all(u.conversation_id == done.conversation_id for u in records)
        assert all(u.ok and u.provider_mode == "fake" for u in records)
        assert all(u.model == f"fake-{u.agent}" for u in records)
        assert sum((u.usage for u in records), Usage()) == done.usage
        expected = {"solo": 1, "duel": 2, "debate": 7}[mode]
        assert len(records) == expected
