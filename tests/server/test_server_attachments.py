"""``/api/attachments`` and the ``attachments`` of ``turn.start`` (docs/PROTOCOL.md
«Adjunts», docs/adr/0009-attachments.md): the type from the content, the limits (413, 415,
422), the session and Origin checks, what each file is served as, thumbnails, deletion
and the maintenance that removes what was never sent."""

import asyncio
import errno
import functools
import hashlib
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from attachment_files import (
    Page,
    blank_pdf,
    drawn_pdf,
    encrypted_pdf,
    gif,
    jpeg,
    pdf,
    png,
    shown,
    webp_lossy,
)
from starlette.testclient import TestClient

from agentic_os import attachments
from agentic_os.config import Settings
from agentic_os.domain import AgentName
from agentic_os.fx import manual_rate
from agentic_os.orchestrator.store import NewMessage
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.server import middleware
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState
from agentic_os.server.ws import ProtocolError, parse_turn_start
from agentic_os.storage import RuntimeSettings, SqliteStore
from agentic_os.storage.files import IncomingFile

pytestmark = pytest.mark.filterwarnings("ignore:Using `httpx` with:DeprecationWarning")

ORIGIN = "https://aos.example"
ORIGIN_HEADERS = {"origin": ORIGIN}
COOKIE = "__Host-aos_session"
FX = manual_rate()
T0 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
UPLOAD = "/api/attachments"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def make_settings(tmp_path: Path) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
        "claude_mode": "fake",
        "chatgpt_mode": "fake",
    }
    return Settings(**values)


def fakes() -> dict[AgentName, FakeProvider]:
    return {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }


@dataclass
class Harness:
    client: httpx.AsyncClient
    state: AppState
    root: Path

    async def login(self) -> None:
        token = await self.state.sessions.create(T0, ip=None, user_agent=None)
        self.client.cookies.set(self.state.cookie_name, token)

    async def upload(
        self, data: bytes | AsyncIterable[bytes], name: str | None, **headers: str
    ) -> httpx.Response:
        params = {"name": name} if name is not None else {}
        return await self.client.put(
            UPLOAD, params=params, content=data, headers={**ORIGIN_HEADERS, **headers}
        )

    async def uploaded(self, data: bytes, name: str) -> dict[str, Any]:
        response = await self.upload(data, name)
        assert response.status_code == 201, response.text
        wire: dict[str, Any] = response.json()
        return wire

    def leftovers(self) -> list[Path]:
        incoming = self.root / "incoming"
        return list(incoming.iterdir()) if incoming.is_dir() else []


@asynccontextmanager
async def running(tmp_path: Path) -> AsyncIterator[Harness]:
    settings = make_settings(tmp_path)
    providers: dict[AgentName, Provider] = dict(fakes())
    app = create_app(settings, providers=providers, clock=Clock())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            yield Harness(client, app.state.aos, settings.data_dir / "attachments")


@pytest.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    async with running(tmp_path) as harness:
        await harness.login()
        yield harness


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- upload and metadata -------------------------------------------------------------------


async def test_an_uploaded_image_is_described_and_served_in_place(h: Harness) -> None:
    data = png(1600, 1200)
    # The Content-Type the browser claims is ignored: the content says PNG.
    response = await h.upload(data, "C:\\fotos\\platja.png", **{"content-type": "text/html"})
    assert response.status_code == 201
    wire = response.json()
    assert wire == {
        "id": wire["id"],
        "name": "platja.png",
        "kind": "image",
        "mime": "image/png",
        "size": len(data),
        "pages": None,
        "width": 1600,
        "height": 1200,
        "sha256": sha(data),
        "created_at": "2026-09-29T12:00:00.000Z",
        "has_thumbnail": False,
        "text_available": False,
        "estimated_tokens": 58 * 43,
        "pdf_notes": None,
    }
    assert (await h.client.get(f"{UPLOAD}/{wire['id']}")).json() == wire

    content = await h.client.get(f"{UPLOAD}/{wire['id']}/content")
    assert content.status_code == 200
    assert content.content == data
    assert content.headers["content-type"] == "image/png"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-disposition"] == (
        "inline; filename=\"platja.png\"; filename*=UTF-8''platja.png"
    )
    assert content.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert content.headers["cache-control"] == "no-store"
    assert not h.leftovers()


