"""How a turn ended is stored on its question (``meta.outcome``, ADR 0007), so a reloaded
turn shows what the live one did: its status, its failures and its total usage, which
the terminal events carry too (audit points 9 and 14, new issue N10)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from agentic_os import i18n
from agentic_os.domain import AGENTS, AgentName, DebateOptions, TurnMode, TurnOptions, Usage
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.events import (
    ErrorInfo,
    ServerEvent,
    StreamDelta,
    StreamFailed,
    TurnCompleted,
    TurnFailed,
    TurnOutcome,
    TurnStarted,
)
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.store import JsonValue, NewMessage, SavingRecord, StoredMessage
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.pricing import ModelPrice, estimate_cost_usd
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
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
DECLINED = Usage(input_tokens=5000, output_tokens=300, cache_read_tokens=1000)


async def collect(events: AsyncIterator[ServerEvent]) -> list[ServerEvent]:
    return [event async for event in events]


def of_type[E](events: Sequence[ServerEvent], kind: type[E]) -> list[E]:
    return [event for event in events if isinstance(event, kind)]


def other_tasks() -> set[asyncio.Task[object]]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}


def usage_of(value: JsonValue) -> Usage:
    """A ``Usage`` from its wire form."""
    assert isinstance(value, dict)
    ints = {
        key: item
        for key, item in value.items()
        if key != "cost_usd" and isinstance(item, int) and not isinstance(item, bool)
    }
    cost = value.get("cost_usd")
    assert cost is None or isinstance(cost, float | int)
    return Usage(**ints, cost_usd=None if cost is None else float(cost))


def priced(model: str, usage: Usage) -> Usage:
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cost_usd=estimate_cost_usd(model, usage, PRICES),
    )


def question_of(store: InMemoryStore, turn_id: int) -> StoredMessage:
    (question,) = [m for m in store.messages if m.id == turn_id and m.kind == "question"]
    return question


def outcome_of(store: InMemoryStore, turn_id: int) -> dict[str, JsonValue]:
    outcome = question_of(store, turn_id).meta["outcome"]
    assert isinstance(outcome, dict)
    return outcome


def records_usage(store: InMemoryStore, turn_id: int) -> Usage:
    """Everything the usage table has for the turn (what the global stats add up)."""
    return sum((u.usage for u in store.usage if u.turn_id == turn_id), Usage())


def turn_id_of(events: Sequence[ServerEvent]) -> int:
    return of_type(events, TurnStarted)[0].turn_id


class RecordingStore(InMemoryStore):
    """Keeps every message as it was first stored."""

    def __init__(self) -> None:
        super().__init__()
        self.added: list[NewMessage] = []

    async def add_message(self, message: NewMessage) -> int:
        self.added.append(message)
        return await super().add_message(message)


class GatedStore(InMemoryStore):
    """Signals when ChatGPT's answer is stored."""

    def __init__(self) -> None:
        super().__init__()
        self.chatgpt_stored = asyncio.Event()

    async def add_message(self, message: NewMessage) -> int:
        message_id = await super().add_message(message)
        if message.kind == "answer" and message.agent == "chatgpt":
            self.chatgpt_stored.set()
        return message_id


class BlockingOutcomeStore(InMemoryStore):
    """Its outcome writes wait for :attr:`release`."""

    def __init__(self) -> None:
        super().__init__()
        self.writing = asyncio.Event()
        self.release = asyncio.Event()
        self.written = asyncio.Event()

    async def set_turn_outcome(self, question_message_id: int, outcome: TurnOutcome) -> None:
        self.writing.set()
        await self.release.wait()
        await super().set_turn_outcome(question_message_id, outcome)
        self.written.set()


class SlowSavings(InMemoryStore):
    """Its saving rows wait for :attr:`release` (a slow disk), after being written or
    before."""

    def __init__(self, *, write_first: bool) -> None:
        super().__init__()
        self._write_first = write_first
        self.recording = asyncio.Event()
        self.release = asyncio.Event()

    async def record_saving(self, record: SavingRecord) -> None:
        self.recording.set()
        if self._write_first:
            await super().record_saving(record)
        await self.release.wait()
        if not self._write_first:
            await super().record_saving(record)


