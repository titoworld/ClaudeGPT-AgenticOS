"""TurnManager (buffering, replay, limits, cancellation) and ProviderMonitor."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from agentic_os.domain import AgentName, ProviderMode, Usage
from agentic_os.orchestrator.events import (
    ErrorInfo,
    PhaseChanged,
    ServerEvent,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from agentic_os.orchestrator.events import Savings as TurnSavings
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.pricing import ModelPrice
from agentic_os.providers.base import (
    GenerationRequest,
    ModelInfo,
    ProviderEvent,
    ProviderStatus,
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
        self._accept = accept

    def send(self, text: str) -> bool:
        if self._accept is not None and len(self.messages) >= self._accept:
            return False
        self.messages.append(json.loads(text))
        return True

    @property
    def types(self) -> list[str]:
        return [m["type"] for m in self.messages]


class ScriptedRunner:
    """Emits started + phase, waits on a gate, then completes (or crashes)."""

    def __init__(self, *, crash: bool = False, silent_end: bool = False) -> None:
        self.gate = asyncio.Event()
        self.crash = crash
        self.silent_end = silent_end
        self.cancelled: list[str] = []
        self.thresholds: list[int | None] = []
        self.prices: list[Mapping[str, ModelPrice] | None] = []

    async def run(
        self,
        request: TurnRequest,
        *,
        compaction_threshold_tokens: int | None = None,
        price_overrides: Mapping[str, ModelPrice] | None = None,
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
    assert live.messages[-1] == {"type": "turn.cancelled", "request_id": "r1", "seq": 3}
    assert turns.cancel("r1")  # known, already over: nothing happens
    await settle()
    assert len(live.messages) == 3
    assert not turns.cancel("unknown")
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
        await turns.aclose()


async def test_engine_turn_failed_is_terminal() -> None:
    class FailingRunner:
        async def run(
            self,
            request: TurnRequest,
            *,
            compaction_threshold_tokens: int | None = None,
            price_overrides: Mapping[str, ModelPrice] | None = None,
        ) -> AsyncIterator[ServerEvent]:
            yield TurnFailed(request.request_id, ErrorInfo("invalid", "La pregunta és buida."))

    turns = TurnManager(FailingRunner())
    live = Recorder()
    turns.start(request("r1"), live)
    await settle()
    assert live.types == ["turn.failed"]
    assert live.messages[0]["error"]["message"] == "La pregunta és buida."
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

    async def list_models(self) -> Sequence[ModelInfo]:
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