@pytest.mark.parametrize(
    ("data", "mime", "size"),
    [
        (jpeg(4032, 3024), "image/jpeg", (4032, 3024)),
        (gif(320, 240), "image/gif", (320, 240)),
        (webp_lossy(2576, 1449), "image/webp", (2576, 1449)),
    ],
)
async def test_every_image_type(h: Harness, data: bytes, mime: str, size: tuple[int, int]) -> None:
    wire = await h.uploaded(data, "imatge")
    assert (wire["mime"], wire["width"], wire["height"]) == (mime, *size)


async def test_pdfs_and_text_files_are_downloads(h: Harness) -> None:
    document = pdf(["Resum de l'informe", "Conclusions"])
    wire = await h.uploaded(document, "informe final.pdf")
    assert (wire["kind"], wire["mime"], wire["pages"]) == ("pdf", "application/pdf", 2)
    assert (wire["text_available"], wire["estimated_tokens"]) == (True, 2 * 3600)
    content = await h.client.get(f"{UPLOAD}/{wire['id']}/content")
    assert content.content == document
    assert content.headers["content-type"] == "application/pdf"
    assert content.headers["content-disposition"] == (
        "attachment; filename=\"informe final.pdf\"; filename*=UTF-8''informe%20final.pdf"
    )

    page = "<html><script>alert(document.cookie)</script></html>\n"
    wire = await h.uploaded(page.encode(), "pàgina.html")
    assert (wire["kind"], wire["mime"], wire["estimated_tokens"]) == ("text", "text/plain", 14)
    content = await h.client.get(f"{UPLOAD}/{wire['id']}/content")
    assert content.text == page
    assert content.headers["content-type"] == "text/plain; charset=utf-8"
    assert content.headers["content-disposition"] == (
        "attachment; filename=\"pagina.html\"; filename*=UTF-8''p%C3%A0gina.html"
    )
    assert content.headers["x-content-type-options"] == "nosniff"


async def test_a_pdf_can_be_read_in_ranges(h: Harness) -> None:
    document = pdf(["Una pàgina"])
    wire = await h.uploaded(document, "rang.pdf")
    part = await h.client.get(f"{UPLOAD}/{wire['id']}/content", headers={"range": "bytes=0-7"})
    assert part.status_code == 206
    assert part.content == document[:8]
    assert part.headers["content-range"] == f"bytes 0-7/{len(document)}"


async def test_a_pdf_without_text_has_none_available(h: Harness) -> None:
    wire = await h.uploaded(pdf([None]), "escanejat.pdf")
    assert (wire["pages"], wire["text_available"]) == (1, False)
    assert wire["pdf_notes"] == {"no_text": [1], "garbled": [], "hidden": []}


SUMMARY = "Resum de l'informe de vendes del segon trimestre de l'any"
ANALYSED = drawn_pdf(
    [
        Page(shown(SUMMARY)),
        Page(b"q 595 0 0 842 0 0 cm /Im1 Do Q", image=True),  # a scanned page
        Page(shown(SUMMARY) + shown("Nota oculta per als models", y=600, mode=3)),
    ]
)
"""A PDF whose second page is a scan and whose third has invisible text."""
ANALYSED_NOTES = {"no_text": [2], "garbled": [], "hidden": [3]}