class LateClaude(FakeProvider):
    """Waits until ChatGPT's answer is stored, then fails after billing: a refusal with
    its usage (after streaming some text) or an empty reply."""

    def __init__(self, gate: asyncio.Event, how: str) -> None:
        super().__init__("claude", chunk_delay=0)
        self._gate = gate
        self._how = how

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        if self._how == "refusal":
            yield TextDelta("Comencem per ")
        await self._gate.wait()
        if self._how == "refusal":
            raise RefusalError("Claude ha declinat.", usage=DECLINED, model="fake-claude")
        yield GenerationResult(
            text="",
            usage=Usage(input_tokens=5000, output_tokens=300),
            model="fake-claude",
            latency_ms=1,
        )


class Stalls(FakeProvider):
    """Streams its first words, then thinks until it is cancelled."""

    def __init__(self, agent: AgentName) -> None:
        super().__init__(agent, chunk_delay=0)
        self.cancelled = 0

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        yield TextDelta("Encara escric ")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        yield TextDelta("mai")


class SlowToStop(FakeProvider):
    """Streams its first words and thinks until it is cancelled; stopping then takes
    until :attr:`stopped` is set (a CLI process being terminated)."""

    def __init__(self, agent: AgentName) -> None:
        super().__init__(agent, chunk_delay=0)
        self.stopping = asyncio.Event()
        self.stopped = asyncio.Event()

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        yield TextDelta("Encara escric ")
        try:
            await asyncio.sleep(3600)
        finally:
            self.stopping.set()
            await self.stopped.wait()


class RaisesCancelled(FakeProvider):
    """Raises ``CancelledError`` by itself, with nobody cancelling the turn (a bug in a
    provider or in a library it uses)."""

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        yield TextDelta("Comencem ")
        raise asyncio.CancelledError


class RefusesAfterText(FakeProvider):
    """Streams a little and then declines, billed (as the Claude API does)."""

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        yield TextDelta("Comencem per ")
        raise RefusalError("Claude ha declinat.", usage=DECLINED, model="fake-claude")


def fakes() -> dict[AgentName, FakeProvider]:
    return {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }


# -- the question starts with an open outcome ------------------------------------------------


async def test_the_question_is_stored_with_an_open_outcome() -> None:
    store = RecordingStore()
    events = await collect(
        Engine(fakes(), store, retry_delay=0).run(TurnRequest("r", "Q?", "solo"))
    )
    question = store.added[0]
    assert question.kind == "question"
    # null until the engine writes how the turn ended: a turn that never ends (a crash,
    # a restart) keeps it, so a reload can tell it from a turn stored before outcomes.
    assert "outcome" in question.meta and question.meta["outcome"] is None
    assert outcome_of(store, turn_id_of(events))["status"] == "completed"


# -- completed turns -----------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["solo", "duel", "debate"])
async def test_a_completed_turn_stores_what_turn_completed_said(mode: TurnMode) -> None:
    store = InMemoryStore()
    outcomes: list[TurnOutcome] = []
    options = TurnOptions(debate=DebateOptions(rounds=1), use_cache=False)
    events = await collect(
        Engine(fakes(), store, retry_delay=0).run(
            TurnRequest("r", "Q?", mode, options=options),
            price_overrides=PRICES,
            on_outcome=outcomes.append,
        )
    )
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    assert outcome_of(store, done.turn_id) == {
        "status": "completed",
        "failures": [],
        "usage": done.usage.to_dict(),
        "savings": done.savings.to_wire(),
        "consensus": done.consensus.to_wire() if done.consensus else None,
        "final_message_ids": list(done.final_message_ids),
        "cached": False,
    }
    assert usage_of(outcome_of(store, done.turn_id)["usage"]) == records_usage(store, done.turn_id)
    (reported,) = outcomes
    assert reported.to_wire() == outcome_of(store, done.turn_id)


