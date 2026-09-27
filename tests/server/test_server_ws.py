"""WebSocket /api/ws end to end (Starlette TestClient, real engine and SQLite store)."""

import asyncio
import functools
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from agentic_os import __version__
from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode, Usage
from agentic_os.pricing import ModelPrice
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    Provider,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
)
from agentic_os.providers.fake import FakeProvider
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState
from agentic_os.server.ws import ProtocolError, parse_turn_start
from agentic_os.storage import RuntimeSettings

pytestmark = pytest.mark.filterwarnings("ignore:Using `httpx` with:DeprecationWarning")

ORIGIN = "https://aos.example"
COOKIE = "__Host-aos_session"
T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
Message = dict[str, Any]


class GateProvider:
    """Streams ``"Hola "``, then waits for :attr:`release` before finishing."""

    def __init__(self, agent: AgentName) -> None:
        self._agent: AgentName = agent
        self.release = asyncio.Event()
        self.cancelled = 0

    @property
    def agent(self) -> AgentName:
        return self._agent

    @property
    def mode(self) -> ProviderMode:
        return "fake"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        try:
            yield TextDelta("Hola ")
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        yield TextDelta("món")
        yield GenerationResult(
            text="Hola món", usage=Usage(3, 2), model="gate", latency_ms=5, ttft_ms=1
        )

    async def prewarm(self, request: GenerationRequest) -> None:
        return None

    async def status(self) -> ProviderStatus:
        return ProviderStatus(self._agent, "fake", True, "gate", "Porta")

    @property
    def fast_model(self) -> str:
        return "fast"

    @property
    def models_live(self) -> bool:
        return True

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        model = "gate"
        return (ModelInfo(id=model, label=model, is_default=True),)

    async def aclose(self) -> None:
        return None


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
def app_client(
    tmp_path: Path, providers: dict[AgentName, Provider] | None = None
) -> Iterator[tuple[TestClient, AppState, str]]:
    """Running app, its state and a valid session token."""
    if providers is None:
        providers = {
            "claude": FakeProvider("claude", chunk_delay=0),
            "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
        }
    app = create_app(make_settings(tmp_path), providers=providers, clock=lambda: T0)
    with TestClient(app, base_url="https://testserver") as client:
        state: AppState = app.state.aos
        token = call(client, functools.partial(state.sessions.create, T0, ip=None, user_agent=None))
        yield client, state, token


def connect(
    client: TestClient, token: str | None, origin: str | None = ORIGIN
) -> WebSocketTestSession:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    if token is not None:
        headers["cookie"] = f"{COOKIE}={token}"
    return client.websocket_connect("/api/ws", headers=headers)


def receive_until(ws: WebSocketTestSession, done: Callable[[Message], bool]) -> list[Message]:
    messages: list[Message] = []
    while True:
        message: Message = ws.receive_json()
        messages.append(message)
        if done(message):
            return messages


def is_type(kind: str, request_id: str | None = None) -> Callable[[Message], bool]:
    def check(message: Message) -> bool:
        return message["type"] == kind and (
            request_id is None or message.get("request_id") == request_id
        )

    return check


def start(request_id: str, **fields: Any) -> Message:
    message: Message = {
        "type": "turn.start",
        "request_id": request_id,
        "text": "Com organitzo un projecte petit?",
        "mode": "solo",
        "target": "claude",
        "conversation_id": None,
    }
    message.update(fields)
    return message


def assert_contiguous(events: list[Message], first: int = 1) -> None:
    assert [event["seq"] for event in events] == list(range(first, first + len(events)))


# -- handshake -------------------------------------------------------------------------


def test_closes_4401_without_a_session(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, _state, _token):
        for token in (None, "0" * 43):
            with connect(client, token) as ws, pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401


def test_closes_4403_on_a_foreign_origin(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, _state, token):
        for origin in ("https://evil.example", None):
            with connect(client, token, origin) as ws, pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4403