async def test_an_uploaded_pdf_has_the_notes_of_its_pages(h: Harness) -> None:
    """The reader analyses every page as it reads the text: the card warns of the pages
    without text, with unreadable text and with text that may not be visible."""
    wire = await h.uploaded(ANALYSED, "informe.pdf")
    assert (wire["pages"], wire["text_available"]) == (3, True)
    assert wire["pdf_notes"] == ANALYSED_NOTES
    assert (await h.client.get(f"{UPLOAD}/{wire['id']}")).json() == wire
    [stored] = await h.state.store.get_attachments([wire["id"]])
    assert stored.pdf_pages is not None
    assert [(page.number, page.images, page.invisible) for page in stored.pdf_pages] == [
        (1, False, 0),
        (2, True, 0),
        (3, False, 26),
    ]
    # Only a PDF has notes.
    assert (await h.uploaded(b"# Notes", "notes.md"))["pdf_notes"] is None


async def test_the_type_comes_from_the_content(h: Harness) -> None:
    assert (await h.uploaded(png(8, 8), "no-és-text.txt"))["mime"] == "image/png"
    assert (await h.uploaded(pdf(["x"]), "foto.jpg"))["mime"] == "application/pdf"
    cases = [
        (b"Hola", "foto.png", attachments.UNSUPPORTED_DETAIL),
        (b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64, "programa", attachments.UNSUPPORTED_DETAIL),
        (b"PK\x03\x04" + b"\x00" * 64, "document.docx", attachments.UNSUPPORTED_DETAIL),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "logo.svg", attachments.SVG_DETAIL),
        (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64, "IMG_1.HEIC", attachments.HEIC_DETAIL),
        ("caf\xe9 en latin-1".encode("latin-1"), "notes.txt", attachments.UNSUPPORTED_DETAIL),
    ]
    for data, name, detail in cases:
        response = await h.upload(data, name)
        assert response.status_code == 415, name
        assert response.json() == {"detail": detail}
    assert not h.leftovers()


async def test_invalid_uploads_are_422(h: Harness) -> None:
    cases = [
        (b"", "buit.txt", attachments.EMPTY_DETAIL),
        (b"Hola", None, attachments.NAME_REQUIRED_DETAIL),
        (b"Hola", "   ", attachments.NAME_REQUIRED_DETAIL),
        (b"Hola", "../..", attachments.BAD_NAME_DETAIL),
        (png(8001, 10), "gran.png", "La imatge fa 8.001 x 10 píxels: com a molt 8.000 per costat."),
        (png(10, 10)[:20], "tallada.png", attachments.BAD_IMAGE_DETAIL),
        (encrypted_pdf(), "secret.pdf", attachments.PDF_ENCRYPTED_DETAIL),
        (b"%PDF-1.7 trencat", "trencat.pdf", attachments.PDF_INVALID_DETAIL),
    ]
    for data, name, detail in cases:
        response = await h.upload(data, name)
        assert response.status_code == 422, name
        assert response.json() == {"detail": detail}
    assert not h.leftovers()


async def test_a_pdf_with_too_many_pages(h: Harness) -> None:
    response = await h.upload(blank_pdf(101), "llibre.pdf")
    assert response.status_code == 422
    assert response.json() == {"detail": "El PDF té 101 pàgines: com a molt 100."}


# -- limits ---------------------------------------------------------------------------------


async def chunks(data: bytes, size: int = 256 * 1024) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def test_each_type_is_cut_at_its_limit(h: Harness) -> None:
    big_image = png(100, 100, extra=b"\x00" * 7_000_000)
    for body in (big_image, chunks(big_image)):  # announced, or counted as it arrives
        response = await h.upload(body, "gran.png")
        assert response.status_code == 413
        assert response.json() == {
            "detail": "El fitxer és massa gran: una imatge pot tenir com a molt 7 MB."
        }
    text = b"a" * 200_001
    response = await h.upload(text, "llarg.txt")
    assert response.status_code == 413
    assert response.json() == {
        "detail": "El fitxer és massa gran: un fitxer de text pot tenir com a molt 200 kB."
    }
    # Exactly at the limit is fine, and larger than the 1 MiB of the other routes.
    assert (await h.upload(b"a" * 200_000, "just.txt")).status_code == 201
    assert (await h.upload(png(10, 10, extra=b"\x00" * 2_000_000), "2mb.png")).status_code == 201
    assert not h.leftovers()


