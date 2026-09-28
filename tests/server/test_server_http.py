"""REST API, middlewares, login/sessions and static files (httpx + ASGITransport)."""

import asyncio
import functools
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyotp
import pytest
from fastapi import FastAPI
from starlette.types import Message

from agentic_os.config import Settings
from agentic_os.domain import AgentName, DebateOptions, ProviderMode, Usage
from agentic_os.fx import FxRate
from agentic_os.orchestrator.store import NewMessage, UsageRecord
from agentic_os.pricing import DEFAULT_PRICES, price_table
from agentic_os.providers.base import ModelInfo, Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.security.passwords import hash_password
from agentic_os.security.sessions import hash_token
from agentic_os.security.throttle import GLOBAL_KEY, client_key
from agentic_os.server import middleware
from agentic_os.server.app import create_app
from agentic_os.server.catalog import ModelCatalog
from agentic_os.server.deps import AppState
from agentic_os.server.fx_rates import FxFetcher
from agentic_os.server.middleware import CONTENT_SECURITY_POLICY
from agentic_os.storage import FxSettings, RuntimeSettings, SqliteStore

ORIGIN = "https://aos.example"
ORIGIN_HEADERS = {"origin": ORIGIN}
PASSWORD = "una frase de pas prou llarga"
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
T0 = datetime(2026, 9, 27, 12, 0, 5, tzinfo=UTC)
COOKIE = "__Host-aos_session"
DEVICE_COOKIE = "__Host-aos_device"
WRONG_PASSWORD = "incorrecta però llarga"


@functools.cache
def password_hash() -> str:
    return hash_password(PASSWORD)


