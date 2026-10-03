"""Turn usage, savings and their value through the engine, in the awkward cases:
cache-heavy usage, billed calls that return nothing, failed attempts and reloads."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace

import pytest

from agentic_os.domain import AgentName, DebateOptions, TurnMode, TurnOptions, Usage
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.events import (
    ServerEvent,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from agentic_os.orchestrator.memory import empty_summary_error
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.store import JsonValue, StoredMessage
from agentic_os.orchestrator.types import EngineConfig, TurnRequest
from agentic_os.pricing import ModelPrice, estimate_cost_usd
from agentic_os.providers.base import (
    DeclinedAttempt,
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ProviderEvent,
    RefusalError,
    TextDelta,
)
from agentic_os.providers.fake import FakeProvider

PRICE = ModelPrice(input=4.0, output=20.0, cache_read=0.2, cache_write=5.0)
PRICES = {
    model: PRICE
    for model in ("fake-claude", "fake-chatgpt", "fake-claude-mini", "fake-chatgpt-mini")
}
NO_CACHE = TurnOptions(use_cache=False)


async def collect(events: AsyncIterator[ServerEvent]) -> list[ServerEvent]:
    return [event async for event in events]


def completed(events: Sequence[ServerEvent]) -> TurnCompleted:
    last = events[-1]
    assert isinstance(last, TurnCompleted), last
    return last


def usage_of(meta: JsonValue) -> Usage:
    """A ``Usage`` from its wire form (``meta.usage`` / ``meta.compaction_usage``)."""
    assert isinstance(meta, dict)
    ints = {
        key: value
        for key, value in meta.items()
        if key != "cost_usd" and isinstance(value, int) and not isinstance(value, bool)
    }
    cost = meta.get("cost_usd")
    assert cost is None or isinstance(cost, float | int)
    return Usage(**ints, cost_usd=None if cost is None else float(cost))


def turn_messages(store: InMemoryStore, done: TurnCompleted) -> list[StoredMessage]:
    return [m for m in store.messages if m.turn_id == done.turn_id]


def reloaded_usage(store: InMemoryStore, done: TurnCompleted) -> Usage:
    """What a reloaded client adds up: every message's ``usage``, the question's
    ``compaction_usage`` and the last final message's ``unstored_usage`` (PROTOCOL.md,
    message meta)."""
    total = Usage()
    unstored: Usage | None = None
    for message in turn_messages(store, done):
        if message.kind == "question":
            if "compaction_usage" in message.meta:
                total += usage_of(message.meta["compaction_usage"])
            continue
        total += usage_of(message.meta["usage"])
        if message.final:
            unstored = None
            if "unstored_usage" in message.meta:
                unstored = usage_of(message.meta["unstored_usage"])
    return total if unstored is None else total + unstored


def records_usage(store: InMemoryStore, done: TurnCompleted) -> Usage:
    """Everything recorded in the usage table for the turn."""
    return sum((u.usage for u in store.usage if u.turn_id == done.turn_id), Usage())


class CachingFake(FakeProvider):
    """Reports usage as the Claude CLI/API and Codex do: the input is the uncached
    remainder, the context goes to cache writes."""

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        async for event in super().stream(request):
            if isinstance(event, GenerationResult):
                usage = Usage(
                    input_tokens=3,
                    output_tokens=event.usage.output_tokens,
                    cache_write_tokens=20_000,
                )
                event = replace(event, usage=usage)
            yield event


class ReplyWith(FakeProvider):
    """Streams ``replies[purpose]`` instead of the canned text, with the canned usage."""

    def __init__(self, agent: AgentName, replies: dict[str, str]) -> None:
        super().__init__(agent, chunk_delay=0)
        self._replies = replies

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        reply = self._replies.get(request.purpose)
        async for event in super().stream(request):
            if reply is None:
                yield event
            elif isinstance(event, GenerationResult):
                if reply:
                    yield TextDelta(reply)
                yield replace(event, text=reply)


class FailsFirst(FakeProvider):
    """The first ``count`` answer calls fail before sending anything."""

    def __init__(self, agent: AgentName, error: ProviderError, count: int = 1) -> None:
        super().__init__(agent, chunk_delay=0)
        self._error = error
        self._left = count

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        if request.purpose == "answer" and self._left > 0:
            self._left -= 1
            self.requests.append(request)
            raise self._error
        async for event in super().stream(request):
            yield event


async def seed_conversation(engine: Engine, turns: int = 3) -> int:
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
        conversation_id = next(e for e in events if isinstance(e, TurnStarted)).conversation_id
    assert conversation_id is not None
    return conversation_id


# -- savings value (ACC-1) --------------------------------------------------------------


async def test_kept_answers_are_valued_at_the_output_price(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: CachingFake(agent, chunk_delay=0, agreements=(90,))
        for agent in ("claude", "chatgpt")
    }
    engine = Engine(providers, store, retry_delay=0)
    request = TurnRequest(
        "r", "Pregunta llarga?", "debate", options=TurnOptions(debate=DebateOptions(rounds=1))
    )
    done = completed(await collect(engine.run(request, price_overrides=PRICES)))
    savings = done.savings
    assert savings.unchanged > 0 and savings.cache == savings.compaction == 0
    # Before: ~1100 $/MTok (a blended cost including cache writes); a kept answer is
    # output its model did not write, at that model's output price.
    assert savings.cost_usd == pytest.approx(savings.unchanged * PRICE.output / 1e6)


async def test_early_stop_value_is_the_cost_of_the_skipped_revisions(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: CachingFake(agent, chunk_delay=0, agreements=[88]) for agent in ("claude", "chatgpt")
    }
    options = TurnOptions(debate=DebateOptions(rounds=4, consensus_threshold=85))
    engine = Engine(providers, store, retry_delay=0)
    done = completed(
        await collect(
            engine.run(TurnRequest("r", "Q?", "debate", options=options), price_overrides=PRICES)
        )
    )
    assert done.consensus is not None and done.consensus.round == 1
    records = [u for u in store.usage if u.turn_id == done.turn_id]
    revisions = [u.usage.cost_usd or 0.0 for u in records if u.purpose == "revision"]
    early_stop_value = 3 * 2 * sum(revisions) / len(revisions)
    expected = early_stop_value + done.savings.unchanged * PRICE.output / 1e6
    assert done.savings.cost_usd == pytest.approx(expected)
    saved = {s.kind: s for s in store.savings if s.turn_id == done.turn_id}
    assert saved["early_stop"].cost_usd == pytest.approx(early_stop_value)
    assert sum(s.cost_usd or 0.0 for s in saved.values()) == pytest.approx(done.savings.cost_usd)


# -- billed calls that returned nothing (ACC-3, ACC-4) -----------------------------------------


async def test_a_billed_empty_reply_counts_in_the_turn_usage(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": ReplyWith("claude", {"synthesis": ""}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    request = TurnRequest("r", "Q?", "debate", options=TurnOptions(debate=DebateOptions(rounds=0)))
    events = await collect(engine.run(request, price_overrides=PRICES))
    done = completed(events)
    assert [e.error.kind for e in events if isinstance(e, StreamFailed)] == ["invalid"]
    empty = next(u for u in store.usage if not u.ok)
    assert empty.usage.processed_tokens > 0 and empty.usage.cost_usd
    # TurnCompleted.usage is every billed call of the turn, as in the usage table.
    assert done.usage == records_usage(store, done)
    assert done.usage.cost_usd == pytest.approx(records_usage(store, done).cost_usd)
    # The savings are still valued on the calls that produced messages.
    assert done.savings.cost_usd == 0.0
    # The final synthesis carries the empty call, so a reload adds up to the live total.
    (synthesis,) = [m for m in turn_messages(store, done) if m.final and m.kind != "question"]
    assert usage_of(synthesis.meta["unstored_usage"]) == empty.usage
    assert reloaded_usage(store, done) == done.usage


async def test_an_empty_summary_keeps_its_billed_usage(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": ReplyWith("claude", {"summary": "   "}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, EngineConfig(keep_recent_messages=2), retry_delay=0)
    conversation_id = await seed_conversation(engine)
    events = await collect(
        engine.run(
            TurnRequest("x", "Q?", "solo", conversation_id=conversation_id),
            compaction_threshold_tokens=100,
            price_overrides=PRICES,
        )
    )
    done = completed(events)
    claude, chatgpt = (u for u in store.usage if u.purpose == "summary")
    assert (claude.agent, claude.ok, claude.error) == ("claude", False, empty_summary_error())
    assert claude.model == "fake-claude-mini"
    assert claude.usage.processed_tokens > 0 and claude.usage.cost_usd
    assert (chatgpt.agent, chatgpt.ok) == ("chatgpt", True)
    # Both summary calls are part of the turn's usage (live and reloaded).
    summaries = claude.usage + chatgpt.usage
    answer = next(u for u in store.usage if u.turn_id == done.turn_id)
    assert done.usage == summaries + answer.usage
    question = turn_messages(store, done)[0]
    assert usage_of(question.meta["compaction_usage"]) == summaries
    assert done.savings.compaction > 0


async def test_a_summary_that_fails_everywhere_still_counts_what_it_billed(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": ReplyWith("claude", {"summary": ""}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, fail={"summary"}),
    }
    engine = Engine(providers, store, EngineConfig(keep_recent_messages=2), retry_delay=0)
    conversation_id = await seed_conversation(engine)
    done = completed(
        await collect(
            engine.run(
                TurnRequest("x", "Q?", "solo", conversation_id=conversation_id),
                compaction_threshold_tokens=100,
                price_overrides=PRICES,
            )
        )
    )
    claude = next(u for u in store.usage if u.purpose == "summary" and u.agent == "claude")
    assert claude.usage.processed_tokens > 0
    assert done.savings.compaction == 0  # nothing was compacted
    assert reloaded_usage(store, done) == done.usage
    assert usage_of(turn_messages(store, done)[0].meta["compaction_usage"]) == claude.usage


# -- billed failures (K7) and reload parity (K9) -------------------------------------------------

DECLINED = Usage(input_tokens=5000, output_tokens=300, cache_read_tokens=1000)


class Refuses(FakeProvider):
    """The calls of ``purposes`` are declined as the Claude API does: a refusal that
    carries the billed usage and the model that declined."""

    def __init__(
        self,
        agent: AgentName,
        purposes: set[str],
        *,
        usage: Usage = DECLINED,
        model: str = "fake-claude",
    ) -> None:
        super().__init__(agent, chunk_delay=0)
        self._purposes = purposes
        self._usage = usage
        self._model = model

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        if request.purpose in self._purposes:
            self.requests.append(request)
            raise RefusalError("Claude ha declinat.", usage=self._usage, model=self._model)
        async for event in super().stream(request):
            yield event


class BilledRetryable(ProviderError):
    """A retryable failure that says what it billed (e.g. a stream cut after output)."""

    def __init__(self, usage: Usage, model: str) -> None:
        super().__init__("La resposta s'ha interromput.", kind="unavailable", retryable=True)
        self.usage = usage
        self.model = model


def priced(model: str, usage: Usage) -> Usage:
    return replace(usage, cost_usd=estimate_cost_usd(model, usage, PRICES))


async def test_a_billed_refusal_is_recorded_priced_and_in_the_turn_usage(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Refuses("claude", {"answer"}, model="fake-claude"),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    events = await collect(engine.run(TurnRequest("r", "Q?", "duel"), price_overrides=PRICES))
    done = completed(events)
    assert [e.error.kind for e in events if isinstance(e, StreamFailed)] == ["invalid"]
    (refused,) = [u for u in store.usage if u.agent == "claude"]
    # Before: Usage() with no model cost, although Anthropic billed the refusal.
    assert (refused.ok, refused.model) == (False, "fake-claude")
    assert refused.usage == priced("fake-claude", DECLINED)
    assert refused.usage.cost_usd
    assert done.usage == records_usage(store, done)
    answer = next(u for u in store.usage if u.agent == "chatgpt")
    assert done.usage == answer.usage + refused.usage


async def test_an_unbilled_refusal_records_its_model_and_no_cost(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Refuses("claude", {"answer"}, usage=Usage(), model="claude-opus-5"),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    done = completed(await collect(engine.run(TurnRequest("r", "Q?", "duel"))))
    (refused,) = [u for u in store.usage if u.agent == "claude"]
    assert (refused.ok, refused.model, refused.usage) == (False, "claude-opus-5", Usage())
    assert done.usage == records_usage(store, done)
    assert all("unstored_usage" not in m.meta for m in turn_messages(store, done))


async def test_a_billed_retried_attempt_counts_and_travels_with_the_answer(
    store: InMemoryStore,
) -> None:
    cut = Usage(input_tokens=800, output_tokens=40)
    providers: dict[AgentName, FakeProvider] = {
        "claude": FailsFirst("claude", BilledRetryable(cut, "fake-claude")),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    done = completed(
        await collect(engine.run(TurnRequest("r", "Q?", "solo"), price_overrides=PRICES))
    )
    first, second = [u for u in store.usage if u.turn_id == done.turn_id]
    assert (first.ok, first.usage) == (False, priced("fake-claude", cut))
    assert second.ok
    assert done.usage == first.usage + second.usage
    (answer,) = [m for m in turn_messages(store, done) if m.kind == "answer"]
    assert usage_of(answer.meta["unstored_usage"]) == first.usage
    assert reloaded_usage(store, done) == done.usage


async def test_a_refused_revision_travels_with_the_synthesis(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Refuses("claude", {"revision"}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, agreements=(50,)),
    }
    engine = Engine(providers, store, retry_delay=0)
    options = TurnOptions(debate=DebateOptions(rounds=2), use_cache=False)
    request = TurnRequest("r", "Q?", "debate", options=options)
    done = completed(await collect(engine.run(request, price_overrides=PRICES)))
    refusals = [u.usage for u in store.usage if not u.ok]
    assert len(refusals) == 2 and all(u == priced("fake-claude", DECLINED) for u in refusals)
    (synthesis,) = [m for m in turn_messages(store, done) if m.final and m.kind != "question"]
    assert usage_of(synthesis.meta["unstored_usage"]) == refusals[0] + refusals[1]
    # Before: a reload lost the refusals (the live total had none of them either).
    assert done.usage == records_usage(store, done)
    assert reloaded_usage(store, done) == done.usage


async def test_a_degraded_synthesis_carries_the_refused_synthesis_calls(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Refuses("claude", {"synthesis"}),
        "chatgpt": Refuses("chatgpt", {"synthesis"}, model="fake-chatgpt"),
    }
    engine = Engine(providers, store, retry_delay=0)
    request = TurnRequest("r", "Q?", "debate", options=TurnOptions(debate=DebateOptions(rounds=0)))
    done = completed(await collect(engine.run(request, price_overrides=PRICES)))
    (synthesis,) = [m for m in turn_messages(store, done) if m.final and m.kind != "question"]
    assert synthesis.meta["degraded"] is True
    expected = priced("fake-claude", DECLINED) + priced("fake-chatgpt", DECLINED)
    assert usage_of(synthesis.meta["unstored_usage"]) == expected
    assert reloaded_usage(store, done) == done.usage == records_usage(store, done)


@pytest.mark.parametrize("mode", ["solo", "duel", "debate"])
async def test_every_final_message_carries_the_unstored_usage_so_far(
    store: InMemoryStore, mode: TurnMode
) -> None:
    cut = Usage(input_tokens=800, output_tokens=40)
    providers: dict[AgentName, FakeProvider] = {
        "claude": FailsFirst("claude", BilledRetryable(cut, "fake-claude")),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    request = TurnRequest("r", "Q?", mode, options=NO_CACHE)
    done = completed(await collect(engine.run(request, price_overrides=PRICES)))
    finals = [m for m in turn_messages(store, done) if m.final and m.kind != "question"]
    # The failure came first, so every final message (the last one included) has it.
    assert finals and all(
        usage_of(m.meta["unstored_usage"]) == priced("fake-claude", cut) for m in finals
    )
    assert reloaded_usage(store, done) == done.usage


async def test_a_cache_hit_does_not_replay_the_unstored_usage(store: InMemoryStore) -> None:
    cut = Usage(input_tokens=800, output_tokens=40)
    providers: dict[AgentName, FakeProvider] = {
        "claude": FailsFirst("claude", BilledRetryable(cut, "fake-claude")),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    first = completed(await collect(engine.run(TurnRequest("a", "Hola", "solo"))))
    assert "unstored_usage" in turn_messages(store, first)[-1].meta
    done = completed(await collect(engine.run(TurnRequest("b", "Hola", "solo"))))
    assert done.cached and done.usage == Usage()
    assert all("unstored_usage" not in m.meta for m in turn_messages(store, done))
    assert reloaded_usage(store, done) == Usage()


async def test_a_refused_summary_counts_in_the_compaction_usage(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": Refuses("claude", {"summary"}, model="fake-claude-mini"),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, EngineConfig(keep_recent_messages=2), retry_delay=0)
    conversation_id = await seed_conversation(engine)
    done = completed(
        await collect(
            engine.run(
                TurnRequest("x", "Q?", "solo", conversation_id=conversation_id),
                compaction_threshold_tokens=100,
                price_overrides=PRICES,
            )
        )
    )
    claude, chatgpt = (u for u in store.usage if u.purpose == "summary")
    assert (claude.agent, claude.ok, claude.model) == ("claude", False, "fake-claude-mini")
    assert claude.usage == priced("fake-claude-mini", DECLINED)
    assert chatgpt.ok and done.savings.compaction > 0
    question = turn_messages(store, done)[0]
    assert usage_of(question.meta["compaction_usage"]) == claude.usage + chatgpt.usage
    assert reloaded_usage(store, done) == done.usage


async def test_a_billed_refusal_carried_the_compacted_context(store: InMemoryStore) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    config = EngineConfig(keep_recent_messages=2)
    conversation_id = await seed_conversation(Engine(fakes, store, config, retry_delay=0))
    fakes["claude"] = Refuses("claude", {"answer"})
    engine = Engine(fakes, store, config, retry_delay=0)
    request = TurnRequest("d", "Pregunta?", "duel", conversation_id=conversation_id)
    done = completed(
        await collect(engine.run(request, compaction_threshold_tokens=100, price_overrides=PRICES))
    )
    # The refusal was billed for the (compacted) context it carried, like the answer.
    (record,) = [s for s in store.savings if s.turn_id == done.turn_id]
    assert record.kind == "compaction" and record.detail.endswith("en 2 crides")
    per_request = record.tokens_saved // 2
    assert record.cost_usd == pytest.approx(2 * per_request * PRICE.input / 1e6)


# -- compaction value (K8) -----------------------------------------------------------------------


async def test_compaction_is_valued_at_the_input_price_of_each_call(store: InMemoryStore) -> None:
    prices = {
        **PRICES,
        "fake-claude": ModelPrice(input=4.0, output=20.0, cache_read=0.4, cache_write=5.0),
        "fake-chatgpt": ModelPrice(input=1.0, output=8.0, cache_read=0.1, cache_write=0.0),
    }
    fakes: dict[AgentName, FakeProvider] = {
        "claude": CachingFake("claude", chunk_delay=0),
        "chatgpt": CachingFake("chatgpt", chunk_delay=0),
    }
    engine = Engine(fakes, store, EngineConfig(keep_recent_messages=2), retry_delay=0)
    conversation_id = await seed_conversation(engine)
    request = TurnRequest("d", "I ara?", "duel", conversation_id=conversation_id, options=NO_CACHE)
    done = completed(
        await collect(engine.run(request, compaction_threshold_tokens=100, price_overrides=prices))
    )
    (record,) = [s for s in store.savings if s.turn_id == done.turn_id]
    per_request = record.tokens_saved // 2
    assert record.kind == "compaction" and per_request > 0
    # Each call's uncached input price (before: the turn's cost per billed token, which
    # the 20 000 cache-write tokens of every call pulled far from both).
    expected = per_request * (4.0 + 1.0) / 1e6
    assert record.cost_usd == pytest.approx(expected)
    assert done.savings.cost_usd == pytest.approx(expected)


# -- context requests (ACC-5) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("duel", ProviderError("not logged in", kind="auth")),
        ("solo", ProviderError("overloaded", kind="rate_limit", retryable=True)),
    ],
)
async def test_compaction_saving_counts_only_calls_that_reached_a_model(
    store: InMemoryStore, mode: TurnMode, error: ProviderError
) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    config = EngineConfig(keep_recent_messages=2)
    conversation_id = await seed_conversation(Engine(fakes, store, config, retry_delay=0))
    failing: AgentName = "chatgpt" if mode == "duel" else "claude"
    fakes[failing] = FailsFirst(failing, error)
    engine = Engine(fakes, store, config, retry_delay=0)
    request = TurnRequest("d", "Pregunta?", mode, conversation_id=conversation_id)
    done = completed(await collect(engine.run(request, compaction_threshold_tokens=100)))
    ok_calls = [u for u in store.usage if u.turn_id == done.turn_id and u.ok]
    assert len(ok_calls) == 1
    (record,) = [s for s in store.savings if s.turn_id == done.turn_id]
    assert record.kind == "compaction" and record.detail.endswith("en 1 crides")
    assert done.savings.compaction == record.tokens_saved


# -- revisions without an answer (ACC-6) -------------------------------------------------------


async def test_an_empty_revision_fails_and_is_not_an_unchanged_saving(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": ReplyWith("claude", {"revision": ""}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, agreements=(50,)),
    }
    engine = Engine(providers, store, retry_delay=0)
    options = TurnOptions(debate=DebateOptions(rounds=1))
    events = await collect(engine.run(TurnRequest("r", "Q?", "debate", options=options)))
    done = completed(events)
    assert [e.error.kind for e in events if isinstance(e, StreamFailed)] == ["invalid"]
    assert not [m for m in store.messages if m.kind == "revision" and m.agent == "claude"]
    assert done.savings.unchanged == 0
    assert not [s for s in store.savings if s.kind == "unchanged"]


async def test_a_revision_cut_off_before_its_answer_keeps_it_without_a_saving(
    store: InMemoryStore,
) -> None:
    truncated = "<critique>\nLa resposta de ChatGPT és correcta, però li falta"
    providers: dict[AgentName, FakeProvider] = {
        "claude": ReplyWith("claude", {"revision": truncated}),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, agreements=(50,)),
    }
    engine = Engine(providers, store, retry_delay=0)
    options = TurnOptions(debate=DebateOptions(rounds=1))
    events = await collect(engine.run(TurnRequest("r", "Q?", "debate", options=options)))
    done = completed(events)
    answer = next(m for m in store.messages if m.kind == "answer" and m.agent == "claude")
    revision = next(m for m in store.messages if m.kind == "revision" and m.agent == "claude")
    assert revision.content == answer.content
    assert revision.meta["unchanged"] is False and revision.meta["agreement"] is None
    assert revision.meta["critique"] == "La resposta de ChatGPT és correcta, però li falta"
    assert done.savings.unchanged == 0
    assert not [s for s in store.savings if s.kind == "unchanged"]
    # The live view shows the kept answer, as the store does.
    shown = [e.text for e in events if isinstance(e, StreamDelta) and e.section == "answer"]
    assert answer.content in shown


async def test_unchanged_is_still_a_saving(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, agreements=(90,))
        for agent in ("claude", "chatgpt")
    }
    engine = Engine(providers, store, retry_delay=0)
    options = TurnOptions(debate=DebateOptions(rounds=1))
    done = completed(await collect(engine.run(TurnRequest("r", "Q?", "debate", options=options))))
    revisions = [m for m in store.messages if m.kind == "revision"]
    assert all(m.meta["unchanged"] is True for m in revisions)
    assert done.savings.unchanged > 0


# -- cache key (ACC-7) --------------------------------------------------------------------------


async def test_a_question_about_other_code_is_not_served_from_the_cache(
    engine: Engine, fakes: dict[AgentName, FakeProvider]
) -> None:
    outside = "Per què falla?\n```python\nif x:\n    print(1)\nprint(2)\n```"
    inside = "Per què falla?\n```python\nif x:\n    print(1)\n    print(2)\n```"
    await collect(engine.run(TurnRequest("a", outside, "solo")))
    assert not completed(await collect(engine.run(TurnRequest("b", inside, "solo")))).cached
    assert completed(await collect(engine.run(TurnRequest("c", outside + "\n", "solo")))).cached
    assert len(fakes["claude"].requests) == 2


# -- reloaded turn total (ACC-9 / F1) ----------------------------------------------------------


@pytest.mark.parametrize("mode", ["solo", "duel", "debate"])
async def test_a_reloaded_turn_adds_up_to_the_live_total(
    store: InMemoryStore, mode: TurnMode
) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(fakes, store, EngineConfig(keep_recent_messages=2), retry_delay=0)
    conversation_id = await seed_conversation(engine)
    done = completed(
        await collect(
            engine.run(
                TurnRequest("x", "I ara?", mode, conversation_id=conversation_id),
                compaction_threshold_tokens=100,
                price_overrides=PRICES,
            )
        )
    )
    summary = next(u for u in store.usage if u.purpose == "summary")
    question = turn_messages(store, done)[0]
    assert question.kind == "question"
    assert usage_of(question.meta["compaction_usage"]) == summary.usage
    assert question.meta["compaction_usage"] == summary.usage.to_dict()
    live, reloaded = done.usage, reloaded_usage(store, done)
    assert reloaded.processed_tokens == live.processed_tokens
    assert reloaded.cost_usd == pytest.approx(live.cost_usd)


async def test_no_compaction_usage_without_a_summary(engine: Engine, store: InMemoryStore) -> None:
    done = completed(
        await collect(engine.run(TurnRequest("r", "Hola", "duel"), price_overrides=PRICES))
    )
    question = turn_messages(store, done)[0]
    assert "compaction_usage" not in question.meta
    assert reloaded_usage(store, done) == done.usage


async def test_a_cache_hit_records_the_value_of_the_replayed_turn(
    engine: Engine, store: InMemoryStore
) -> None:
    first = completed(
        await collect(engine.run(TurnRequest("a", "Hola", "solo"), price_overrides=PRICES))
    )
    done = completed(
        await collect(engine.run(TurnRequest("b", "Hola", "solo"), price_overrides=PRICES))
    )
    assert done.cached and done.usage == Usage()
    (record,) = [s for s in store.savings if s.turn_id == done.turn_id]
    assert record.kind == "cache" and record.cost_usd == pytest.approx(first.usage.cost_usd)
    assert done.savings.cost_usd == pytest.approx(first.usage.cost_usd)


# -- processed tokens (audit point 7, ADR 0008) ---------------------------------------------------

CASE = Usage(input_tokens=3, output_tokens=100, cache_read_tokens=10_000, cache_write_tokens=20_000)
"""A call as the Claude CLI/API reports it in a conversation with context."""


class CacheHeavy(FakeProvider):
    """Canned text, but every call reports :data:`CASE`."""

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        async for event in super().stream(request):
            if isinstance(event, GenerationResult):
                event = replace(event, usage=CASE)
            yield event


def test_processed_tokens_count_every_billed_token_once() -> None:
    # Reasoning is part of the output for Anthropic, OpenAI and Codex: never added.
    assert Usage(3, 100, 10_000, 20_000, reasoning_tokens=50).processed_tokens == 30_103
    assert Usage().processed_tokens == 0


async def test_a_replay_saves_the_processed_tokens_of_the_original_turn(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: CacheHeavy(agent, chunk_delay=0) for agent in ("claude", "chatgpt")
    }
    engine = Engine(providers, store, retry_delay=0)
    first = completed(
        await collect(engine.run(TurnRequest("a", "Hola", "solo"), price_overrides=PRICES))
    )
    done = completed(
        await collect(engine.run(TurnRequest("b", "Hola", "solo"), price_overrides=PRICES))
    )
    assert done.cached
    # Before: 103 tokens (input + output) valued at what the 30 103 tokens cost.
    assert done.savings.cache == first.usage.processed_tokens == 30_103
    assert done.savings.cost_usd == pytest.approx(first.usage.cost_usd)
    (record,) = [s for s in store.savings if s.turn_id == done.turn_id]
    assert (record.kind, record.tokens_saved) == ("cache", 30_103)


async def test_early_stop_counts_the_processed_tokens_of_the_skipped_rounds(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: CacheHeavy(agent, chunk_delay=0, agreements=[88]) for agent in ("claude", "chatgpt")
    }
    options = TurnOptions(debate=DebateOptions(rounds=4, consensus_threshold=85))
    engine = Engine(providers, store, retry_delay=0)
    done = completed(
        await collect(
            engine.run(TurnRequest("r", "Q?", "debate", options=options), price_overrides=PRICES)
        )
    )
    assert done.consensus is not None and done.consensus.round == 1
    # 3 skipped rounds of 2 revisions (before: 618 tokens instead of 180 618).
    assert done.savings.early_stop == 3 * 2 * 30_103
    saved = {s.kind: s for s in store.savings if s.turn_id == done.turn_id}
    assert saved["early_stop"].tokens_saved == 3 * 2 * 30_103


# -- declined attempts of a server-side fallback (audit point 8) ---------------------------------

BIG = ModelPrice(input=10.0, output=50.0, cache_read=0.25, cache_write=12.5)
"""The declining model's rates (Fable 5.1's), twice those of the model that serves."""
FALLBACK_PRICES = {**PRICES, "fake-claude-big": BIG}


class FallsBack(FakeProvider):
    """Every call is first declined by ``fake-claude-big`` (billed ``declined``) and then
    served by the requested model, as a server-side fallback of the Claude API does."""

    def __init__(self, agent: AgentName, declined: Usage = DECLINED) -> None:
        super().__init__(agent, chunk_delay=0)
        self._declined = declined

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        async for event in super().stream(request):
            if isinstance(event, GenerationResult):
                attempt = DeclinedAttempt("fake-claude-big", self._declined)
                event = replace(event, declined=(attempt,))
            yield event


class RefusesAfterAFallback(FakeProvider):
    """Declined by ``fake-claude-big`` (billed), then refused by the fallback model before
    any output (not billed)."""

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        yield TextDelta("Comença")
        raise RefusalError(
            "Claude ha declinat.",
            usage=Usage(),
            model="fake-claude",
            declined=(DeclinedAttempt("fake-claude-big", DECLINED),),
        )


def big(usage: Usage) -> Usage:
    return replace(usage, cost_usd=estimate_cost_usd("fake-claude-big", usage, FALLBACK_PRICES))


async def test_a_declined_attempt_is_a_billed_call_of_its_own_model(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FallsBack("claude"),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    request = TurnRequest("r", "Q?", "solo", options=NO_CACHE)
    events = await collect(engine.run(request, price_overrides=FALLBACK_PRICES))
    done = completed(events)
    declined, served = [u for u in store.usage if u.turn_id == done.turn_id]
    # Before: one row at the serving model's rates, with the declined tokens added to it.
    assert (declined.model, declined.ok, declined.usage) == (
        "fake-claude-big",
        False,
        big(DECLINED),
    )
    assert declined.error is not None and "fake-claude-big" in declined.error
    assert (served.model, served.ok) == ("fake-claude", True)
    (answer,) = [m for m in turn_messages(store, done) if m.kind == "answer"]
    # Tokens of different models are never summed: the answer keeps its own usage...
    assert usage_of(answer.meta["usage"]) == served.usage
    (stream_completed,) = [e for e in events if isinstance(e, StreamCompleted)]
    assert stream_completed.usage == served.usage
    # ...and says which attempts were declined before it, each with its own cost.
    assert answer.meta["declined"] == [
        {"model": "fake-claude-big", "usage": big(DECLINED).to_dict()}
    ]
    assert done.usage == declined.usage + served.usage == records_usage(store, done)
    outcome = turn_messages(store, done)[0].meta["outcome"]
    assert isinstance(outcome, dict) and outcome["usage"] == done.usage.to_dict()


async def test_a_replay_is_worth_the_declined_attempts_at_their_own_prices(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FallsBack("claude"),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    first = completed(
        await collect(engine.run(TurnRequest("a", "Hola", "solo"), price_overrides=FALLBACK_PRICES))
    )
    done = completed(
        await collect(engine.run(TurnRequest("b", "Hola", "solo"), price_overrides=FALLBACK_PRICES))
    )
    assert done.cached and done.usage == Usage()
    # The whole original turn: the answer and the attempt declined before it.
    assert done.savings.cache == first.usage.processed_tokens
    assert done.savings.cost_usd == pytest.approx(first.usage.cost_usd)
    (replayed,) = [m for m in turn_messages(store, done) if m.kind == "answer"]
    assert "declined" not in replayed.meta and replayed.meta["usage"] == Usage().to_dict()


async def test_a_refusal_after_a_fallback_is_billed_at_the_declining_model_rates(
    store: InMemoryStore,
) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": RefusesAfterAFallback("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    request = TurnRequest("r", "Q?", "solo", options=NO_CACHE)
    events = await collect(engine.run(request, price_overrides=FALLBACK_PRICES))
    failed = events[-1]
    assert isinstance(failed, TurnFailed)
    rows = [(u.model, u.ok, u.usage) for u in store.usage]
    # Before: one row of the fallback model, with the declined tokens at its rates.
    assert rows == [("fake-claude-big", False, big(DECLINED)), ("fake-claude", False, Usage())]
    assert failed.usage == big(DECLINED)
    # The refused call itself billed nothing: its card has no cost.
    (stream_failed,) = [e for e in events if isinstance(e, StreamFailed)]
    assert stream_failed.usage is None


async def test_a_declined_summary_attempt_counts_in_the_compaction_usage(
    store: InMemoryStore,
) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    config = EngineConfig(keep_recent_messages=2)
    conversation_id = await seed_conversation(Engine(fakes, store, config, retry_delay=0))
    fakes["claude"] = FallsBack("claude")
    engine = Engine(fakes, store, config, retry_delay=0)
    request = TurnRequest("x", "Q?", "solo", conversation_id=conversation_id, options=NO_CACHE)
    done = completed(
        await collect(
            engine.run(request, compaction_threshold_tokens=100, price_overrides=FALLBACK_PRICES)
        )
    )
    summaries = [u for u in store.usage if u.purpose == "summary"]
    assert [(u.model, u.ok) for u in summaries] == [
        ("fake-claude-big", False),
        ("fake-claude-mini", True),
    ]
    question = turn_messages(store, done)[0]
    spent = summaries[0].usage + summaries[1].usage
    assert summaries[0].usage == big(DECLINED)
    assert usage_of(question.meta["compaction_usage"]) == spent
    total = spent + records_usage(store, done)
    assert done.usage.processed_tokens == total.processed_tokens
    assert done.usage.cost_usd == pytest.approx(total.cost_usd)