async def test_a_body_larger_than_any_attachment_is_refused_before_reading(h: Harness) -> None:
    body = pdf(["x"]) + b"\x00" * 20_000_000
    response = await h.upload(body, "enorme.pdf")
    assert response.status_code == 413
    assert response.json() == {"detail": "La petició és massa gran (màxim 20 MB)."}
    response = await h.upload(chunks(body), "enorme.pdf")
    assert response.status_code == 413
    assert not h.leftovers()


async def test_the_upload_has_its_own_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(middleware, "BODY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(middleware, "UPLOAD_TIMEOUT_SECONDS", 2.0)

    async def trickle() -> AsyncIterator[bytes]:  # slower than the other routes allow
        for piece in (b"Hola, ", b"com ", b"va?\n"):
            yield piece
            await asyncio.sleep(0.15)

    async with running(tmp_path / "a") as h:
        await h.login()
        response = await h.upload(trickle(), "lent.txt")
        assert response.status_code == 201

    monkeypatch.setattr(middleware, "UPLOAD_TIMEOUT_SECONDS", 0.2)
    async with running(tmp_path / "b") as h:
        await h.login()
        response = await h.upload(trickle(), "lent.txt")
        assert response.status_code == 408
        assert response.headers["connection"] == "close"
        assert not h.leftovers()


async def test_a_full_disk_is_507(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def full(self: IncomingFile, chunk: bytes) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(IncomingFile, "write", full)
    response = await h.upload(b"Hola", "nota.txt")
    assert response.status_code == 507
    assert response.json() == {
        "detail": "El servidor no té prou espai al disc per desar el fitxer."
    }


# -- session and Origin ----------------------------------------------------------------------


async def test_every_route_needs_the_session(tmp_path: Path) -> None:
    async with running(tmp_path) as h:
        routes = [
            ("PUT", f"{UPLOAD}?name=a.txt"),
            ("GET", f"{UPLOAD}/1"),
            ("GET", f"{UPLOAD}/1/content"),
            ("PUT", f"{UPLOAD}/1/thumbnail"),
            ("GET", f"{UPLOAD}/1/thumbnail"),
            ("DELETE", f"{UPLOAD}/1"),
        ]
        for method, path in routes:
            response = await h.client.request(method, path, content=b"x", headers=ORIGIN_HEADERS)
            assert response.status_code == 401, (method, path)
            assert response.json() == {"detail": "Cal iniciar sessió."}
        assert not h.leftovers()


async def test_changes_need_an_allowed_origin(h: Harness) -> None:
    wire = await h.uploaded(b"Hola", "nota.txt")
    for method, path in (
        ("PUT", f"{UPLOAD}?name=a.txt"),
        ("PUT", f"{UPLOAD}/{wire['id']}/thumbnail"),
        ("DELETE", f"{UPLOAD}/{wire['id']}"),
    ):
        for headers in ({}, {"origin": "https://evil.example"}):
            response = await h.client.request(method, path, content=png(4, 4), headers=headers)
            assert response.status_code == 403, (method, path, headers)
    assert (await h.client.get(f"{UPLOAD}/{wire['id']}")).status_code == 200


async def test_ids_out_of_range_and_missing_attachments(h: Harness) -> None:
    for suffix in ("", "/content", "/thumbnail"):
        missing = await h.client.get(f"{UPLOAD}/99{suffix}")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "L'adjunt no existeix."}
        for bad in ("0", "x", str(2**63)):
            invalid = await h.client.get(f"{UPLOAD}/{bad}{suffix}")
            assert invalid.status_code == 422
            assert invalid.json() == {"detail": "Dades no vàlides: «attachment_id»."}
    missing = await h.client.put(
        f"{UPLOAD}/99/thumbnail", content=png(4, 4), headers=ORIGIN_HEADERS
    )
    assert missing.status_code == 404
    missing = await h.client.delete(f"{UPLOAD}/99", headers=ORIGIN_HEADERS)
    assert missing.status_code == 404


# -- thumbnails ------------------------------------------------------------------------------


async def test_thumbnails(h: Harness) -> None:
    wire = await h.uploaded(pdf(["Hola"]), "informe.pdf")
    path = f"{UPLOAD}/{wire['id']}/thumbnail"
    none = await h.client.get(path)
    assert none.status_code == 404
    assert none.json() == {"detail": "Aquest adjunt no té miniatura."}

    thumbnail = webp_lossy(256, 362)
    stored = await h.client.put(path, content=thumbnail, headers=ORIGIN_HEADERS)
    assert stored.status_code == 204
    got = await h.client.get(path)
    assert got.content == thumbnail
    assert got.headers["content-type"] == "image/webp"
    assert got.headers["content-disposition"].startswith("inline;")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert (await h.client.get(f"{UPLOAD}/{wire['id']}")).json()["has_thumbnail"] is True

    cases = [
        (jpeg(100, 100), 415, "La miniatura ha de ser una imatge PNG o WebP."),
        (b"<svg/>", 415, "La miniatura ha de ser una imatge PNG o WebP."),
        (png(513, 100), 422, "La miniatura fa 513 x 100 píxels: com a molt 512 per costat."),
        (
            png(100, 100, extra=b"\x00" * 100_000),
            413,
            "La miniatura és massa gran: com a molt 100 kB.",
        ),
    ]
    for data, status, detail in cases:
        response = await h.client.put(path, content=data, headers=ORIGIN_HEADERS)
        assert response.status_code == status
        assert response.json() == {"detail": detail}
    assert (await h.client.get(path)).content == thumbnail  # unchanged


# -- deleting --------------------------------------------------------------------------------


async def test_an_unsent_attachment_can_be_deleted_and_a_sent_one_cannot(h: Harness) -> None:
    unsent = await h.uploaded(b"Esborra'm", "esborrany.txt")
    deleted = await h.client.delete(f"{UPLOAD}/{unsent['id']}", headers=ORIGIN_HEADERS)
    assert deleted.status_code == 204
    assert (await h.client.get(f"{UPLOAD}/{unsent['id']}")).status_code == 404
    assert not h.state.store.content_path(unsent["sha256"]).exists()

    sent = await h.uploaded(b"Enviat", "enviat.txt")
    store = h.state.store
    conversation_id = await store.create_conversation("C")
    question_id = await store.add_message(
        NewMessage(conversation_id, "question", "Q", final=True, meta={"mode": "solo"})
    )
    await store.link_attachments(question_id, [sent["id"]])
    refused = await h.client.delete(f"{UPLOAD}/{sent['id']}", headers=ORIGIN_HEADERS)
    assert refused.status_code == 409
    assert refused.json() == {
        "detail": "Aquest adjunt ja s'ha enviat en una conversa: s'esborrarà quan s'esborri "
        "la conversa."
    }
    # Deleting the conversation deletes it.
    assert (
        await h.client.delete(f"/api/conversations/{conversation_id}", headers=ORIGIN_HEADERS)
    ).status_code == 204
    assert (await h.client.get(f"{UPLOAD}/{sent['id']}")).status_code == 404
    assert not store.content_path(sent["sha256"]).exists()


async def test_the_maintenance_deletes_what_was_never_sent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    old = T0 - timedelta(hours=25)
    async with await SqliteStore.open(settings.db_path, clock=lambda: old) as store:
        upload = store.new_upload()
        upload.write(b"oblidat")
        upload.finish()
        record = await store.add_attachment(upload, kind="text", mime="text/plain", name="o.txt")
    async with running(tmp_path) as h:
        for _ in range(50):
            if await h.state.store.get_attachment(record.id) is None:
                break
            await asyncio.sleep(0.02)
        assert await h.state.store.get_attachment(record.id) is None
        assert not h.state.store.content_path(record.sha256).exists()


# -- settings -------------------------------------------------------------------------------


async def test_the_pdf_setting_of_the_revisions(h: Harness) -> None:
    assert (await h.client.get("/api/settings")).json()["pdf_in_revisions"] == "text"
    saved = await h.client.put(
        "/api/settings", json={"revision": 0, "pdf_in_revisions": "full"}, headers=ORIGIN_HEADERS
    )
    assert saved.status_code == 200
    assert saved.json()["pdf_in_revisions"] == "full"
    refused = await h.client.put(
        "/api/settings", json={"revision": 1, "pdf_in_revisions": "tot"}, headers=ORIGIN_HEADERS
    )
    assert refused.status_code == 422
    assert refused.json() == {"detail": "«pdf_in_revisions» ha de ser «full» o «text»."}


# -- turn.start ------------------------------------------------------------------------------


def test_parse_turn_start_takes_the_attachments_and_the_pdf_setting() -> None:
    runtime = RuntimeSettings(pdf_in_revisions="full")
    data: dict[str, object] = {"request_id": "r", "text": "Què hi diu?"}
    request = parse_turn_start(data, RuntimeSettings(), fx=FX)
    assert (request.attachments, request.pdf_in_revisions) == ((), "text")
    request = parse_turn_start({**data, "attachments": [3, 1, 2]}, runtime, fx=FX)
    assert (request.attachments, request.pdf_in_revisions) == ((3, 1, 2), "full")
    assert parse_turn_start({**data, "attachments": None}, runtime, fx=FX).attachments == ()
    invalid = "«attachments» ha de ser una llista d'identificadors d'adjunt (enters positius)."
    for value, message in (
        (7, invalid),
        ("1,2", invalid),
        ([0], invalid),
        ([-1], invalid),
        ([1.0], invalid),
        (["1"], invalid),
        ([True], invalid),
        ([2**63], invalid),
        ([1, 2, 3, 4, 5, 6], "Un missatge pot portar com a màxim 5 adjunts."),
        ([4, 4], "Un mateix adjunt no pot anar dues vegades al missatge."),
    ):
        with pytest.raises(ProtocolError) as refused:
            parse_turn_start({**data, "attachments": value}, runtime, fx=FX)
        assert refused.value.message == message, value
        assert refused.value.request_id == "r"


def socket_headers(token: str) -> dict[str, str]:
    return {**ORIGIN_HEADERS, "cookie": f"{COOKIE}={token}"}


@pytest.fixture
def client(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, AppState, str, dict[AgentName, FakeProvider]]]:
    providers = fakes()
    app = create_app(make_settings(tmp_path), providers=dict(providers), clock=Clock())
    with TestClient(app, base_url="https://testserver") as test_client:
        state: AppState = app.state.aos
        assert test_client.portal is not None
        token = test_client.portal.call(
            functools.partial(state.sessions.create, T0, ip=None, user_agent=None)
        )
        test_client.cookies.set(COOKIE, token)
        yield test_client, state, token, providers


