"""REST API, middlewares, login/sessions and static files (httpx + ASGITransport)."""

import asyncio
import functools
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyotp
import pytest
from fastapi import FastAPI

from agentic_os.config import Settings
from agentic_os.domain import AgentName, DebateOptions
from agentic_os.orchestrator.store import NewMessage
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.security.passwords import hash_password
from agentic_os.security.sessions import hash_token
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState
from agentic_os.server.middleware import CONTENT_SECURITY_POLICY
from agentic_os.storage import RuntimeSettings, SqliteStore

ORIGIN = "https://aos.example"
ORIGIN_HEADERS = {"origin": ORIGIN}
PASSWORD = "una frase de pas prou llarga"
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
T0 = datetime(2026, 9, 27, 12, 0, 5, tzinfo=UTC)
COOKIE = "__Host-aos_session"


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
    settings: Settings, providers: Mapping[AgentName, Provider] | None = None
) -> AsyncIterator[Harness]:
    clock = Clock()
    app = create_app(settings, providers=providers or fakes(), clock=clock)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            yield Harness(app, client, app.state.aos, clock)


@pytest.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    async with running(make_settings(tmp_path)) as harness:
        yield harness


def set_cookie_attributes(response: httpx.Response) -> dict[str, str]:
    """Attributes of the single Set-Cookie header, lowercase keys."""
    [header] = response.headers.get_list("set-cookie")
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
            "/api/settings", json={}, headers={"origin": "http://localhost:5173"}
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

    response = await h.client.post("/api/auth/logout", headers=ORIGIN_HEADERS)
    assert response.status_code == 204
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
    cookie = set_cookie_attributes(response)
    assert cookie["__name__"] == "aos_session"
    assert "secure" not in cookie
    assert "httponly" in cookie


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
    response = await h.client.put("/api/settings", json=wanted, headers=ORIGIN_HEADERS)
    assert response.status_code == 200
    assert response.json() == wanted
    assert (await h.client.get("/api/settings")).json() == wanted

    invalid = {**wanted, "debate": {"rounds": 9}}
    response = await h.client.put("/api/settings", json=invalid, headers=ORIGIN_HEADERS)
    assert response.status_code == 422
    assert response.json() == {"detail": "«debate.rounds» ha de ser un enter entre 0 i 4."}
    response = await h.client.put("/api/settings", content=b"nope", headers=ORIGIN_HEADERS)
    assert response.status_code == 422
    assert (await h.client.get("/api/settings")).json() == wanted


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
    }
    assert set(stats["totals"]["by_agent"]) == {"claude", "chatgpt"}
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
