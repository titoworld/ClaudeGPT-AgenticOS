"""Files attached to a question (docs/PROTOCOL.md «Adjunts», docs/adr/0009-adjunts.md).

- The limits, in one place: the server, the store and the engine use these constants.
- What a file is comes from its content, never from its name or its ``Content-Type``:
  magic bytes for images (PNG, JPEG, GIF, WebP) and PDFs; text is valid UTF-8 without NUL
  with an allowed extension. Anything else (SVG and HEIC included) is refused.
- Image dimensions are read from the headers in pure Python (PNG IHDR, JPEG SOF, GIF
  logical screen, WebP VP8/VP8L/VP8X), so no image is ever decoded on the server.
- A PDF is read by pypdf in a subprocess with a timeout and a memory limit (plus no
  file writes, no child processes, no core dumps and a CPU limit), so a hostile or
  huge PDF cannot stall or exhaust the server; the server process itself never parses
  a PDF. If the container runs out of memory anyway, the reader is the process the
  kernel kills first (:data:`OOM_SCORE_ADJ`).
- The display name of an upload is sanitized: no path, no control or invisible format
  characters (bidirectional overrides...), bounded length.
- ``estimated_tokens``: an approximation shown before sending (see :func:`estimate_tokens`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
import struct
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from agentic_os.orchestrator.store import JsonValue
from agentic_os.providers.base import Attachment, AttachmentKind

# -- limits ------------------------------------------------------------------------------

MAX_ATTACHMENTS: Final = 5
"""Attachments one message may carry."""
MAX_TURN_BYTES: Final = 20_000_000
"""Raw bytes of all the attachments of one turn."""
MAX_IMAGE_BYTES: Final = 7_000_000
"""One image (the Claude API takes at most 10 MB base64-encoded per image)."""
MAX_IMAGE_SIDE: Final = 8000
"""Pixels per side of an image (the Claude API's limit)."""
DOWNSCALE_EDGE: Final = 2576
"""The models see an image with its long edge at most this long (Claude 4.7 and later):
the browser downscales a larger one before uploading it, and the token estimate fits it
into this edge."""
MAX_PDF_BYTES: Final = 20_000_000
MAX_PDF_PAGES: Final = 100
"""Pages of a PDF (the Claude API's limit below a 1M-token context window)."""
MAX_TEXT_BYTES: Final = 200_000
"""One text file."""
MAX_UPLOAD_BYTES: Final = max(MAX_IMAGE_BYTES, MAX_PDF_BYTES, MAX_TEXT_BYTES)
"""Where an upload is cut before its type is known."""
MAX_THUMBNAIL_BYTES: Final = 100_000
MAX_THUMBNAIL_SIDE: Final = 512
MAX_NAME_LENGTH: Final = 200
"""Characters of a display name (a longer one is shortened, its extension kept)."""
ORPHAN_TTL: Final = timedelta(hours=24)
"""An attachment never sent in a turn is deleted this long after its upload."""

TEXT_EXTENSIONS: Final = frozenset(
    {
        # documents and data
        "txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "xml", "html",
        "htm", "log", "ini", "toml", "cfg",
        # source code
        "py", "js", "ts", "jsx", "tsx", "svelte", "css", "scss", "sql", "sh", "bash",
        "rs", "go", "java", "kt", "c", "h", "cpp", "hpp", "cs", "rb", "php", "swift",
        "lua", "r", "pl",
    }
)  # fmt: skip
"""Extensions of the text files accepted (their content must also be UTF-8 text)."""

PDF_TIMEOUT_SECONDS: Final = 60.0
"""Wall time the PDF reader has for the page count and the text together."""
PDF_MEMORY_BYTES: Final = 512 * 1024 * 1024
"""Address space of the PDF reader's process (``RLIMIT_AS``)."""
PDF_CONCURRENCY: Final = 2
"""PDFs read at the same time; more uploads wait for a turn."""
OOM_SCORE_ADJ: Final = 1000
"""``oom_score_adj`` of the PDF reader's process, the highest: two readers may take about
1 GiB of the app container's memory, and if it runs out the kernel must kill a reader
(its upload gets a 422), never a CLI in the middle of a turn or the server."""
MAX_PDF_TEXT_CHARS: Final = 1_000_000
"""Characters of the text kept from a PDF (about 250 000 tokens): beyond it the text is
cut, with a notice at the end."""

# Token estimate (approximate, shown on the attachment card before sending).
IMAGE_TOKEN_PATCH: Final = 28
"""An image costs one token per 28 x 28 pixel patch."""
MAX_IMAGE_TOKENS: Final = 4784
PDF_PAGE_TOKENS: Final = 3600
"""A PDF page is sent as its text and as an image: about 1 500 - 3 000 text tokens plus
the image's."""
TEXT_CHARS_PER_TOKEN: Final = 4

# -- types -------------------------------------------------------------------------------

PNG: Final = "image/png"
JPEG: Final = "image/jpeg"
GIF: Final = "image/gif"
WEBP: Final = "image/webp"
PDF: Final = "application/pdf"
TEXT: Final = "text/plain"
IMAGE_TYPES: Final = (PNG, JPEG, GIF, WEBP)
SNIFF_BYTES: Final = 16
"""Bytes enough to tell every accepted type apart (and HEIC, for its message)."""

KIND_LIMITS: Final[Mapping[AttachmentKind, int]] = {
    "image": MAX_IMAGE_BYTES,
    "pdf": MAX_PDF_BYTES,
    "text": MAX_TEXT_BYTES,
}


@dataclass(frozen=True, slots=True)
class FileType:
    kind: AttachmentKind
    mime: str


IMAGE_PNG: Final = FileType("image", PNG)
IMAGE_JPEG: Final = FileType("image", JPEG)
IMAGE_GIF: Final = FileType("image", GIF)
IMAGE_WEBP: Final = FileType("image", WEBP)
PDF_FILE: Final = FileType("pdf", PDF)
TEXT_FILE: Final = FileType("text", TEXT)


class AttachmentError(Exception):
    """A file that is refused. ``status`` is the HTTP status of the answer (413 too
    big, 415 a type that is not accepted, 422 not valid) and ``message`` the reason, in
    Catalan, fit to show to the owner as it is."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _number(value: int) -> str:
    """Catalan thousands: ``100.000``."""
    return f"{value:,}".replace(",", ".")


def size_text(size: int) -> str:
    """A decimal size as the messages write it: ``20 MB``, ``200 kB``."""
    if size >= 1_000_000 and size % 1_000_000 == 0:
        return f"{size // 1_000_000} MB"
    if size >= 1000 and size % 1000 == 0:
        return f"{size // 1000} kB"
    return f"{_number(size)} bytes"


UNSUPPORTED_DETAIL: Final = (
    "Aquest tipus de fitxer no s'admet. Pots adjuntar imatges (PNG, JPEG, GIF o WebP), "
    "PDF i fitxers de text (UTF-8)."
)
SVG_DETAIL: Final = (
    "Les imatges SVG no s'admeten, perquè poden portar codi. Converteix-la a PNG i torna-la "
    "a adjuntar."
)
HEIC_DETAIL: Final = (
    "Les imatges HEIC no s'admeten. Converteix-la a JPEG (o fes-ne una captura) i torna-la "
    "a adjuntar."
)
EMPTY_DETAIL: Final = "El fitxer és buit."
NAME_REQUIRED_DETAIL: Final = "Cal indicar el nom del fitxer (paràmetre «name»)."
BAD_NAME_DETAIL: Final = "El nom del fitxer no és vàlid."
BAD_IMAGE_DETAIL: Final = (
    "No s'han pogut llegir les dimensions de la imatge: el fitxer no és vàlid."
)
PDF_INVALID_DETAIL: Final = "El PDF no és vàlid o està malmès."
PDF_ENCRYPTED_DETAIL: Final = (
    "El PDF està xifrat o protegit amb contrasenya. Treu-ne la protecció i torna'l a adjuntar."
)
PDF_EMPTY_DETAIL: Final = "El PDF no té cap pàgina."
PDF_TIMEOUT_DETAIL: Final = (
    f"No s'ha pogut llegir el PDF en {int(PDF_TIMEOUT_SECONDS)} segons. Prova'n una versió "
    "més senzilla o més petita."
)
THUMBNAIL_TYPE_DETAIL: Final = "La miniatura ha de ser una imatge PNG o WebP."
THUMBNAIL_BAD_DETAIL: Final = "No s'han pogut llegir les dimensions de la miniatura."
_KIND_NAMES: Final[Mapping[AttachmentKind, str]] = {
    "image": "una imatge",
    "pdf": "un PDF",
    "text": "un fitxer de text",
}


def too_large_detail(kind: AttachmentKind) -> str:
    return (
        f"El fitxer és massa gran: {_KIND_NAMES[kind]} pot tenir com a molt "
        f"{size_text(KIND_LIMITS[kind])}."
    )


def thumbnail_too_large_detail() -> str:
    return f"La miniatura és massa gran: com a molt {size_text(MAX_THUMBNAIL_BYTES)}."


def image_too_big_detail(width: int, height: int) -> str:
    return (
        f"La imatge fa {_number(width)} x {_number(height)} píxels: com a molt "
        f"{_number(MAX_IMAGE_SIDE)} per costat."
    )


def thumbnail_too_big_detail(width: int, height: int) -> str:
    return (
        f"La miniatura fa {_number(width)} x {_number(height)} píxels: com a molt "
        f"{MAX_THUMBNAIL_SIDE} per costat."
    )


def pdf_pages_detail(pages: int) -> str:
    return f"El PDF té {_number(pages)} pàgines: com a molt {MAX_PDF_PAGES}."


# -- names -------------------------------------------------------------------------------

_DROPPED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
"""Control characters, invisible format characters (bidirectional overrides, zero-width
characters...), surrogates and line or paragraph separators."""
_MAX_EXTENSION: Final = 20


def display_name(raw: str | None) -> str:
    """The name shown for an uploaded file, from the name the browser gave.

    Only the last part of a path is kept (``/`` or ``\\``), in NFC, without control or
    invisible format characters (a bidirectional override could disguise the type), with
    runs of blank space as one space, trimmed, and at most :data:`MAX_NAME_LENGTH`
    characters (a long one keeps its extension). The name is only ever shown and
    downloaded with: files are stored by their content's hash. Raises
    :class:`AttachmentError` (422) if no name is given or nothing usable is left."""
    if raw is None or not raw.strip():
        raise AttachmentError(422, NAME_REQUIRED_DETAIL)
    base = re.split(r"[/\\]", raw)[-1]
    kept = "".join(
        " " if char.isspace() else char
        for char in unicodedata.normalize("NFC", base)
        if char.isspace() or unicodedata.category(char) not in _DROPPED_CATEGORIES
    )
    name = " ".join(kept.split())
    if not name.strip("."):
        raise AttachmentError(422, BAD_NAME_DETAIL)
    if len(name) > MAX_NAME_LENGTH:
        stem, dot, extension = name.rpartition(".")
        if dot and stem and len(extension) <= _MAX_EXTENSION:
            keep = MAX_NAME_LENGTH - len(extension) - 2
            name = f"{stem[:keep].rstrip()}….{extension}"
        else:
            name = name[: MAX_NAME_LENGTH - 1].rstrip() + "…"
    return name


def extension(name: str) -> str:
    """The lowercase extension of a display name (``""`` without one)."""
    stem, dot, suffix = name.rpartition(".")
    return suffix.lower() if dot and stem else ""


# -- type of a file ----------------------------------------------------------------------

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_HEIF_BRANDS: Final = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif"}
)


def sniff(head: bytes) -> FileType | None:
    """The type of a file from its first bytes (:data:`SNIFF_BYTES` are enough): PNG,
    JPEG, GIF, WebP or PDF. ``None`` for anything else; text is decided from the whole
    content and the name (:func:`upload_type`, :func:`text_content`)."""
    if head.startswith(_PNG_SIGNATURE):
        return IMAGE_PNG
    if head.startswith(b"\xff\xd8\xff"):
        return IMAGE_JPEG
    if head.startswith((b"GIF87a", b"GIF89a")):
        return IMAGE_GIF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return IMAGE_WEBP
    if head.startswith(b"%PDF-"):
        return PDF_FILE
    return None


def _unsupported(head: bytes, suffix: str) -> AttachmentError:
    if suffix in ("svg", "svgz"):
        return AttachmentError(415, SVG_DETAIL)
    if suffix in ("heic", "heif", "avif") or (head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS):
        return AttachmentError(415, HEIC_DETAIL)
    return AttachmentError(415, UNSUPPORTED_DETAIL)


def upload_type(head: bytes, name: str) -> tuple[FileType, int]:
    """The type of an upload from its first bytes (at least :data:`SNIFF_BYTES`, or all
    of them if it is shorter) and, for text only, the extension of its display name;
    with the most bytes a file of that type may have. A file with no known signature is
    a text candidate if its extension is allowed and its start has no NUL (the whole
    content is checked by :func:`text_content`). Raises :class:`AttachmentError` 415
    otherwise, SVG and HEIC included."""
    found = sniff(head)
    if found is not None:
        return found, KIND_LIMITS[found.kind]
    suffix = extension(name)
    if suffix in TEXT_EXTENSIONS and b"\x00" not in head:
        return TEXT_FILE, MAX_TEXT_BYTES
    raise _unsupported(head, suffix)


def text_content(data: bytes, name: str = "") -> str:
    """The content of a text file: valid UTF-8 without NUL (a leading byte order mark
    is dropped). Raises :class:`AttachmentError` 415: anything else is not text."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _unsupported(data[:SNIFF_BYTES], extension(name)) from None
    if "\x00" in text:
        raise _unsupported(data[:SNIFF_BYTES], extension(name))
    return text.removeprefix("﻿")


# -- image dimensions ----------------------------------------------------------------------

_JPEG_SOF: Final = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
"""Start-of-frame markers (not DHT C4, JPG C8 or DAC CC, which share the range)."""
_JPEG_STANDALONE: Final = frozenset({0x01, 0xD8, *range(0xD0, 0xD8)})
"""Markers without a length: TEM, SOI and the restart markers."""
_JPEG_FILL: Final = re.compile(rb"\xff+")
"""A marker's 0xFF and the fill bytes before it."""
_JPEG_MAX_MARKERS: Final = 1000
"""A real JPEG has a few dozen segments before its frame header: past this many
markers the file is not read any further (it is not a valid image)."""


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[8:16] != b"\x00\x00\x00\x0dIHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    index, end = 2, len(data)
    for _ in range(_JPEG_MAX_MARKERS):
        index = data.find(b"\xff", index)  # stray bytes between segments are skipped
        if index < 0:
            return None
        fill = _JPEG_FILL.match(data, index)
        if fill is None:  # pragma: no cover - there is a 0xFF at index
            return None
        index = fill.end()
        if index >= end:
            return None
        marker = data[index]
        index += 1
        if marker in _JPEG_STANDALONE:
            continue
        if marker in (0xD9, 0xDA) or index + 2 > end:  # EOI or scan data before a frame
            return None
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2:
            return None
        if marker in _JPEG_SOF:
            if length < 7 or index + 7 > end:
                return None
            height, width = struct.unpack(">HH", data[index + 3 : index + 7])
            return width, height
        index += length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":  # lossy: a key frame's header
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L":  # lossless
        if data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":  # extended: the canvas
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    return None


_SIZE_READERS: Final = {PNG: _png_size, JPEG: _jpeg_size, GIF: _gif_size, WEBP: _webp_size}


def image_size(data: bytes, mime: str) -> tuple[int, int] | None:
    """Width and height of an image of type ``mime``, read from its headers (a GIF's
    logical screen, a WebP's canvas); ``None`` when they cannot be read or are 0."""
    reader = _SIZE_READERS.get(mime)
    size = reader(data) if reader is not None else None
    if size is None or size[0] <= 0 or size[1] <= 0:
        return None
    return size


def image_dimensions(data: bytes, mime: str) -> tuple[int, int]:
    """:func:`image_size` of an uploaded image within the limits. Raises
    :class:`AttachmentError` 422 when it cannot be read or a side is longer than
    :data:`MAX_IMAGE_SIDE`."""
    size = image_size(data, mime)
    if size is None:
        raise AttachmentError(422, BAD_IMAGE_DETAIL)
    width, height = size
    if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
        raise AttachmentError(422, image_too_big_detail(width, height))
    return size


def thumbnail_type(data: bytes) -> str:
    """The type of a thumbnail the browser made: a PNG or WebP of at most
    :data:`MAX_THUMBNAIL_BYTES` and :data:`MAX_THUMBNAIL_SIDE` pixels per side.
    Raises :class:`AttachmentError` (413, 415 or 422)."""
    if len(data) > MAX_THUMBNAIL_BYTES:
        raise AttachmentError(413, thumbnail_too_large_detail())
    found = sniff(data[:SNIFF_BYTES])
    if found is None or found.mime not in (PNG, WEBP):
        raise AttachmentError(415, THUMBNAIL_TYPE_DETAIL)
    size = image_size(data, found.mime)
    if size is None:
        raise AttachmentError(422, THUMBNAIL_BAD_DETAIL)
    if size[0] > MAX_THUMBNAIL_SIDE or size[1] > MAX_THUMBNAIL_SIDE:
        raise AttachmentError(422, thumbnail_too_big_detail(*size))
    return found.mime


# -- estimated tokens --------------------------------------------------------------------


def estimate_tokens(
    kind: AttachmentKind,
    *,
    width: int | None = None,
    height: int | None = None,
    pages: int | None = None,
    chars: int | None = None,
) -> int:
    """Approximate input tokens of an attachment for one call.

    - Image: ``ceil(w'/28) * ceil(h'/28)``, at most :data:`MAX_IMAGE_TOKENS`, with
      ``(w', h')`` the image fitted into :data:`DOWNSCALE_EDGE` on its long edge (never
      enlarged).
    - PDF: :data:`PDF_PAGE_TOKENS` per page.
    - Text: ``ceil(chars / 4)`` (``chars``: characters of the text)."""
    if kind == "image":
        if not width or not height:
            return 0
        long_edge = max(width, height)
        if long_edge > DOWNSCALE_EDGE:
            width = max(1, width * DOWNSCALE_EDGE // long_edge)
            height = max(1, height * DOWNSCALE_EDGE // long_edge)
        patches = math.ceil(width / IMAGE_TOKEN_PATCH) * math.ceil(height / IMAGE_TOKEN_PATCH)
        return min(MAX_IMAGE_TOKENS, patches)
    if kind == "pdf":
        return (pages or 0) * PDF_PAGE_TOKENS
    return math.ceil((chars or 0) / TEXT_CHARS_PER_TOKEN)


def attachment_tokens(attachment: Attachment) -> int:
    """:func:`estimate_tokens` of an attachment as the store gives it to the engine."""
    return estimate_tokens(
        attachment.kind,
        width=attachment.width,
        height=attachment.height,
        pages=attachment.pages,
        chars=len(attachment.text) if attachment.text is not None else None,
    )


def _timestamp(value: datetime) -> str:
    """UTC ``YYYY-MM-DDTHH:MM:SS.mmmZ``, as :func:`agentic_os.storage.models.format_ts`."""
    utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def attachment_wire(
    attachment_id: int,
    *,
    name: str,
    kind: AttachmentKind,
    mime: str,
    size: int,
    sha256: str,
    pages: int | None,
    width: int | None,
    height: int | None,
    created_at: datetime | None,
    has_thumbnail: bool,
    text_chars: int | None,
) -> dict[str, JsonValue]:
    """``Attachment`` of docs/PROTOCOL.md. ``text_chars`` are the characters of the
    stored text (a text file's content, a PDF's extracted text), ``None`` without one."""
    return {
        "id": attachment_id,
        "name": name,
        "kind": kind,
        "mime": mime,
        "size": size,
        "pages": pages,
        "width": width,
        "height": height,
        "sha256": sha256,
        "created_at": _timestamp(created_at) if created_at is not None else None,
        "has_thumbnail": has_thumbnail,
        "text_available": text_chars is not None,
        "estimated_tokens": estimate_tokens(
            kind, width=width, height=height, pages=pages, chars=text_chars
        ),
    }


def snapshot(attachment_id: int, attachment: Attachment) -> dict[str, JsonValue]:
    """The ``Attachment`` wire of an attachment the store gave the engine (the question's
    ``meta.attachments``)."""
    return attachment_wire(
        attachment_id,
        name=attachment.name,
        kind=attachment.kind,
        mime=attachment.mime,
        size=attachment.size,
        sha256=attachment.sha256,
        pages=attachment.pages,
        width=attachment.width,
        height=attachment.height,
        created_at=attachment.created_at,
        has_thumbnail=attachment.has_thumbnail,
        text_chars=len(attachment.text) if attachment.text is not None else None,
    )


# -- PDFs ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PdfInfo:
    pages: int
    text: str | None
    """The extracted text, one block per page introduced by «--- Pàgina N ---»; ``None``
    when it could not be extracted or no page has any (a scanned PDF)."""


def page_header(number: int) -> str:
    return f"--- Pàgina {number} ---"


def cut_notice(limit: int) -> str:
    return f"[Text retallat: el text extret del PDF passava de {_number(limit)} caràcters.]"


def clean_text(text: str) -> str:
    """Text that can be stored and sent as JSON: lone surrogates (a broken font map can
    yield them) become U+FFFD and NUL characters are dropped."""
    fixed = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return fixed.replace("\x00", "")


def _page_texts(pages: Sequence[str], limit: int) -> str | None:
    """The text of a PDF from the text of each page (see :class:`PdfInfo`)."""
    if not any(text.strip() for text in pages):
        return None
    blocks = [
        f"{page_header(number)}\n{text.lstrip(chr(13) + chr(10)).rstrip()}"
        for number, text in enumerate(pages, 1)
    ]
    joined = "\n\n".join(blocks)
    if len(joined) > limit:
        joined = f"{joined[:limit].rstrip()}\n\n{cut_notice(limit)}"
    return joined


def lower_limits(memory_bytes: int, cpu_seconds: int) -> None:
    """Resource limits of the PDF reader's process, set on itself before it reads the
    file: address space, CPU time, no files written, no core dumps and no new processes
    or threads. A limit is only ever lowered."""
    import resource

    def lower(which: int, value: int) -> None:
        _, hard = resource.getrlimit(which)
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(which, (value, value))

    lower(resource.RLIMIT_AS, memory_bytes)
    lower(resource.RLIMIT_CPU, cpu_seconds)
    lower(resource.RLIMIT_FSIZE, 0)
    lower(resource.RLIMIT_CORE, 0)
    lower(resource.RLIMIT_NPROC, 0)


def prefer_oom_kill() -> None:
    """Make this process the first one the kernel's OOM killer takes
    (:data:`OOM_SCORE_ADJ`: raising a process's own score needs no privilege). Where
    there is no ``/proc`` it stays as it is."""
    with contextlib.suppress(OSError):
        Path("/proc/self/oom_score_adj").write_text(f"{OOM_SCORE_ADJ}\n", encoding="ascii")


def pdf_worker(argv: Sequence[str]) -> int:
    """Entry point of the PDF reader's process (see :class:`PdfReader`). Arguments: the
    path, the page limit, the memory limit in bytes and the text limit in characters.
    Before reading anything it offers itself to the OOM killer (:func:`prefer_oom_kill`)
    and lowers its own limits (:func:`lower_limits`).

    Writes JSON lines on stdout: first ``{"pages": n}``, ``{"encrypted": true}`` or
    ``{"invalid": true}``; then, if the PDF has from 1 to the page limit pages,
    ``{"text": str | null}``."""
    path, max_pages, memory, max_chars = argv[0], int(argv[1]), int(argv[2]), int(argv[3])
    prefer_oom_kill()
    lower_limits(memory, int(PDF_TIMEOUT_SECONDS) + 5)
    import logging
    import warnings

    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")

    def emit(value: object) -> None:
        sys.stdout.write(json.dumps(value, ensure_ascii=True) + "\n")
        sys.stdout.flush()

    from pypdf import PdfReader as Reader  # only ever imported here, after the limits

    try:
        reader = Reader(path)
        if reader.is_encrypted:
            emit({"encrypted": True})
            return 0
        pages = len(reader.pages)
    except Exception:
        emit({"invalid": True})
        return 0
    emit({"pages": pages})
    if not 1 <= pages <= max_pages:
        return 0
    texts: list[str] = []
    try:
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            texts.append(text)
            total += len(text)
            if total > max_chars:
                break
    except Exception:
        emit({"text": None})
        return 0
    emit({"text": _page_texts(texts, max_chars)})
    return 0


_WORKER_BOOT: Final = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from agentic_os.attachments import pdf_worker; raise SystemExit(pdf_worker(sys.argv[2:]))"
)
_PACKAGE_ROOT: Final = str(Path(__file__).resolve().parents[1])
_WORKER_ENV: Final = {"LC_ALL": "C.UTF-8"}
"""The reader's whole environment: nothing of the server's (keys, tokens) reaches it."""
_MAX_WORKER_LINE: Final = 16 * 1024 * 1024
"""Longest line read from the reader (the text line: at most 12 bytes per character)."""


class PdfReader:
    """Reads the page count and the text of stored PDFs with pypdf, each in a new
    process of its own: isolated Python (``-I``: no environment variables, no user
    site), with an empty environment of the server's and the resource limits of
    :func:`lower_limits`. At most ``concurrency`` run at once. Cancelling a read kills
    its process."""

    def __init__(
        self,
        *,
        timeout: float = PDF_TIMEOUT_SECONDS,
        memory_bytes: int = PDF_MEMORY_BYTES,
        concurrency: int = PDF_CONCURRENCY,
        max_pages: int = MAX_PDF_PAGES,
        max_chars: int = MAX_PDF_TEXT_CHARS,
        command: Sequence[str] | None = None,
    ) -> None:
        self._timeout = timeout
        self._memory = memory_bytes
        self._max_pages = max_pages
        self._max_chars = max_chars
        self._command = tuple(command) if command is not None else None
        self._slots = asyncio.Semaphore(concurrency)

    def _argv(self, path: Path) -> tuple[str, ...]:
        arguments = (str(path), str(self._max_pages), str(self._memory), str(self._max_chars))
        if self._command is not None:  # tests: a stand-in reader
            return (*self._command, *arguments)
        return (sys.executable, "-I", "-B", "-c", _WORKER_BOOT, _PACKAGE_ROOT, *arguments)

    async def read(self, path: Path) -> PdfInfo:
        """The page count and the text of the PDF at ``path``. Raises
        :class:`AttachmentError` 422 for an encrypted PDF, one without pages or with more
        than the page limit, or one whose pages could not be counted (not a PDF, broken,
        or the reader ran out of time or memory first). A text that could not be
        extracted in time is ``None``."""
        async with self._slots:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._timeout
            process = await asyncio.create_subprocess_exec(
                *self._argv(path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=_WORKER_ENV,
                cwd="/",
                start_new_session=True,
                limit=_MAX_WORKER_LINE,
            )
            try:
                return await self._results(process, deadline)
            finally:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                await process.wait()

    async def _results(self, process: asyncio.subprocess.Process, deadline: float) -> PdfInfo:
        first = await _line(process, deadline)
        if first is _TIMEOUT:
            raise AttachmentError(422, PDF_TIMEOUT_DETAIL)
        if not isinstance(first, dict):
            raise AttachmentError(422, PDF_INVALID_DETAIL)
        if first.get("encrypted") is True:
            raise AttachmentError(422, PDF_ENCRYPTED_DETAIL)
        pages = first.get("pages")
        if not isinstance(pages, int) or isinstance(pages, bool) or pages < 0:
            raise AttachmentError(422, PDF_INVALID_DETAIL)
        if pages == 0:
            raise AttachmentError(422, PDF_EMPTY_DETAIL)
        if pages > self._max_pages:
            raise AttachmentError(422, pdf_pages_detail(pages))
        second = await _line(process, deadline)
        text = second.get("text") if isinstance(second, dict) else None
        if not isinstance(text, str) or not text.strip():
            return PdfInfo(pages=pages, text=None)
        return PdfInfo(pages=pages, text=clean_text(text))


class _Timeout:
    pass


_TIMEOUT: Final = _Timeout()


async def _line(process: asyncio.subprocess.Process, deadline: float) -> object:
    """The next JSON line of the reader; :data:`_TIMEOUT` past the deadline and
    ``None`` if it ended, wrote something else or a line too long."""
    stdout = process.stdout
    if stdout is None:  # pragma: no cover - always a pipe
        return None
    try:
        async with asyncio.timeout_at(deadline):
            line = await stdout.readline()
    except TimeoutError:
        return _TIMEOUT
    except ValueError:  # longer than the stream's limit
        return None
    try:
        value: object = json.loads(line)
    except ValueError:
        return None
    return value


def is_sha256(value: str) -> bool:
    """A SHA-256 as stored: 64 lowercase hexadecimal digits (and so a safe file name)."""
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


__all__ = [
    "KIND_LIMITS",
    "MAX_ATTACHMENTS",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_SIDE",
    "MAX_PDF_BYTES",
    "MAX_PDF_PAGES",
    "MAX_TEXT_BYTES",
    "MAX_THUMBNAIL_BYTES",
    "MAX_THUMBNAIL_SIDE",
    "MAX_TURN_BYTES",
    "MAX_UPLOAD_BYTES",
    "ORPHAN_TTL",
    "TEXT_EXTENSIONS",
    "AttachmentError",
    "FileType",
    "PdfInfo",
    "PdfReader",
    "attachment_tokens",
    "attachment_wire",
    "display_name",
    "estimate_tokens",
    "image_dimensions",
    "image_size",
    "snapshot",
    "sniff",
    "text_content",
    "thumbnail_type",
    "upload_type",
]