def test_a_turn_sends_the_attachments_it_names(
    client: tuple[TestClient, AppState, str, dict[AgentName, FakeProvider]],
) -> None:
    test_client, state, token, providers = client
    image = test_client.put(
        UPLOAD, params={"name": "gràfic.png"}, content=png(640, 480), headers=ORIGIN_HEADERS
    ).json()
    notes = test_client.put(
        UPLOAD, params={"name": "notes.md"}, content=b"# Notes\n", headers=ORIGIN_HEADERS
    ).json()
    with test_client.websocket_connect("/api/ws", headers=socket_headers(token)) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json(
            {
                "type": "turn.start",
                "request_id": "amb-adjunts",
                "text": "Què hi veus?",
                "mode": "solo",
                "target": "claude",
                "attachments": [notes["id"], image["id"]],
            }
        )
        events: list[dict[str, Any]] = []
        while not events or events[-1]["type"] not in ("turn.completed", "turn.failed"):
            events.append(ws.receive_json())
    assert events[-1]["type"] == "turn.completed", events[-1]
    [sent] = providers["claude"].attachments
    assert [(a.name, a.kind, a.mode) for a in sent] == [
        ("notes.md", "text", "full"),
        ("gràfic.png", "image", "full"),
    ]
    assert sent[1].path == state.store.content_path(image["sha256"])
    detail = test_client.get(f"/api/conversations/{events[-1]['conversation_id']}").json()
    question = detail["messages"][0]
    assert [a["id"] for a in question["meta"]["attachments"]] == [notes["id"], image["id"]]
    assert question["meta"]["attachments"][1] == image
    # Sent: it can no longer be deleted on its own.
    refused = test_client.delete(f"{UPLOAD}/{image['id']}", headers=ORIGIN_HEADERS)
    assert refused.status_code == 409


