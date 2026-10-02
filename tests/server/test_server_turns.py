"""TurnManager (buffering, replay, limits, cancellation) and ProviderMonitor."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from agentic_os.domain import AgentName, ProviderMode, TurnOptions, Usage
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.events import (
    ErrorInfo,
    PhaseChanged,
    ServerEvent,
    TurnCompleted,
    TurnFailed,
    TurnOutcome,
    TurnStarted,
)
from agentic_os.orchestrator.events import Savings as TurnSavings
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.pricing import ModelPrice
from agentic_os.providers.base import (
    GenerationRequest,
    ModelInfo,
    ProviderEvent,
    ProviderStatus,
    RefusalError,
    TextDelta,
    UsageLimit,
)
from agentic_os.providers.fake import FakeProvider
from agentic_os.server.status import ProviderMonitor, status_to_wire
from agentic_os.server.tasks import cancel_and_wait
from agentic_os.server.turns import TurnManager, TurnRejectedError
from agentic_os.server.ws import ClientConnection


class Recorder:
    """Subscriber that decodes what it receives."""

    def __init__(self, *, accept: int | None = None) -> None:
        self.messages: list[dict[str, Any]] = []
        self.batches: list[int] = []
        """Size of each batch received."""
        self._accept = accept

    def send(self, text: str) -> bool:
        if self._accept is not None and len(self.messages) >= self._accept:
            return False
        self.messages.append(json.loads(text))
        return True

    def send_batch(self, texts: Sequence[str]) -> bool:
        self.batches.append(len(texts))
        return all(self.send(text) for text in texts)

    @property
    def types(self) -> list[str]:
        return [m["type"] for m in self.messages]


class ScriptedRunner:
    """Emits started + phase, waits on a gate, then completes (or crashes). When
    cancelled it reports ``spent`` as its outcome, as the engine does."""

    def __init__(
        self, *, crash: bool = False, silent_end: bool = False, spent: Usage | None = None
    ) -> None:
        self.gate = asyncio.Event()
        self.crash = crash
        self.silent_end = silent_end
        self.spent = spent
        self.cancelled: list[str] = []
        self.thresholds: list[int | None] = []
        self.prices: list[Mapping[str, ModelPrice] | None] = []

    async def run(
        self,
        request: TurnRequest,
        *,
        compaction_threshold_tokens: int | None = None,
        price_overrides: Mapping[str, ModelPrice] | None = None,
        on_outcome: Callable[[TurnOutcome], None] | None = None,
        stop: asyncio.Event | None = None,
    ) -> AsyncIterator[ServerEvent]:
        self.thresholds.append(compaction_threshold_tokens)
        self.prices.append(price_overrides)
        rid = request.request_id
        yield TurnStarted(rid, 40 + len(self.thresholds), 7, request.mode, True)
        yield PhaseChanged(rid, "answer", 0)
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled.append(rid)
            if on_outcome is not None and self.spent is not None:
                on_outcome(TurnOutcome("cancelled", self.spent, TurnSavings()))
            raise
        if self.crash:
            raise RuntimeError("boom")
        if self.silent_end:
            return
        yield TurnCompleted(rid, 41, 7, (8,), Usage(1, 2), TurnSavings())


def request(request_id: str, conversation_id: int | None = None) -> TurnRequest:
    return TurnRequest(
        request_id=request_id, text="Hola?", mode="solo", conversation_id=conversation_id
    )


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_events_get_seq_and_replay_after_seq() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    live = Recorder()
    prices = {"my-model": ModelPrice(1, 2, 0.1, 1.25)}
    turns.start(request("r1"), live, compaction_threshold_tokens=1234, price_overrides=prices)
    await settle()
    assert live.types == ["turn.started", "phase"]
    assert [m["seq"] for m in live.messages] == [1, 2]
    assert runner.thresholds == [1234]
    assert runner.prices == [prices]
    assert turns.active_turns() == [{"request_id": "r1", "conversation_id": 41, "last_seq": 2}]

    late = Recorder()
    assert turns.subscribe("r1", late, after_seq=1)
    assert [m["seq"] for m in late.messages] == [2]
    runner.gate.set()
    await settle()
    assert live.types[-1] == late.types[-1] == "turn.completed"
    assert live.messages[-1] == late.messages[-1]
    assert late.messages[-1]["seq"] == 3
    assert not turns.is_running("r1")
    assert turns.active_turns() == []

    replay = Recorder()
    assert turns.subscribe("r1", replay, after_seq=0)
    assert replay.messages == live.messages
    await turns.aclose()


async def test_buffers_expire_after_the_retention() -> None:
    runner = ScriptedRunner()
    runner.gate.set()
    turns = TurnManager(runner, retention_seconds=0.05)
    turns.start(request("r1"))
    await settle()
    assert turns.subscribe("r1", Recorder())
    await asyncio.sleep(0.1)
    assert not turns.subscribe("r1", Recorder())
    assert not turns.cancel("r1")
    await turns.aclose()


async def test_cancel_emits_turn_cancelled_last() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    live = Recorder()
    turns.start(request("r1"), live)
    await settle()
    assert turns.cancel("r1")
    await settle()
    assert runner.cancelled == ["r1"]
    assert live.types == ["turn.started", "phase", "turn.cancelled"]
    assert live.messages[-1] == {
        "type": "turn.cancelled",
        "request_id": "r1",
        "usage": Usage().to_dict(),
        "seq": 3,
    }
    assert turns.cancel("r1")  # known, already over: nothing happens
    await settle()
    assert len(live.messages) == 3
    assert not turns.cancel("unknown")
    await turns.aclose()


async def test_turn_cancelled_carries_what_the_engine_reported_as_spent() -> None:
    spent = Usage(input_tokens=1200, output_tokens=300, cost_usd=0.042)
    turns = TurnManager(ScriptedRunner(spent=spent))
    live = Recorder()
    turns.start(request("r1"), live)
    await settle()
    assert turns.cancel("r1")
    await settle()
    # Before: turn.cancelled carried nothing, so a cancelled turn showed no cost.
    assert live.messages[-1] == {
        "type": "turn.cancelled",
        "request_id": "r1",
        "usage": spent.to_dict(),
        "seq": 3,
    }
    await turns.aclose()


async def test_cancel_before_the_task_runs() -> None:
    turns = TurnManager(ScriptedRunner())
    live = Recorder()
    turns.start(request("r1"), live)
    turns.cancel("r1")
    await settle()
    assert live.types == ["turn.cancelled"]
    await turns.aclose()


async def test_crash_and_missing_terminal_event_become_turn_failed() -> None:
    for runner in (ScriptedRunner(crash=True), ScriptedRunner(silent_end=True)):
        turns = TurnManager(runner)
        live = Recorder()
        turns.start(request("r1"), live)
        runner.gate.set()
        await settle()
        assert live.types == ["turn.started", "phase", "turn.failed"]
        assert live.messages[-1]["error"] == {
            "kind": "internal",
            "message": "S'ha produït un error intern i el torn s'ha aturat.",
        }
        assert live.messages[-1]["usage"] == Usage().to_dict()
        await turns.aclose()


async def test_engine_turn_failed_is_terminal() -> None:
    class FailingRunner:
        async def run(
            self,
            request: TurnRequest,
            *,
            compaction_threshold_tokens: int | None = None,
            price_overrides: Mapping[str, ModelPrice] | None = None,
            on_outcome: Callable[[TurnOutcome], None] | None = None,
            stop: asyncio.Event | None = None,
        ) -> AsyncIterator[ServerEvent]:
            yield TurnFailed(request.request_id, ErrorInfo("invalid", "La pregunta és buida."))

    turns = TurnManager(FailingRunner())
    live = Recorder()
    turns.start(request("r1"), live)
    await settle()
    assert live.types == ["turn.failed"]
    assert live.messages[0]["error"]["message"] == "La pregunta és buida."
    await turns.aclose()


class Stalls(FakeProvider):
    """Streams its first words, then thinks until it is cancelled."""

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        yield TextDelta("Encara escric ")
        await asyncio.sleep(3600)
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


class SlowStopRunner:
    """Stops like the engine: when cancelled it stops its calls (until :attr:`stopped`
    is set), then reports what the turn spent and lets the cancellation go on."""

    def __init__(self, spent: Usage) -> None:
        self.spent = spent
        self.stopping = asyncio.Event()
        self.stopped = asyncio.Event()
        self.interrupted = 0
        """Cancellations that reached it while it was stopping."""

    async def run(
        self,
        request: TurnRequest,
        *,
        compaction_threshold_tokens: int | None = None,
        price_overrides: Mapping[str, ModelPrice] | None = None,
        on_outcome: Callable[[TurnOutcome], None] | None = None,
        stop: asyncio.Event | None = None,
    ) -> AsyncIterator[ServerEvent]:
        yield TurnStarted(request.request_id, 41, 7, request.mode, True)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stopping.set()
            try:
                await self.stopped.wait()
            except asyncio.CancelledError:
                self.interrupted += 1
                raise
            if on_outcome is not None:
                on_outcome(TurnOutcome("cancelled", self.spent, TurnSavings()))
            raise


class OutcomeLogStore(InMemoryStore):
    """Adds each outcome it has written to a shared log."""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    async def set_turn_outcome(self, question_message_id: int, outcome: TurnOutcome) -> None:
        await super().set_turn_outcome(question_message_id, outcome)
        self._log.append(f"outcome {outcome.status}")


class TerminalLogRecorder(Recorder):
    """Adds each terminal event it receives to a shared log."""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    def send(self, text: str) -> bool:
        accepted = super().send(text)
        kind = self.messages[-1]["type"]
        if kind in ("turn.completed", "turn.failed", "turn.cancelled"):
            self._log.append(kind)
        return accepted


class Declines(FakeProvider):
    """Declines every call after some text, billed as the Claude API does."""

    def __init__(self, agent: AgentName, billed: Usage) -> None:
        super().__init__(agent, chunk_delay=0)
        self._billed = billed

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        yield TextDelta("Comencem ")
        raise RefusalError("Claude ha declinat.", usage=self._billed, model="fake-claude")


async def until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.001)


async def test_a_cancelled_engine_turn_reports_and_stores_what_it_spent() -> None:
    store = InMemoryStore()
    engine = Engine(
        {
            "claude": Stalls("claude", chunk_delay=0),
            "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
        },
        store,
        retry_delay=0,
    )
    turns = TurnManager(engine)
    live = Recorder()
    duel = TurnRequest("r1", "Pregunta?", "duel", options=TurnOptions(use_cache=False))
    prices = {"fake-chatgpt": ModelPrice(4.0, 20.0, 0.2, 5.0)}
    turns.start(duel, live, price_overrides=prices)
    await until(lambda: "stream.completed" in live.types)  # ChatGPT answered and was billed
    assert turns.cancel("r1")
    await until(lambda: "turn.cancelled" in live.types)
    answered = next(m for m in live.messages if m["type"] == "stream.completed")
    cancelled = live.messages[-1]
    assert cancelled["type"] == "turn.cancelled" and cancelled["usage"] == answered["usage"]
    assert cancelled["usage"]["cost_usd"]
    question = next(m for m in store.messages if m.kind == "question")
    outcome = question.meta["outcome"]
    assert isinstance(outcome, dict)
    assert outcome["status"] == "cancelled" and outcome["usage"] == cancelled["usage"]
    await turns.aclose()


async def test_an_engine_turn_that_fails_reports_what_it_spent() -> None:
    store = InMemoryStore()
    billed = Usage(input_tokens=5000, output_tokens=300)
    engine = Engine(
        {"claude": Declines("claude", billed), "chatgpt": FakeProvider("chatgpt", chunk_delay=0)},
        store,
        retry_delay=0,
    )
    turns = TurnManager(engine)
    live = Recorder()
    prices = {"fake-claude": ModelPrice(4.0, 20.0, 0.2, 5.0)}
    turns.start(request("r1"), live, price_overrides=prices)
    await until(lambda: "turn.failed" in live.types)
    failed = live.messages[-1]
    expected = Usage(input_tokens=5000, output_tokens=300, cost_usd=0.026).to_dict()
    assert failed["usage"] == pytest.approx(expected)
    stream_failed = next(m for m in live.messages if m["type"] == "stream.failed")
    assert stream_failed["usage"] == failed["usage"]
    await turns.aclose()


@pytest.mark.parametrize("again", ["turn.cancel", "shutdown"])
async def test_a_turn_that_is_stopping_is_not_cancelled_again(again: str) -> None:
    spent = Usage(input_tokens=1200, output_tokens=300, cost_usd=0.042)
    runner = SlowStopRunner(spent)
    turns = TurnManager(runner)
    live = Recorder()
    turns.start(request("r1"), live)
    await settle()
    assert turns.cancel("r1")
    await asyncio.wait_for(runner.stopping.wait(), 5)
    closing: asyncio.Task[None] | None = None
    if again == "shutdown":
        closing = asyncio.create_task(turns.aclose())
    else:
        assert turns.cancel("r1")  # the owner presses stop again
    await settle()
    # Before: the second cancellation interrupted the turn while it stopped, and
    # turn.cancelled went out at once, without what the turn had spent.
    assert live.types == ["turn.started"]
    runner.stopped.set()
    await until(lambda: "turn.cancelled" in live.types)
    assert runner.interrupted == 0
    assert live.messages[-1]["usage"] == spent.to_dict()
    if closing is not None:
        await asyncio.wait_for(closing, 5)
    else:
        await turns.aclose()


@pytest.mark.parametrize("again", ["turn.cancel", "shutdown"])
async def test_stopping_an_engine_turn_again_still_announces_its_stored_outcome(
    again: str,
) -> None:
    log: list[str] = []
    store = OutcomeLogStore(log)
    claude = SlowToStop("claude")
    engine = Engine(
        {"claude": claude, "chatgpt": FakeProvider("chatgpt", chunk_delay=0)},
        store,
        retry_delay=0,
    )
    turns = TurnManager(engine)
    live = TerminalLogRecorder(log)
    duel = TurnRequest("r1", "Pregunta?", "duel", options=TurnOptions(use_cache=False))
    prices = {"fake-chatgpt": ModelPrice(4.0, 20.0, 0.2, 5.0)}
    turns.start(duel, live, price_overrides=prices)
    await until(lambda: "stream.completed" in live.types)  # ChatGPT answered and was billed
    assert turns.cancel("r1")
    await asyncio.wait_for(claude.stopping.wait(), 5)  # Claude's call is stopping
    closing: asyncio.Task[None] | None = None
    if again == "shutdown":
        closing = asyncio.create_task(turns.aclose())
    else:
        assert turns.cancel("r1")  # the owner presses stop again
    await settle()
    # Before: turn.cancelled went out at once, with no usage, and the outcome was written
    # after it (at shutdown, maybe to a store about to be closed).
    assert log == []
    claude.stopped.set()
    if closing is not None:
        await asyncio.wait_for(closing, 5)
    await until(lambda: "turn.cancelled" in live.types)
    assert log == ["outcome cancelled", "turn.cancelled"]
    answered = next(m for m in live.messages if m["type"] == "stream.completed")
    cancelled = live.messages[-1]
    assert cancelled["usage"] == answered["usage"] and cancelled["usage"]["cost_usd"]
    question = next(m for m in store.messages if m.kind == "question")
    outcome = question.meta["outcome"]
    assert isinstance(outcome, dict) and outcome["usage"] == cancelled["usage"]
    if closing is None:
        await turns.aclose()


async def test_limits() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner, max_concurrent=3)
    turns.start(request("a", conversation_id=5))
    with pytest.raises(TurnRejectedError) as exc:
        turns.start(request("b", conversation_id=5))
    assert exc.value.code == "busy"
    with pytest.raises(TurnRejectedError) as exc:
        turns.start(request("a"))
    assert exc.value.code == "duplicate"
    turns.start(request("c"))
    turns.start(request("d"))
    with pytest.raises(TurnRejectedError) as exc:
        turns.start(request("e"))
    assert exc.value.code == "busy"

    runner.gate.set()
    await settle()
    turns.start(request("e", conversation_id=5))  # slots and conversation free again
    await turns.aclose()
    with pytest.raises(TurnRejectedError) as exc:
        turns.start(request("f"))
    assert exc.value.code == "unavailable"


async def test_new_conversation_id_is_tracked_for_the_busy_check() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    turns.start(request("a"))
    await settle()  # TurnStarted created conversation 41
    with pytest.raises(TurnRejectedError):
        turns.start(request("b", conversation_id=41))
    assert turns.cancel_conversation(41) == 1
    await settle()
    turns.start(request("b", conversation_id=41))
    await turns.aclose()


async def test_gone_subscribers_are_dropped_and_turns_keep_running() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    slow = Recorder(accept=1)
    detached = Recorder()
    turns.start(request("r1"), slow)
    assert turns.subscribe("r1", detached)
    turns.detach(detached)
    await settle()
    runner.gate.set()
    await settle()
    assert slow.types == ["turn.started"]
    assert detached.messages == []
    replay = Recorder()
    turns.subscribe("r1", replay)
    assert replay.types == ["turn.started", "phase", "turn.completed"]
    await turns.aclose()


async def test_aclose_cancels_running_turns() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    turns.start(request("a"))
    turns.start(request("b"))
    await settle()
    await turns.aclose()
    assert sorted(runner.cancelled) == ["a", "b"]
    assert turns.active_turns() == []


# -- deleted conversations (audit point 6) ---------------------------------------------


async def test_forgetting_a_conversation_drops_its_finished_turns_at_once() -> None:
    runner = ScriptedRunner()
    runner.gate.set()
    turns = TurnManager(runner)
    turns.start(request("r1"))  # conversation 41
    await settle()
    turns.start(request("r2"))  # conversation 42
    await settle()
    assert not turns.is_running("r1") and not turns.is_running("r2")

    assert turns.forget_conversation(41) == 1
    assert not turns.subscribe("r1", Recorder())
    assert not turns.cancel("r1")
    replay = Recorder()
    assert turns.subscribe("r2", replay)  # another conversation keeps its turns
    assert replay.types == ["turn.started", "phase", "turn.completed"]
    assert turns.forget_conversation(41) == 0
    await turns.aclose()


async def test_forgetting_a_conversation_drops_its_running_turns_when_they_end() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    live = Recorder()
    turns.start(request("r1"), live)  # conversation 41
    await settle()

    assert turns.forget_conversation(41) == 1
    assert not turns.subscribe("r1", Recorder())  # unknown at once
    assert turns.active_turns() == []  # not announced to new connections either
    assert turns.is_running("r1")  # it still holds its slot until it ends
    with pytest.raises(TurnRejectedError):
        turns.start(request("again", conversation_id=41))

    runner.gate.set()
    await settle()
    assert live.types == ["turn.started", "phase", "turn.completed"]  # its tab saw the end
    assert not turns.is_running("r1")
    assert not turns.subscribe("r1", Recorder())
    assert not turns.cancel("r1")  # gone at once, without the retention
    await turns.aclose()


async def test_a_turn_cancelled_before_the_conversation_is_deleted_is_forgotten() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    live = Recorder()
    turns.start(request("r1"), live)
    await settle()
    # What DELETE /api/conversations/{id} does: cancel, delete, then forget.
    assert turns.cancel_conversation(41) == 1
    assert turns.forget_conversation(41) == 1
    await settle()
    assert live.types == ["turn.started", "phase", "turn.cancelled"]
    assert not turns.subscribe("r1", Recorder())
    assert turns.active_turns() == []
    await turns.aclose()


# -- provider status -------------------------------------------------------------------


class SlowProvider:
    def __init__(self, agent: AgentName, delay: float, *, fail: bool = False) -> None:
        self._agent: AgentName = agent
        self.delay = delay
        self.fail = fail
        self.calls = 0

    @property
    def agent(self) -> AgentName:
        return self._agent

    @property
    def mode(self) -> ProviderMode:
        return "cli"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def prewarm(self, request: GenerationRequest) -> None:
        return None

    async def status(self) -> ProviderStatus:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("no")
        return ProviderStatus(self._agent, "cli", True, "model-x", "Subscripció activa")

    @property
    def fast_model(self) -> str:
        return "fast"

    @property
    def models_live(self) -> bool:
        return True

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        model = "slow"
        return (ModelInfo(id=model, label=model, is_default=True),)

    async def aclose(self) -> None:
        return None


async def test_monitor_answers_with_a_placeholder_while_a_check_is_slow() -> None:
    slow = SlowProvider("chatgpt", 0.1)
    monitor = ProviderMonitor(
        {"claude": FakeProvider("claude"), "chatgpt": slow}, wait_seconds=0.01
    )
    first = await monitor.statuses()
    assert [s.agent for s in first] == ["claude", "chatgpt"]
    assert first[0].available
    assert first[1] == ProviderStatus("chatgpt", "cli", False, "", "S'està comprovant l'estat…")
    second = await monitor.statuses(wait_seconds=1.0)  # joins the check still running
    assert second[1].detail == "Subscripció activa"
    assert slow.calls == 1
    await monitor.aclose()


async def test_monitor_reports_failures_and_timeouts() -> None:
    failing = SlowProvider("claude", 0, fail=True)
    hanging = SlowProvider("chatgpt", 10)
    monitor = ProviderMonitor(
        {"claude": failing, "chatgpt": hanging}, wait_seconds=1.0, hard_timeout_seconds=0.05
    )
    claude, chatgpt = await monitor.statuses()
    assert (claude.available, claude.detail) == (
        False,
        "No s'ha pogut consultar l'estat del proveïdor.",
    )
    assert (chatgpt.available, chatgpt.detail) == (False, "El proveïdor no respon.")
    await monitor.aclose()


def test_status_to_wire() -> None:
    status = ProviderStatus(
        "claude",
        "cli",
        True,
        "opus",
        "Subscripció Max activa",
        limits=(
            UsageLimit("5h", 42.5, datetime(2026, 9, 27, 17, 0, tzinfo=UTC), "warning"),
            UsageLimit("7d", None, None),
        ),
    )
    assert status_to_wire(status) == {
        "agent": "claude",
        "mode": "cli",
        "available": True,
        "model": "opus",
        "detail": "Subscripció Max activa",
        "limits": [
            {
                "window": "5h",
                "used_percent": 42.5,
                "resets_at": "2026-09-27T17:00:00.000Z",
                "status": "warning",
            },
            {"window": "7d", "used_percent": None, "resets_at": None, "status": "allowed"},
        ],
    }


# -- WebSocket send queue --------------------------------------------------------------


class StuckWebSocket:
    """Accepts one frame, then blocks forever (a client that stopped reading)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)
        if len(self.sent) > 1:
            await asyncio.Event().wait()


class RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


async def test_a_replay_takes_one_place_in_the_send_queue() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    turns.start(request("r1"))
    await settle()  # seq 1 and 2 buffered, nobody listening
    websocket = RecordingWebSocket()
    connection = ClientConnection(websocket, queue_size=1)  # type: ignore[arg-type]
    # The whole backlog is queued at once, before the writer can run.
    assert turns.subscribe("r1", connection, after_seq=0)
    assert not connection.overflowed.is_set()
    writer = asyncio.create_task(connection.write_loop())
    await settle()
    runner.gate.set()  # the rest arrives live
    await settle()
    assert not connection.overflowed.is_set()
    assert [json.loads(t)["seq"] for t in websocket.sent] == [1, 2, 3]
    await cancel_and_wait([writer])
    await turns.aclose()


async def test_a_client_that_cannot_keep_up_is_dropped() -> None:
    websocket = StuckWebSocket()
    connection = ClientConnection(websocket, queue_size=3)  # type: ignore[arg-type]
    writer = asyncio.create_task(connection.write_loop())
    assert all(connection.send(f"m{i}") for i in range(3))
    await settle()  # m0 written, m1 stuck in send_text, m2 queued
    assert connection.send("m3")
    assert connection.send("m4")
    assert not connection.overflowed.is_set()
    assert not connection.send("m5")  # queue full: the connection is given up
    assert connection.overflowed.is_set()
    assert connection.closed
    assert not connection.send("m6")
    await cancel_and_wait([writer])
    assert websocket.sent == ["m0", "m1"]


