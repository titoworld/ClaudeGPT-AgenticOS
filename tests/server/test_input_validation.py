"""Input at the boundaries of the server (audit points 22 and 23, N22).

Identifiers beyond SQLite's INTEGER range (2**63 - 1) and text that cannot be encoded
as UTF-8 (a lone surrogate written as a ``\\ud800`` JSON escape, which is valid JSON)
get a Catalan 422 over REST and an ``invalid`` error over the WebSocket: never a 500,
an internal error nor one of Python's own messages. A password with such text is a
wrong password: 401, and the attempt counts for the lockout.
"""

import functools
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pyotp
import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from agentic_os import fx
from agentic_os.config import Settings
from agentic_os.domain import AgentName
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.security.passwords import hash_password
from agentic_os.security.throttle import client_key
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState

pytestmark = pytest.mark.filterwarnings("ignore:Using `httpx` with:DeprecationWarning")

ORIGIN = "https://aos.example"
ORIGIN_HEADERS = {"origin": ORIGIN}
JSON_HEADERS = {**ORIGIN_HEADERS, "content-type": "application/json"}
COOKIE = "__Host-aos_session"
PASSWORD = "una frase de pas prou llarga"
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
T0 = datetime(2026, 9, 27, 12, 0, 5, tzinfo=UTC)
MAX_ID = 2**63 - 1
LONE = "\\ud800"
"""A JSON escape of a lone surrogate (inside the JSON text, not a Python string)."""
PAIR = "\\ud83d\\ude00"
"""A JSON escape of a valid surrogate pair (😀)."""
INVALID_BODY_TEXT = "La petició conté text que no és UTF-8 vàlid."
INVALID_MESSAGE_TEXT = "El missatge conté text que no és UTF-8 vàlid."
Message = dict[str, Any]


async def no_ecb() -> fx.FxRate:
    """The ECB rate is never downloaded in tests."""
    raise fx.FxError("Sense xarxa als tests.")


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
        "claude_mode": "fake",
        "chatgpt_mode": "fake",
        **overrides,
    }
    return Settings(**values)


def fakes() -> dict[AgentName, Provider]:
    return {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }


@functools.cache
def password_hash() -> str:
    return hash_password(PASSWORD)


@dataclass
class Harness:
    client: httpx.AsyncClient
    state: AppState

    async def add_owner(self) -> None:
        await self.state.store.set_owner(
            password_hash=password_hash(), totp_secret=SECRET, totp_last_step=0
        )

    async def login_session(self) -> None:
        token = await self.state.sessions.create(T0, ip=None, user_agent=None)
        self.client.cookies.set(self.state.cookie_name, token)

    async def send(self, method: str, path: str, raw: str) -> httpx.Response:
        """A request whose body is the JSON text ``raw``, sent as it is."""
        return await self.client.request(method, path, content=raw.encode(), headers=JSON_HEADERS)


@asynccontextmanager
async def running(settings: Settings) -> AsyncIterator[Harness]:
    app = create_app(settings, providers=fakes(), clock=lambda: T0, fx_fetcher=no_ecb)
    async with app.router.lifespan_context(app):
        # Exceptions of the app are raised here too, so a 500 fails loudly.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            yield Harness(client, app.state.aos)


@pytest.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    async with running(make_settings(tmp_path)) as harness:
        await harness.login_session()
        yield harness


# -- identifiers beyond SQLite's range (audit point 22) --------------------------------


@pytest.mark.parametrize("conversation_id", [2**63, 10**30, -(2**63) - 1, 0, -1])
@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_conversation_ids_out_of_range_are_invalid(
    h: Harness, method: str, conversation_id: int
) -> None:
    # Ids are positive; beyond SQLite's INTEGER range they even crashed the query.
    response = await h.send(method, f"/api/conversations/{conversation_id}", '{"title": "nou"}')
    assert response.status_code == 422
    assert response.json() == {"detail": "Dades no vàlides: «conversation_id»."}


