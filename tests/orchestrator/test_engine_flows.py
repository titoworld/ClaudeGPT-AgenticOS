"""Solo, duel and debate turns end to end with fake providers and the in-memory store."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence

import pytest

from agentic_os.domain import AgentName, DebateOptions, ProviderMode, TurnOptions
from agentic_os.orchestrator.engine import Engine, make_title
from agentic_os.orchestrator.events import (
    PhaseChanged,
    ServerEvent,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.tokens import estimate_tokens
from agentic_os.orchestrator.types import EngineConfig, TurnRequest
from agentic_os.providers.base import (
    GenerationRequest,
    ModelInfo,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
)
from agentic_os.providers.fake import FakeProvider

QUESTION = "Com organitzo un projecte petit?"


async def collect(events: AsyncIterator[ServerEvent]) -> list[ServerEvent]:
    return [event async for event in events]


def debate(rounds: int = 2, threshold: int = 85, synthesizer: AgentName = "claude") -> TurnRequest:
    options = TurnOptions(
        debate=DebateOptions(rounds=rounds, consensus_threshold=threshold, synthesizer=synthesizer)
    )
    return TurnRequest("req", QUESTION, "debate", options=options)


def of_type[E](events: Sequence[ServerEvent], kind: type[E]) -> list[E]:
    return [event for event in events if isinstance(event, kind)]


def phases(events: Sequence[ServerEvent]) -> list[tuple[str, int]]:
    return [(e.phase, e.round) for e in of_type(events, PhaseChanged)]


def text_of(events: Sequence[ServerEvent], stream_id: str, section: str = "text") -> str:
    return "".join(
        e.text
        for e in of_type(events, StreamDelta)
        if e.stream_id == stream_id and e.section == section
    )


class FlakyProvider:
    """Wraps a FakeProvider: raises ``error`` on the first ``failures`` calls, optionally
    after streaming some text first."""

    def __init__(
        self,
        inner: FakeProvider,
        error: ProviderError,
        *,
        failures: int = 1,
        stream_first: str | None = None,
    ) -> None:
        self.inner = inner
        self.error = error
        self.failures = failures
        self.stream_first = stream_first
        self.calls = 0

    @property
    def agent(self) -> AgentName:
        return self.inner.agent

    @property
    def mode(self) -> ProviderMode:
        return self.inner.mode

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls <= self.failures:
            if self.stream_first is not None:
                yield TextDelta(self.stream_first)
            raise self.error
        async for event in self.inner.stream(request):
            yield event

    async def prewarm(self, request: GenerationRequest) -> None:
        await self.inner.prewarm(request)

    async def status(self) -> ProviderStatus:
        return await self.inner.status()

    @property
    def fast_model(self) -> str:
        return "fast"

    @property
    def models_live(self) -> bool:
        return True

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        model = "flaky"
        return (ModelInfo(id=model, label=model, is_default=True),)

    async def aclose(self) -> None:
        await self.inner.aclose()


# -- solo -----------------------------------------------------------------------------


async def test_solo_event_order_and_storage(engine: Engine, store: InMemoryStore) -> None:
    events = await collect(engine.run(TurnRequest("r1", f"  {QUESTION}\n", "solo", "chatgpt")))
    kinds = [type(e).__name__ for e in events]
    assert kinds[:3] == ["TurnStarted", "PhaseChanged", "StreamStarted"]
    assert kinds[-2:] == ["StreamCompleted", "TurnCompleted"]
    assert set(kinds[3:-2]) == {"StreamDelta"}
    assert all(e.request_id == "r1" for e in events)

    started = of_type(events, TurnStarted)[0]
    assert started.new_conversation and started.mode == "solo"
    stream = of_type(events, StreamStarted)[0]
    assert (stream.agent, stream.kind, stream.round, stream.model) == (
        "chatgpt",
        "answer",
        0,
        "fake-chatgpt",
    )
    completed = of_type(events, StreamCompleted)[0]
    done = of_type(events, TurnCompleted)[0]
    assert done.final_message_ids == (completed.message_id,)
    assert done.consensus is None and not done.cached
    assert done.usage == completed.usage

    question, answer = store.messages
    assert question.kind == "question" and question.content == QUESTION and question.final
    assert question.meta["mode"] == "solo" and question.meta["target"] == "chatgpt"
    assert answer.turn_id == question.id == started.turn_id
    assert answer.final and answer.agent == "chatgpt"
    assert answer.content == text_of(events, stream.stream_id)
    assert answer.meta["model"] == "fake-chatgpt" and answer.meta["cached"] is False
    assert [(u.agent, u.purpose, u.ok) for u in store.usage] == [("chatgpt", "answer", True)]
    assert store.conversations[started.conversation_id].title == QUESTION


async def test_second_turn_sends_canonical_history(
    engine: Engine, store: InMemoryStore, fakes: dict[AgentName, FakeProvider]
) -> None:
    first = await collect(engine.run(TurnRequest("r1", "Primera", "solo")))
    conversation_id = of_type(first, TurnStarted)[0].conversation_id
    second = await collect(
        engine.run(TurnRequest("r2", "Segona", "solo", conversation_id=conversation_id))
    )
    assert not of_type(second, TurnStarted)[0].new_conversation
    request = fakes["claude"].requests[-1]
    assert request.prompt == "Segona"
    assert [(t.role, t.agent) for t in request.history] == [("user", None), ("assistant", "claude")]
    assert request.history[0].content == "Primera"
    assert request.max_output_tokens == EngineConfig().max_output_tokens


async def test_solo_failure_fails_the_turn(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, fail={"answer"})
    }
    events = await collect(Engine(providers, store).run(TurnRequest("r", QUESTION, "solo")))
    assert isinstance(events[-1], TurnFailed)
    assert events[-1].error.kind == "unavailable"
    assert len(of_type(events, StreamFailed)) == 1
    assert [u.ok for u in store.usage] == [False]


# -- duel -----------------------------------------------------------------------------


async def test_duel_streams_interleave_and_both_are_final(
    engine: Engine, store: InMemoryStore
) -> None:
    events = await collect(engine.run(TurnRequest("r", QUESTION, "duel")))
    streams = {e.stream_id: e.agent for e in of_type(events, StreamStarted)}
    assert sorted(streams.values()) == ["chatgpt", "claude"]
    order = [streams[e.stream_id] for e in of_type(events, StreamDelta)]
    first_switch = next(i for i, agent in enumerate(order) if agent != order[0])
    assert order[first_switch:].count(order[0]) > 0, "deltas of both agents interleave"

    done = events[-1]
    assert isinstance(done, TurnCompleted)
    answers = [m for m in store.messages if m.kind == "answer"]
    assert sorted(done.final_message_ids) == sorted(m.id for m in answers)
    assert all(m.final for m in answers)


async def test_duel_survives_one_failure(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, fail={"answer"}),
    }
    events = await collect(Engine(providers, store).run(TurnRequest("r", QUESTION, "duel")))
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert len(done.final_message_ids) == 1
    assert len(of_type(events, StreamFailed)) == 1
    assert store.cache == {}


async def test_duel_both_fail(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, fail={"answer"})
        for agent in ("claude", "chatgpt")
    }
    events = await collect(Engine(providers, store).run(TurnRequest("r", QUESTION, "duel")))
    assert isinstance(events[-1], TurnFailed)
    assert "Cap dels dos" in events[-1].error.message


# -- debate ---------------------------------------------------------------------------


async def test_full_debate(
    engine: Engine, store: InMemoryStore, fakes: dict[AgentName, FakeProvider]
) -> None:
    events = await collect(engine.run(debate(rounds=2)))
    assert phases(events) == [("answer", 0), ("revision", 1), ("revision", 2), ("synthesis", 2)]
    assert isinstance(events[0], TurnStarted)
    done = events[-1]
    assert isinstance(done, TurnCompleted)

    streams = of_type(events, StreamStarted)
    assert [(s.kind, s.round) for s in streams] == [
        ("answer", 0),
        ("answer", 0),
        ("revision", 1),
        ("revision", 1),
        ("revision", 2),
        ("revision", 2),
        ("synthesis", 2),
    ]
    assert streams[-1].agent == "claude"
    revision_sections = {
        e.section for e in of_type(events, StreamDelta) if e.stream_id == streams[2].stream_id
    }
    assert revision_sections == {"critique", "answer"}
    for delta in of_type(events, StreamDelta):
        assert "<critique>" not in delta.text and "<agreement>" not in delta.text

    assert all(not m.final for m in store.messages if m.kind in ("answer", "revision"))
    synthesis = [m for m in store.messages if m.final and m.kind != "question"]
    assert len(synthesis) == 1 and synthesis[0].kind == "synthesis"
    assert done.final_message_ids == (synthesis[0].id,)

    # Default fake agreements: 72 in round 1, 90 (and UNCHANGED) in round 2.
    completions = {e.stream_id: e for e in of_type(events, StreamCompleted)}
    round2 = [completions[s.stream_id] for s in streams if s.kind == "revision" and s.round == 2]
    assert all(c.agreement == 90 and c.unchanged for c in round2)
    assert done.consensus is not None
    assert done.consensus.reached and done.consensus.round == 2
    assert done.consensus.scores == {"claude": 90, "chatgpt": 90}
    assert synthesis[0].meta["consensus"] == {
        "reached": True,
        "round": 2,
        "scores": {"claude": 90, "chatgpt": 90},
    }

    revisions = [m for m in store.messages if m.kind == "revision"]
    assert {m.meta["agreement"] for m in revisions} == {72, 90}
    critique = revisions[0].meta["critique"]
    assert isinstance(critique, str) and critique.startswith("- ")

    # Revisions are self-contained; answers and the synthesis carry the context.
    for provider in fakes.values():
        for request in provider.requests:
            if request.purpose == "revision":
                assert request.history == () and request.context_summary is None
                assert QUESTION in request.prompt
    assert [r.purpose for r in fakes["claude"].requests] == [
        "answer",
        "revision",
        "revision",
        "synthesis",
    ]
    assert QUESTION in fakes["claude"].requests[-1].prompt
    assert {r.purpose for r in fakes["claude"].prewarmed} == {"revision", "synthesis"}
    assert [r.purpose for r in fakes["chatgpt"].prewarmed] == ["revision", "revision"]
    assert [(u.purpose, u.ok) for u in store.usage].count(("revision", True)) == 4
    assert len(store.usage) == 7


async def test_unchanged_keeps_previous_answer(
    engine: Engine, store: InMemoryStore, fakes: dict[AgentName, FakeProvider]
) -> None:
    events = await collect(engine.run(debate(rounds=2)))
    answers = {m.agent: m for m in store.messages if m.kind == "revision" and m.round == 1}
    unchanged = {m.agent: m for m in store.messages if m.kind == "revision" and m.round == 2}
    streams = {s.stream_id: s for s in of_type(events, StreamStarted)}
    for agent in ("claude", "chatgpt"):
        assert unchanged[agent].content == answers[agent].content
        assert unchanged[agent].meta["unchanged"] is True
        stream_id = next(
            sid
            for sid, s in streams.items()
            if s.agent == agent and s.kind == "revision" and s.round == 2
        )
        # The kept answer is shown again instead of the literal marker.
        assert text_of(events, stream_id, "answer") == answers[agent].content
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    expected = sum(estimate_tokens(m.content) for m in answers.values())
    assert done.savings.unchanged == expected
    assert [(s.kind, s.tokens_saved) for s in store.savings] == [("unchanged", expected)]


async def test_early_stop_on_consensus(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, agreements=[88])
        for agent in ("claude", "chatgpt")
    }
    events = await collect(Engine(providers, store).run(debate(rounds=4, threshold=85)))
    assert phases(events) == [("answer", 0), ("revision", 1), ("synthesis", 1)]
    done = events[-1]
    assert isinstance(done, TurnCompleted) and done.consensus is not None
    assert done.consensus.reached and done.consensus.round == 1
    revision_usage = [e.usage for e in of_type(events, StreamCompleted) if e.agreement is not None]
    pair = sum(u.total_tokens for u in revision_usage)
    assert done.savings.early_stop == 3 * pair
    assert ("early_stop", 3 * pair) in [(s.kind, s.tokens_saved) for s in store.savings]


async def test_no_consensus_runs_all_rounds(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, agreements=[40])
        for agent in ("claude", "chatgpt")
    }
    events = await collect(Engine(providers, store).run(debate(rounds=3)))
    assert phases(events)[-1] == ("synthesis", 3)
    done = events[-1]
    assert isinstance(done, TurnCompleted) and done.consensus is not None
    assert not done.consensus.reached and done.consensus.scores == {"claude": 40, "chatgpt": 40}
    assert done.savings.early_stop == 0


async def test_zero_rounds_goes_straight_to_synthesis(
    engine: Engine, fakes: dict[AgentName, FakeProvider]
) -> None:
    events = await collect(engine.run(debate(rounds=0, synthesizer="chatgpt")))
    assert phases(events) == [("answer", 0), ("synthesis", 0)]
    assert of_type(events, StreamStarted)[-1].agent == "chatgpt"
    assert [r.purpose for r in fakes["chatgpt"].prewarmed] == ["synthesis"]


async def test_degraded_debate_when_one_agent_fails(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, fail={"answer"}),
    }
    events = await collect(Engine(providers, store).run(debate()))
    assert phases(events) == [("answer", 0), ("synthesis", 0)]
    assert len(of_type(events, StreamFailed)) == 1
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert done.consensus is not None and not done.consensus.reached
    answer = next(m for m in store.messages if m.kind == "answer")
    synthesis = next(m for m in store.messages if m.kind == "synthesis")
    assert synthesis.content == answer.content and synthesis.agent == "claude"
    assert synthesis.meta["degraded"] is True and synthesis.final
    assert done.final_message_ids == (synthesis.id,)
    # No revision or synthesis call was made.
    assert [r.purpose for r in providers["claude"].requests] == ["answer"]
    assert store.cache == {}


async def test_revision_failure_keeps_last_answer(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, agreements=[50]),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, fail={"revision"}),
    }
    events = await collect(Engine(providers, store).run(debate(rounds=2)))
    assert phases(events) == [("answer", 0), ("revision", 1), ("revision", 2), ("synthesis", 2)]
    assert len(of_type(events, StreamFailed)) == 2
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert done.consensus is not None and done.consensus.scores == {"claude": 50}
    chatgpt_answer = next(m for m in store.messages if m.kind == "answer" and m.agent == "chatgpt")
    synthesis_prompt = providers["claude"].requests[-1].prompt
    assert chatgpt_answer.content in synthesis_prompt
    assert store.cache == {}
    assert [u.ok for u in store.usage].count(False) == 2


async def test_synthesis_falls_back_to_the_other_agent(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, fail={"synthesis"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    events = await collect(Engine(providers, store).run(debate(rounds=1)))
    syntheses = [s for s in of_type(events, StreamStarted) if s.kind == "synthesis"]
    assert [s.agent for s in syntheses] == ["claude", "chatgpt"]
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    final = next(m for m in store.messages if m.id in done.final_message_ids)
    assert final.agent == "chatgpt" and final.kind == "synthesis"


async def test_debate_both_fail(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, fail={"answer"})
        for agent in ("claude", "chatgpt")
    }
    events = await collect(Engine(providers, store).run(debate()))
    assert isinstance(events[-1], TurnFailed)
    assert not of_type(events, TurnCompleted)


# -- retries and errors -----------------------------------------------------------------


async def test_retry_once_on_retryable_error_before_streaming(store: InMemoryStore) -> None:
    flaky = FlakyProvider(
        FakeProvider("claude", chunk_delay=0),
        ProviderError("Sobrecàrrega", kind="rate_limit", retryable=True),
    )
    engine = Engine({"claude": flaky}, store, retry_delay=0)
    events = await collect(engine.run(TurnRequest("r", QUESTION, "solo")))
    assert isinstance(events[-1], TurnCompleted)
    assert not of_type(events, StreamFailed)
    assert len(of_type(events, StreamStarted)) == 1
    assert [(u.ok, u.error) for u in store.usage] == [
        (False, "rate_limit: Sobrecàrrega"),
        (True, None),
    ]


async def test_no_retry_for_non_retryable_or_after_streaming(store: InMemoryStore) -> None:
    fatal = FlakyProvider(
        FakeProvider("claude", chunk_delay=0), ProviderError("Sense sessió", kind="auth")
    )
    events = await collect(
        Engine({"claude": fatal}, store, retry_delay=0).run(TurnRequest("r", QUESTION, "solo"))
    )
    assert fatal.calls == 1 and isinstance(events[-1], TurnFailed)

    streamed = FlakyProvider(
        FakeProvider("chatgpt", chunk_delay=0),
        ProviderError("Tall", kind="unavailable", retryable=True),
        stream_first="Hola",
    )
    events = await collect(
        Engine({"chatgpt": streamed}, store, retry_delay=0).run(
            TurnRequest("r", QUESTION, "solo", "chatgpt")
        )
    )
    assert streamed.calls == 1
    failed = of_type(events, StreamFailed)
    assert len(failed) == 1 and failed[0].error.kind == "unavailable"
    assert isinstance(events[-1], TurnFailed)


async def test_retry_gives_up_after_the_second_failure(store: InMemoryStore) -> None:
    flaky = FlakyProvider(
        FakeProvider("claude", chunk_delay=0),
        ProviderError("Temps esgotat", kind="timeout", retryable=True),
        failures=5,
    )
    events = await collect(
        Engine({"claude": flaky}, store, retry_delay=0).run(TurnRequest("r", QUESTION, "solo"))
    )
    assert flaky.calls == 2
    assert of_type(events, StreamFailed)[0].error.kind == "timeout"


async def test_unexpected_provider_exception_is_a_stream_failure(store: InMemoryStore) -> None:
    broken = FlakyProvider(FakeProvider("claude", chunk_delay=0), ProviderError("x", kind="auth"))
    broken.error = RuntimeError("bug")  # type: ignore[assignment]
    events = await collect(Engine({"claude": broken}, store).run(TurnRequest("r", "Q", "solo")))
    assert of_type(events, StreamFailed)[0].error.kind == "internal"
    assert isinstance(events[-1], TurnFailed)


async def test_store_crash_ends_with_turn_failed(
    fakes: dict[AgentName, FakeProvider], store: InMemoryStore
) -> None:
    async def broken(conversation_id: int) -> bool:
        raise OSError("disk")

    store.conversation_exists = broken  # type: ignore[method-assign]
    events = await collect(
        Engine(fakes, store).run(TurnRequest("r", "Q", "solo", conversation_id=1))
    )
    assert len(events) == 1 and isinstance(events[0], TurnFailed)
    assert events[0].error.kind == "internal"


# -- validation and conversations ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [("   \n ", "La pregunta és buida."), ("x" * 101, "massa llarga")],
)
async def test_invalid_questions(
    fakes: dict[AgentName, FakeProvider], store: InMemoryStore, text: str, message: str
) -> None:
    engine = Engine(fakes, store, EngineConfig(max_question_chars=100))
    events = await collect(engine.run(TurnRequest("r", text, "solo")))
    assert len(events) == 1 and isinstance(events[0], TurnFailed)
    assert events[0].error.kind == "invalid" and message in events[0].error.message
    assert store.conversations == {} and store.messages == []


async def test_invalid_debate_options(engine: Engine) -> None:
    events = await collect(engine.run(debate(rounds=9)))
    assert isinstance(events[0], TurnFailed) and events[0].error.kind == "invalid"


async def test_missing_provider(store: InMemoryStore) -> None:
    engine = Engine({"claude": FakeProvider("claude", chunk_delay=0)}, store)
    events = await collect(engine.run(TurnRequest("r", "Q", "duel")))
    assert isinstance(events[0], TurnFailed) and events[0].error.kind == "unavailable"


async def test_unknown_conversation(engine: Engine, store: InMemoryStore) -> None:
    events = await collect(engine.run(TurnRequest("r", "Q", "solo", conversation_id=42)))
    assert len(events) == 1 and isinstance(events[0], TurnFailed)
    assert events[0].error.kind == "not_found"
    assert store.messages == []


def test_make_title() -> None:
    assert make_title("\n  Hola, món  \nsegona línia") == "Hola, món"
    long = make_title("a" * 100)
    assert len(long) == 60 and long.endswith("…")
    assert make_title("x" * 60) == "x" * 60


async def test_new_conversation_title_uses_first_line(engine: Engine, store: InMemoryStore) -> None:
    events = await collect(engine.run(TurnRequest("r", "Títol curt\nDetalls llargs", "solo")))
    conversation_id = of_type(events, TurnStarted)[0].conversation_id
    assert store.conversations[conversation_id].title == "Títol curt"


# -- cancellation -----------------------------------------------------------------------


def other_tasks() -> set[asyncio.Task[object]]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}


async def test_cancellation_mid_debate_leaves_no_tasks(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0.002) for agent in ("claude", "chatgpt")
    }
    engine = Engine(providers, store)
    in_revision = asyncio.Event()
    seen: list[ServerEvent] = []

    async def consume() -> None:
        async for event in engine.run(debate(rounds=2)):
            seen.append(event)
            if isinstance(event, StreamDelta) and event.section == "critique":
                in_revision.set()

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(in_revision.wait(), timeout=10)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert other_tasks() == set()
    assert not any(isinstance(e, TurnCompleted | TurnFailed) for e in seen)
    # Nothing is stored for the interrupted revisions.
    assert not [m for m in store.messages if m.kind in ("revision", "synthesis")]


async def test_closing_the_iterator_cancels_the_turn(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0.002) for agent in ("claude", "chatgpt")
    }
    events = Engine(providers, store).run(TurnRequest("r", QUESTION, "duel"))
    async for event in events:
        if isinstance(event, StreamDelta):
            break
    assert isinstance(events, AsyncGenerator)
    await events.aclose()
    assert other_tasks() == set()
    assert not [m for m in store.messages if m.kind == "answer"]


async def test_synthesis_failure_degrades_to_the_latest_answer(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, fail={"revision", "synthesis"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, agreements=[50], fail={"synthesis"}),
    }
    events = await collect(Engine(providers, store).run(debate(rounds=1)))
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    # Claude failed its revision, so ChatGPT is tried first and its revision is kept.
    syntheses = [s.agent for s in of_type(events, StreamStarted) if s.kind == "synthesis"]
    assert syntheses == ["chatgpt", "claude", "chatgpt"]
    revision = next(m for m in store.messages if m.kind == "revision")
    final = next(m for m in store.messages if m.id in done.final_message_ids)
    assert final.meta["degraded"] is True and final.content == revision.content
    assert store.cache == {}


async def test_both_revisions_failing_skips_the_remaining_rounds(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, fail={"revision"})
        for agent in ("claude", "chatgpt")
    }
    events = await collect(Engine(providers, store).run(debate(rounds=3)))
    assert phases(events) == [("answer", 0), ("revision", 1), ("synthesis", 1)]
    done = events[-1]
    assert isinstance(done, TurnCompleted) and done.consensus is not None
    assert not done.consensus.reached and done.savings.early_stop == 0


class BrokenPrewarm(FakeProvider):
    async def prewarm(self, request: GenerationRequest) -> None:
        raise RuntimeError("no warm-up")


async def test_prewarm_errors_are_ignored(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: BrokenPrewarm(agent, chunk_delay=0) for agent in ("claude", "chatgpt")
    }
    events = await collect(Engine(providers, store).run(debate(rounds=1)))
    assert isinstance(events[-1], TurnCompleted)
    assert other_tasks() == set()


async def test_cache_read_errors_are_a_miss(
    fakes: dict[AgentName, FakeProvider], store: InMemoryStore
) -> None:
    async def broken(key: str, now: object) -> None:
        raise OSError("disk")

    store.cache_get = broken  # type: ignore[method-assign]
    events = await collect(Engine(fakes, store).run(TurnRequest("r", "Q", "solo")))
    done = events[-1]
    assert isinstance(done, TurnCompleted) and not done.cached
