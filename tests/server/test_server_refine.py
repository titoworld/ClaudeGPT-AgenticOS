"""The refine mode on the server (docs/adr/0010-mode-perfecciona.md): ``turn.start`` with
mode ``refine`` and its options, its budget in dollars for the engine, ``turn.stop``
through the WebSocket and the runtime settings of the mode.

The engine is replaced by :class:`RefineRunner`, which records what the server gives it
(the request and the stop event) and stops like the engine does: these tests are about
the server, not the loop (tests/orchestrator has that)."""

import asyncio
import functools
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from agentic_os.config import Settings
from agentic_os.domain import AgentName, RefineOptions, Usage
from agentic_os.fx import FxRate, manual_rate
from agentic_os.orchestrator.events import (
    PhaseChanged,
    RefineChange,
    RefineRound,
    Savings,
    ServerEvent,
    TurnCompleted,
    TurnOutcome,
    TurnStarted,
    TurnStopping,
)
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.pricing import ModelPrice
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.server import app as app_module
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState
from agentic_os.server.ws import ProtocolError, parse_turn_start, refine_budget_usd
from agentic_os.storage import FxSettings, RuntimeSettings

pytestmark = pytest.mark.filterwarnings("ignore:Using `httpx` with:DeprecationWarning")

ORIGIN = "https://aos.example"
COOKIE = "__Host-aos_session"
T0 = datetime(2026, 10, 2, 9, 0, 0, tzinfo=UTC)
Message = dict[str, Any]
ROUND = Usage(input_tokens=1200, output_tokens=300, cost_usd=0.01)
TOTAL = Usage(input_tokens=5000, output_tokens=900, cost_usd=0.04)
STOP_ONLY_REFINE = (
    "Només un torn «Perfecciona» es pot aturar en acabar la ronda; per aturar-lo ara, cancel·la'l."
)


class RefineRunner:
    """Stands in for the engine. A refine turn goes on (round 2, its reviews) until the
    owner's stop event is set; then it announces it (``turn.stopping``), finishes the
    round and ends with its last version, as the engine does. Any other turn waits for
    :attr:`release`. Every request and the stop event the manager gave it are kept."""

    def __init__(self) -> None:
        self.requests: list[TurnRequest] = []
        self.stops: list[asyncio.Event | None] = []
        self.release = asyncio.Event()

    async def run(
        self,
        request: TurnRequest,
        *,
        compaction_threshold_tokens: int | None = None,
        price_overrides: Mapping[str, ModelPrice] | None = None,
        on_outcome: Callable[[TurnOutcome], None] | None = None,
        stop: asyncio.Event | None = None,
    ) -> AsyncIterator[ServerEvent]:
        self.requests.append(request)
        self.stops.append(stop)
        rid = request.request_id
        conversation_id = len(self.requests)
        yield TurnStarted(rid, conversation_id, 10 * conversation_id, request.mode, True)
        if request.mode != "refine" or stop is None:
            await self.release.wait()
            yield TurnCompleted(rid, conversation_id, 10, (11,), Usage(), Savings())
            return
        yield PhaseChanged(rid, "review", 2)
        await stop.wait()
        yield TurnStopping(rid, 2)
        yield PhaseChanged(rid, "edit", 2)
        yield RefineRound(
            rid,
            round=2,
            version=2,
            accepted=True,
            words=320,
            budget_words=360,
            usage=ROUND,
            total=TOTAL,
            changes=(RefineChange("defect", "Corregeix la data de lliurament."),),
            proposals={"claude": 1, "chatgpt": 0},
            scores={"claude": 84, "chatgpt": 93},
        )
        final = (15,)
        if on_outcome is not None:
            outcome = TurnOutcome(
                "completed", TOTAL, Savings(), final_message_ids=final, stop_reason="owner"
            )
            on_outcome(outcome)
        yield TurnCompleted(rid, conversation_id, 10, final, TOTAL, Savings(), stop_reason="owner")


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> RefineRunner:
    """The engine of the app: ``server.app`` builds its turn manager with it."""
    fake = RefineRunner()

    def engine(*args: object, **kwargs: object) -> RefineRunner:
        return fake

    monkeypatch.setattr(app_module, "Engine", engine)
    return fake


def make_settings(tmp_path: Path) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
    }
    return Settings(**{"_env_file": None, **values})


