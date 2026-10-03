"""Response integrity through the engine: truncated replies, refusals, the budgets sent to
the providers and the revision parser rules (ADR 0005)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace

from agentic_os.domain import AgentName, DebateOptions, TurnOptions, Usage
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.events import (
    ServerEvent,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from agentic_os.orchestrator.memory import truncated_summary_error
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.prompts import INCOMPLETE_NOTE, OWN_INCOMPLETE_NOTE
from agentic_os.orchestrator.store import StoredMessage
from agentic_os.orchestrator.types import EngineConfig, TurnRequest
from agentic_os.pricing import ModelPrice
from agentic_os.providers.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    GenerationRequest,
    GenerationResult,
    ProviderEvent,
    RefusalError,
    TextDelta,
)
from agentic_os.providers.fake import FakeProvider

QUESTION = "Com organitzo un projecte petit?"
PRICE = ModelPrice(input=4.0, output=20.0, cache_read=0.2, cache_write=5.0)
PRICES = {model: PRICE for model in ("fake-claude", "fake-chatgpt")}


async def collect(events: AsyncIterator[ServerEvent]) -> list[ServerEvent]:
    return [event async for event in events]


def of_type[E](events: Sequence[ServerEvent], kind: type[E]) -> list[E]:
    return [event for event in events if isinstance(event, kind)]


def solo(agent: AgentName = "claude", request_id: str = "r") -> TurnRequest:
    return TurnRequest(request_id, QUESTION, "solo", agent)


def debate(rounds: int = 1) -> TurnRequest:
    options = TurnOptions(debate=DebateOptions(rounds=rounds, consensus_threshold=100))
    return TurnRequest("d", QUESTION, "debate", options=options)


def answers_of(store: InMemoryStore) -> list[StoredMessage]:
    return [m for m in store.messages if m.kind != "question"]


class Scripted(FakeProvider):
    """Streams ``replies[purpose]`` (in two chunks) instead of the canned text."""

    def __init__(self, agent: AgentName, replies: dict[str, str]) -> None:
        super().__init__(agent, chunk_delay=0)
        self._replies = replies

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        reply = self._replies.get(request.purpose)
        async for event in super().stream(request):
            if reply is None:
                yield event
            elif isinstance(event, GenerationResult):
                half = len(reply) // 2
                for part in (reply[:half], reply[half:]):
                    if part:
                        yield TextDelta(part)
                yield replace(event, text=reply)


# -- budgets and reasoning policy (A10, N4) -------------------------------------------------


async def test_explicit_budgets_and_reasoning_reach_every_call(store: InMemoryStore) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0) for agent in ("claude", "chatgpt")
    }
    engine = Engine(fakes, store, EngineConfig(keep_recent_messages=2), retry_delay=0)
    conversation_id: int | None = None
    for i in range(3):
        events = await collect(
            engine.run(
                TurnRequest(
                    f"s{i}",
                    f"Pregunta {i}: " + "detall " * 60,
                    "solo",
                    conversation_id=conversation_id,
                    options=TurnOptions(use_cache=False),
                )
            )
        )
        conversation_id = of_type(events, TurnStarted)[0].conversation_id
    await collect(
        engine.run(
            replace(debate(), conversation_id=conversation_id), compaction_threshold_tokens=100
        )
    )
    calls = [*fakes["claude"].requests, *fakes["chatgpt"].requests]
    summaries = [r for r in calls if r.purpose == "summary"]
    others = [r for r in calls if r.purpose != "summary"]
    assert summaries and {(r.max_output_tokens, r.reasoning) for r in summaries} == {(2000, "off")}
    assert {r.purpose for r in others} == {"answer", "revision", "synthesis"}
    assert {(r.max_output_tokens, r.reasoning) for r in others} == {(16_000, "default")}
    assert DEFAULT_MAX_OUTPUT_TOKENS == EngineConfig().max_output_tokens == 16_000
    assert EngineConfig().summary_max_output_tokens == 2000
    prewarmed = [*fakes["claude"].prewarmed, *fakes["chatgpt"].prewarmed]
    assert prewarmed and {(r.max_output_tokens, r.reasoning) for r in prewarmed} == {
        (16_000, "default")
    }


async def test_a_cut_off_summary_is_never_used(store: InMemoryStore) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, truncate={"summary"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(fakes, store, EngineConfig(keep_recent_messages=2), retry_delay=0)
    conversation_id: int | None = None
    for i in range(3):
        events = await collect(
            engine.run(
                TurnRequest(
                    f"s{i}",
                    f"Pregunta {i}: " + "detall " * 60,
                    "solo",
                    conversation_id=conversation_id,
                    options=TurnOptions(use_cache=False),
                )
            )
        )
        conversation_id = of_type(events, TurnStarted)[0].conversation_id
    assert conversation_id is not None
    done = (
        await collect(
            engine.run(
                TurnRequest("x", QUESTION, "solo", conversation_id=conversation_id),
                compaction_threshold_tokens=100,
            )
        )
    )[-1]
    assert isinstance(done, TurnCompleted)
    claude, chatgpt = (u for u in store.usage if u.purpose == "summary")
    # Billed, but a partial summary would replace the older messages for good.
    assert (claude.agent, claude.ok, claude.error) == ("claude", False, truncated_summary_error())
    assert claude.usage.output_tokens > 0
    assert (chatgpt.agent, chatgpt.ok) == ("chatgpt", True)
    history = await store.get_history(conversation_id)
    assert history.summary is not None and history.summary.startswith("Resum (demostració)")


# -- truncated replies (A10, N4, N14) --------------------------------------------------------


async def test_a_truncated_answer_is_stored_as_such_and_never_cached(store: InMemoryStore) -> None:
    claude = FakeProvider("claude", chunk_delay=0, truncate={"answer"})
    engine = Engine({"claude": claude}, store, retry_delay=0)

    events = await collect(engine.run(solo()))
    done = events[-1]
    assert isinstance(done, TurnCompleted) and not done.cached
    (completed,) = of_type(events, StreamCompleted)
    assert (completed.truncated, completed.finish_reason) == (True, "max_tokens")
    # The live event says why, like the stored meta (the same view after a reload).
    wire = completed.to_wire()
    assert (wire["truncated"], wire["finish_reason"]) == (True, "max_tokens")
    (answer,) = answers_of(store)
    assert answer.final and answer.id in done.final_message_ids
    assert answer.meta["truncated"] is True
    assert answer.meta["finish_reason"] == "max_tokens"
    streamed = "".join(e.text for e in of_type(events, StreamDelta))
    assert answer.content == streamed.strip()
    assert not store.cache

    # The same question again is answered by the model, not replayed from the cache.
    again = await collect(engine.run(solo(request_id="r2")))
    assert isinstance(again[-1], TurnCompleted) and not again[-1].cached
    assert len(claude.requests) == 2


async def test_the_budget_truncates_like_a_real_model(store: InMemoryStore) -> None:
    claude = FakeProvider("claude", chunk_delay=0)
    engine = Engine({"claude": claude}, store, EngineConfig(max_output_tokens=20), retry_delay=0)
    events = await collect(engine.run(solo()))
    assert isinstance(events[-1], TurnCompleted)
    (answer,) = answers_of(store)
    assert answer.meta["truncated"] is True and len(answer.content) <= 20 * 4
    assert not store.cache


async def test_a_complete_answer_has_no_truncation_fields(store: InMemoryStore) -> None:
    engine = Engine({"claude": FakeProvider("claude", chunk_delay=0)}, store, retry_delay=0)
    events = await collect(engine.run(solo()))
    (completed,) = of_type(events, StreamCompleted)
    assert completed.truncated is False and completed.finish_reason is None
    assert not {"truncated", "finish_reason", "unchanged_note"} & set(completed.to_wire())
    (answer,) = answers_of(store)
    assert "truncated" not in answer.meta and "finish_reason" not in answer.meta
    assert store.cache  # complete turns are still cached


async def test_an_empty_truncated_reply_says_the_limit_was_exhausted(
    store: InMemoryStore,
) -> None:
    class Exhausted(FakeProvider):
        async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
            async for event in super().stream(request):
                if isinstance(event, GenerationResult):
                    yield replace(event, text="", truncated=True, finish_reason="max_tokens")

    engine = Engine({"claude": Exhausted("claude", chunk_delay=0)}, store, retry_delay=0)
    events = await collect(engine.run(solo(), price_overrides=PRICES))
    (failed,) = of_type(events, StreamFailed)
    assert "límit de sortida" in failed.error.message
    assert isinstance(events[-1], TurnFailed)
    (record,) = store.usage
    assert not record.ok and record.usage.output_tokens > 0 and record.usage.cost_usd
    assert not store.cache


async def test_a_truncated_debate_answer_is_marked_incomplete_in_later_prompts(
    store: InMemoryStore,
) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, truncate={"answer"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(fakes, store, retry_delay=0)
    events = await collect(engine.run(debate(rounds=1)))
    assert isinstance(events[-1], TurnCompleted)
    revision = {r.purpose: r for r in fakes["chatgpt"].requests}["revision"]
    own = {r.purpose: r for r in fakes["claude"].requests}["revision"]
    # ChatGPT is told Claude's answer is incomplete; Claude is told its own answer is.
    assert INCOMPLETE_NOTE in revision.prompt and OWN_INCOMPLETE_NOTE not in revision.prompt
    assert OWN_INCOMPLETE_NOTE in own.prompt
    first = next(m for m in store.messages if m.kind == "answer" and m.agent == "claude")
    assert first.meta["truncated"] is True
    assert not store.cache


async def test_a_truncated_synthesis_input_is_marked_incomplete(store: InMemoryStore) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, truncate={"answer"}),
    }
    engine = Engine(fakes, store, retry_delay=0)
    await collect(engine.run(debate(rounds=0)))
    synthesis = next(r for r in fakes["claude"].requests if r.purpose == "synthesis")
    chatgpt_part = synthesis.prompt.split('<answer from="ChatGPT">', 1)[1]
    claude_part = synthesis.prompt.split('<answer from="Claude">', 1)[1].split("</answer>")[0]
    assert INCOMPLETE_NOTE in chatgpt_part.split("</answer>")[0]
    assert INCOMPLETE_NOTE not in claude_part


async def test_a_degraded_synthesis_keeps_the_truncation_of_its_answer(
    store: InMemoryStore,
) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, truncate={"answer"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, fail={"answer"}),
    }
    events = await collect(Engine(fakes, store, retry_delay=0).run(debate(rounds=1)))
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    final = next(m for m in store.messages if m.id in done.final_message_ids)
    assert final.meta["degraded"] is True and final.meta["truncated"] is True
    assert final.meta["finish_reason"] == "max_tokens"
    last = of_type(events, StreamCompleted)[-1]
    assert last.message_id == final.id
    assert (last.truncated, last.finish_reason) == (True, "max_tokens")


# -- refusals (A17) ------------------------------------------------------------------------


async def test_a_refusal_is_reported_with_its_own_message_and_billed(
    store: InMemoryStore,
) -> None:
    chatgpt = FakeProvider("chatgpt", chunk_delay=0, refuse={"answer"})
    engine = Engine({"chatgpt": chatgpt}, store, retry_delay=0)
    events = await collect(engine.run(solo("chatgpt"), price_overrides=PRICES))
    (failed,) = of_type(events, StreamFailed)
    assert failed.error.kind == "invalid"
    assert "declinat" in failed.error.message and "buida" not in failed.error.message
    assert isinstance(events[-1], TurnFailed)
    (record,) = store.usage
    assert not record.ok and record.error is not None and "declinat" in record.error
    assert record.usage.input_tokens > 0 and record.usage.cost_usd
    assert not answers_of(store) and not store.cache
    assert len(chatgpt.requests) == 1  # a refusal is not retried


async def test_a_refusal_after_partial_text_stores_nothing_and_is_not_cached(
    store: InMemoryStore,
) -> None:
    chatgpt = FakeProvider("chatgpt", chunk_delay=0, refuse={"answer"}, refuse_after=3)
    engine = Engine({"chatgpt": chatgpt}, store, retry_delay=0)
    events = await collect(engine.run(solo("chatgpt"), price_overrides=PRICES))
    assert of_type(events, StreamDelta)  # the fragment was streamed...
    assert of_type(events, StreamFailed) and isinstance(events[-1], TurnFailed)
    assert not answers_of(store) and not store.cache  # ...but never became an answer
    (record,) = store.usage
    assert record.usage.output_tokens > 0

    again = await collect(engine.run(solo("chatgpt", request_id="r2"), price_overrides=PRICES))
    assert isinstance(again[-1], TurnFailed)
    assert len(chatgpt.requests) == 2


async def test_a_refused_synthesis_falls_back_to_the_other_agent(store: InMemoryStore) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0, refuse={"synthesis"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    events = await collect(Engine(fakes, store, retry_delay=0).run(debate(rounds=0)))
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    syntheses = [s for s in of_type(events, StreamStarted) if s.kind == "synthesis"]
    assert [s.agent for s in syntheses] == ["claude", "chatgpt"]
    failed = {e.stream_id: e for e in of_type(events, StreamFailed)}
    assert "declinat" in failed[syntheses[0].stream_id].error.message
    # The UI shows the synthesis in final_message_ids, not the failed first attempt (A1).
    completed = {e.stream_id: e for e in of_type(events, StreamCompleted)}
    assert completed[syntheses[1].stream_id].message_id in done.final_message_ids
    assert syntheses[0].stream_id not in completed


def test_refusal_error_is_shared_and_not_retryable() -> None:
    error = RefusalError(
        "Declinat.", usage=Usage(input_tokens=5), model="m", category="cyber", refusal="  No.\x00 "
    )
    assert (error.kind, error.retryable, error.model, error.category) == (
        "invalid",
        False,
        "m",
        "cyber",
    )
    assert error.usage == Usage(input_tokens=5)
    assert error.refusal == "No."


# -- revisions: tags as text and UNCHANGED notes (N1, N2) --------------------------------------

REVISED = (
    "Ask the model to wrap its reply in `<answer>` and `</answer>` tags, then extract the "
    "text between them.\n\nStep 2: validate the output."
)


async def test_a_revision_with_tags_in_code_is_stored_whole(store: InMemoryStore) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": Scripted(
            "claude",
            {
                "revision": (
                    f"<critique>- x</critique><answer>{REVISED}</answer><agreement>60</agreement>"
                )
            },
        ),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    events = await collect(Engine(fakes, store, retry_delay=0).run(debate(rounds=1)))
    assert isinstance(events[-1], TurnCompleted)
    revision = next(m for m in store.messages if m.kind == "revision" and m.agent == "claude")
    assert revision.content == REVISED
    assert revision.meta["agreement"] == 60
    synthesis = next(r for r in fakes["claude"].requests if r.purpose == "synthesis")
    assert "Step 2: validate the output." in synthesis.prompt


async def test_unchanged_with_a_note_keeps_the_answer_and_the_note(store: InMemoryStore) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": Scripted(
            "claude",
            {
                "revision": "<critique>None</critique>\n<answer>\nUNCHANGED (my previous "
                "answer already covers this)\n</answer>\n<agreement>80</agreement>"
            },
        ),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    events = await collect(Engine(fakes, store, retry_delay=0).run(debate(rounds=1)))
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    first = next(m for m in store.messages if m.kind == "answer" and m.agent == "claude")
    revision = next(m for m in store.messages if m.kind == "revision" and m.agent == "claude")
    assert revision.content == first.content
    assert revision.meta["unchanged"] is True
    assert revision.meta["unchanged_note"] == "my previous answer already covers this"
    assert done.savings.unchanged > 0
    synthesis = next(r for r in fakes["claude"].requests if r.purpose == "synthesis")
    assert first.content[:40] in synthesis.prompt
    assert "UNCHANGED" not in synthesis.prompt
    stream = next(
        s.stream_id
        for s in of_type(events, StreamStarted)
        if s.kind == "revision" and s.agent == "claude"
    )
    shown = "".join(
        e.text
        for e in of_type(events, StreamDelta)
        if e.stream_id == stream and e.section == "answer"
    )
    assert shown == first.content  # the kept answer, never the marker or the note
    completed = {e.stream_id: e for e in of_type(events, StreamCompleted)}[stream]
    assert completed.unchanged is True
    assert completed.to_wire()["unchanged_note"] == "my previous answer already covers this"

    # Replayed from the cache: the same live view, note included.
    again = await collect(
        Engine(fakes, store, retry_delay=0).run(replace(debate(), request_id="e"))
    )
    assert isinstance(again[-1], TurnCompleted) and again[-1].cached
    replayed = next(
        s.stream_id
        for s in of_type(again, StreamStarted)
        if s.kind == "revision" and s.agent == "claude"
    )
    completed = {e.stream_id: e for e in of_type(again, StreamCompleted)}[replayed]
    assert completed.unchanged_note == "my previous answer already covers this"
