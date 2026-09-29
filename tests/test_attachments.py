"""Attachments: limits, the type from the content, image dimensions from the headers, safe
display names, the token estimate and the PDF reader's subprocess (docs/adr/0009-adjunts.md)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from attachment_files import (
    blank_pdf,
    encrypted_pdf,
    gif,
    jpeg,
    pdf,
    png,
    webp_extended,
    webp_lossless,
    webp_lossy,
)

from agentic_os import attachments
from agentic_os.attachments import (
    AttachmentError,
    PdfReader,
    display_name,
    estimate_tokens,
    image_dimensions,
    image_size,
    sniff,
    text_content,
    thumbnail_type,
    upload_type,
)
from agentic_os.providers.base import Attachment
from agentic_os.storage.models import format_ts

# -- limits ------------------------------------------------------------------------------


def test_the_limits_are_the_contracted_ones() -> None:
    assert attachments.MAX_ATTACHMENTS == 5
    assert attachments.MAX_TURN_BYTES == 20_000_000
    assert attachments.MAX_IMAGE_BYTES == 7_000_000
    assert attachments.MAX_IMAGE_SIDE == 8000
    assert attachments.DOWNSCALE_EDGE == 2576
    assert attachments.MAX_PDF_BYTES == 20_000_000
    assert attachments.MAX_PDF_PAGES == 100
    assert attachments.MAX_TEXT_BYTES == 200_000
    assert attachments.MAX_UPLOAD_BYTES == 20_000_000
    assert attachments.MAX_THUMBNAIL_BYTES == 100_000
    assert attachments.MAX_THUMBNAIL_SIDE == 512
    assert attachments.PDF_TIMEOUT_SECONDS == 60
    assert {"txt", "md", "csv", "json", "py", "svelte", "pl"} <= attachments.TEXT_EXTENSIONS
    assert not {"svg", "heic", "exe", "docx", "zip"} & attachments.TEXT_EXTENSIONS


# -- the type comes from the content ---------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (png(10, 20), "image/png"),
        (jpeg(10, 20), "image/jpeg"),
        (b"GIF87a" + b"\x01\x00\x01\x00", "image/gif"),
        (gif(3, 4), "image/gif"),
        (webp_lossy(10, 20), "image/webp"),
        (pdf(["Hola"]), "application/pdf"),
    ],
)
def test_sniff_recognizes_the_accepted_types(data: bytes, mime: str) -> None:
    found = sniff(data[:16])
    assert found is not None and found.mime == mime


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"Hola, com va?\n",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00",
        b"PK\x03\x04\x14\x00\x00\x00",
        b"RIFF\x00\x00\x00\x00WAVEfmt ",
        b" %PDF-1.4",  # the signature must be at the start
    ],
)
def test_sniff_knows_nothing_else(data: bytes) -> None:
    assert sniff(data[:16]) is None


def test_the_type_comes_from_the_content_never_from_the_name() -> None:
    assert upload_type(png(1, 1)[:16], "informe.txt")[0].mime == "image/png"
    assert upload_type(pdf(["x"])[:16], "foto.png")[0].mime == "application/pdf"
    file_type, limit = upload_type(b"Hola", "notes.md")
    assert (file_type.kind, file_type.mime, limit) == ("text", "text/plain", 200_000)
    assert upload_type(png(1, 1)[:16], "x.png")[1] == 7_000_000
    assert upload_type(pdf(["x"])[:16], "x.pdf")[1] == 20_000_000
    for name in ("foto.png", "document.pdf", "programa.exe", "sense extensió", ".bashrc"):
        with pytest.raises(AttachmentError) as refused:
            upload_type(b"Hola", name)  # text that claims another type, or no known one
        assert refused.value.status == 415
        assert refused.value.message == attachments.UNSUPPORTED_DETAIL


def test_text_is_utf8_without_nul() -> None:
    assert text_content("Bon dia, món! 👋\n".encode()) == "Bon dia, món! 👋\n"
    assert text_content(b"\xef\xbb\xbfamb BOM") == "amb BOM"
    for data in (b"caf\xe9", b"a\x00b", "\ud800".encode("utf-8", "surrogatepass")):
        with pytest.raises(AttachmentError) as refused:
            text_content(data, "notes.txt")
        assert refused.value.status == 415
    with pytest.raises(AttachmentError) as refused:
        upload_type(b"text\x00with a NUL", "notes.txt")
    assert refused.value.status == 415


@pytest.mark.parametrize(
    ("head", "name", "message"),
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'>", "logo.svg", attachments.SVG_DETAIL),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00", "IMG_0001.HEIC", attachments.HEIC_DETAIL),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00", "foto.jpg", attachments.HEIC_DETAIL),
    ],
)
def test_svg_and_heic_are_refused_with_their_own_message(
    head: bytes, name: str, message: str
) -> None:
    with pytest.raises(AttachmentError) as refused:
        upload_type(head, name)
    assert refused.value.status == 415
    assert refused.value.message == message


# -- image dimensions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "mime", "size"),
    [
        (png(640, 480), "image/png", (640, 480)),
        (png(8000, 1), "image/png", (8000, 1)),
        (jpeg(4032, 3024), "image/jpeg", (4032, 3024)),
        (jpeg(300, 200, app_segments=3), "image/jpeg", (300, 200)),  # SOF after 180 KB
        (jpeg(1200, 800, sof=0xC2), "image/jpeg", (1200, 800)),  # progressive
        (gif(320, 240), "image/gif", (320, 240)),
        (webp_lossy(1024, 768), "image/webp", (1024, 768)),
        (webp_lossless(16383, 2), "image/webp", (16383, 2)),
        (webp_extended(2576, 1449), "image/webp", (2576, 1449)),
    ],
)
def test_image_size_from_the_headers(data: bytes, mime: str, size: tuple[int, int]) -> None:
    assert image_size(data, mime) == size


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (png(0, 10), "image/png"),
        (png(10, 10)[:20], "image/png"),  # truncated IHDR
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"),  # no IHDR first
        (jpeg(10, 0), "image/jpeg"),  # height defined later (DNL): not supported
        (b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 10, "image/jpeg"),  # no frame
        (b"\xff\xd8\xff\xda\x00\x08" + b"\x00" * 10, "image/jpeg"),  # scan before a frame
        (b"GIF89a\x01", "image/gif"),
        (webp_lossy(10, 10).replace(b"\x9d\x01\x2a", b"\x00\x00\x00"), "image/webp"),
        (webp_lossless(10, 10)[:24], "image/webp"),
        (webp_lossless(10, 10).replace(b"\x2f", b"\x00", 1), "image/webp"),
        (webp_lossy(10, 10).replace(b"VP8 ", b"ALPH"), "image/webp"),
        (png(10, 10), "image/svg+xml"),
    ],
)
def test_unreadable_images_have_no_size(data: bytes, mime: str) -> None:
    assert image_size(data, mime) is None


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xd8" + b"\xff\x01" * 3_500_000,  # millions of markers without a frame
        b"\xff\xd8" + b"\xff" * 7_000_000,  # one endless run of fill bytes
        b"\xff\xd8" + b"\x00" * 7_000_000,  # no marker at all
    ],
)
def test_a_hostile_jpeg_is_given_up_quickly(data: bytes) -> None:
    started = time.monotonic()
    assert image_size(data, "image/jpeg") is None
    assert time.monotonic() - started < 0.5


def test_image_limits() -> None:
    assert image_dimensions(png(8000, 8000), "image/png") == (8000, 8000)
    with pytest.raises(AttachmentError) as refused:
        image_dimensions(png(8001, 600), "image/png")
    assert refused.value.status == 422
    assert refused.value.message == "La imatge fa 8.001 x 600 píxels: com a molt 8.000 per costat."
    with pytest.raises(AttachmentError) as refused:
        image_dimensions(webp_lossless(10, 9000), "image/webp")
    assert refused.value.status == 422
    with pytest.raises(AttachmentError) as refused:
        image_dimensions(b"\xff\xd8\xff\xd9", "image/jpeg")
    assert refused.value.status == 422
    assert refused.value.message == attachments.BAD_IMAGE_DETAIL


def test_a_thumbnail_is_a_small_png_or_webp() -> None:
    assert thumbnail_type(png(512, 256)) == "image/png"
    assert thumbnail_type(webp_lossy(256, 512)) == "image/webp"
    cases = [
        (jpeg(100, 100), 415),
        (gif(10, 10), 415),
        (pdf(["x"]), 415),
        (b"<svg/>", 415),
        (png(513, 10), 422),
        (png(10, 10)[:18], 422),
        (png(100, 100, extra=b"\x00" * 100_000), 413),
    ]
    for data, status in cases:
        with pytest.raises(AttachmentError) as refused:
            thumbnail_type(data)
        assert refused.value.status == status, data[:16]


# -- display names -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("informe.pdf", "informe.pdf"),
        ("C:\\Users\\jo\\Desktop\\foto.jpg", "foto.jpg"),
        ("../../etc/passwd", "passwd"),
        ("carpeta/sub/notes.md", "notes.md"),
        ("  molts   espais\tde\nveritat .txt ", "molts espais de veritat .txt"),
        ("factura\u202egpj.exe", "facturagpj.exe"),  # a right-to-left override
        ("zero\u200bwidth\ufeff.txt", "zerowidth.txt"),
        ("control\x00\x07\x1b[31m.log", "control[31m.log"),
        ("Cafe\u0301.txt", "Café.txt"),  # NFC
        ("línia\u2028separada.md", "línia separada.md"),
        ("ok.json", "ok.json"),
    ],
)
def test_display_names_are_safe(raw: str, name: str) -> None:
    assert display_name(raw) == name


def test_long_names_keep_their_extension() -> None:
    name = display_name("a" * 500 + ".markdown")
    assert len(name) == attachments.MAX_NAME_LENGTH
    assert name.endswith("….markdown")
    name = display_name("b" * 500)
    assert len(name) == attachments.MAX_NAME_LENGTH
    assert name.endswith("…")


@pytest.mark.parametrize("raw", [None, "", "   ", ".", "..", "dir/", "\u202e", "\x00\x01"])
def test_names_that_leave_nothing_are_refused(raw: str | None) -> None:
    with pytest.raises(AttachmentError) as refused:
        display_name(raw)
    assert refused.value.status == 422


# -- estimated tokens ----------------------------------------------------------------------


def test_estimated_tokens() -> None:
    assert estimate_tokens("image", width=28, height=28) == 1
    assert estimate_tokens("image", width=29, height=28) == 2
    assert estimate_tokens("image", width=1000, height=500) == 36 * 18
    # Larger than 2576 px on the long edge: fitted first, then capped at 4784.
    assert estimate_tokens("image", width=5152, height=280) == 92 * 5
    assert estimate_tokens("image", width=4032, height=3024) == 4784
    assert estimate_tokens("image", width=None, height=None) == 0
    assert estimate_tokens("pdf", pages=12) == 12 * 3600
    assert estimate_tokens("pdf") == 0
    assert estimate_tokens("text", chars=0) == 0
    assert estimate_tokens("text", chars=1) == 1
    assert estimate_tokens("text", chars=401) == 101


BASE = Attachment(
    kind="pdf",
    name="informe.pdf",
    mime="application/pdf",
    sha256="a" * 64,
    size=1234,
    path=Path("/data/attachments/aa") / ("a" * 64),
    pages=3,
    text="--- Pàgina 1 ---\nHola",
    created_at=datetime(2026, 9, 29, 10, 0, 0, 123456, tzinfo=UTC),
    has_thumbnail=True,
)


def attachment(**changes: Any) -> Attachment:
    return dataclasses.replace(BASE, **changes)


def test_the_snapshot_is_the_protocol_attachment() -> None:
    assert attachments.snapshot(7, attachment()) == {
        "id": 7,
        "name": "informe.pdf",
        "kind": "pdf",
        "mime": "application/pdf",
        "size": 1234,
        "pages": 3,
        "width": None,
        "height": None,
        "sha256": "a" * 64,
        "created_at": "2026-09-29T10:00:00.123Z",
        "has_thumbnail": True,
        "text_available": True,
        "estimated_tokens": 3 * 3600,
    }
    text = attachments.snapshot(
        8, attachment(kind="text", mime="text/plain", pages=None, text="x" * 10)
    )
    assert (text["text_available"], text["estimated_tokens"]) == (True, 3)
    image = attachments.snapshot(
        9,
        attachment(kind="image", mime="image/png", pages=None, text=None, width=56, height=28),
    )
    assert (image["text_available"], image["estimated_tokens"]) == (False, 2)
    assert attachments.attachment_tokens(attachment(pages=2)) == 7200


def test_the_timestamps_are_the_stores() -> None:
    for moment in (
        datetime(2026, 9, 29, 10, 0, 0, 999999, tzinfo=UTC),
        datetime(2026, 1, 2, 3, 4, 5),  # naive: taken as UTC
    ):
        created = attachments.snapshot(1, attachment(created_at=moment))["created_at"]
        assert created == format_ts(moment)


# -- PDFs --------------------------------------------------------------------------------


def write(tmp_path: Path, data: bytes, name: str = "doc") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


async def test_the_pdf_reader_gives_the_pages_and_their_text(tmp_path: Path) -> None:
    path = write(tmp_path, pdf(["Primera pàgina", None, "Tercera (i última) pàgina"]))
    info = await PdfReader().read(path)
    assert info.pages == 3
    assert info.text == (
        "--- Pàgina 1 ---\nPrimera pàgina\n\n"
        "--- Pàgina 2 ---\n\n\n"
        "--- Pàgina 3 ---\nTercera (i última) pàgina"
    )


async def test_a_pdf_without_any_text_has_none(tmp_path: Path) -> None:
    info = await PdfReader().read(write(tmp_path, pdf([None, None])))
    assert (info.pages, info.text) == (2, None)
    info = await PdfReader().read(write(tmp_path, blank_pdf(100)))
    assert (info.pages, info.text) == (100, None)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (encrypted_pdf(), attachments.PDF_ENCRYPTED_DETAIL),
        (blank_pdf(101), "El PDF té 101 pàgines: com a molt 100."),
        (b"%PDF-1.7\nnot a PDF at all\n", attachments.PDF_INVALID_DETAIL),
        (png(10, 10), attachments.PDF_INVALID_DETAIL),
        (blank_pdf(0), attachments.PDF_EMPTY_DETAIL),
    ],
)
async def test_pdfs_that_are_refused(tmp_path: Path, data: bytes, message: str) -> None:
    with pytest.raises(AttachmentError) as refused:
        await PdfReader().read(write(tmp_path, data))
    assert refused.value.status == 422
    assert refused.value.message == message


async def test_a_long_text_is_cut_with_a_notice(tmp_path: Path) -> None:
    path = write(tmp_path, pdf(["a" * 400, "b" * 400, "c" * 400]))
    info = await PdfReader(max_chars=500).read(path)
    assert info.pages == 3
    assert info.text is not None
    notice = "[Text retallat: el text extret del PDF passava de 500 caràcters.]"
    kept, _, end = info.text.partition("\n\n" + notice)
    assert end == ""
    assert kept.startswith("--- Pàgina 1 ---\n" + "a" * 80 + "\n")
    assert "--- Pàgina 2 ---\nbbb" in kept
    assert len(kept) <= 500
    assert "c" not in kept


def stand_in(code: str) -> list[str]:
    """A stand-in for the reader's process (it gets the same arguments)."""
    return [sys.executable, "-c", code]


