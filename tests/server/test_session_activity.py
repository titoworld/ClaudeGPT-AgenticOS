"""What counts as the owner's activity (N7).

The idle timeout must end a session nobody uses, even in a tab that stays open: the
requests the SPA makes by itself (marked ``X-AOS-Background: 1``), the WebSocket
handshake of a reconnection, pings and the resubscriptions after a reconnection
check the session read-only. The owner's actions (opening a conversation, saving,
``turn.start``, ``turn.cancel``...) keep refreshing it.
"""

import functools
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from agentic_os import fx
from agentic_os.config import Settings
from agentic_os.domain import AgentName
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.security.sessions import hash_token
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState

pytestmark = pytest.mark.filterwarnings("ignore:Using `httpx` with:DeprecationWarning")

ORIGIN = "https://aos.example"
COOKIE = "__Host-aos_session"
T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
BACKGROUND = {"X-AOS-Background": "1"}
ROUTES = (
    "/api/auth/state",
    "/api/providers",
    "/api/models",
    "/api/pricing",
    "/api/spend",
    "/api/settings",
    "/api/conversations",
    "/api/stats",
)


async def no_ecb() -> fx.FxRate:
    """The ECB rate is never downloaded in tests."""
    raise fx.FxError("Sense xarxa als tests.")


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


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


@dataclass
class Harness:
    client: httpx.AsyncClient
    state: AppState
    clock: Clock
    token: str

    async def last_seen(self) -> datetime | None:
        record = await self.state.store.get_session(hash_token(self.token))
        return None if record is None else record.last_seen_at


@asynccontextmanager
async def running(settings: Settings) -> AsyncIterator[Harness]:
    clock = Clock()
    app = create_app(settings, providers=fakes(), clock=clock, fx_fetcher=no_ecb)
    async with app.router.lifespan_context(app):
        state: AppState = app.state.aos
        token = await state.sessions.create(clock(), ip=None, user_agent=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            client.cookies.set(state.cookie_name, token)
            yield Harness(client, state, clock, token)


def test_the_header_name_is_the_documented_one() -> None:
    from agentic_os.server.deps import BACKGROUND_HEADER

    assert BACKGROUND_HEADER.lower() == "x-aos-background"
    protocol = (Path(__file__).parents[2] / "docs" / "PROTOCOL.md").read_text()
    assert "`X-AOS-Background: 1`" in protocol


async def test_background_requests_check_the_session_without_refreshing_it(
    tmp_path: Path,
) -> None:
    async with running(make_settings(tmp_path)) as h:
        h.clock.advance(minutes=10)
        for path in ROUTES:
            response = await h.client.get(path, headers=BACKGROUND)
            assert response.status_code == 200, path
        state = await h.client.get("/api/auth/state", headers=BACKGROUND)
        assert state.json()["authenticated"] is True
        assert await h.last_seen() == T0

        # The owner opens a conversation list, a drawer...: that is activity.
        assert (await h.client.get("/api/settings")).status_code == 200
        assert await h.last_seen() == T0 + timedelta(minutes=10)


async def test_background_requests_do_not_keep_an_idle_session_alive(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path, session_idle_hours=1)) as h:
        for _ in range(2):
            h.clock.advance(minutes=29)
            assert (await h.client.get("/api/spend", headers=BACKGROUND)).status_code == 200
        h.clock.advance(minutes=3)  # an hour since the last activity
        assert (await h.client.get("/api/spend", headers=BACKGROUND)).status_code == 401
        state = await h.client.get("/api/auth/state", headers=BACKGROUND)
        assert state.json()["authenticated"] is False


async def test_owner_requests_keep_the_session_alive(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path, session_idle_hours=1)) as h:
        for _ in range(4):
            h.clock.advance(minutes=29)
            assert (await h.client.get("/api/spend")).status_code == 200
        assert await h.last_seen() == T0 + timedelta(minutes=4 * 29)


@pytest.mark.parametrize("value", ["0", "true", ""])
async def test_only_the_value_1_marks_a_background_request(tmp_path: Path, value: str) -> None:
    async with running(make_settings(tmp_path)) as h:
        h.clock.advance(minutes=10)
        response = await h.client.get("/api/spend", headers={"X-AOS-Background": value})
        assert response.status_code == 200
        assert await h.last_seen() == T0 + timedelta(minutes=10)


async def test_a_background_logout_still_ends_the_session(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path)) as h:
        response = await h.client.post("/api/auth/logout", headers={**BACKGROUND, "origin": ORIGIN})
        assert response.status_code == 204
        assert await h.last_seen() is None


# -- WebSocket -------------------------------------------------------------------------


@contextmanager
def app_client(tmp_path: Path, clock: Clock) -> Iterator[tuple[TestClient, AppState, str]]:
    app = create_app(make_settings(tmp_path), providers=fakes(), clock=clock, fx_fetcher=no_ecb)
    with TestClient(app, base_url="https://testserver") as client:
        state: AppState = app.state.aos
        assert client.portal is not None
        token = client.portal.call(
            functools.partial(state.sessions.create, clock(), ip=None, user_agent=None)
        )
        yield client, state, token


def connect(client: TestClient, token: str) -> WebSocketTestSession:
    headers = {"origin": ORIGIN, "cookie": f"{COOKIE}={token}"}
    return client.websocket_connect("/api/ws", headers=headers)


def last_seen(client: TestClient, state: AppState, token: str) -> datetime:
    assert client.portal is not None
    record = client.portal.call(state.store.get_session, hash_token(token))
    assert record is not None
    return record.last_seen_at


def test_reconnections_do_not_keep_an_idle_session_alive(tmp_path: Path) -> None:
    clock = Clock()
    with app_client(tmp_path, clock) as (client, state, token):
        step = state.sessions.idle_timeout / 3
        for _ in range(2):
            clock.now += step
            with connect(client, token) as ws:
                assert ws.receive_json()["type"] == "hello"
                ws.send_json({"type": "ping", "t": 1})
                assert ws.receive_json() == {"type": "pong", "t": 1}
        assert last_seen(client, state, token) == T0
        clock.now += step  # the idle timeout since the login
        with connect(client, token) as ws, pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_resubscribing_is_not_activity_but_the_owner_actions_are(tmp_path: Path) -> None:
    clock = Clock()
    with app_client(tmp_path, clock) as (client, state, token), connect(client, token) as ws:
        ws.receive_json()
        clock.now += timedelta(minutes=10)
        ws.send_json({"type": "turn.subscribe", "request_id": "old", "after_seq": 0})
        assert ws.receive_json() == {"type": "turn.unknown", "request_id": "old"}
        assert last_seen(client, state, token) == T0

        ws.send_json({"type": "turn.cancel", "request_id": "old"})
        assert ws.receive_json() == {"type": "turn.unknown", "request_id": "old"}
        assert last_seen(client, state, token) == T0 + timedelta(minutes=10)

        clock.now += timedelta(minutes=10)
        ws.send_json({"type": "turn.start", "request_id": "r1", "text": "Hola", "mode": "solo"})
        while ws.receive_json()["type"] != "turn.completed":
            pass
        assert last_seen(client, state, token) == T0 + timedelta(minutes=20)