async def test_a_repeated_subscribe_is_ignored() -> None:
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    starter, late = Recorder(), Recorder()
    turns.start(request("r1"), starter)
    await settle()
    assert turns.subscribe("r1", late, after_seq=0)
    for subscriber in (starter, late, late):
        assert turns.subscribe("r1", subscriber, after_seq=0)  # known turn, nothing sent
    assert starter.types == late.types == ["turn.started", "phase"]
    assert late.batches == [2]
    runner.gate.set()
    await settle()
    assert starter.types == late.types == ["turn.started", "phase", "turn.completed"]
    # Also once the turn is over.
    assert turns.subscribe("r1", late, after_seq=0)
    assert len(late.messages) == 3
    # A subscriber that went away (its connection closed) is a new one afterwards.
    turns.detach(late)
    assert turns.subscribe("r1", late, after_seq=0)
    assert [m["seq"] for m in late.messages] == [1, 2, 3, 1, 2, 3]
    await turns.aclose()


async def test_a_client_that_never_reads_cannot_pile_up_replays() -> None:
    """Many ``turn.subscribe`` for one turn from a socket that does not read: the
    events are queued once, not once per message."""
    runner = ScriptedRunner()
    turns = TurnManager(runner)
    turns.start(request("r1"))
    await settle()
    websocket = StuckWebSocket()
    connection = ClientConnection(websocket)  # type: ignore[arg-type]
    writer = asyncio.create_task(connection.write_loop())
    await settle()
    for _ in range(5000):  # more than the send queue has places
        assert turns.subscribe("r1", connection, after_seq=0)
    assert not connection.overflowed.is_set()
    assert connection.pending == 2  # one replay of the two buffered events
    await settle()
    assert len(websocket.sent) == 2  # seq 1 went out; seq 2 is stuck in send_text
    assert connection.pending == 1
    await cancel_and_wait([writer])
    await turns.aclose()