def test_a_turn_with_a_missing_attachment_fails(
    client: tuple[TestClient, AppState, str, dict[AgentName, FakeProvider]],
) -> None:
    test_client, _, token, _ = client
    with test_client.websocket_connect("/api/ws", headers=socket_headers(token)) as ws:
        ws.receive_json()
        ws.send_json({"type": "turn.start", "request_id": "r", "text": "Hola", "attachments": [42]})
        failed = ws.receive_json()
        assert failed["type"] == "turn.failed"
        assert failed["error"] == {
            "kind": "invalid",
            "message": "L'adjunt 42 no existeix.",
            "attachment_id": 42,
        }
        ws.send_json({"type": "turn.start", "request_id": "s", "text": "Hola", "attachments": [0]})
        error = ws.receive_json()
        assert error == {
            "type": "error",
            "code": "invalid",
            "message": "«attachments» ha de ser una llista d'identificadors d'adjunt (enters "
            "positius).",
            "request_id": "s",
        }


def test_the_question_keeps_the_notes_of_its_pdfs(
    client: tuple[TestClient, AppState, str, dict[AgentName, FakeProvider]],
) -> None:
    """``meta.attachments`` of the question is the ``Attachment`` of each file as it was
    when the turn started: an analysed PDF has its notes there too, and the models get
    the facts of its pages."""
    test_client, _, token, providers = client
    report = test_client.put(
        UPLOAD, params={"name": "informe.pdf"}, content=ANALYSED, headers=ORIGIN_HEADERS
    ).json()
    with test_client.websocket_connect("/api/ws", headers=socket_headers(token)) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json(
            {
                "type": "turn.start",
                "request_id": "amb-informe",
                "text": "Què diu l'informe?",
                "mode": "solo",
                "target": "claude",
                "attachments": [report["id"]],
            }
        )
        events: list[dict[str, Any]] = []
        while not events or events[-1]["type"] not in ("turn.completed", "turn.failed"):
            events.append(ws.receive_json())
    assert events[-1]["type"] == "turn.completed", events[-1]
    [[sent]] = providers["claude"].attachments
    assert sent.pdf_pages is not None and len(sent.pdf_pages) == 3
    assert sent.pdf_notes is not None and sent.pdf_notes.to_wire() == ANALYSED_NOTES
    detail = test_client.get(f"/api/conversations/{events[-1]['conversation_id']}").json()
    [meta] = detail["messages"][0]["meta"]["attachments"]
    assert meta == report
    assert meta["pdf_notes"] == ANALYSED_NOTES


