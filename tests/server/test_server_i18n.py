"""The server's texts in each client's language (docs/adr/0011-internationalization.md): an
HTTP request's ``Accept-Language``, a WebSocket's ``?lang=``. What is kept once for every
client (the agents' status, the model lists) is made in the language of each."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from agentic_os import i18n
from agentic_os.config import Settings
from agentic_os.domain import AgentName
from agentic_os.i18n import lazy
from agentic_os.locales import MESSAGES
from agentic_os.providers.base import ModelInfo, Provider, ProviderStatus
from agentic_os.providers.fake import FakeProvider
from agentic_os.server import routes_auth
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState
from agentic_os.server.status import ProviderMonitor, status_to_wire

ORIGIN = "https://aos.example"
T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
EN = {"accept-language": "en-GB,en;q=0.9"}
ES = {"accept-language": "es-ES,es;q=0.9,ca;q=0.8"}


def make_settings(tmp_path: Path) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
    }
    return Settings(**{"_env_file": None, **values})


@pytest.fixture(autouse=True)
def texts(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a provider's status and a model's description say (their catalogs belong to
    the providers): texts of this test, in the three languages."""
    monkeypatch.setitem(
        MESSAGES,
        "test.subscription",
        {"en": "Subscription active", "es": "Suscripción activa", "ca": "Subscripció activa"},
    )
    monkeypatch.setitem(
        MESSAGES,
        "test.model",
        {"en": "The fast one", "es": "El rápido", "ca": "El ràpid"},
    )


class LazyProvider(FakeProvider):
    """A fake whose status and model list carry lazy texts, as the real providers' do, and
    which counts its model listings."""

    def __init__(self, agent: AgentName) -> None:
        super().__init__(agent, chunk_delay=0)
        self.listings = 0

    async def status(self) -> ProviderStatus:
        return ProviderStatus(self.agent, self.mode, True, "fast-1", lazy("test.subscription"))

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        self.listings += 1
        return (ModelInfo("fast-1", "Fast 1", lazy("test.model"), is_default=True),)


def lazy_providers() -> dict[AgentName, Provider]:
    return {"claude": LazyProvider("claude"), "chatgpt": LazyProvider("chatgpt")}


@asynccontextmanager
async def http_client(
    tmp_path: Path, providers: dict[AgentName, Provider] | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, AppState]]:
    app = create_app(make_settings(tmp_path), providers=providers or lazy_providers())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            yield client, app.state.aos


async def log_in(client: httpx.AsyncClient, state: AppState) -> None:
    token = await state.sessions.create(state.clock(), ip=None, user_agent=None)
    client.cookies.set(state.cookie_name, token)


# -- HTTP ------------------------------------------------------------------------------


async def test_http_errors_speak_the_language_of_accept_language(tmp_path: Path) -> None:
    async with http_client(tmp_path) as (client, state):
        details: dict[str, list[str]] = {}
        for lang, headers in (("en", EN), ("es", ES), ("ca", {})):
            client.cookies.clear()
            answers = [
                await client.get("/api/nope", headers=headers),  # 404 without a detail
                await client.get("/api/conversations", headers=headers),  # 401
                await client.post("/api/auth/logout", headers=headers),  # 403 Origin
                await client.post(  # 413 before the body is read
                    "/api/auth/login",
                    content=b"x" * 5000,
                    headers={**headers, "origin": ORIGIN},
                ),
            ]
            await log_in(client, state)
            answers.append(  # 422 of FastAPI's own validation
                await client.get("/api/conversations?limit=0", headers=headers)
            )
            answers.append(  # 404 with a detail of the route
                await client.get("/api/conversations/77", headers=headers)
            )
            assert [a.status_code for a in answers] == [404, 401, 403, 413, 422, 404]
            details[lang] = [a.json()["detail"] for a in answers]
    assert details == {
        "en": [
            "Not found.",
            "You need to log in.",
            "Origin not allowed.",
            "The request is too large (maximum 4 KiB).",
            'Invalid data: "limit".',
            "The conversation does not exist.",
        ],
        "es": [
            "No se ha encontrado.",
            "Tienes que iniciar sesión.",
            "Origen no permitido.",
            "La petición es demasiado grande (máximo 4 KiB).",
            "Datos no válidos: «limit».",
            "La conversación no existe.",
        ],
        "ca": [
            "No s'ha trobat.",
            "Cal iniciar sessió.",
            "Origen no permès.",
            "La petició és massa gran (màxim 4 KiB).",
            "Dades no vàlides: «limit».",
            "La conversa no existeix.",
        ],
    }


async def test_settings_errors_speak_the_language_of_the_request(tmp_path: Path) -> None:
    async with http_client(tmp_path) as (client, state):
        await log_in(client, state)
        headers = {"origin": ORIGIN}
        body = {"revision": 0, "fx": {"mode": "manual", "eur_per_usd": 7}}
        english = await client.put("/api/settings", json=body, headers={**headers, **EN})
        spanish = await client.put("/api/settings", json=body, headers={**headers, **ES})
    assert english.json() == {"detail": '"fx.eur_per_usd" must be a number between 0.2 and 5.'}
    assert spanish.json() == {"detail": "«fx.eur_per_usd» debe ser un número entre 0,2 y 5."}