def test_hello_and_ping(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, _state, token), connect(client, token) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["version"] == __version__
        assert [p["agent"] for p in hello["providers"]] == ["claude", "chatgpt"]
        assert hello["providers"][0]["available"] is True
        assert hello["fx"] == {"eur_per_usd": 0.86, "as_of": None, "source": "manual"}
        assert hello["active_turns"] == []
        ws.send_json({"type": "ping", "t": 1727450000000})
        assert ws.receive_json() == {"type": "pong", "t": 1727450000000}


def test_revoked_session_closes_on_the_next_message(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, state, token), connect(client, token) as ws:
        ws.receive_json()
        call(client, functools.partial(state.sessions.revoke, token))
        ws.send_json({"type": "turn.subscribe", "request_id": "x", "after_seq": 0})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


# -- turns -----------------------------------------------------------------------------


def test_debate_turn_event_sequence(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, state, token), connect(client, token) as ws:
        ws.receive_json()
        ws.send_json(
            start(
                "debat-1",
                mode="debate",
                options={
                    "debate": {"rounds": 2, "consensus_threshold": 85, "synthesizer": "claude"},
                    "use_cache": True,
                },
            )
        )
        events = receive_until(ws, is_type("turn.completed"))

        assert all(event["request_id"] == "debat-1" for event in events)
        assert_contiguous(events)
        assert events[0]["type"] == "turn.started"
        assert events[0]["mode"] == "debate"
        assert events[0]["new_conversation"] is True
        phases = [(e["phase"], e["round"]) for e in events if e["type"] == "phase"]
        assert phases == [("answer", 0), ("revision", 1), ("revision", 2), ("synthesis", 2)]
        started = [e for e in events if e["type"] == "stream.started"]
        assert [(s["kind"], s["round"]) for s in started] == [
            ("answer", 0),
            ("answer", 0),
            ("revision", 1),
            ("revision", 1),
            ("revision", 2),
            ("revision", 2),
            ("synthesis", 2),
        ]
        for stream_id in {s["stream_id"] for s in started}:
            own = [e["type"] for e in events if e.get("stream_id") == stream_id]
            assert own[0] == "stream.started"
            assert own[-1] == "stream.completed"
            assert set(own[1:-1]) == {"stream.delta"}
        sections = {e["section"] for e in events if e["type"] == "stream.delta"}
        assert sections == {"text", "critique", "answer"}

        completed = events[-1]
        assert completed["consensus"] == {
            "reached": True,
            "round": 2,
            "scores": {"claude": 90, "chatgpt": 90},
        }
        assert completed["cached"] is False
        detail = call(
            client,
            functools.partial(state.store.get_conversation, events[0]["conversation_id"]),
        )
        assert detail is not None
        assert detail.messages[-1].kind == "synthesis"


def test_turn_start_defaults_come_from_runtime_settings(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, state, token), connect(client, token) as ws:
        ws.receive_json()
        runtime = RuntimeSettings(default_mode="solo", default_target="chatgpt")
        call(client, functools.partial(state.store.put_runtime_settings, runtime))
        ws.send_json({"type": "turn.start", "request_id": "r1", "text": "Hola?"})
        events = receive_until(ws, is_type("turn.completed"))
        assert events[0]["mode"] == "solo"
        agents = {e["agent"] for e in events if e["type"] == "stream.started"}
        assert agents == {"chatgpt"}


def test_turn_survives_disconnect_and_subscribe_replays(tmp_path: Path) -> None:
    gate = GateProvider("claude")
    providers: dict[AgentName, Provider] = {"claude": gate, "chatgpt": GateProvider("chatgpt")}
    with app_client(tmp_path, providers) as (client, _state, token):
        with connect(client, token) as ws:
            ws.receive_json()
            ws.send_json(start("r1"))
            first = receive_until(ws, is_type("stream.delta"))
        assert [e["type"] for e in first] == [
            "turn.started",
            "phase",
            "stream.started",
            "stream.delta",
        ]
        assert_contiguous(first)

        with connect(client, token) as ws:
            hello = ws.receive_json()
            assert hello["active_turns"] == [
                {
                    "request_id": "r1",
                    "conversation_id": first[0]["conversation_id"],
                    "last_seq": 4,
                }
            ]
            call(client, gate.release.set)  # the turn goes on with nobody listening
            ws.send_json({"type": "turn.subscribe", "request_id": "r1", "after_seq": 2})
            rest = receive_until(ws, is_type("turn.completed"))
        assert_contiguous(rest, first=3)
        assert rest[:2] == first[2:]
        assert [e["text"] for e in rest if e["type"] == "stream.delta"] == ["Hola ", "món"]

        with connect(client, token) as ws:
            assert ws.receive_json()["active_turns"] == []
            ws.send_json({"type": "turn.subscribe", "request_id": "r1", "after_seq": 0})
            replay = receive_until(ws, is_type("turn.completed"))
        assert replay == first + rest[2:]


