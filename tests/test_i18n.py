"""The languages of the server's texts (docs/adr/0011-internationalization.md): how a
request, a WebSocket connection and the command line choose one, and the catalogs."""

from __future__ import annotations

import asyncio
import re

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

from agentic_os import i18n
from agentic_os.cli import main
from agentic_os.locales import AREAS, MESSAGES
from agentic_os.server.middleware import LanguageMiddleware


@pytest.mark.parametrize(
    ("header", "lang"),
    [
        ("es", "es"),
        ("ca-ES,ca;q=0.9", "ca"),
        ("es-ES,es;q=0.9,en;q=0.8", "es"),
        ("fr-FR,fr;q=0.9,ca;q=0.5,en;q=0.7", "en"),
        ("en;q=0, es", "es"),
        ("de, it", None),
        ("", None),
        (None, None),
        ("es;q=abc, ca;q=0.2", "ca"),
        ("ca;q=0.5, es;q=0.5", "ca"),
    ],
)
def test_the_language_an_accept_language_header_prefers(
    header: str | None, lang: i18n.Lang | None
) -> None:
    assert i18n.from_accept_language(header) == lang


def test_the_language_of_the_system_locale() -> None:
    assert i18n.from_environ({"LANG": "ca_ES.UTF-8"}) == "ca"
    assert i18n.from_environ({"LC_MESSAGES": "es_ES", "LANG": "ca_ES.UTF-8"}) == "es"
    # LC_ALL wins, even when it names no language of ours.
    assert i18n.from_environ({"LC_ALL": "C.UTF-8", "LANG": "es_ES.UTF-8"}) is None
    assert i18n.from_environ({}) is None
    assert i18n.as_lang("ca-ES-valencia") == "ca"
    assert i18n.as_lang("english") is None


def test_without_a_choice_texts_use_the_default_language() -> None:
    assert i18n.chosen() is None
    assert i18n.current() == i18n.DEFAULT_LANG == "ca"  # tests/conftest.py
    with i18n.use("en"):
        assert i18n.current() == "en"
        with i18n.use(None):
            assert i18n.current() == "ca"
    assert i18n.chosen() is None


def test_numbers_are_written_as_the_web_writes_them() -> None:
    written = {}
    for lang in i18n.LANGS:
        with i18n.use(lang):
            written[lang] = [i18n.number(1234.5, 1), i18n.number(12345), i18n.number(-0.25, 2)]
    assert written == {
        "en": ["1,234.5", "12,345", "-0.25"],
        "es": ["1234,5", "12.345", "-0,25"],
        "ca": ["1.234,5", "12.345", "-0,25"],
    }


def test_every_text_has_its_area_and_the_same_placeholders_in_every_language() -> None:
    keys = [key for area in AREAS.values() for key in area]
    assert len(keys) == len(set(keys)) == len(MESSAGES)
    for area, texts in AREAS.items():
        for key, text in texts.items():
            assert key.startswith(f"{area}."), key
            assert re.fullmatch(r"[a-z0-9_.]+", key), key
            assert set(text) == set(i18n.LANGS), key
            values = [text["en"], text["es"], text["ca"]]
            assert all(value.strip() for value in values), key
            names = {i18n.placeholders(value) for value in values}
            assert len(names) == 1, (key, names)


def test_t_gives_the_text_in_the_language_in_force(monkeypatch: pytest.MonkeyPatch) -> None:
    text: i18n.Text = {"en": "{n} turns", "es": "{n} turnos", "ca": "{n} torns"}
    monkeypatch.setitem(MESSAGES, "test.turns", text)
    assert i18n.t("test.turns", n=3) == "3 torns"
    with i18n.use("es"):
        assert i18n.t("test.turns", n=3) == "3 turnos"
    with i18n.use("en"):
        assert i18n.t("test.turns", n=3) == "3 turns"


def _app() -> Starlette:
    async def lang(request: Request) -> PlainTextResponse:
        return PlainTextResponse(i18n.current())

    async def socket(websocket: WebSocket) -> None:
        await websocket.accept()
        # A task the connection starts (a turn) inherits the language.
        await websocket.send_text(await asyncio.create_task(_current()))
        await websocket.close()

    async def _current() -> str:
        return i18n.current()

    app = Starlette(routes=[Route("/lang", lang), WebSocketRoute("/ws", socket)])
    app.add_middleware(LanguageMiddleware)
    return app


def test_a_request_speaks_the_language_of_its_accept_language_header() -> None:
    with TestClient(_app()) as client:
        assert client.get("/lang", headers={"Accept-Language": "es"}).text == "es"
        assert client.get("/lang", headers={"Accept-Language": "en-GB,en;q=0.9"}).text == "en"
        assert client.get("/lang", headers={"Accept-Language": "fr"}).text == "ca"
        assert client.get("/lang").text == "ca"


def test_a_websocket_and_its_tasks_speak_the_language_of_its_lang_parameter() -> None:
    with TestClient(_app()) as client:
        for query, lang in (("?lang=en", "en"), ("?lang=es", "es"), ("?lang=xx", "ca"), ("", "ca")):
            with client.websocket_connect(f"/ws{query}") as ws:
                assert ws.receive_text() == lang


def test_the_command_line_speaks_the_language_of_the_system(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[i18n.Lang] = []

    def command(argv: object) -> int:
        seen.append(i18n.current())
        return 0

    monkeypatch.setattr("agentic_os.cli._main", command)
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    assert main([]) == 0
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert main([]) == 0
    monkeypatch.delenv("LANG")
    assert main([]) == 0
    assert seen == ["es", "en", "ca"]
    # Nothing of it stays chosen once the command is over.
    assert i18n.chosen() is None