def test_the_login_lockout_names_its_wait_in_the_language_in_force() -> None:
    waits = {}
    for lang in i18n.LANGS:
        with i18n.use(lang):
            waits[lang] = [routes_auth._wait_text(s) for s in (1, 5, 60, 61, 3600)]
    with i18n.use("es"):
        body = bytes(routes_auth._too_many_attempts(5).body)
    assert waits == {
        "en": ["1 second", "5 seconds", "1 minute", "2 minutes", "60 minutes"],
        "es": ["1 segundo", "5 segundos", "1 minuto", "2 minutos", "60 minutos"],
        "ca": ["1 segon", "5 segons", "1 minut", "2 minuts", "60 minuts"],
    }
    assert "Demasiados intentos fallidos. Vuelve a intentarlo dentro de 5 segundos." in (
        body.decode()
    )


async def test_the_page_without_a_build_speaks_the_language_of_the_browser(
    tmp_path: Path,
) -> None:
    async with http_client(tmp_path) as (client, _state):
        spanish = await client.get("/", headers=ES)
        english = await client.get("/", headers=EN)
    assert spanish.status_code == english.status_code == 503
    assert '<html lang="es">' in spanish.text
    assert "El servidor funciona, pero no encuentra la interfaz web compilada." in spanish.text
    assert '<html lang="en">' in english.text
    assert "or say where it is with the variable <code>AOS_WEB_DIST</code>." in english.text


# -- shared caches ---------------------------------------------------------------------


async def test_the_status_and_the_models_kept_for_every_client_speak_each_ones_language(
    tmp_path: Path,
) -> None:
    claude, chatgpt = LazyProvider("claude"), LazyProvider("chatgpt")
    providers: dict[AgentName, Provider] = {"claude": claude, "chatgpt": chatgpt}
    async with http_client(tmp_path, providers) as (client, state):
        await log_in(client, state)
        statuses = {
            lang: (await client.get("/api/providers", headers=headers)).json()
            for lang, headers in (("en", EN), ("es", ES))
        }
        models = {
            lang: (await client.get("/api/models", headers=headers)).json()
            for lang, headers in (("en", EN), ("es", ES))
        }
    assert [s["detail"] for s in statuses["en"]] == ["Subscription active"] * 2
    assert [s["detail"] for s in statuses["es"]] == ["Suscripción activa"] * 2
    for lang, description in (("en", "The fast one"), ("es", "El rápido")):
        for agent in ("claude", "chatgpt"):
            assert [m["description"] for m in models[lang][agent]["models"]] == [description]
    # The second client got the listing the first one filled (cached for every client).
    assert (claude.listings, chatgpt.listings) == (1, 1)


def test_one_status_is_written_in_the_language_of_each_reader() -> None:
    status = ProviderStatus("claude", "cli", False, "", lazy("server.status.timeout"))
    written = {}
    for lang in i18n.LANGS:
        with i18n.use(lang):
            written[lang] = status_to_wire(status)["detail"]
    assert written == {
        "en": "The provider is not responding.",
        "es": "El proveedor no responde.",
        "ca": "El proveïdor no respon.",
    }


# -- WebSocket -------------------------------------------------------------------------


def call(client: TestClient, fn: Any) -> Any:
    assert client.portal is not None
    return client.portal.call(fn)


def test_the_hello_and_the_errors_of_a_socket_speak_its_lang_parameter(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path), providers=lazy_providers(), clock=lambda: T0)
    with TestClient(app, base_url="https://testserver") as client:
        state: AppState = app.state.aos
        token = call(client, functools.partial(state.sessions.create, T0, ip=None, user_agent=None))
        headers = {"origin": ORIGIN, "cookie": f"{state.cookie_name}={token}"}
        seen = {}
        for query in ("?lang=en", "?lang=es", ""):
            with client.websocket_connect(f"/api/ws{query}", headers=headers) as ws:
                hello = ws.receive_json()
                ws.send_json({"type": "nope"})
                unknown = ws.receive_json()
                ws.send_json({"type": "turn.start", "request_id": "r1", "text": "Hi", "mode": "x"})
                mode = ws.receive_json()
                seen[query] = (
                    [p["detail"] for p in hello["providers"]],
                    unknown["message"],
                    (mode["message"], mode["request_id"]),
                )
    assert seen == {
        "?lang=en": (
            ["Subscription active"] * 2,
            'Unknown message type: "nope".',
            ('"mode" must be "solo", "duel", "debate" or "refine".', "r1"),
        ),
        "?lang=es": (
            ["Suscripción activa"] * 2,
            "Tipo de mensaje desconocido: «nope».",
            ("«mode» debe ser «solo», «duel», «debate» o «refine».", "r1"),
        ),
        "": (
            ["Subscripció activa"] * 2,
            "Tipus de missatge desconegut: «nope».",
            ("«mode» ha de ser «solo», «duel», «debate» o «refine».", "r1"),
        ),
    }


class AnswersOnce(LazyProvider):
    """Answers its first status check, then hangs: the monitor answers with the last one."""

    def __init__(self, agent: AgentName) -> None:
        super().__init__(agent)
        self.checks = 0

    async def status(self) -> ProviderStatus:
        self.checks += 1
        if self.checks > 1:
            await asyncio.sleep(10)
        return await super().status()


async def test_the_last_known_status_is_written_in_the_language_of_each_reader() -> None:
    monitor = ProviderMonitor({"claude": AnswersOnce("claude")}, wait_seconds=1.0)
    with i18n.use("en"):
        [fresh] = await monitor.statuses()
        assert status_to_wire(fresh)["detail"] == "Subscription active"
    with i18n.use("es"):
        [kept] = await monitor.statuses(wait_seconds=0.01)  # the new check hangs
        assert kept is fresh
        assert status_to_wire(kept)["detail"] == "Suscripción activa"
    await monitor.aclose()