async def test_a_reader_out_of_time_before_the_pages_is_killed(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    code = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    reader = PdfReader(timeout=1.0, command=stand_in(code))
    with pytest.raises(AttachmentError) as refused:
        await reader.read(write(tmp_path, pdf(["x"])))
    assert refused.value.status == 422
    assert refused.value.message == attachments.PDF_TIMEOUT_DETAIL
    assert not Path(f"/proc/{pid_file.read_text()}").exists()  # killed and reaped


async def test_a_reader_out_of_time_on_the_text_keeps_the_pages(tmp_path: Path) -> None:
    code = "import json, time; print(json.dumps({'pages': 4}), flush=True); time.sleep(60)"
    info = await PdfReader(timeout=1.0, command=stand_in(code)).read(write(tmp_path, b"%PDF-"))
    assert (info.pages, info.text) == (4, None)


@pytest.mark.parametrize(
    "output",
    [
        "",  # it crashed
        "not json\n",
        '{"pages": "tres"}\n',
        '{"pages": true}\n',
        "[1]\n",
    ],
)
async def test_a_reader_that_answers_nonsense_is_an_invalid_pdf(
    tmp_path: Path, output: str
) -> None:
    code = f"import sys; sys.stdout.write({output!r})"
    with pytest.raises(AttachmentError) as refused:
        await PdfReader(command=stand_in(code)).read(write(tmp_path, b"%PDF-"))
    assert refused.value.message == attachments.PDF_INVALID_DETAIL


async def test_the_text_is_cleaned_of_lone_surrogates_and_nul(tmp_path: Path) -> None:
    text = json.dumps({"text": "a\ud800b\x00c 😀"})
    code = f"import json; print(json.dumps({{'pages': 1}})); print({text!r})"
    info = await PdfReader(command=stand_in(code)).read(write(tmp_path, b"%PDF-"))
    assert info.text == "a\ufffdbc 😀"


async def test_cancelling_a_read_kills_the_reader(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    code = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    task = asyncio.create_task(PdfReader(command=stand_in(code)).read(write(tmp_path, b"%PDF-")))
    for _ in range(200):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not Path(f"/proc/{pid_file.read_text()}").exists()


async def test_the_reader_gets_none_of_the_servers_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("AOS_OPENAI_API_KEY", "sk-secret")
    code = (
        "import json, os; print(json.dumps({'pages': 1})); "
        "print(json.dumps({'text': json.dumps(dict(os.environ))}))"
    )
    info = await PdfReader(command=stand_in(code)).read(write(tmp_path, b"%PDF-"))
    assert info.text is not None
    environment = json.loads(info.text)
    assert "ANTHROPIC_API_KEY" not in environment
    assert not [name for name in environment if name.startswith("AOS_")]
    assert set(environment) <= {"LC_ALL", "LC_CTYPE"}


def run_limited(code: str) -> subprocess.CompletedProcess[str]:
    """``code`` in a process that first applies the reader's resource limits."""
    prelude = "from agentic_os.attachments import lower_limits; lower_limits(200 * 2**20, 30); "
    return subprocess.run(
        [sys.executable, "-c", prelude + code], capture_output=True, text=True, check=False
    )


def test_the_reader_runs_within_a_memory_limit() -> None:
    result = run_limited("b = bytearray(400 * 2**20)")
    assert result.returncode != 0
    assert "MemoryError" in result.stderr
    assert run_limited("b = bytearray(20 * 2**20)").returncode == 0


def test_the_reader_cannot_write_files(tmp_path: Path) -> None:
    target = tmp_path / "escrit.txt"
    result = run_limited(f"with open({str(target)!r}, 'w') as file: file.write('x')")
    assert result.returncode != 0
    assert not target.exists() or target.stat().st_size == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root is not bound by RLIMIT_NPROC")
def test_the_reader_cannot_start_processes() -> None:
    result = run_limited("import os; os.fork()")
    assert result.returncode != 0


OOM_SCORE = Path("/proc/self/oom_score_adj")


@pytest.mark.skipif(not OOM_SCORE.exists(), reason="no /proc/self/oom_score_adj")
async def test_the_reader_is_the_first_process_the_oom_killer_takes(tmp_path: Path) -> None:
    """Two readers may take about 1 GiB of the app container's memory: when it runs out,
    the kernel must kill a reader (its upload gets a 422), never a CLI in the middle of a
    turn or the server. A stand-in pypdf reports the score the worker reads it with."""
    if OOM_SCORE.read_text().strip() == str(attachments.OOM_SCORE_ADJ):
        pytest.skip("the tests already run with the reader's score")
    fake = tmp_path / "fake" / "pypdf"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(
        "from pathlib import Path\n\n"
        "class _Page:\n"
        "    def extract_text(self):\n"
        f"        return Path({str(OOM_SCORE)!r}).read_text().strip()\n\n"
        "class PdfReader:\n"
        "    is_encrypted = False\n\n"
        "    def __init__(self, path):\n"
        "        self.pages = [_Page()]\n"
    )
    package_root = Path(attachments.__file__).resolve().parents[1]  # the code under test
    boot = (
        f"import sys; sys.path[:0] = [{str(fake.parent)!r}, {str(package_root)!r}]; "
        "from agentic_os.attachments import pdf_worker; raise SystemExit(pdf_worker(sys.argv[1:]))"
    )
    reader = PdfReader(command=[sys.executable, "-c", boot])
    info = await reader.read(write(tmp_path, b"%PDF-"))
    assert info == attachments.PdfInfo(pages=1, text="--- Pàgina 1 ---\n1000")