def test_cancel(tmp_path: Path) -> None:
    gate = GateProvider("claude")
    providers: dict[AgentName, Provider] = {"claude": gate, "chatgpt": GateProvider("chatgpt")}
    with app_client(tmp_path, providers) as (client, _state, token), connect(client, token) as ws:
        ws.receive_json()
        ws.send_json(start("r1"))
        events = receive_until(ws, is_type("stream.delta"))
        ws.send_json({"type": "turn.cancel", "request_id": "r1"})
        events += receive_until(ws, is_type("turn.cancelled"))
        assert_contiguous(events)
        assert events[-1] == {"type": "turn.cancelled", "request_id": "r1", "seq": len(events)}
        assert gate.cancelled == 1

        ws.send_json({"type": "turn.cancel", "request_id": "r1"})  # already over: no answer
        ws.send_json({"type": "ping", "t": 1})
        assert ws.receive_json() == {"type": "pong", "t": 1}
        for kind in ("turn.cancel", "turn.subscribe"):
            ws.send_json({"type": kind, "request_id": "nope"})
            assert ws.receive_json() == {"type": "turn.unknown", "request_id": "nope"}


def test_limits(tmp_path: Path) -> None:
    gates: dict[AgentName, GateProvider] = {
        "claude": GateProvider("claude"),
        "chatgpt": GateProvider("chatgpt"),
    }
    providers: dict[AgentName, Provider] = dict(gates)
    with app_client(tmp_path, providers) as (client, state, token):
        conversation = call(client, functools.partial(state.store.create_conversation, "Conv"))
        with connect(client, token) as ws:
            ws.receive_json()
            ws.send_json(start("a", conversation_id=conversation))
            receive_until(ws, is_type("turn.started", "a"))

            ws.send_json(start("b", conversation_id=conversation))
            [error] = [m for m in receive_until(ws, is_type("error")) if m["type"] == "error"]
            assert error == {
                "type": "error",
                "message": "Aquesta conversa ja té un torn en curs.",
                "code": "busy",
                "request_id": "b",
            }

            ws.send_json(start("c"))
            ws.send_json(start("d", target="chatgpt"))
            streaming: set[str] = set()

            def c_and_d_streaming(message: Message) -> bool:
                if message["type"] == "stream.delta":
                    streaming.add(message["request_id"])
                return {"c", "d"} <= streaming

            receive_until(ws, c_and_d_streaming)
            ws.send_json(start("e"))
            error = receive_until(ws, is_type("error", "e"))[-1]
            assert error["code"] == "busy"
            assert error["message"] == "Ja hi ha 3 torns en curs. Espera que n'acabi algun."

            ws.send_json(start("a"))
            error = receive_until(ws, is_type("error", "a"))[-1]
            assert error["code"] == "duplicate"
        assert state.turns.is_running("a")
    # Shutdown cancelled the three running turns.
    assert gates["claude"].cancelled == 2
    assert gates["chatgpt"].cancelled == 1


