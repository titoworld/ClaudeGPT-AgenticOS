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
    StreamDelta,
    StreamFailed,
    TurnCompleted,
    TurnStarted,
)
from agentic_os.orchestrator.memory import EMPTY_SUMMARY_ERROR
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.store import JsonValue, StoredMessage
from agentic_os.orchestrator.types import EngineConfig, TurnRequest
from agentic_os.pricing import ModelPrice
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ProviderEvent,
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
    """What a reloaded client adds up: every message's ``usage`` plus the question's
    ``compaction_usage`` (PROTOCOL.md, message meta)."""
    total = Usage()
    for message in turn_messages(store, done):
        if message.kind == "question":
            if "compaction_usage" in message.meta:
                total += usage_of(message.meta["compaction_usage"])
        else:
            total += usage_of(message.meta["usage"])
    return total


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


async def test_savings_value_is_below_the_highest_token_price(store: InMemoryStore) -> None:
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
    assert savings.cost_usd is not None
    # Before: valued at ~1100 $/MTok, while the model's highest price is 20 $/MTok.
    assert savings.cost_usd <= savings.total * PRICE.output / 1e6
    billed = sum(
        u.usage.total_tokens + u.usage.cache_read_tokens + u.usage.cache_write_tokens
        for u in store.usage
        if u.turn_id == done.turn_id
    )
    assert savings.cost_usd == pytest.approx(
        savings.unchanged * (done.usage.cost_usd or 0.0) / billed
    )


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
    billed = sum(
        u.usage.total_tokens + u.usage.cache_read_tokens + u.usage.cache_write_tokens
        for u in records
    )
    rate = (done.usage.cost_usd or 0.0) / billed
    early_stop_value = 3 * 2 * sum(revisions) / len(revisions)
    expected = early_stop_value + (done.savings.unchanged + done.savings.compaction) * rate
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
    assert empty.usage.total_tokens > 0 and empty.usage.cost_usd
    # TurnCompleted.usage is every billed call of the turn, as in the usage table.
    assert done.usage == records_usage(store, done)
    assert done.usage.cost_usd == pytest.approx(records_usage(store, done).cost_usd)
    # The savings are still valued on the calls that produced messages.
    assert done.savings.cost_usd == 0.0


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
    assert (claude.agent, claude.ok, claude.error) == ("claude", False, EMPTY_SUMMARY_ERROR)
    assert claude.model == "fake-claude-mini"
    assert claude.usage.total_tokens > 0 and claude.usage.cost_usd
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
    assert claude.usage.total_tokens > 0
    assert done.savings.compaction == 0  # nothing was compacted
    assert reloaded_usage(store, done) == done.usage
    assert usage_of(turn_messages(store, done)[0].meta["compaction_usage"]) == claude.usage


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
    assert reloaded.total_tokens == live.total_tokens
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