class Clock:
    """Mutable fake clock (aware UTC)."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class Harness:
    app: FastAPI
    client: httpx.AsyncClient
    state: AppState
    clock: Clock

    def code(self) -> str:
        return pyotp.TOTP(SECRET).at(self.clock())

    async def add_owner(self) -> None:
        await self.state.store.set_owner(
            password_hash=password_hash(), totp_secret=SECRET, totp_last_step=0
        )

    async def login_session(self) -> str:
        """A valid session cookie without going through /login."""
        token = await self.state.sessions.create(self.clock(), ip=None, user_agent=None)
        self.client.cookies.set(self.state.cookie_name, token)
        return token

    async def login(self, *, password: str = PASSWORD, code: str | None = None) -> httpx.Response:
        return await self.client.post(
            "/api/auth/login",
            json={"password": password, "totp": self.code() if code is None else code},
            headers=ORIGIN_HEADERS,
        )


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
        "claude_mode": "fake",
        "chatgpt_mode": "fake",
    }
    values.update(overrides)
    return Settings(**{"_env_file": None, **values})


def fakes() -> dict[AgentName, Provider]:
    return {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }


@asynccontextmanager
async def running(
    settings: Settings,
    providers: Mapping[AgentName, Provider] | None = None,
    *,
    fx_fetcher: FxFetcher | None = None,
) -> AsyncIterator[Harness]:
    clock = Clock()
    app = create_app(settings, providers=providers or fakes(), clock=clock, fx_fetcher=fx_fetcher)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            yield Harness(app, client, app.state.aos, clock)


@pytest.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    async with running(make_settings(tmp_path)) as harness:
        yield harness


def set_cookie_attributes(response: httpx.Response, name: str = COOKIE) -> dict[str, str]:
    """Attributes of the single Set-Cookie header for ``name``, lowercase keys."""
    [header] = [
        h for h in response.headers.get_list("set-cookie") if h.split("=", 1)[0].strip() == name
    ]
    name_value, *attributes = [part.strip() for part in header.split(";")]
    result = {"__name__": name_value.split("=", 1)[0], "__value__": name_value.split("=", 1)[1]}
    for attribute in attributes:
        key, _, value = attribute.partition("=")
        result[key.lower()] = value
    return result


# -- middleware ------------------------------------------------------------------------


async def test_security_headers_on_every_response(h: Harness) -> None:
    for response in (
        await h.client.get("/api/health"),
        await h.client.get("/api/nope"),
        await h.client.get("/"),
        await h.client.post("/api/auth/logout"),  # 403 from the Origin check
    ):
        headers = response.headers
        assert headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["x-frame-options"] == "DENY"
        assert headers["permissions-policy"].startswith("camera=()")
        assert headers["cross-origin-opener-policy"] == "same-origin"
        assert headers["cross-origin-resource-policy"] == "same-origin"
        assert headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"
    api = await h.client.get("/api/health")
    assert api.headers["cache-control"] == "no-store"


def test_csp_matches_the_vite_preview_policy() -> None:
    vite = (Path(__file__).parents[2] / "web" / "vite.config.ts").read_text()
    for directive in CONTENT_SECURITY_POLICY.split("; "):
        assert f'"{directive}"' in vite


async def test_no_hsts_without_secure_cookies(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path, secure_cookies=False)) as h:
        response = await h.client.get("/api/health")
    assert "strict-transport-security" not in response.headers
    assert "content-security-policy" in response.headers


async def test_body_limit(h: Harness) -> None:
    big = b"x" * (1024 * 1024 + 1)
    response = await h.client.post("/api/auth/login", content=big, headers=ORIGIN_HEADERS)
    assert response.status_code == 413
    assert response.json() == {"detail": "La petició és massa gran (màxim 1 MiB)."}

    async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: counted as it arrives
        for _ in range(5):
            yield b"y" * (300 * 1024)

    await h.login_session()
    response = await h.client.put("/api/settings", content=chunks(), headers=ORIGIN_HEADERS)
    assert response.status_code == 413
    assert response.json()["detail"].startswith("La petició és massa gran")


async def test_a_slow_body_gets_408_and_closes_the_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(middleware, "BODY_TIMEOUT_SECONDS", 0.2)
    async with running(make_settings(tmp_path)) as h:
        await h.add_owner()

        async def trickle() -> AsyncIterator[bytes]:  # every chunk on time, the whole late
            for _ in range(40):
                yield b" "
                await asyncio.sleep(0.05)

        async def stalled() -> AsyncIterator[bytes]:  # never finishes
            yield b'{"password": "'
            await asyncio.sleep(3600)

        for body in (trickle(), stalled()):
            async with asyncio.timeout(5):
                response = await h.client.post(
                    "/api/auth/login", content=body, headers=ORIGIN_HEADERS
                )
            assert response.status_code == 408
            assert response.json() == {
                "detail": "La petició ha trigat massa a arribar. Torna-ho a provar."
            }
            assert response.headers["connection"] == "close"
        # Nothing was counted as a failed login, and a normal login still works.
        assert await h.state.store.get_throttle(GLOBAL_KEY) is None
        assert (await h.login()).status_code == 204


async def test_the_body_deadline_does_not_limit_the_handler(tmp_path: Path) -> None:
    app = FastAPI()
    app.add_middleware(middleware.BodyLimitMiddleware, timeout=0.1)

    @app.post("/slow")
    async def slow(body: dict[str, int]) -> dict[str, int]:
        await asyncio.sleep(0.3)  # longer than the body deadline
        return body

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.post("/slow", json={"a": 1})
    assert response.status_code == 200
    assert response.json() == {"a": 1}


async def test_a_client_disconnect_mid_body_is_a_quiet_400(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}  # the client went away before the body

    async def send(message: Message) -> None:
        sent.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"origin", ORIGIN.encode()),
            (b"content-type", b"application/json"),
            (b"content-length", b"1000"),
        ],
        "client": ("203.0.113.9", 50000),
        "server": ("testserver", 443),
    }
    with caplog.at_level(logging.INFO):
        await h.app(scope, receive, send)  # does not raise (uvicorn would log it)
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"]) == {
        "detail": "La connexió s'ha tancat abans de rebre la petició sencera."
    }
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR or r.exc_info]


async def test_origin_check_on_state_changing_requests(h: Harness) -> None:
    await h.login_session()
    cases = [
        ("POST", "/api/auth/logout"),
        ("PUT", "/api/settings"),
        ("PATCH", "/api/conversations/1"),
        ("DELETE", "/api/conversations/1"),
    ]
    for method, path in cases:
        for headers in ({}, {"origin": "https://evil.example"}, {"origin": "null"}):
            response = await h.client.request(method, path, headers=headers, json={})
            assert response.status_code == 403, (method, path, headers)
            assert response.json() == {"detail": "Origen no permès."}
    # Safe methods need no Origin.
    assert (await h.client.get("/api/settings")).status_code == 200


async def test_extra_origins_are_allowed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, extra_origins=["http://localhost:5173/"])
    async with running(settings) as h:
        await h.login_session()
        response = await h.client.put(
            "/api/settings", json={"revision": 0}, headers={"origin": "http://localhost:5173"}
        )
    assert response.status_code == 200


# -- auth ------------------------------------------------------------------------------


async def test_public_routes(h: Harness) -> None:
    assert (await h.client.get("/api/health")).json() == {"status": "ok"}
    state = await h.client.get("/api/auth/state")
    assert state.json() == {"authenticated": False, "setup_required": True}
    await h.add_owner()
    state = await h.client.get("/api/auth/state")
    assert state.json() == {"authenticated": False, "setup_required": False}


async def test_routes_require_a_session(h: Harness) -> None:
    cases = [
        ("GET", "/api/providers"),
        ("GET", "/api/models"),
        ("GET", "/api/pricing"),
        ("GET", "/api/spend"),
        ("GET", "/api/settings"),
        ("PUT", "/api/settings"),
        ("GET", "/api/conversations"),
        ("GET", "/api/conversations/1"),
        ("PATCH", "/api/conversations/1"),
        ("DELETE", "/api/conversations/1"),
        ("GET", "/api/stats"),
        ("POST", "/api/auth/logout"),
    ]
    h.client.cookies.set(COOKIE, "not-a-real-session-token-000000000")
    for method, path in cases:
        response = await h.client.request(method, path, headers=ORIGIN_HEADERS, json={})
        assert response.status_code == 401, (method, path)
        assert response.json() == {"detail": "Cal iniciar sessió."}


async def test_login_sets_a_secure_cookie_and_logout_clears_it(h: Harness) -> None:
    await h.add_owner()
    response = await h.login()
    assert response.status_code == 204
    cookie = set_cookie_attributes(response)
    assert cookie["__name__"] == COOKIE
    assert cookie["max-age"] == str(30 * 24 * 3600)
    assert cookie["path"] == "/"
    assert cookie["samesite"].lower() == "strict"
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "domain" not in cookie

    session = await h.state.store.get_session(hash_token(cookie["__value__"]))
    assert session is not None
    assert session.ip == "127.0.0.1"
    assert (await h.client.get("/api/auth/state")).json()["authenticated"] is True
    assert (await h.client.get("/api/settings")).status_code == 200

    device = set_cookie_attributes(response, DEVICE_COOKIE)
    assert device["max-age"] == str(365 * 24 * 3600)
    assert device["path"] == "/"
    assert device["samesite"].lower() == "strict"
    assert "httponly" in device
    assert "secure" in device
    assert "domain" not in device
    assert device["__value__"] != cookie["__value__"]
    assert await h.state.store.get_device(hash_token(device["__value__"])) is not None
    assert await h.state.store.get_device(device["__value__"]) is None  # only the hash

    response = await h.client.post("/api/auth/logout", headers=ORIGIN_HEADERS)
    assert response.status_code == 204
    assert all(not c.startswith(DEVICE_COOKIE) for c in response.headers.get_list("set-cookie"))
    assert h.client.cookies.get(DEVICE_COOKIE) == device["__value__"]  # kept at logout
    assert await h.state.store.get_device(hash_token(device["__value__"])) is not None
    cleared = set_cookie_attributes(response)
    assert cleared["__name__"] == COOKIE
    assert cleared["max-age"] == "0"
    assert await h.state.store.get_session(hash_token(cookie["__value__"])) is None
    h.client.cookies.set(COOKIE, cookie["__value__"])
    assert (await h.client.get("/api/settings")).status_code == 401


async def test_login_rotates_a_presented_session(h: Harness) -> None:
    await h.add_owner()
    old = await h.login_session()
    assert (await h.login()).status_code == 204
    assert await h.state.store.get_session(hash_token(old)) is None


async def test_dev_cookie_without_secure_flag(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path, secure_cookies=False)) as h:
        await h.add_owner()
        response = await h.login()
    assert response.status_code == 204
    cookie = set_cookie_attributes(response, "aos_session")
    assert cookie["__name__"] == "aos_session"
    assert "secure" not in cookie
    assert "httponly" in cookie
    device = set_cookie_attributes(response, "aos_device")
    assert "secure" not in device
    assert "httponly" in device


async def test_login_failures_are_generic(h: Harness) -> None:
    no_owner = await h.login()
    assert no_owner.status_code == 401
    assert no_owner.json() == {"detail": "Credencials incorrectes."}

    await h.add_owner()
    wrong_password = await h.login(password="una altra frase de pas")
    wrong_code = await h.login(code="000000" if h.code() != "000000" else "111111")
    for response in (wrong_password, wrong_code):
        assert response.status_code == 401
        assert response.json() == {"detail": "Credencials incorrectes."}
        assert "set-cookie" not in response.headers

    h.clock.advance(minutes=10)  # below the lockout threshold again after a reset
    await h.state.throttle.record_success("127.0.0.1")
    code = h.code()
    assert (await h.login(code=code)).status_code == 204
    replay = await h.login(code=code)
    assert replay.status_code == 401


async def test_login_malformed_body(h: Harness) -> None:
    for body in ({"password": PASSWORD}, {"password": 1, "totp": "123456"}, [1, 2]):
        response = await h.client.post("/api/auth/login", json=body, headers=ORIGIN_HEADERS)
        assert response.status_code == 422
        assert response.json() == {"detail": "Cal indicar la contrasenya i el codi TOTP."}
    response = await h.client.post("/api/auth/login", content=b"{", headers=ORIGIN_HEADERS)
    assert response.status_code == 422
    assert response.json() == {"detail": "El cos de la petició ha de ser JSON vàlid."}


async def test_login_lockout_with_retry_after(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path, login_max_failures=2)) as h:
        await h.add_owner()
        assert (await h.login(password="incorrecta però llarga")).status_code == 401
        assert (await h.login(password="incorrecta però llarga")).status_code == 401
        locked = await h.login()  # right credentials, but locked out
        assert locked.status_code == 429
        assert locked.headers["retry-after"] == "5"
        body = locked.json()
        assert body["retry_after"] == 5
        assert body["detail"] == "Massa intents fallits. Torna-ho a provar d'aquí a 5 segons."

        h.clock.advance(seconds=6)
        assert (await h.login(password="incorrecta però llarga")).status_code == 401
        locked = await h.login()
        assert locked.status_code == 429
        assert locked.json()["retry_after"] == 10

        h.clock.advance(seconds=31)
        assert (await h.login()).status_code == 204


async def test_parallel_attempts_cannot_bypass_the_lockout(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path, login_max_failures=2)) as h:
        await h.add_owner()
        responses = await asyncio.gather(
            *(h.login(password=f"contrasenya incorrecta {i}") for i in range(6))
        )
    assert sorted(r.status_code for r in responses) == [401, 401, 429, 429, 429, 429]


def client_from(h: Harness, ip: str) -> httpx.AsyncClient:
    """A client with its own address and cookie jar."""
    transport = httpx.ASGITransport(app=h.app, client=(ip, 50000))
    return httpx.AsyncClient(transport=transport, base_url="https://testserver")


async def login_from(
    h: Harness, client: httpx.AsyncClient, *, password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        "/api/auth/login", json={"password": password, "totp": h.code()}, headers=ORIGIN_HEADERS
    )


async def lock_globally(h: Harness, attacker: httpx.AsyncClient) -> None:
    """One anonymous address follows Retry-After until the global key is locked."""
    for _ in range(50):
        response = await login_from(h, attacker, password=WRONG_PASSWORD)
        if response.status_code == 429:
            h.clock.advance(seconds=response.json()["retry_after"])
            continue
        state = await h.state.store.get_throttle(GLOBAL_KEY)
        if state is not None and state.locked_until is not None and state.locked_until > h.clock():
            return
    pytest.fail("the global key never locked")


async def test_a_known_device_is_not_locked_out_by_anonymous_failures(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path, login_max_failures=2)) as h:
        await h.add_owner()
        async with (
            client_from(h, "198.51.100.7") as owner,
            client_from(h, "203.0.113.9") as attacker,
            client_from(h, "192.0.2.1") as stranger,
        ):
            assert (await login_from(h, owner)).status_code == 204
            device = owner.cookies[DEVICE_COOKIE]
            assert (await owner.post("/api/auth/logout", headers=ORIGIN_HEADERS)).status_code == 204
            h.clock.advance(seconds=30)

            await lock_globally(h, attacker)
            # Unknown clients are locked out, even with the right credentials...
            assert (await login_from(h, stranger)).status_code == 429
            stranger.cookies.set(DEVICE_COOKIE, "a-forged-device-token-00000000000000000000")
            assert (await login_from(h, stranger)).status_code == 429
            # ...but not the owner's browser, from any address.
            async with client_from(h, "192.0.2.200") as roaming:
                roaming.cookies.set(DEVICE_COOKIE, device)
                response = await login_from(h, roaming)
            assert response.status_code == 204
            # The device token was replaced, and the global lock is still on.
            new_device = set_cookie_attributes(response, DEVICE_COOKIE)["__value__"]
            assert new_device != device
            assert await h.state.store.get_device(hash_token(device)) is None
            assert await h.state.store.get_device(hash_token(new_device)) is not None
            assert (await login_from(h, stranger)).status_code == 429


async def test_a_known_device_has_its_own_failure_counter(tmp_path: Path) -> None:
    async with running(make_settings(tmp_path, login_max_failures=2)) as h:
        await h.add_owner()
        async with client_from(h, "198.51.100.7") as owner:
            assert (await login_from(h, owner)).status_code == 204
            h.clock.advance(seconds=30)
            for _ in range(2):
                wrong = await login_from(h, owner, password=WRONG_PASSWORD)
                assert wrong.status_code == 401
            locked = await login_from(h, owner)  # right credentials, device locked
            assert locked.status_code == 429
            assert locked.json()["retry_after"] == 5
            # The address and global counters were not touched by the device.
            assert await h.state.store.get_throttle(client_key("198.51.100.7")) is None
            assert await h.state.store.get_throttle(GLOBAL_KEY) is None
            h.clock.advance(seconds=6)
            assert (await login_from(h, owner)).status_code == 204


async def test_session_idle_and_absolute_expiry(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, session_idle_hours=1, session_max_days=1)
    async with running(settings) as h:
        await h.login_session()
        h.clock.advance(minutes=61)
        assert (await h.client.get("/api/settings")).status_code == 401

        await h.login_session()
        for _ in range(47):  # activity every 30 minutes keeps it alive...
            h.clock.advance(minutes=30)
            assert (await h.client.get("/api/settings")).status_code == 200
        h.clock.advance(minutes=31)  # ...until the absolute lifetime (24 h)
        assert (await h.client.get("/api/settings")).status_code == 401


# -- API -------------------------------------------------------------------------------


async def test_providers(h: Harness) -> None:
    await h.login_session()
    response = await h.client.get("/api/providers")
    assert response.json() == [
        {
            "agent": agent,
            "mode": "fake",
            "available": True,
            "model": f"fake-{agent}",
            "detail": "Mode demostració",
            "limits": [],
        }
        for agent in ("claude", "chatgpt")
    ]


async def test_settings_roundtrip_and_validation(h: Harness) -> None:
    await h.login_session()
    assert (await h.client.get("/api/settings")).json() == RuntimeSettings().to_wire()

    wanted = RuntimeSettings(
        default_mode="solo",
        default_target="chatgpt",
        debate=DebateOptions(rounds=3, consensus_threshold=90, synthesizer="chatgpt"),
        use_cache=False,
        compaction_threshold_tokens=9000,
    ).to_wire()
    assert wanted["revision"] == 0  # the revision the edit is based on
    response = await h.client.put("/api/settings", json=wanted, headers=ORIGIN_HEADERS)
    assert response.status_code == 200
    saved = {**wanted, "revision": 1}
    assert response.json() == saved
    assert (await h.client.get("/api/settings")).json() == saved

    invalid = {**saved, "debate": {"rounds": 9}}
    response = await h.client.put("/api/settings", json=invalid, headers=ORIGIN_HEADERS)
    assert response.status_code == 422
    assert response.json() == {"detail": "«debate.rounds» ha de ser un enter entre 0 i 4."}
    response = await h.client.put("/api/settings", content=b"nope", headers=ORIGIN_HEADERS)
    assert response.status_code == 422
    assert (await h.client.get("/api/settings")).json() == saved


async def add_conversation(h: Harness, title: str, question: str) -> int:
    store = h.state.store
    conversation_id = await store.create_conversation(title)
    turn_id = await store.add_message(
        NewMessage(conversation_id, "question", question, final=True, meta={"mode": "solo"})
    )
    await store.add_message(
        NewMessage(conversation_id, "answer", "Resposta", turn_id=turn_id, agent="claude")
    )
    h.clock.advance(seconds=1)
    return conversation_id


async def test_conversations(h: Harness) -> None:
    await h.login_session()
    first = await add_conversation(h, "Primera", "Hola?")
    second = await add_conversation(h, "Segona", "Què tal?")

    listed = (await h.client.get("/api/conversations")).json()
    assert [c["id"] for c in listed] == [second, first]
    assert listed[0] == {
        "id": second,
        "title": "Segona",
        "created_at": listed[0]["created_at"],
        "updated_at": listed[0]["updated_at"],
        "last_mode": "solo",
        "message_count": 2,
    }
    page = await h.client.get("/api/conversations", params={"limit": 1, "before": second})
    assert [c["id"] for c in page.json()] == [first]
    for params in ({"limit": 0}, {"limit": 201}, {"before": "x"}):
        response = await h.client.get("/api/conversations", params=params)
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)
        assert response.json()["detail"].startswith("Dades no vàlides")

    detail = (await h.client.get(f"/api/conversations/{first}")).json()
    assert detail["summary"] is None
    assert [m["kind"] for m in detail["messages"]] == ["question", "answer"]
    assert detail["messages"][0]["content"] == "Hola?"

    renamed = await h.client.patch(
        f"/api/conversations/{first}", json={"title": "  Nom nou "}, headers=ORIGIN_HEADERS
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Nom nou"
    empty = await h.client.patch(
        f"/api/conversations/{first}", json={"title": " "}, headers=ORIGIN_HEADERS
    )
    assert empty.status_code == 422
    assert empty.json() == {"detail": "El títol no pot estar buit."}
    missing_title = await h.client.patch(
        f"/api/conversations/{first}", json={}, headers=ORIGIN_HEADERS
    )
    assert missing_title.status_code == 422

    deleted = await h.client.delete(f"/api/conversations/{first}", headers=ORIGIN_HEADERS)
    assert deleted.status_code == 204
    for response in (
        await h.client.get(f"/api/conversations/{first}"),
        await h.client.delete(f"/api/conversations/{first}", headers=ORIGIN_HEADERS),
        await h.client.patch(
            f"/api/conversations/{first}", json={"title": "x"}, headers=ORIGIN_HEADERS
        ),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "La conversa no existeix."}


async def test_stats_shape(h: Harness) -> None:
    await h.login_session()
    response = await h.client.get("/api/stats", params={"days": 7})
    assert response.status_code == 200
    stats = response.json()
    assert stats["days"] == 7
    assert set(stats) == {
        "days",
        "totals",
        "savings",
        "daily",
        "savings_daily",
        "latency",
        "turns",
        "consensus",
        "costs",
        "month",
    }
    assert set(stats["totals"]["by_agent"]) == {"claude", "chatgpt"}
    assert stats["costs"]["fx"] == {"eur_per_usd": 0.86, "as_of": None, "source": "manual"}
    assert stats["month"]["month"] == "2026-09"
    assert stats["savings"]["cost_usd"] is None
    assert set(stats["turns"]) == {"solo", "duel", "debate"}
    for days in (0, 366):
        assert (await h.client.get("/api/stats", params={"days": days})).status_code == 422


async def test_unknown_api_paths_are_json_404(h: Harness) -> None:
    for method in ("GET", "POST"):
        response = await h.client.request(method, "/api/does-not-exist", headers=ORIGIN_HEADERS)
        assert response.status_code == 404
        assert response.json() == {"detail": "No s'ha trobat."}


# -- static files ----------------------------------------------------------------------


async def test_without_a_build_explains_how_to_build(h: Harness) -> None:
    response = await h.client.get("/")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert "npm run build" in response.text
    assert "AOS_WEB_DIST" in response.text


async def test_serves_the_spa_with_cache_headers(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SPA</title>")
    (dist / "assets" / "app-3f9a.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("secret")

    async with running(make_settings(tmp_path, web_dist=dist)) as h:
        for path in ("/", "/conversa/12", "/index.html", "/../secret.txt", "/assets"):
            response = await h.client.get(path)
            assert response.status_code == 200, path
            assert response.text == "<!doctype html><title>SPA</title>", path
            assert response.headers["cache-control"] == "no-cache"
            assert "content-security-policy" in response.headers

        asset = await h.client.get("/assets/app-3f9a.js")
        assert asset.status_code == 200
        assert asset.text == "console.log(1)"
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        etag = asset.headers["etag"]
        cached = await h.client.get("/assets/app-3f9a.js", headers={"if-none-match": etag})
        assert cached.status_code == 304

        missing = await h.client.get("/assets/missing.js")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "No s'ha trobat."}

        favicon = await h.client.get("/favicon.svg")
        assert favicon.text == "<svg/>"
        assert favicon.headers["cache-control"] == "no-cache"

        head = await h.client.head("/conversa/3")
        assert head.status_code == 200

        post = await h.client.post("/conversa/3", headers=ORIGIN_HEADERS)
        assert post.status_code == 405
        assert post.json() == {"detail": "Mètode no permès."}

        api = await h.client.get("/api/unknown")
        assert api.status_code == 404
        assert api.headers["content-type"] == "application/json"


async def test_startup_purges_expired_sessions(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    async with await SqliteStore.open(settings.db_path) as store:
        await store.create_session(
            "a" * 64,
            created_at=T0 - timedelta(days=40),
            expires_at=T0 - timedelta(days=10),
            ip=None,
            user_agent=None,
        )
    async with running(settings) as h:
        for _ in range(20):
            if await h.state.store.get_session("a" * 64) is None:
                break
            await asyncio.sleep(0.01)
        assert await h.state.store.get_session("a" * 64) is None


async def test_shutdown_closes_the_providers(tmp_path: Path) -> None:
    providers: dict[AgentName, FakeProvider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    async with running(make_settings(tmp_path), providers):
        assert not providers["claude"].closed
    assert providers["claude"].closed
    assert providers["chatgpt"].closed


# -- models, prices, exchange rate and spend -------------------------------------------

MONEY_SETTINGS: dict[str, Any] = {
    "models": {"claude": "claude-opus-5[1m]", "chatgpt": None},
    "fast_models": {"claude": None, "chatgpt": "gpt-6-luna"},
    "prices": {"gpt-7-nova": {"input": 3, "output": 12, "cache_read": 0.3, "cache_write": 0}},
    "fx": {"mode": "manual", "eur_per_usd": 0.9},
    "budgets_eur": {"claude": 20, "chatgpt": None},
    "plans_eur": {"claude": None, "chatgpt": 23},
}


async def test_settings_with_models_prices_and_money(h: Harness) -> None:
    await h.login_session()
    response = await h.client.put(
        "/api/settings", json={**MONEY_SETTINGS, "revision": 0}, headers=ORIGIN_HEADERS
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved == {**RuntimeSettings().to_wire(), **saved}
    assert saved["revision"] == 1
    assert saved["models"] == MONEY_SETTINGS["models"]
    assert saved["prices"]["gpt-7-nova"] == {
        "input": 3.0,
        "output": 12.0,
        "cache_read": 0.3,
        "cache_write": 0.0,
    }
    assert saved["budgets_eur"] == {"claude": 20.0, "chatgpt": None}
    assert (await h.client.get("/api/settings")).json() == saved

    for patch, detail in (
        (
            {"models": {"claude": "opus 5"}},
            "«models.claude» ha de ser un identificador de model vàlid: fins a 100 lletres, "
            "xifres o els signes . _ : / @ [ ] -, sense espais.",
        ),
        ({"fx": {"eur_per_usd": 7}}, "«fx.eur_per_usd» ha de ser un nombre entre 0,2 i 5."),
        (
            {"plans_eur": {"chatgpt": -1}},
            "«plans_eur.chatgpt» ha de ser un nombre entre 0 i 100.000.",
        ),
    ):
        response = await h.client.put(
            "/api/settings", json={**saved, **patch}, headers=ORIGIN_HEADERS
        )
        assert response.status_code == 422
        assert response.json() == {"detail": detail}
    # JSON NaN is not a number the settings accept.
    response = await h.client.put(
        "/api/settings",
        content=b'{"revision": 1, "budgets_eur": {"claude": NaN}}',
        headers={**ORIGIN_HEADERS, "content-type": "application/json"},
    )
    assert response.status_code == 422
    assert (await h.client.get("/api/settings")).json() == saved


async def test_settings_refuse_huge_integers_and_ambiguous_prices(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    await h.login_session()
    huge = "1" + "0" * 400  # a valid JSON integer, too large for a float
    price = {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0}
    cases: list[tuple[bytes, str]] = [
        (
            f'{{"revision": 0, "budgets_eur": {{"claude": {huge}}}}}'.encode(),
            "«budgets_eur.claude» ha de ser un nombre entre 0 i 100.000.",
        ),
        (
            f'{{"revision": 0, "plans_eur": {{"chatgpt": {huge}}}}}'.encode(),
            "«plans_eur.chatgpt» ha de ser un nombre entre 0 i 100.000.",
        ),
        (
            f'{{"revision": 0, "fx": {{"eur_per_usd": {huge}}}}}'.encode(),
            "«fx.eur_per_usd» ha de ser un nombre entre 0,2 i 5.",
        ),
        (
            f'{{"revision": 0, "prices": {{"m": {{"input": {huge}, "output": 1, '
            f'"cache_read": 0, "cache_write": 0}}}}}}'.encode(),
            "«prices.m»: Preu invàlid per a «input»: ha de ser un nombre ≥ 0.",
        ),
        (
            json.dumps({"revision": 0, "prices": {"openai/": price}}).encode(),
            "«prices»: «openai/» no identifica cap model (sense el prefix del proveïdor, la "
            "data o el context no en queda res).",
        ),
        (
            json.dumps(
                {"revision": 0, "prices": {"Claude-Opus-5": price, "claude-opus-5": price}}
            ).encode(),
            "«prices»: «Claude-Opus-5» i «claude-opus-5» són el mateix model "
            "(«claude-opus-5»). Deixa'n només un.",
        ),
    ]
    with caplog.at_level(logging.ERROR):
        for body, detail in cases:
            response = await h.client.put(
                "/api/settings",
                content=body,
                headers={**ORIGIN_HEADERS, "content-type": "application/json"},
            )
            assert response.status_code == 422, detail
            assert response.json() == {"detail": detail}
    assert not caplog.records  # no internal error
    assert (await h.client.get("/api/settings")).json() == RuntimeSettings().to_wire()


async def test_pricing_lists_the_table_the_engine_charges_with(h: Harness) -> None:
    await h.login_session()
    custom = {"input": 4, "output": 20, "cache_read": 0.4, "cache_write": 5}
    response = await h.client.put(
        "/api/settings",
        json={"revision": 0, "prices": {"Claude-Opus-5-20260101": custom, "gpt-7-nova": custom}},
        headers=ORIGIN_HEADERS,
    )
    assert response.status_code == 200
    runtime = await h.state.store.get_runtime_settings()
    rows = (await h.client.get("/api/pricing")).json()["prices"]
    fields = ("model", "input", "output", "cache_read", "cache_write", "source")
    assert [{field: row[field] for field in fields} for row in rows] == [
        {"model": entry.model, **entry.price.to_wire(), "source": entry.source}
        for entry in sorted(price_table(runtime.prices).values(), key=lambda e: e.model)
    ]


async def test_answers_given_before_the_body_ask_to_close_the_connection(h: Harness) -> None:
    await h.add_owner()
    body = {"password": WRONG_PASSWORD, "totp": "000000"}
    # 403 from the Origin check and 401 without a session: the body was never read.
    for response in (
        await h.client.post("/api/auth/login", json=body),
        await h.client.put("/api/settings", json={}, headers=ORIGIN_HEADERS),
        await h.client.request("GET", "/api/health", content=b"ignored"),
    ):
        assert response.headers["connection"] == "close", response.request.url
    # Requests without a body, or whose body was read, keep the connection.
    assert "connection" not in (await h.client.get("/api/health")).headers
    assert (await h.login(password=WRONG_PASSWORD)).status_code == 401
    assert "connection" not in (await h.login(password=WRONG_PASSWORD)).headers
    await h.login_session()
    response = await h.client.put("/api/settings", json={"revision": 0}, headers=ORIGIN_HEADERS)
    assert response.status_code == 200
    assert "connection" not in response.headers


async def test_models_catalog(h: Harness) -> None:
    await h.login_session()
    response = await h.client.get("/api/models")
    assert response.status_code == 200
    catalog = response.json()
    assert set(catalog) == {"claude", "chatgpt"}
    assert catalog["claude"] == {
        "mode": "fake",
        "default_model": "fake-claude",
        "fast_model": "fake-claude-mini",
        "models": [m.to_wire() for m in await FakeProvider("claude").list_models()],
        "live": True,
    }
    await h.state.store.put_runtime_settings(RuntimeSettings.from_wire(MONEY_SETTINGS))
    catalog = (await h.client.get("/api/models")).json()
    assert catalog["claude"]["default_model"] == "claude-opus-5[1m]"
    assert catalog["claude"]["fast_model"] == "fake-claude-mini"
    assert catalog["chatgpt"]["default_model"] == "fake-chatgpt"
    assert catalog["chatgpt"]["fast_model"] == "gpt-6-luna"


class CountingModels(FakeProvider):
    def __init__(self, agent: AgentName) -> None:
        super().__init__(agent, chunk_delay=0)
        self.refreshes: list[bool] = []
        self.gate: asyncio.Event | None = None

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        self.refreshes.append(refresh)
        if self.gate is not None:
            await self.gate.wait()
        return await super().list_models()


async def test_models_are_cached_and_refresh_bypasses_the_caches(tmp_path: Path) -> None:
    claude = CountingModels("claude")
    providers: dict[AgentName, Provider] = {"claude": claude, "chatgpt": fakes()["chatgpt"]}
    async with running(make_settings(tmp_path), providers) as h:
        await h.login_session()
        await h.client.get("/api/models")
        await h.client.get("/api/models")
        assert claude.refreshes == [False]
        assert (await h.client.get("/api/models", params={"refresh": 1})).status_code == 200
        assert claude.refreshes == [False, True]


async def test_a_slow_model_list_does_not_block_the_catalog(tmp_path: Path) -> None:
    claude = CountingModels("claude")
    claude.gate = asyncio.Event()
    providers: dict[AgentName, Provider] = {"claude": claude, "chatgpt": fakes()["chatgpt"]}
    async with running(make_settings(tmp_path), providers) as h:
        await h.login_session()
        await h.state.catalog.aclose()
        h.state.catalog = ModelCatalog(providers, h.state.settings, wait_seconds=0.05)
        catalog = (await h.client.get("/api/models")).json()
        assert catalog["claude"]["live"] is False
        assert catalog["claude"]["models"] == [
            {
                "id": "fake-claude",
                "label": "fake-claude",
                "description": "",
                "is_default": True,
                "context_window": None,
            }
        ]
        assert catalog["chatgpt"]["live"] is True
        claude.gate.set()
        await asyncio.sleep(0.01)
        catalog = (await h.client.get("/api/models")).json()
        assert catalog["claude"]["live"] is True
        assert len(catalog["claude"]["models"]) == 2
        await h.state.catalog.aclose()


async def test_pricing(h: Harness) -> None:
    await h.login_session()
    pricing = (await h.client.get("/api/pricing")).json()
    assert pricing["fx"] == {"eur_per_usd": 0.86, "as_of": None, "source": "manual"}
    assert len(pricing["prices"]) == len(DEFAULT_PRICES)
    assert {p["source"] for p in pricing["prices"]} == {"default"}
    models = [p["model"] for p in pricing["prices"]]
    assert models == sorted(models)

    overrides = {
        **MONEY_SETTINGS,
        "prices": {
            **MONEY_SETTINGS["prices"],
            "gpt-6-luna": {"input": 0.2, "output": 1, "cache_read": 0.02, "cache_write": 0},
        },
    }
    await h.state.store.put_runtime_settings(RuntimeSettings.from_wire(overrides))
    pricing = (await h.client.get("/api/pricing")).json()
    assert pricing["fx"] == {"eur_per_usd": 0.9, "as_of": None, "source": "manual"}
    by_model = {p["model"]: p for p in pricing["prices"]}
    assert len(by_model) == len(DEFAULT_PRICES) + 1
    assert by_model["gpt-6-luna"] == {
        "model": "gpt-6-luna",
        "key": "gpt-6-luna",
        "input": 0.2,
        "output": 1.0,
        "cache_read": 0.02,
        "cache_write": 0.0,
        "source": "custom",
        "default": DEFAULT_PRICES["gpt-6-luna"].to_wire(),
    }
    assert by_model["gpt-7-nova"]["source"] == "custom"
    assert by_model["gpt-7-nova"]["default"] is None
    assert by_model["gpt-6-sol"]["source"] == "default"
    assert by_model["gpt-6-sol"]["default"] is None


async def test_spend_of_the_current_month(h: Harness) -> None:
    await h.login_session()
    store = h.state.store
    await store.put_runtime_settings(RuntimeSettings.from_wire(MONEY_SETTINGS))
    calls: tuple[tuple[AgentName, ProviderMode, float], ...] = (
        ("claude", "api", 4.0),
        ("chatgpt", "cli", 10.0),
    )
    for agent, mode, cost in calls:
        await store.record_usage(
            UsageRecord(
                conversation_id=None,
                turn_id=None,
                agent=agent,
                provider_mode=mode,
                model="m",
                purpose="answer",
                usage=Usage(100, 50, cost_usd=cost),
                latency_ms=10,
                ttft_ms=None,
                ok=True,
            )
        )
    spend = (await h.client.get("/api/spend")).json()
    assert spend == {
        "month": "2026-09",
        "fx": {"eur_per_usd": 0.9, "as_of": None, "source": "manual"},
        "by_agent": {
            "claude": {
                "api_usd": 4.0,
                "equivalent_usd": 0.0,
                "unpriced_calls": 0,
                "budget_eur": 20.0,
                "budget_used": 0.18,
                "plan_eur": None,
                "plan_value": None,
            },
            "chatgpt": {
                "api_usd": 0.0,
                "equivalent_usd": 10.0,
                "unpriced_calls": 0,
                "budget_eur": None,
                "budget_used": None,
                "plan_eur": 23.0,
                "plan_value": 0.391304,
            },
        },
    }
    assert (await h.client.get("/api/stats")).json()["month"] == spend


async def wait_for(condition: Any, timeout: float = 2.0) -> None:
    for _ in range(int(timeout / 0.01)):
        if await condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


async def test_startup_refreshes_the_ecb_rate(tmp_path: Path) -> None:
    ecb = FxRate(eur_per_usd=0.8547, as_of=date(2026, 9, 25), source="ecb")
    calls: list[int] = []

    async def fetch() -> FxRate:
        calls.append(1)
        return ecb

    async with running(make_settings(tmp_path), fx_fetcher=fetch) as h:
        await h.login_session()

        async def stored() -> bool:
            return await h.state.store.get_ecb_rate() is not None

        await wait_for(stored)
        pricing = (await h.client.get("/api/pricing")).json()
        assert pricing["fx"] == {"eur_per_usd": 0.8547, "as_of": "2026-09-25", "source": "ecb"}
    # A restart within 12 hours reuses the stored rate.
    async with running(make_settings(tmp_path), fx_fetcher=fetch) as h:
        await asyncio.sleep(0.05)
        assert (await h.state.store.current_fx(h.clock())).source == "ecb"
    assert calls == [1]


async def test_a_failed_download_keeps_the_manual_rate(h: Harness, blocked_ecb: Any) -> None:
    # Without fx_fetcher the app uses agentic_os.fx.fetch_ecb_rate (blocked in tests).
    async def tried() -> bool:
        return bool(blocked_ecb.calls)

    await wait_for(tried)
    await h.login_session()
    pricing = (await h.client.get("/api/pricing")).json()
    assert pricing["fx"] == {"eur_per_usd": 0.86, "as_of": None, "source": "manual"}


async def test_switching_to_auto_fetches_the_rate_now(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    async with await SqliteStore.open(settings.db_path) as store:
        await store.put_runtime_settings(RuntimeSettings(fx=FxSettings(mode="manual")))
    calls: list[int] = []

    async def fetch() -> FxRate:
        calls.append(1)
        return FxRate(eur_per_usd=0.85, as_of=date(2026, 9, 26), source="ecb")

    async with running(settings, fx_fetcher=fetch) as h:
        await h.login_session()
        await asyncio.sleep(0.05)
        assert calls == []  # manual mode: never downloaded
        current = (await h.client.get("/api/settings")).json()
        response = await h.client.put(
            "/api/settings",
            json={**current, "fx": {**current["fx"], "mode": "auto"}},
            headers=ORIGIN_HEADERS,
        )
        assert response.status_code == 200

        async def fetched() -> bool:
            return bool(calls)

        await wait_for(fetched)

        async def stored() -> bool:
            return (await h.state.store.current_fx(h.clock())).source == "ecb"

        await wait_for(stored)