def call(client: TestClient, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` (sync or async) on the app's event loop."""
    assert client.portal is not None
    return client.portal.call(fn)


@contextmanager
def app_client(tmp_path: Path) -> Iterator[tuple[TestClient, AppState, str]]:
    """Running app (with the fake engine of :func:`runner`), its state and a session."""
    providers: dict[AgentName, Provider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    app = create_app(make_settings(tmp_path), providers=providers, clock=lambda: T0)
    with TestClient(app, base_url="https://testserver") as client:
        state: AppState = app.state.aos
        token = call(client, functools.partial(state.sessions.create, T0, ip=None, user_agent=None))
        yield client, state, token


def connect(client: TestClient, token: str) -> WebSocketTestSession:
    headers = {"origin": ORIGIN, "cookie": f"{COOKIE}={token}"}
    return client.websocket_connect("/api/ws", headers=headers)


def auth_headers(token: str) -> dict[str, str]:
    return {"origin": ORIGIN, "cookie": f"{COOKIE}={token}"}


def receive_until(ws: WebSocketTestSession, done: Callable[[Message], bool]) -> list[Message]:
    messages: list[Message] = []
    while True:
        message: Message = ws.receive_json()
        messages.append(message)
        if done(message):
            return messages


def is_type(kind: str) -> Callable[[Message], bool]:
    return lambda message: message["type"] == kind


def start(request_id: str, **fields: Any) -> Message:
    return {
        "type": "turn.start",
        "request_id": request_id,
        "text": "Perfecciona el pla de llançament.",
        "mode": "refine",
        **fields,
    }


def stop_turn(ws: WebSocketTestSession, request_id: str) -> list[Message]:
    """Ask a refine turn of :class:`RefineRunner` to stop and read it to its end."""
    ws.send_json({"type": "turn.stop", "request_id": request_id})
    return receive_until(ws, is_type("turn.completed"))


# -- turn.stop -----------------------------------------------------------------------------


def test_turn_stop_ends_a_refine_turn_after_its_round(tmp_path: Path, runner: RefineRunner) -> None:
    with app_client(tmp_path) as (client, _state, token), connect(client, token) as ws:
        ws.receive_json()  # hello
        ws.send_json(start("p1"))
        events = receive_until(ws, is_type("phase"))
        [stop] = runner.stops
        assert stop is not None and not stop.is_set()

        events += stop_turn(ws, "p1")
        assert stop.is_set()
        assert [event["type"] for event in events] == [
            "turn.started",
            "phase",
            "turn.stopping",
            "phase",
            "refine.round",
            "turn.completed",
        ]
        assert [event["seq"] for event in events] == list(range(1, 7))
        assert events[2] == {"type": "turn.stopping", "request_id": "p1", "round": 2, "seq": 3}
        assert events[-1]["stop_reason"] == "owner"
        round_ = events[4]
        assert (round_["round"], round_["version"], round_["accepted"]) == (2, 2, True)
        assert round_["proposals"] == {"claude": 1, "chatgpt": 0}

        # The turn is over: stopping it again gets no answer, and it was never cancelled.
        ws.send_json({"type": "turn.stop", "request_id": "p1"})
        ws.send_json({"type": "ping", "t": 1})
        assert ws.receive_json() == {"type": "pong", "t": 1}


def test_a_repeated_turn_stop_gets_no_answer(tmp_path: Path, runner: RefineRunner) -> None:
    with app_client(tmp_path) as (client, _state, token), connect(client, token) as ws:
        ws.receive_json()
        ws.send_json(start("p1"))
        receive_until(ws, is_type("phase"))
        ws.send_json({"type": "turn.stop", "request_id": "p1"})
        ws.send_json({"type": "turn.stop", "request_id": "p1"})
        events = receive_until(ws, is_type("turn.completed"))
        assert [event["type"] for event in events].count("turn.stopping") == 1
        ws.send_json({"type": "ping", "t": 2})
        assert ws.receive_json() == {"type": "pong", "t": 2}  # no error for the second


def test_turn_stop_of_another_mode_unknown_and_ended_turns(
    tmp_path: Path, runner: RefineRunner
) -> None:
    with app_client(tmp_path) as (client, _state, token), connect(client, token) as ws:
        ws.receive_json()
        ws.send_json(start("s1", mode="solo"))
        receive_until(ws, is_type("turn.started"))

        # A solo turn has no round to end after: stopping it now is turn.cancel.
        ws.send_json({"type": "turn.stop", "request_id": "s1"})
        assert ws.receive_json() == {
            "type": "error",
            "message": STOP_ONLY_REFINE,
            "code": "invalid",
            "request_id": "s1",
        }
        [stop] = runner.stops
        assert stop is not None and not stop.is_set()
        call(client, runner.release.set)
        receive_until(ws, is_type("turn.completed"))  # it went on to its end

        ws.send_json({"type": "turn.stop", "request_id": "s1"})  # over: no answer
        ws.send_json({"type": "turn.stop", "request_id": "nope"})
        assert ws.receive_json() == {"type": "turn.unknown", "request_id": "nope"}

        ws.send_json({"type": "turn.stop", "request_id": ""})
        error = ws.receive_json()
        assert error["type"] == "error" and error["code"] == "invalid"
        assert "request_id" not in error


def test_turn_stop_after_a_cancel_changes_nothing(tmp_path: Path, runner: RefineRunner) -> None:
    with app_client(tmp_path) as (client, _state, token), connect(client, token) as ws:
        ws.receive_json()
        ws.send_json(start("p1"))
        events = receive_until(ws, is_type("phase"))
        ws.send_json({"type": "turn.cancel", "request_id": "p1"})
        ws.send_json({"type": "turn.stop", "request_id": "p1"})
        events += receive_until(ws, is_type("turn.cancelled"))
        assert "turn.stopping" not in [event["type"] for event in events]
        ws.send_json({"type": "ping", "t": 3})
        assert ws.receive_json() == {"type": "pong", "t": 3}


# -- turn.start ----------------------------------------------------------------------------


def test_turn_start_takes_the_refine_mode_and_its_options(
    tmp_path: Path, runner: RefineRunner
) -> None:
    with app_client(tmp_path) as (client, state, token), connect(client, token) as ws:
        ws.receive_json()
        saved = RuntimeSettings(refine=RefineOptions(max_rounds=20, editor="chatgpt"))
        call(client, functools.partial(state.store.put_runtime_settings, saved))

        # Partial options over the owner's: the missing keys keep the saved values.
        ws.send_json(start("p1", options={"refine": {"max_words": 800, "budget_eur": 5}}))
        [started] = receive_until(ws, is_type("turn.started"))
        assert started["mode"] == "refine"
        stop_turn(ws, "p1")
        # null is "no word limit" (automatic), over a saved limit too.
        ws.send_json(start("p2", options={"refine": {"max_words": None}, "use_cache": False}))
        receive_until(ws, is_type("phase"))
        stop_turn(ws, "p2")
        ws.send_json(start("p3"))  # no options: the owner's
        receive_until(ws, is_type("phase"))
        stop_turn(ws, "p3")

    first, second, third = runner.requests
    assert first.mode == "refine"
    assert first.options.refine == RefineOptions(
        max_rounds=20, budget_eur=5.0, max_words=800, editor="chatgpt"
    )
    assert second.options.refine == RefineOptions(max_rounds=20, editor="chatgpt")
    assert second.options.use_cache is False
    assert third.options.refine == saved.refine


def test_the_refine_budget_goes_to_the_engine_in_dollars_at_the_rate_the_app_shows(
    tmp_path: Path, runner: RefineRunner
) -> None:
    with app_client(tmp_path) as (client, state, token), connect(client, token) as ws:
        hello = ws.receive_json()
        assert hello["fx"]["eur_per_usd"] == 0.86  # no ECB rate yet: the manual one

        ws.send_json(start("manual", options={"refine": {"budget_eur": 4.3}}))
        receive_until(ws, is_type("phase"))
        stop_turn(ws, "manual")

        # A recent ECB rate is the one the app shows euros with (auto mode).
        ecb = FxRate(eur_per_usd=0.8, as_of=date(2026, 10, 1), source="ecb")
        call(client, functools.partial(state.store.put_ecb_rate, ecb, T0 - timedelta(hours=3)))
        ws.send_json(start("ecb"))
        receive_until(ws, is_type("phase"))
        stop_turn(ws, "ecb")

        # The owner's manual rate wins in manual mode, ECB rate or not.
        manual = RuntimeSettings(fx=FxSettings(mode="manual", eur_per_usd=1.25))
        call(client, functools.partial(state.store.put_runtime_settings, manual))
        ws.send_json(start("own", options={"refine": {"budget_eur": 100}}))
        receive_until(ws, is_type("phase"))
        stop_turn(ws, "own")

        # Other modes have no refine budget.
        ws.send_json(start("solo", mode="solo"))
        receive_until(ws, is_type("turn.started"))
        call(client, runner.release.set)
        receive_until(ws, is_type("turn.completed"))

    budgets = [request.refine_budget_usd for request in runner.requests]
    assert budgets == [4.3 / 0.86, 3.0 / 0.8, 100 / 1.25, None]


def test_invalid_refine_options_are_refused_with_the_request_id(
    tmp_path: Path, runner: RefineRunner
) -> None:
    cases: list[tuple[Message, str]] = [
        (start("m", mode="trio"), "«mode» ha de ser «solo», «duel», «debate» o «refine»."),
        (start("o", options={"refine": [12]}), "«options.refine» ha de ser un objecte."),
        (
            start("r", options={"refine": {"max_rounds": 1}}),
            "«refine.max_rounds» ha de ser un enter entre 2 i 50.",
        ),
        (
            start("b", options={"refine": {"budget_eur": 1000}}),
            "«refine.budget_eur» ha de ser un nombre entre 0,1 i 100.",
        ),
        (
            start("w", options={"refine": {"max_words": 10}}),
            "«refine.max_words» ha de ser null (automàtic) o un enter entre 100 i 20000.",
        ),
        (
            start("t", options={"refine": {"convergence_threshold": 101}}),
            "«refine.convergence_threshold» ha de ser un enter entre 50 i 100.",
        ),
        (
            start("e", options={"refine": {"editor": "gemini"}}),
            "«refine.editor» ha de ser «claude» o «chatgpt».",
        ),
    ]
    with app_client(tmp_path) as (client, _state, token), connect(client, token) as ws:
        ws.receive_json()
        for message, error in cases:
            ws.send_json(message)
            assert ws.receive_json() == {
                "type": "error",
                "message": error,
                "code": "invalid",
                "request_id": message["request_id"],
            }
    assert runner.requests == []  # nothing started


# -- parse_turn_start and the conversion ---------------------------------------------------


def test_parse_turn_start_converts_the_refine_budget_at_the_rate_given() -> None:
    data: dict[str, object] = {"request_id": "r", "text": "Perfecciona-ho", "mode": "refine"}
    ecb = FxRate(eur_per_usd=0.8, as_of=date(2026, 10, 1), source="ecb")
    request = parse_turn_start(data, RuntimeSettings(), fx=ecb)
    assert request.options.refine == RefineOptions()
    assert request.refine_budget_usd == 3.0 / 0.8 == 3.75
    debate = parse_turn_start({**data, "mode": "debate"}, RuntimeSettings(), fx=ecb)
    assert debate.refine_budget_usd is None
    assert refine_budget_usd(RefineOptions(budget_eur=0.1), manual_rate(0.2)) == 0.1 / 0.2
    with pytest.raises(ProtocolError) as exc:
        parse_turn_start({**data, "options": {"refine": "molt"}}, RuntimeSettings(), fx=ecb)
    assert exc.value.request_id == "r"
    assert exc.value.message == "«options.refine» ha de ser un objecte."


# -- runtime settings ----------------------------------------------------------------------


def test_the_refine_settings_over_rest(tmp_path: Path, runner: RefineRunner) -> None:
    with app_client(tmp_path) as (client, _state, token):
        headers = auth_headers(token)
        current = client.get("/api/settings", headers=headers).json()
        assert current["refine"] == {
            "max_rounds": 12,
            "budget_eur": 3.0,
            "max_words": None,
            "stop_on_convergence": True,
            "convergence_threshold": 90,
            "editor": "claude",
        }
        refine = {**current["refine"], "max_rounds": 30, "max_words": 1500, "editor": "chatgpt"}
        saved = client.put("/api/settings", headers=headers, json={**current, "refine": refine})
        assert saved.status_code == 200
        assert saved.json() == {**current, "refine": refine, "revision": 1}

        for patch, detail in (
            ({"default_mode": "refine"}, "El mode per defecte no pot ser «refine»."),
            (
                {"refine": {**refine, "budget_eur": 0.01}},
                "«refine.budget_eur» ha de ser un nombre entre 0,1 i 100.",
            ),
            ({"refine": None}, "«refine» ha de ser un objecte."),
        ):
            refused = client.put("/api/settings", headers=headers, json={**saved.json(), **patch})
            assert refused.status_code == 422
            assert refused.json() == {"detail": detail}
        assert client.get("/api/settings", headers=headers).json() == saved.json()