async def test_the_largest_sqlite_id_is_a_valid_id(h: Harness) -> None:
    for method in ("GET", "PATCH", "DELETE"):
        response = await h.send(method, f"/api/conversations/{MAX_ID}", '{"title": "nou"}')
        assert response.status_code == 404, method
        assert response.json() == {"detail": "La conversa no existeix."}
    listed = await h.client.get(f"/api/conversations?before={MAX_ID}")
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.parametrize("before", [2**63, 10**30])
async def test_a_page_cursor_beyond_sqlite_is_invalid(h: Harness, before: int) -> None:
    response = await h.client.get(f"/api/conversations?before={before}")
    assert response.status_code == 422
    assert response.json() == {"detail": "Dades no vàlides: «before»."}


# -- text that is not valid UTF-8 (N22) ------------------------------------------------


async def test_bodies_with_text_that_is_not_utf8_are_invalid(h: Harness) -> None:
    conversation_id = await h.state.store.create_conversation("Prova")
    price = '{"input": 1, "output": 1, "cache_read": 0, "cache_write": 0}'
    cases = [
        ("PATCH", f"/api/conversations/{conversation_id}", '{"title": "t' + LONE + '"}'),
        ("PATCH", f"/api/conversations/{conversation_id}", '{"title' + LONE + '": "t"}'),
        ("PUT", "/api/settings", '{"revision": 0, "prices": {"m' + LONE + '": ' + price + "}}"),
        ("PUT", "/api/settings", '{"revision": 0, "models": {"claude": "x' + LONE + '"}}'),
        ("PUT", "/api/settings", '{"revision": 0, "unknown' + LONE + '": 1}'),
        ("PUT", "/api/settings", '["' + LONE + '"]'),
    ]
    for method, path, raw in cases:
        response = await h.send(method, path, raw)
        assert response.status_code == 422, raw
        assert response.json() == {"detail": INVALID_BODY_TEXT}, raw
    # Nothing was changed.
    detail = await h.state.store.get_conversation(conversation_id)
    assert detail is not None and detail.conversation.title == "Prova"
    assert (await h.state.store.get_runtime_settings()).revision == 0


async def test_python_messages_never_reach_the_client(h: Harness) -> None:
    conversation_id = await h.state.store.create_conversation("Prova")
    response = await h.send(
        "PATCH", f"/api/conversations/{conversation_id}", '{"title": "' + LONE * 3 + '"}'
    )
    assert response.status_code == 422
    assert "codec" not in response.text and "surrogate" not in response.text


async def test_surrogate_pairs_are_valid_text(h: Harness) -> None:
    conversation_id = await h.state.store.create_conversation("Prova")
    response = await h.send(
        "PATCH", f"/api/conversations/{conversation_id}", '{"title": "Hola ' + PAIR + '"}'
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Hola 😀"


# -- passwords that are not valid UTF-8 (audit point 23) -------------------------------


@pytest.mark.parametrize("owner", [True, False])
async def test_a_password_that_is_not_utf8_is_a_failed_attempt(tmp_path: Path, owner: bool) -> None:
    async with running(make_settings(tmp_path, login_max_failures=2)) as h:
        if owner:
            await h.add_owner()
        raw = '{"password": "' + LONE + "x" * 20 + '", "totp": "123456"}'
        first = await h.send("POST", "/api/auth/login", raw)
        second = await h.send("POST", "/api/auth/login", raw)
        for response in (first, second):
            assert response.status_code == 401
            assert response.json() == {"detail": "Credencials incorrectes."}
        # Two failures: locked out, even with the right credentials.
        locked = await h.client.post(
            "/api/auth/login",
            json={"password": PASSWORD, "totp": pyotp.TOTP(SECRET).at(T0)},
            headers=ORIGIN_HEADERS,
        )
        assert locked.status_code == 429


async def test_a_code_that_is_not_utf8_is_a_failed_attempt(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path)) as h:
        await h.add_owner()
        raw = json.dumps({"password": PASSWORD, "totp": "12345"})[:-2] + LONE + '"}'
        response = await h.send("POST", "/api/auth/login", raw)
        assert response.status_code == 401
        assert response.json() == {"detail": "Credencials incorrectes."}
        counted = await h.state.store.get_throttle(client_key("127.0.0.1"))
        assert counted is not None and counted.failures == 1