def test_invalid_messages_never_close_the_socket(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, _state, token), connect(client, token) as ws:
        ws.receive_json()

        def error_for(send: Callable[[], None]) -> Message:
            send()
            message: Message = ws.receive_json()
            assert message["type"] == "error"
            return message

        assert error_for(lambda: ws.send_bytes(b"\x00"))["message"] == (
            "Només s'accepten missatges de text JSON."
        )
        assert error_for(lambda: ws.send_text("{"))["message"] == "El missatge no és JSON vàlid."
        assert error_for(lambda: ws.send_text("[" * 100_000))["code"] == "invalid"
        assert "«type»" in error_for(lambda: ws.send_text("[]"))["message"]
        assert error_for(lambda: ws.send_text("x" * (600 * 1024)))["code"] == "too_large"
        unknown = error_for(lambda: ws.send_json({"type": "nope"}))
        assert unknown["message"] == "Tipus de missatge desconegut: «nope»."

        no_text = error_for(lambda: ws.send_json(start("r1", text=None)))
        assert no_text == {
            "type": "error",
            "message": "«text» ha de ser un text.",
            "code": "invalid",
            "request_id": "r1",
        }
        bad_id = error_for(lambda: ws.send_json(start("")))
        assert "request_id" not in bad_id
        assert error_for(lambda: ws.send_json(start("r2", mode="x")))["request_id"] == "r2"
        assert error_for(lambda: ws.send_json(start("r3", target="gemini")))["message"] == (
            "«target» ha de ser «claude» o «chatgpt»."
        )
        assert error_for(lambda: ws.send_json(start("r4", conversation_id=True)))["message"] == (
            "«conversation_id» ha de ser un enter positiu o null."
        )
        rounds = error_for(lambda: ws.send_json(start("r5", options={"debate": {"rounds": 9}})))
        assert rounds["message"] == "«debate.rounds» ha de ser un enter entre 0 i 4."
        assert error_for(lambda: ws.send_json(start("r6", options=[])))["request_id"] == "r6"
        negative = error_for(
            lambda: ws.send_json({"type": "turn.subscribe", "request_id": "r", "after_seq": -1})
        )
        assert negative["request_id"] == "r"
        assert error_for(lambda: ws.send_json({"type": "ping"}))["message"] == (
            "«t» ha de ser un número."
        )
        assert error_for(lambda: ws.send_text('{"type": "ping", "t": NaN}'))["code"] == "invalid"

        ws.send_json({"type": "ping", "t": 2.5})
        assert ws.receive_json() == {"type": "pong", "t": 2.5}


# -- models and prices -------------------------------------------------------------------


def answers_meta(client: TestClient, state: AppState, conversation_id: int) -> list[Message]:
    detail = call(client, functools.partial(state.store.get_conversation, conversation_id))
    assert detail is not None
    return [dict(m.meta) for m in detail.messages if m.kind != "question"]


def test_turn_start_models_reach_the_engine(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, state, token), connect(client, token) as ws:
        ws.receive_json()
        runtime = RuntimeSettings(models={"claude": "claude-demo-1", "chatgpt": "gpt-demo-1"})
        call(client, functools.partial(state.store.put_runtime_settings, runtime))

        # The owner's default models apply when the turn does not choose.
        ws.send_json(start("r1", mode="duel"))
        events = receive_until(ws, is_type("turn.completed"))
        started = {e["agent"]: e["model"] for e in events if e["type"] == "stream.started"}
        assert started == {"claude": "claude-demo-1", "chatgpt": "gpt-demo-1"}
        metas = answers_meta(client, state, events[0]["conversation_id"])
        assert sorted(m["model"] for m in metas) == ["claude-demo-1", "gpt-demo-1"]

        # A model chosen for the turn wins; null keeps the default.
        ws.send_json(start("r2", mode="duel", models={"claude": "opus-nou", "chatgpt": None}))
        events = receive_until(ws, is_type("turn.completed"))
        started = {e["agent"]: e["model"] for e in events if e["type"] == "stream.started"}
        assert started == {"claude": "opus-nou", "chatgpt": "gpt-demo-1"}
        conversation_id = events[0]["conversation_id"]
        metas = answers_meta(client, state, conversation_id)
        assert sorted(m["model"] for m in metas) == ["gpt-demo-1", "opus-nou"]
        detail = call(client, functools.partial(state.store.get_conversation, conversation_id))
        assert detail is not None
        assert detail.messages[0].meta["models"] == {
            "claude": "opus-nou",
            "chatgpt": "gpt-demo-1",
        }