async def test_queued_events_are_bounded_per_connection() -> None:
    websocket = StuckWebSocket()
    connection = ClientConnection(websocket, max_pending=10)  # type: ignore[arg-type]
    writer = asyncio.create_task(connection.write_loop())
    assert connection.send("m0")
    await settle()  # m0 written; the socket then stops taking frames
    assert connection.pending == 0
    assert connection.send_batch([f"a{i}" for i in range(6)])
    # Still under the bound when it arrives: a replay may take it over the limit, so
    # a turn longer than the bound can still be recovered.
    assert connection.send_batch([f"b{i}" for i in range(8)])
    assert connection.pending == 14
    assert not connection.overflowed.is_set()
    assert not connection.send("m1")  # too many events waiting: dropped (1013)
    assert connection.overflowed.is_set()
    assert connection.closed
    await cancel_and_wait([writer])


async def test_written_events_leave_the_bound() -> None:
    websocket = RecordingWebSocket()
    connection = ClientConnection(websocket, max_pending=3)  # type: ignore[arg-type]
    writer = asyncio.create_task(connection.write_loop())
    for round_ in range(3):
        assert connection.send_batch([f"{round_}-{i}" for i in range(5)])
        await settle()
        assert connection.pending == 0
    assert not connection.overflowed.is_set()
    assert len(websocket.sent) == 15
    await cancel_and_wait([writer])