# -- WebSocket -------------------------------------------------------------------------


@contextmanager
def socket(tmp_path: Path) -> Iterator[WebSocketTestSession]:
    app = create_app(
        make_settings(tmp_path), providers=fakes(), clock=lambda: T0, fx_fetcher=no_ecb
    )
    with TestClient(app, base_url="https://testserver") as client:
        state: AppState = app.state.aos
        assert client.portal is not None
        token = client.portal.call(
            functools.partial(state.sessions.create, T0, ip=None, user_agent=None)
        )
        headers = {"origin": ORIGIN, "cookie": f"{COOKIE}={token}"}
        with client.websocket_connect("/api/ws", headers=headers) as ws:
            assert ws.receive_json()["type"] == "hello"
            yield ws


def receive_until(ws: WebSocketTestSession, done: Callable[[Message], bool]) -> list[Message]:
    messages: list[Message] = []
    while True:
        message: Message = ws.receive_json()
        messages.append(message)
        if done(message):
            return messages


def turn_start(request_id: str, **fields: Any) -> str:
    message: Message = {
        "type": "turn.start",
        "request_id": request_id,
        "text": "Hola",
        "mode": "solo",
        "target": "claude",
        "conversation_id": None,
        **fields,
    }
    return json.dumps(message)


def test_a_conversation_id_beyond_sqlite_is_an_invalid_message(tmp_path: Path) -> None:
    with socket(tmp_path) as ws:
        for request_id, conversation_id in (("big", 2**63), ("huge", 10**30)):
            ws.send_text(turn_start(request_id, conversation_id=conversation_id))
            assert ws.receive_json() == {
                "type": "error",
                "message": "«conversation_id» ha de ser un enter positiu o null.",
                "code": "invalid",
                "request_id": request_id,
            }
        # Nothing was started, and the socket keeps working.
        ws.send_text(json.dumps({"type": "turn.subscribe", "request_id": "big"}))
        assert ws.receive_json() == {"type": "turn.unknown", "request_id": "big"}


def test_messages_with_text_that_is_not_utf8_are_invalid(tmp_path: Path) -> None:
    invalid: Message = {"type": "error", "message": INVALID_MESSAGE_TEXT, "code": "invalid"}
    with socket(tmp_path) as ws:
        ws.send_text(turn_start("sur").replace('"Hola"', '"Hola ' + LONE + ' món"'))
        assert ws.receive_json() == {**invalid, "request_id": "sur"}
        ws.send_text(turn_start("models", models={"claude": "x"}).replace('"x"', '"' + LONE + '"'))
        assert ws.receive_json() == {**invalid, "request_id": "models"}
        # Neither the type nor the request id are ever echoed when they are the text.
        ws.send_text('{"type": "turn.st' + LONE + 'art", "request_id": "r1"}')
        assert ws.receive_json() == {**invalid, "request_id": "r1"}
        ws.send_text(turn_start("RID").replace('"RID"', '"r' + LONE + '"'))
        assert ws.receive_json() == invalid
        ws.send_text('{"type": "ping", "t": 1, "x' + LONE + '": 0}')
        assert ws.receive_json() == invalid
        # Nothing was started, and the socket keeps working.
        ws.send_text(json.dumps({"type": "turn.subscribe", "request_id": "sur"}))
        assert ws.receive_json() == {"type": "turn.unknown", "request_id": "sur"}
        ws.send_text(json.dumps({"type": "ping", "t": 5}))
        assert ws.receive_json() == {"type": "pong", "t": 5}


def test_surrogate_pairs_are_valid_in_messages(tmp_path: Path) -> None:
    with socket(tmp_path) as ws:
        ws.send_text(turn_start("emoji").replace('"Hola"', '"Hola ' + PAIR + '"'))
        events = receive_until(ws, lambda m: m["type"] in ("turn.completed", "turn.failed"))
        assert events[-1]["type"] == "turn.completed"