def test_owner_prices_reach_the_engine(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, state, token), connect(client, token) as ws:
        ws.receive_json()
        price = ModelPrice(input=2.0, output=10.0, cache_read=0.2, cache_write=2.5)
        runtime = RuntimeSettings(prices={"fake-claude": price})
        call(client, functools.partial(state.store.put_runtime_settings, runtime))
        ws.send_json(start("r1"))
        events = receive_until(ws, is_type("turn.completed"))
        [completed] = [e for e in events if e["type"] == "stream.completed"]
        usage = completed["usage"]
        expected = (usage["input_tokens"] * 2.0 + usage["output_tokens"] * 10.0) / 1_000_000
        assert usage["cost_usd"] == pytest.approx(expected)
        [meta] = answers_meta(client, state, events[0]["conversation_id"])
        assert meta["cost_basis"] == "equivalent"


def test_invalid_models_are_rejected_with_the_request_id(tmp_path: Path) -> None:
    with app_client(tmp_path) as (client, _state, token), connect(client, token) as ws:
        ws.receive_json()
        cases: list[tuple[object, str]] = [
            (["opus"], "«models» ha de ser un objecte (agent → model)."),
            ({"gemini": "x"}, "«models» només admet «claude» i «chatgpt»."),
            (
                {"claude": "opus 5"},
                "«models.claude» ha de ser un identificador de model vàlid: fins a 100 "
                "lletres, xifres o els signes . _ : / @ [ ] -, sense espais.",
            ),
            ({"chatgpt": 5}, "«models.chatgpt» ha de ser un identificador de model vàlid"),
        ]
        for index, (models, message) in enumerate(cases):
            request_id = f"bad-{index}"
            ws.send_json(start(request_id, models=models))
            error = ws.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "invalid"
            assert error["request_id"] == request_id
            assert error["message"].startswith(message)
        # Nothing was started.
        ws.send_json({"type": "turn.subscribe", "request_id": "bad-0"})
        assert ws.receive_json() == {"type": "turn.unknown", "request_id": "bad-0"}


def test_parse_turn_start_merges_the_models_over_the_settings() -> None:
    runtime = RuntimeSettings(
        models={"claude": "opus", "chatgpt": None},
        fast_models={"claude": None, "chatgpt": "gpt-6-luna"},
    )
    data: dict[str, object] = {"request_id": "r", "text": "Hola"}
    request = parse_turn_start(data, runtime)
    assert request.models == {"claude": "opus"}
    assert request.fast_models == {"chatgpt": "gpt-6-luna"}
    request = parse_turn_start({**data, "models": {"chatgpt": " gpt-6-sol", "claude": ""}}, runtime)
    assert request.models == {"claude": "opus", "chatgpt": "gpt-6-sol"}
    request = parse_turn_start({**data, "models": None}, RuntimeSettings())
    assert request.models == {}
    assert request.fast_models == {}
    with pytest.raises(ProtocolError) as exc:
        parse_turn_start({**data, "models": {"claude": "x y"}}, runtime)
    assert exc.value.request_id == "r"


def test_rejected_turns_always_name_the_request(tmp_path: Path) -> None:
    gates: dict[AgentName, Provider] = {
        "claude": GateProvider("claude"),
        "chatgpt": GateProvider("chatgpt"),
    }
    with app_client(tmp_path, gates) as (client, state, token), connect(client, token) as ws:
        ws.receive_json()
        ws.send_json(start("a"))
        receive_until(ws, is_type("turn.started", "a"))
        ws.send_json(start("a"))
        duplicate = receive_until(ws, is_type("error"))[-1]
        assert duplicate == {
            "type": "error",
            "message": "Aquest identificador de petició ja s'ha fet servir.",
            "code": "duplicate",
            "request_id": "a",
        }
        call(client, state.turns.aclose)  # shutting down
        ws.send_json(start("b"))
        unavailable = receive_until(ws, is_type("error"))[-1]
        assert unavailable == {
            "type": "error",
            "message": "El servidor s'està aturant.",
            "code": "unavailable",
            "request_id": "b",
        }