def turn_events(ws: Any, request: dict[str, Any]) -> list[dict[str, Any]]:
    """Start a turn on a socket and read its events up to the last one."""
    ws.send_json({"type": "turn.start", **request})
    events: list[dict[str, Any]] = []
    while not events or events[-1]["type"] not in ("turn.completed", "turn.failed"):
        events.append(ws.receive_json())
    return events


def test_claudes_check_reaches_the_socket_and_is_kept_for_later_turns(tmp_path: Path) -> None:
    """With a ChatGPT that cannot open PDFs (Codex), a turn with an analysed PDF has
    Claude check its text: ``pdf.check`` is one more event of the turn (its ``seq``, its
    replay), ChatGPT's answer says how it read the PDF, live and reloaded, and the check
    stored in the database serves the next turn without any call."""
    claude = FakeProvider("claude", chunk_delay=0, mode="cli")  # the demo's never checks
    chatgpt = FakeProvider("chatgpt", chunk_delay=0, mode="cli")
    providers: dict[AgentName, Provider] = {"claude": claude, "chatgpt": chatgpt}
    app = create_app(make_settings(tmp_path), providers=providers, clock=Clock())
    with TestClient(app, base_url="https://testserver") as test_client:
        state: AppState = app.state.aos
        assert test_client.portal is not None
        token = test_client.portal.call(
            functools.partial(state.sessions.create, T0, ip=None, user_agent=None)
        )
        test_client.cookies.set(COOKIE, token)
        report = test_client.put(
            UPLOAD, params={"name": "informe.pdf"}, content=ANALYSED, headers=ORIGIN_HEADERS
        ).json()
        request = {"text": "Què diu l'informe?", "mode": "duel", "attachments": [report["id"]]}
        with test_client.websocket_connect("/api/ws", headers=socket_headers(token)) as ws:
            assert ws.receive_json()["type"] == "hello"
            first = turn_events(ws, {**request, "request_id": "primer"})
        assert first[-1]["type"] == "turn.completed", first[-1]
        assert [event["seq"] for event in first] == list(range(1, len(first) + 1))
        checks = [event for event in first if event["type"] == "pdf.check"]
        assert [(c["state"], c["attachment_id"], c["name"]) for c in checks] == [
            ("checking", report["id"], "informe.pdf"),
            ("checked", report["id"], "informe.pdf"),
        ]
        assert checks[-1]["reused"] is False and checks[-1]["unchecked_pages"] == []
        chatgpt_stream = next(
            event["stream_id"]
            for event in first
            if event["type"] == "stream.started" and event["agent"] == "chatgpt"
        )
        [completed] = [
            event
            for event in first
            if event["type"] == "stream.completed" and event["stream_id"] == chatgpt_stream
        ]
        [reading] = completed["pdf_reading"]
        assert (reading["attachment_id"], reading["checked"]) == (report["id"], True)
        # The check is stored by the file's content, in the database.
        stored = test_client.portal.call(state.store.get_pdf_check, report["sha256"])
        assert stored is not None and stored.complete

        with test_client.websocket_connect("/api/ws", headers=socket_headers(token)) as ws:
            ws.receive_json()
            ws.send_json({"type": "turn.subscribe", "request_id": "primer", "after_seq": 0})
            replay = [ws.receive_json() for _ in first]
        assert replay == first

        conversation = first[-1]["conversation_id"]
        detail = test_client.get(f"/api/conversations/{conversation}").json()
        [answer] = [
            message
            for message in detail["messages"]
            if message["kind"] == "answer" and message["agent"] == "chatgpt"
        ]
        assert answer["meta"]["pdf_reading"] == completed["pdf_reading"]

        calls = len(claude.requests)
        with test_client.websocket_connect("/api/ws", headers=socket_headers(token)) as ws:
            ws.receive_json()
            again = turn_events(
                ws, {**request, "request_id": "segon", "text": "I les conclusions?"}
            )
        assert again[-1]["type"] == "turn.completed", again[-1]
        [reused] = [event for event in again if event["type"] == "pdf.check"]
        assert (reused["state"], reused["reused"], reused["usage"]) == ("checked", True, None)
        assert [r.purpose for r in claude.requests[calls:]] == ["answer"]  # no check call
