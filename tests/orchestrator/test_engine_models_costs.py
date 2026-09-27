"""Model selection per turn and cost accounting through the engine (fake providers)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence

import pytest

from agentic_os.domain import (
    AgentName,
    DebateOptions,
    ProviderMode,
    TurnMode,
    TurnOptions,
    Usage,
)
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.events import (
    ServerEvent,
    StreamCompleted,
    StreamStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.store import JsonValue, StoredMessage
from agentic_os.orchestrator.types import EngineConfig, TurnRequest
from agentic_os.pricing import ModelPrice, estimate_cost_usd
from agentic_os.providers.fake import FakeProvider

QUESTION = "Quin model és millor per resumir?"
PRICES: dict[str, ModelPrice] = {
    "fake-claude": ModelPrice(1.0, 2.0, 0.0, 0.0),
    "fake-chatgpt": ModelPrice(3.0, 4.0, 0.0, 0.0),
}


class ModeFake(FakeProvider):
    """A fake that reports another provider mode (to check the cost basis)."""

    def __init__(self, agent: AgentName, mode: ProviderMode) -> None:
        super().__init__(agent, chunk_delay=0)
        self._mode: ProviderMode = mode

    @property
    def mode(self) -> ProviderMode:
        return self._mode


async def collect(events: AsyncIterator[ServerEvent]) -> list[ServerEvent]:
    return [event async for event in events]


def of_type[E](events: Sequence[ServerEvent], kind: type[E]) -> list[E]:
    return [event for event in events if isinstance(event, kind)]


def completed(events: Sequence[ServerEvent]) -> TurnCompleted:
    last = events[-1]
    assert isinstance(last, TurnCompleted), last
    return last


def finals(store: InMemoryStore, done: TurnCompleted) -> list[StoredMessage]:
    return [m for m in store.messages if m.id in done.final_message_ids]


def meta_cost(message: StoredMessage) -> JsonValue:
    usage = message.meta["usage"]
    assert isinstance(usage, dict)
    return usage["cost_usd"]


def turn(
    mode: TurnMode = "solo",
    *,
    models: Mapping[AgentName, str] | None = None,
    fast_models: Mapping[AgentName, str] | None = None,
    options: TurnOptions | None = None,
    text: str = QUESTION,
    request_id: str = "r",
    conversation_id: int | None = None,
) -> TurnRequest:
    return TurnRequest(
        request_id,
        text,
        mode,
        options=options or TurnOptions(),
        models=dict(models or {}),
        fast_models=dict(fast_models or {}),
        conversation_id=conversation_id,
    )


# -- model selection ----------------------------------------------------------------------


async def test_turn_models_reach_every_call_and_the_meta(
    engine: Engine, store: InMemoryStore, fakes: dict[AgentName, FakeProvider]
) -> None:
    models: dict[AgentName, str] = {"claude": "sonnet", "chatgpt": "gpt-6-sol"}
    events = await collect(engine.run(turn("debate", models=models)))
    done = completed(events)

    for agent, provider in fakes.items():
        assert provider.requests and all(r.model == models[agent] for r in provider.requests)
        assert all(r.model == models[agent] for r in provider.prewarmed)
    assert {(s.agent, s.model) for s in of_type(events, StreamStarted)} == set(models.items())
    answers = [m for m in store.messages if m.kind != "question"]
    assert all(m.agent and m.meta["model"] == models[m.agent] for m in answers)
    assert {(u.agent, u.model) for u in store.usage if u.turn_id == done.turn_id} == set(
        models.items()
    )
    question = store.messages[0]
    assert question.kind == "question" and question.meta["models"] == models


async def test_question_meta_keeps_only_the_models_used(
    engine: Engine, store: InMemoryStore, fakes: dict[AgentName, FakeProvider]
) -> None:
    await collect(engine.run(turn(models={"claude": "haiku", "chatgpt": "gpt-6-luna"})))
    assert store.messages[0].meta["models"] == {"claude": "haiku"}
    assert fakes["chatgpt"].requests == []
    await collect(engine.run(turn(text="Sense models", request_id="r2")))
    assert "models" not in store.messages[-2].meta


async def test_an_override_does_not_become_the_default_model(
    engine: Engine, fakes: dict[AgentName, FakeProvider]
) -> None:
    await collect(engine.run(turn(models={"claude": "opus"})))
    events = await collect(engine.run(turn(text="I ara?", request_id="r2")))
    assert of_type(events, StreamStarted)[0].model == "fake-claude"
    assert fakes["claude"].requests[-1].model is None


async def test_compaction_uses_the_fast_model_of_the_turn(store: InMemoryStore) -> None:
    fakes: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0) for agent in ("claude", "chatgpt")
    }
    engine = Engine(fakes, store, EngineConfig(keep_recent_messages=2))
    conversation_id: int | None = None
    for i in range(3):
        events = await collect(
            engine.run(
                turn(
                    text=f"Pregunta {i}: " + "detall " * 60,
                    request_id=f"s{i}",
                    conversation_id=conversation_id,
                    options=TurnOptions(use_cache=False),
                )
            )
        )
        conversation_id = of_type(events, TurnStarted)[0].conversation_id
    events = await collect(
        engine.run(
            turn(
                text="Resumeix",
                fast_models={"claude": "fake-claude-nano"},
                conversation_id=conversation_id,
            ),
            compaction_threshold_tokens=100,
            price_overrides={"fake-claude-nano": ModelPrice(10.0, 10.0, 0.0, 0.0)},
        )
    )
    summary = fakes["claude"].requests[-2]
    assert summary.purpose == "summary" and summary.fast and summary.model == "fake-claude-nano"
    record = next(u for u in store.usage if u.purpose == "summary")
    assert record.model == "fake-claude-nano"
    assert record.usage.cost_usd == pytest.approx(record.usage.total_tokens * 10 / 1e6)
    # The summary is part of the turn's usage; the answer (no price) adds nothing.
    assert completed(events).usage.cost_usd == record.usage.cost_usd


async def test_invalid_model_ids_are_rejected(engine: Engine, store: InMemoryStore) -> None:
    cases: list[tuple[dict[AgentName, str], dict[AgentName, str]]] = [
        ({"claude": "--help me"}, {}),
        ({}, {"claude": ""}),
    ]
    for models, fast in cases:
        events = await collect(engine.run(turn(models=models, fast_models=fast)))
        assert len(events) == 1 and isinstance(events[0], TurnFailed)
        assert events[0].error.kind == "invalid"
    assert store.messages == []


# -- costs ----------------------------------------------------------------------------------


async def test_price_overrides_cost_every_call(engine: Engine, store: InMemoryStore) -> None:
    events = await collect(engine.run(turn("duel"), price_overrides=PRICES))
    done = completed(events)
    records = [u for u in store.usage if u.turn_id == done.turn_id]
    for record in records:
        price = PRICES[record.model]
        expected = (
            record.usage.input_tokens * price.input + record.usage.output_tokens * price.output
        ) / 1e6
        assert record.usage.cost_usd == pytest.approx(expected)
    streams = {e.message_id: e.usage for e in of_type(events, StreamCompleted)}
    for message in finals(store, done):
        assert message.meta["usage"] == streams[message.id].to_dict()
        assert message.meta["cost_basis"] == "equivalent"  # a demo, valued at "API" prices
    total = sum(r.usage.cost_usd or 0.0 for r in records)
    assert done.usage.cost_usd == pytest.approx(total)


async def test_unknown_models_have_no_cost_and_no_basis_in_fake_mode(
    engine: Engine, store: InMemoryStore
) -> None:
    done = completed(await collect(engine.run(turn())))
    (answer,) = finals(store, done)
    assert meta_cost(answer) is None
    assert "cost_basis" not in answer.meta
    assert done.usage.cost_usd is None and done.savings.cost_usd is None


async def test_cost_basis_follows_the_provider_mode(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": ModeFake("claude", "cli"),
        "chatgpt": ModeFake("chatgpt", "api"),
    }
    engine = Engine(providers, store)
    models: dict[AgentName, str] = {"claude": "claude-opus-5-5", "chatgpt": "gpt-6-mystery"}
    done = completed(await collect(engine.run(turn("duel", models=models))))
    by_agent = {m.agent: m for m in finals(store, done)}
    claude, chatgpt = by_agent["claude"], by_agent["chatgpt"]
    assert claude.meta["cost_basis"] == "equivalent"  # subscription: value at API prices
    assert chatgpt.meta["cost_basis"] == "api"
    record = next(u for u in store.usage if u.agent == "claude")
    assert record.provider_mode == "cli"
    assert record.usage.cost_usd == pytest.approx(
        estimate_cost_usd("claude-opus-5-5", record.usage)
    )
    assert meta_cost(chatgpt) is None


# -- savings ----------------------------------------------------------------------------------


async def test_savings_are_valued_at_the_average_price_of_the_turn(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, agreements=[88])
        for agent in ("claude", "chatgpt")
    }
    options = TurnOptions(debate=DebateOptions(rounds=4, consensus_threshold=85))
    engine = Engine(providers, store)
    events = await collect(engine.run(turn("debate", options=options), price_overrides=PRICES))
    done = completed(events)
    assert done.savings.early_stop > 0 and done.savings.unchanged > 0
    assert done.consensus is not None and done.consensus.round == 1
    calls = sum((e.usage for e in of_type(events, StreamCompleted)), Usage())
    assert calls.cost_usd is not None
    # Kept answers at the average price per token; the 3 skipped rounds at the average
    # cost of a revision pair (the fakes report no cache tokens).
    revisions = [u.usage.cost_usd or 0.0 for u in store.usage if u.purpose == "revision"]
    skipped = 3 * 2 * sum(revisions) / len(revisions)
    expected = done.savings.unchanged * calls.cost_usd / calls.total_tokens + skipped
    assert done.savings.cost_usd == pytest.approx(expected)
    # The final message carries the same savings, for a reloaded conversation.
    (synthesis,) = finals(store, done)
    assert synthesis.meta["savings"] == done.savings.to_wire()
    assert all("savings" not in m.meta for m in store.messages if not m.final)


@pytest.mark.parametrize("mode", ["solo", "duel", "debate"])
async def test_the_last_final_message_has_the_turn_savings(
    engine: Engine, store: InMemoryStore, mode: TurnMode
) -> None:
    done = completed(await collect(engine.run(turn(mode), price_overrides=PRICES)))
    messages = finals(store, done)
    assert all("savings" in m.meta for m in messages)
    assert messages[-1].meta["savings"] == done.savings.to_wire()


async def test_degraded_synthesis_carries_the_savings(store: InMemoryStore) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0, fail={"answer"}),
    }
    engine = Engine(providers, store)
    done = completed(await collect(engine.run(turn("debate"), price_overrides=PRICES)))
    (synthesis,) = finals(store, done)
    assert synthesis.meta["degraded"] is True
    assert synthesis.meta["savings"] == done.savings.to_wire()
    assert done.savings.cost_usd == 0.0  # priced turn, nothing saved


# -- turn cache ---------------------------------------------------------------------------------


async def test_cache_key_includes_the_models(
    engine: Engine, fakes: dict[AgentName, FakeProvider]
) -> None:
    async def ask(request_id: str, model: str | None) -> TurnCompleted:
        models: dict[AgentName, str] = {"claude": model} if model else {}
        return completed(await collect(engine.run(turn(models=models, request_id=request_id))))

    assert not (await ask("r1", "sonnet")).cached
    assert not (await ask("r2", "haiku")).cached
    assert (await ask("r3", "sonnet")).cached
    assert not (await ask("r4", None)).cached
    assert (await ask("r5", "fake-claude")).cached  # the default, named explicitly
    assert [r.model for r in fakes["claude"].requests] == ["sonnet", "haiku", None]


async def test_cache_hit_costs_nothing_and_saves_the_original_cost(
    engine: Engine, store: InMemoryStore
) -> None:
    first = completed(await collect(engine.run(turn("duel"), price_overrides=PRICES)))
    events = await collect(engine.run(turn("duel", request_id="r2"), price_overrides=PRICES))
    done = completed(events)
    assert done.cached and done.usage == Usage()
    assert all(e.usage == Usage() for e in of_type(events, StreamCompleted))
    assert done.savings.cache == first.usage.total_tokens
    assert done.savings.cost_usd == pytest.approx(first.usage.cost_usd)
    replayed = finals(store, done)
    assert len(replayed) == 2
    for message in replayed:
        assert message.meta["cached"] is True
        assert message.meta["usage"] == Usage().to_dict()
        assert "cost_basis" not in message.meta
        assert message.meta["savings"] == done.savings.to_wire()