async def test_a_replayed_turn_stores_a_cached_outcome() -> None:
    store = InMemoryStore()
    engine = Engine(fakes(), store, retry_delay=0)
    await collect(engine.run(TurnRequest("a", "Hola", "duel"), price_overrides=PRICES))
    done = (await collect(engine.run(TurnRequest("b", "Hola", "duel"), price_overrides=PRICES)))[-1]
    assert isinstance(done, TurnCompleted) and done.cached
    outcome = outcome_of(store, done.turn_id)
    assert outcome["status"] == "completed" and outcome["cached"] is True
    assert outcome["usage"] == Usage().to_dict() == done.usage.to_dict()
    assert outcome["savings"] == done.savings.to_wire()
    assert outcome["final_message_ids"] == list(done.final_message_ids)


# -- a billed failure after the other agent stored its answer (audit point 9) ----------------


@pytest.mark.parametrize(
    ("how", "message"),
    [
        ("refusal", "Claude ha declinat."),
        ("empty", "El model ha retornat una resposta buida."),
    ],
)
async def test_a_late_billed_failure_of_a_duel_is_part_of_the_stored_total(
    how: str, message: str
) -> None:
    store = GatedStore()
    providers: dict[AgentName, FakeProvider] = {
        "claude": LateClaude(store.chatgpt_stored, how),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    request = TurnRequest("r", "Pregunta?", "duel", options=NO_CACHE)
    events = await collect(
        Engine(providers, store, retry_delay=0).run(request, price_overrides=PRICES)
    )
    done = events[-1]
    assert isinstance(done, TurnCompleted)
    billed = priced("fake-claude", DECLINED if how == "refusal" else Usage(5000, 300))
    (failed,) = of_type(events, StreamFailed)
    assert failed.usage == billed  # the live card shows what the failed call cost
    outcome = outcome_of(store, done.turn_id)
    # Before: the reload added up 0.0031 USD instead of the live 0.0293 (the failure
    # ended after ChatGPT's answer, the last final message, had been stored).
    assert usage_of(outcome["usage"]) == done.usage == records_usage(store, done.turn_id)
    assert outcome["usage"] == done.usage.to_dict()
    assert outcome["failures"] == [
        {"agent": "claude", "kind": "invalid", "message": message, "round": 0}
    ]
    assert outcome["status"] == "completed" and "error" not in outcome


# -- cancelled turns (audit point 14) --------------------------------------------------------


async def test_a_cancelled_duel_is_stored_as_cancelled_and_the_error_propagates() -> None:
    store = GatedStore()
    claude = Stalls("claude")
    providers: dict[AgentName, FakeProvider] = {
        "claude": claude,
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    seen: list[ServerEvent] = []
    outcomes: list[TurnOutcome] = []

    async def consume() -> None:
        request = TurnRequest("r", "Pregunta cancel·lada?", "duel", options=NO_CACHE)
        async for event in engine.run(request, price_overrides=PRICES, on_outcome=outcomes.append):
            seen.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(store.chatgpt_stored.wait(), 5)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert other_tasks() == set()
    assert claude.cancelled == 1
    assert not of_type(seen, TurnCompleted) and not of_type(seen, TurnFailed)

    turn_id = turn_id_of(seen)
    (answer,) = [m for m in store.messages if m.kind == "answer"]
    assert answer.agent == "chatgpt"
    outcome = outcome_of(store, turn_id)
    # Before: nothing said the turn had been cancelled, and its stored shape was that of
    # a duel completed with a failed agent, so a reload showed it as finished.
    assert outcome == {
        "status": "cancelled",
        "failures": [],
        "usage": outcome["usage"],
        "savings": outcome["savings"],
        "consensus": None,
        "final_message_ids": [answer.id],
        "cached": False,
    }
    assert usage_of(outcome["usage"]) == records_usage(store, turn_id)
    assert usage_of(outcome["usage"]) == usage_of(answer.meta["usage"])
    (reported,) = outcomes
    assert reported.status == "cancelled" and reported.to_wire() == outcome


async def test_the_cancelled_outcome_is_written_under_a_shield() -> None:
    store = BlockingOutcomeStore()
    providers: dict[AgentName, FakeProvider] = {
        "claude": Stalls("claude"),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    streaming = asyncio.Event()

    async def consume() -> None:
        request = TurnRequest("r-shield", "Pregunta?", "solo", options=NO_CACHE)
        async for event in engine.run(request):
            if isinstance(event, StreamDelta):
                streaming.set()

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(streaming.wait(), 5)
    consumer.cancel()
    await asyncio.wait_for(store.writing.wait(), 5)
    # The turn's own task cancelled again while the outcome is being written (Engine.run
    # cancels it only once, so this is the shield's own guarantee): the task stops at
    # once, and the write still ends.
    (turn_task,) = [task for task in other_tasks() if task.get_name() == "turn-r-shield"]
    turn_task.cancel()
    await asyncio.wait((turn_task,), timeout=5)
    assert turn_task.cancelled()
    (question,) = [m for m in store.messages if m.kind == "question"]
    assert question.meta["outcome"] is None  # still being written
    # Engine.run lets the cancellation go on only once the write has ended.
    for _ in range(5):
        await asyncio.sleep(0)
    assert not consumer.done()
    store.release.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert store.written.is_set()
    assert outcome_of(store, question.id)["status"] == "cancelled"
    for _ in range(5):
        await asyncio.sleep(0)
    assert other_tasks() == set()


async def test_a_turn_cancelled_while_its_outcome_is_written_keeps_it() -> None:
    store = BlockingOutcomeStore()
    engine = Engine(fakes(), store, retry_delay=0)

    async def consume() -> None:
        async for _event in engine.run(TurnRequest("r", "Pregunta?", "solo", options=NO_CACHE)):
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(store.writing.wait(), 5)  # the turn is complete: being written
    consumer.cancel()
    await asyncio.sleep(0)
    store.release.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    # The write was not lost (nor replaced): every message of the turn is stored.
    (question,) = [m for m in store.messages if m.kind == "question"]
    assert outcome_of(store, question.id)["status"] == "completed"
    assert other_tasks() == set()


async def test_a_turn_cancelled_again_while_it_stops_still_ends_with_its_outcome() -> None:
    store = GatedStore()
    claude = SlowToStop("claude")
    providers: dict[AgentName, FakeProvider] = {
        "claude": claude,
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    engine = Engine(providers, store, retry_delay=0)
    outcomes: list[TurnOutcome] = []

    async def consume() -> None:
        request = TurnRequest("r", "Pregunta?", "duel", options=NO_CACHE)
        async for _event in engine.run(request, price_overrides=PRICES, on_outcome=outcomes.append):
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(store.chatgpt_stored.wait(), 5)
    consumer.cancel()
    await asyncio.wait_for(claude.stopping.wait(), 5)
    # Cancelled again while Claude's call stops (the owner pressing stop twice, or a
    # shutdown while the turn stops). Before, the consumer ended at once, before the
    # outcome was decided or stored, and whatever announced the cancellation said the
    # turn had spent nothing (the turn went on ending on its own, unwatched).
    consumer.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not consumer.done() and outcomes == []
    claude.stopped.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    (reported,) = outcomes
    assert reported.status == "cancelled"
    (question,) = [m for m in store.messages if m.kind == "question"]
    assert outcome_of(store, question.id) == reported.to_wire()
    assert reported.usage == records_usage(store, question.id) != Usage()
    assert other_tasks() == set()


@pytest.mark.parametrize("write_first", [True, False])
async def test_a_turn_cancelled_while_it_records_its_savings_keeps_them(
    write_first: bool,
) -> None:
    store = SlowSavings(write_first=write_first)
    providers = {agent: FakeProvider(agent, chunk_delay=0, agreements=(95,)) for agent in AGENTS}
    debate = DebateOptions(rounds=3, consensus_threshold=90)
    options = TurnOptions(debate=debate, use_cache=False)
    request = TurnRequest("r", "Pregunta?", "debate", options=options)
    outcomes: list[TurnOutcome] = []

    async def consume() -> None:
        engine = Engine(providers, store, retry_delay=0)
        async for _event in engine.run(request, price_overrides=PRICES, on_outcome=outcomes.append):
            pass

    consumer = asyncio.create_task(consume())
    # Consensus in the first round: two rounds skipped, and every message is stored.
    await asyncio.wait_for(store.recording.wait(), 5)
    consumer.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    store.release.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    (reported,) = outcomes
    assert reported.status == "cancelled"
    # Before: the saving rows (what the dashboard counts) were partly written, or not at
    # all, while the stored outcome said the turn had saved nothing.
    rows = {row.kind: row for row in store.savings}
    assert sorted(rows) == ["early_stop", "unchanged"] and len(store.savings) == 2
    (question,) = [m for m in store.messages if m.kind == "question"]
    outcome = outcome_of(store, question.id)
    assert outcome == reported.to_wire()
    savings = outcome["savings"]
    assert isinstance(savings, dict)
    assert savings["early_stop"] == rows["early_stop"].tokens_saved > 0
    assert savings["unchanged"] == rows["unchanged"].tokens_saved > 0
    assert savings["total"] == sum(row.tokens_saved for row in store.savings)
    assert savings["cost_usd"] == pytest.approx(sum(row.cost_usd or 0 for row in store.savings))
    (synthesis,) = [m for m in store.messages if m.kind == "synthesis"]
    assert synthesis.meta["savings"] == savings
    assert other_tasks() == set()


# -- failed turns (new issue N10) --------------------------------------------------------------


async def test_a_failed_solo_turn_reports_and_stores_its_cost() -> None:
    store = InMemoryStore()
    providers: dict[AgentName, FakeProvider] = {
        "claude": RefusesAfterText("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    request = TurnRequest("r", "Pregunta?", "solo", options=NO_CACHE)
    events = await collect(
        Engine(providers, store, retry_delay=0).run(request, price_overrides=PRICES)
    )
    failed = events[-1]
    assert isinstance(failed, TurnFailed)
    billed = priced("fake-claude", DECLINED)
    assert billed.cost_usd == pytest.approx(0.0262)
    # Before: neither the live event nor anything stored carried the refusal's cost.
    assert failed.usage == billed
    assert failed.to_wire()["usage"] == billed.to_dict()
    (stream_failed,) = of_type(events, StreamFailed)
    assert stream_failed.usage == billed
    assert stream_failed.to_wire()["usage"] == billed.to_dict()
    turn_id = turn_id_of(events)
    assert outcome_of(store, turn_id) == {
        "status": "failed",
        "error": {"kind": "invalid", "message": "Claude no ha pogut respondre."},
        "failures": [
            {"agent": "claude", "kind": "invalid", "message": "Claude ha declinat.", "round": 0}
        ],
        "usage": billed.to_dict(),
        "savings": outcome_of(store, turn_id)["savings"],
        "consensus": None,
        "final_message_ids": [],
        "cached": False,
    }
    assert records_usage(store, turn_id) == billed


async def test_a_duel_where_both_agents_fail_stores_both_failures() -> None:
    store = InMemoryStore()
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, fail={"answer"})
        for agent in ("claude", "chatgpt")
    }
    events = await collect(
        Engine(providers, store, retry_delay=0).run(TurnRequest("r", "Q?", "duel"))
    )
    failed = events[-1]
    assert isinstance(failed, TurnFailed) and failed.usage == Usage()
    assert failed.error == ErrorInfo("unavailable", "Cap dels dos agents ha pogut respondre.")
    # Nothing billed: stream.failed carries no usage.
    assert all(
        e.usage is None and "usage" not in e.to_wire() for e in of_type(events, StreamFailed)
    )
    outcome = outcome_of(store, turn_id_of(events))
    assert outcome["status"] == "failed"
    assert outcome["error"] == {"kind": "unavailable", "message": failed.error.message}
    failures = outcome["failures"]
    assert isinstance(failures, list)
    assert sorted(str(f["agent"]) for f in failures if isinstance(f, dict)) == [
        "chatgpt",
        "claude",
    ]
    assert outcome["usage"] == Usage().to_dict()


async def test_a_failed_turn_stores_its_error_in_the_language_it_started_in() -> None:
    store = InMemoryStore()
    providers: dict[AgentName, FakeProvider] = {
        agent: FakeProvider(agent, chunk_delay=0, fail={"answer"}) for agent in AGENTS
    }
    with i18n.use("en"):
        events = await collect(
            Engine(providers, store, retry_delay=0).run(TurnRequest("r", "Q?", "duel"))
        )
    failed = events[-1]
    assert isinstance(failed, TurnFailed)
    assert failed.error == ErrorInfo("unavailable", "Neither agent could answer.")
    outcome = outcome_of(store, turn_id_of(events))
    assert outcome["error"] == {"kind": "unavailable", "message": "Neither agent could answer."}


async def test_a_request_that_fails_before_its_question_reports_no_usage() -> None:
    store = InMemoryStore()
    events = await collect(
        Engine(fakes(), store, retry_delay=0).run(TurnRequest("r", "   ", "solo"))
    )
    (failed,) = events
    assert isinstance(failed, TurnFailed) and failed.usage == Usage()
    assert failed.to_wire() == {
        "type": "turn.failed",
        "request_id": "r",
        "error": {"kind": "invalid", "message": "La pregunta és buida."},
        "usage": Usage().to_dict(),
    }
    assert store.messages == []


async def test_a_crash_after_the_question_stores_a_failed_outcome() -> None:
    store = InMemoryStore()

    async def broken(message: NewMessage) -> int:
        if message.kind != "question":
            raise OSError("disk")
        return await InMemoryStore.add_message(store, message)

    store.add_message = broken  # type: ignore[method-assign]
    events = await collect(
        Engine(fakes(), store, retry_delay=0).run(
            TurnRequest("r", "Q?", "solo", options=NO_CACHE), price_overrides=PRICES
        )
    )
    failed = events[-1]
    assert isinstance(failed, TurnFailed) and failed.error.kind == "internal"
    turn_id = turn_id_of(events)
    outcome = outcome_of(store, turn_id)
    assert outcome["status"] == "failed" and outcome["error"] == failed.error.to_wire()
    # The answer was billed although it could not be stored.
    assert failed.usage == records_usage(store, turn_id) != Usage()
    assert outcome["usage"] == failed.usage.to_dict()


async def test_a_crashed_turn_cancelled_while_its_outcome_is_written_waits_for_it() -> None:
    store = BlockingOutcomeStore()

    async def broken(message: NewMessage) -> int:
        if message.kind != "question":
            raise OSError("disk")
        return await InMemoryStore.add_message(store, message)

    store.add_message = broken  # type: ignore[method-assign]
    events: list[ServerEvent] = []

    async def consume() -> None:
        request = TurnRequest("r", "Q?", "solo", options=NO_CACHE)
        async for event in Engine(fakes(), store, retry_delay=0).run(request):
            events.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(store.writing.wait(), 5)  # the crash's outcome is being written
    consumer.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    # Before: the cancellation went on at once, with the outcome still being written
    # (at shutdown, to a store about to be closed).
    assert not consumer.done()
    store.release.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert store.written.is_set()
    assert outcome_of(store, turn_id_of(events))["status"] == "failed"
    assert other_tasks() == set()


@pytest.mark.parametrize("mode", ["solo", "duel"])
async def test_a_cancelled_error_a_call_raises_by_itself_is_an_internal_failure(
    mode: TurnMode,
) -> None:
    store = InMemoryStore()
    providers: dict[AgentName, FakeProvider] = {
        "claude": RaisesCancelled("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    request = TurnRequest("r", "Pregunta?", mode, options=NO_CACHE)
    events = await collect(
        Engine(providers, store, retry_delay=0).run(request, price_overrides=PRICES)
    )
    failed = events[-1]
    assert isinstance(failed, TurnFailed) and failed.error.kind == "internal"
    outcome = outcome_of(store, turn_id_of(events))
    # Before: nobody had cancelled the turn, yet it was stored as cancelled (a reload
    # said the owner had stopped it) while the live turn ended with an internal failure.
    assert outcome["status"] == "failed" and outcome["error"] == failed.error.to_wire()
    assert outcome["usage"] == failed.usage.to_dict()
    assert usage_of(outcome["usage"]) == records_usage(store, turn_id_of(events))


async def test_a_store_that_cannot_write_the_outcome_does_not_fail_the_turn() -> None:
    store = InMemoryStore()

    async def broken(question_message_id: int, outcome: TurnOutcome) -> None:
        raise OSError("disk")

    store.set_turn_outcome = broken  # type: ignore[method-assign]
    events = await collect(
        Engine(fakes(), store, retry_delay=0).run(TurnRequest("r", "Q?", "solo"))
    )
    assert isinstance(events[-1], TurnCompleted)
    # The question keeps its open outcome: a reload shows the turn as not finished.
    assert question_of(store, turn_id_of(events)).meta["outcome"] is None
