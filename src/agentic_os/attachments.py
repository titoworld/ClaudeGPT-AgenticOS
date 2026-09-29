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
- In the same pass as the text, the reader analyses every page (:class:`PageSigns`):
  where its text is in the stored text, its letters and broken characters, whether it
  draws an image, and the text it shows invisibly, smaller than a point or off its
  visible box (``pdf_facts.PdfPage``). Claude's check of the text that ChatGPT with
  the subscription reads starts from these facts.
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
from typing import Any, Final

from agentic_os.orchestrator.store import JsonValue
from agentic_os.pdf_facts import PdfNotes, PdfPage, pages_from_data
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
TINY_POINTS: Final = 1.0
"""Text shown smaller than this many points, every scale it is drawn with included,
cannot be read on the page."""
OFFPAGE_MARGIN: Final = 1.0
"""Points outside a page's visible box beyond which the origin of a text is off the page."""
INVISIBLE_MODES: Final = frozenset({3, 7})
"""Text render modes that paint nothing: 3 (neither fill nor stroke) and 7 (only add to
the clipping path)."""
MAX_FORM_DEPTH: Final = 5
"""Levels of forms (a form drawn by a form...) in whose resources a page's images are
looked for."""

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
    pdf_notes: PdfNotes | None = None,
) -> dict[str, JsonValue]:
    """``Attachment`` of docs/PROTOCOL.md. ``text_chars`` are the characters of the
    stored text (a text file's content, a PDF's extracted text), ``None`` without one;
    ``pdf_notes``, the warnings of an analysed PDF's pages (``None`` for anything else)."""
    notes: JsonValue = None
    if pdf_notes is not None:
        notes = {kind: list(pages) for kind, pages in pdf_notes.to_wire().items()}
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
        "pdf_notes": notes,
    }


def snapshot(attachment_id: int, attachment: Attachment) -> dict[str, JsonValue]:
    """The ``Attachment`` wire of an attachment the store gave the engine (the question's
    ``meta.attachments``), with the notes of an analysed PDF's pages."""
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
        pdf_notes=attachment.pdf_notes,
    )


# -- PDFs ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PdfInfo:
    pages: int
    text: str | None
    """The extracted text, one block per page introduced by «--- Pàgina N ---»; ``None``
    when it could not be extracted or no page has any (a scanned PDF)."""
    pdf_pages: tuple[PdfPage, ...] | None = None
    """What the reader found on each page (``text[start:end]`` is a page's text); ``None``
    when the PDF could not be analysed (it is then read as before, unchecked)."""


def page_header(number: int) -> str:
    return f"--- Pàgina {number} ---"


def cut_notice(limit: int) -> str:
    return f"[Text retallat: el text extret del PDF passava de {_number(limit)} caràcters.]"


def clean_text(text: str) -> str:
    """Text that can be stored and sent as JSON: lone surrogates (a broken font map can
    yield them) become U+FFFD and NUL characters are dropped."""
    fixed = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return fixed.replace("\x00", "")


Span = tuple[int | None, int | None, bool]
"""Where a page's text is in a PDF's stored text: ``start``, ``end`` (``None`` when none of
it is stored) and whether the text's limit cut any of it off (``pdf_facts.PdfPage``)."""


class StoredText:
    """The stored text of a PDF, built page by page: a block per page, «--- Pàgina N ---»
    and the page's text (without the line breaks it starts with or the white space it
    ends with), blocks apart by a blank line, and the whole cut at ``limit`` characters
    with :func:`cut_notice`. Once past the limit a page's text is no longer kept (only
    whether it had any), so a PDF of long pages does not fill the reader's memory."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._blocks: list[str] = []
        self._length = 0
        """Characters of the blocks kept, joined."""
        self._spans: list[tuple[int, int] | None] = []
        self._lost: list[bool] = []
        """Pages with text that came after the limit."""

    def add(self, text: str) -> None:
        """The next page's extracted text."""
        body = text.lstrip("\r\n").rstrip()
        if self._length > self._limit:
            self._spans.append(None)
            self._lost.append(bool(body))
            return
        header = f"{page_header(len(self._spans) + 1)}\n"
        start = self._length + (2 if self._blocks else 0) + len(header)
        self._blocks.append(header + body)
        self._length = start + len(body)
        self._spans.append((start, start + len(body)) if body else None)
        self._lost.append(False)

    def finish(self) -> tuple[str | None, list[Span]]:
        """The stored text (``None`` when no page has any) and each page's :data:`Span`:
        the page the cut goes through ends at it, and the later ones are not stored."""
        if not any(self._lost) and all(span is None for span in self._spans):
            return None, [(None, None, False)] * len(self._spans)
        text = "\n\n".join(self._blocks)
        kept = len(text)
        if kept > self._limit:
            kept = len(text[: self._limit].rstrip())
            text = f"{text[:kept]}\n\n{cut_notice(self._limit)}"
        spans: list[Span] = []
        for span, lost in zip(self._spans, self._lost, strict=True):
            if span is None:
                spans.append((None, None, lost))
            elif span[1] <= kept:
                spans.append((span[0], span[1], False))
            elif span[0] < kept:
                spans.append((span[0], kept, True))
            else:
                spans.append((None, None, True))
        return text, spans


def text_counts(text: str) -> tuple[int, int, int]:
    """Characters of a page's extracted text other than white space, its letters, and
    its broken characters: U+FFFD, private-use characters, lone surrogates and control
    characters other than tab and line breaks (what a font without a usable character
    map yields)."""
    chars = letters = garbage = 0
    for char in text:
        if not char.isspace():
            chars += 1
        if char.isalpha():
            letters += 1
        elif char == "\ufffd":
            garbage += 1
        else:
            category = unicodedata.category(char)
            if category in ("Co", "Cs") or (category == "Cc" and char not in "\t\n\r"):
                garbage += 1
    return chars, letters, garbage


# What the reader's page analysis walks is pypdf's objects (only ever imported in the
# reader's process): it reads them as plain dictionaries, lists and numbers, resolving
# indirect references, and never trusts their shape.

Matrix = tuple[float, float, float, float, float, float]
"""A PDF matrix ``[a b c d e f]``, of row vectors: a point ``(x, y)`` goes to
``(a·x + c·y + e, b·x + d·y + f)``."""
IDENTITY: Final[Matrix] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
Box = tuple[float, float, float, float]
"""Left, bottom, right and top of a rectangle, in points."""
_SHOWING: Final = frozenset({b"Tj", b"TJ", b"'", b'"'})
"""The operators that show text."""
_INLINE_IMAGES: Final = frozenset({b"BI", b"INLINE IMAGE"})


def multiply(first: Sequence[float], second: Sequence[float]) -> Matrix:
    """The matrix that applies ``first`` and then ``second``."""
    a, b, c, d, e, f = first[:6]
    g, h, i, j, k, m = second[:6]
    return (
        a * g + b * i,
        a * h + b * j,
        c * g + d * i,
        c * h + d * j,
        e * g + f * i + k,
        e * h + f * j + m,
    )


def _resolved(value: object) -> object:
    """A PDF object itself, not the indirect reference to it."""
    get_object = getattr(value, "get_object", None)
    return get_object() if callable(get_object) else value


def _entry(mapping: object, key: object) -> object:
    """``mapping[key]`` of a PDF dictionary, resolved; ``None`` when it is not one or has
    no such entry."""
    if not isinstance(mapping, dict):
        return None
    return _resolved(mapping.get(key))


def _numbers(value: object, count: int) -> list[float] | None:
    """The ``count`` finite numbers of a PDF array, or ``None``."""
    value = _resolved(value)
    if not isinstance(value, (list, tuple)) or len(value) != count:
        return None
    numbers: list[float] = []
    for item in value:
        item = _resolved(item)
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return None
        try:
            number = float(item)
        except OverflowError:  # an integer too long for a float
            return None
        if not math.isfinite(number):
            return None
        numbers.append(number)
    return numbers


def _matrix(value: object) -> Matrix:
    """A form's ``/Matrix`` (the identity when it has none, or not a valid one)."""
    numbers = _numbers(value, 6)
    if numbers is None:
        return IDENTITY
    a, b, c, d, e, f = numbers
    return (a, b, c, d, e, f)


def _box(value: object) -> Box | None:
    numbers = _numbers(value, 4)
    if numbers is None:
        return None
    left, bottom, right, top = numbers
    return (min(left, right), min(bottom, top), max(left, right), max(bottom, top))


def visible_box(page: Any) -> Box | None:
    """The part of a page a viewer shows: its crop box within its media box (``None``
    when the page does not say)."""
    try:
        crop = _box(list(page.cropbox))
    except Exception:
        crop = None
    try:
        media = _box(list(page.mediabox))
    except Exception:
        media = None
    if crop is None or media is None:
        return crop or media
    return (
        max(crop[0], media[0]),
        max(crop[1], media[1]),
        min(crop[2], media[2]),
        min(crop[3], media[3]),
    )


def has_images(resources: object, depth: int = 0, seen: set[int] | None = None) -> bool:
    """Whether a page's (or a form's) resources have an image, their own or one of their
    forms' (:data:`MAX_FORM_DEPTH` levels down): a page that draws a scan or a picture.
    It counts the images a page could draw, which is enough to tell a scan with its
    recognized text in an invisible layer from a page that hides text."""
    seen = set() if seen is None else seen
    xobjects = _entry(_resolved(resources), "/XObject")
    if not isinstance(xobjects, dict) or id(xobjects) in seen:
        return False
    seen.add(id(xobjects))
    for value in list(xobjects.values()):
        xobject = _resolved(value)
        subtype = _entry(xobject, "/Subtype")
        if subtype == "/Image":
            return True
        if (
            subtype == "/Form"
            and depth < MAX_FORM_DEPTH
            and has_images(_entry(xobject, "/Resources"), depth + 1, seen)
        ):
            return True
    return False


def shown_characters(operands: object) -> int:
    """Characters a text-showing operator shows: its string operands, and the strings of
    a ``TJ`` array (approximate: a two-byte font counts each character twice)."""
    if not isinstance(operands, list):
        return 0
    count = 0
    for operand in operands:
        if isinstance(operand, (str, bytes)):
            count += len(operand)
        elif isinstance(operand, list):
            count += sum(len(item) for item in operand if isinstance(item, (str, bytes)))
    return count


@dataclass(frozen=True, slots=True)
class _Drawing:
    """What a ``Do`` saves (a form is drawn as if between ``q`` and ``Q``), and the
    resources and matrix of the stream that draws it."""

    mode: int
    size: float
    depth: int
    floor: int
    resources: object
    base: Matrix


class PageSigns:
    """The signs of text that a page does not show, gathered while pypdf extracts its
    text (``extract_text``'s operator visitors: :meth:`before` and :meth:`after` each
    operator).

    It follows what pypdf's extraction does not: the text render mode and the font
    size, which ``q`` saves and ``Q`` restores; and the matrix each form is drawn with
    (pypdf reads a form's content from its own origin). A form is drawn as if between
    ``q`` and ``Q``: its state stays inside it, and it cannot restore more states than
    it saved. Every character a text-showing operator shows counts as ``invisible`` in
    a render mode that paints nothing, else as ``tiny`` when its size, every scale
    included, is under :data:`TINY_POINTS`; and as ``offpage`` when the text's origin is
    more than :data:`OFFPAGE_MARGIN` outside the page's visible box.

    ``images`` starts as whether the page's resources have an image (:func:`has_images`)
    and an inline image makes it true.

    An operand it cannot use (a mode that is not a number...) is skipped: the visitors
    never raise, so they never cost the page its text."""

    def __init__(self, resources: object, box: Box | None, *, images: bool = False) -> None:
        self.images = images
        self.invisible = 0
        self.tiny = 0
        self.offpage = 0
        self._box = box
        self._mode = 0
        self._size = 0.0
        """No size until a ``Tf``: text shown without one is not readable."""
        self._saved: list[tuple[int, float]] = []
        self._floor = 0
        """The saved states of the stream being read start here (a form's ``Q`` cannot
        restore the states of the stream that draws it)."""
        self._resources = resources
        self._base = IDENTITY
        """From the space of the stream being read (a form's) to the page's."""
        self._drawings: list[_Drawing] = []

    def before(self, operator: object, operands: object, cm: object, tm: object) -> None:
        with contextlib.suppress(Exception):
            self._before(operator, operands, cm)

    def after(self, operator: object, operands: object, cm: object, tm: object) -> None:
        # Text is counted once pypdf has placed it: «'» and «"» move to the next line
        # first.
        with contextlib.suppress(Exception):
            if operator == b"Do":
                self._drawn()
            elif operator in _SHOWING:
                self._shown(operands, cm, tm)

    def _before(self, operator: object, operands: Any, cm: Any) -> None:
        if operator == b"q":
            self._saved.append((self._mode, self._size))
        elif operator == b"Q":
            if len(self._saved) > self._floor:
                self._mode, self._size = self._saved.pop()
        elif operator == b"Tr":
            self._mode = int(operands[0])
        elif operator == b"Tf":
            self._size = abs(float(operands[1]))
        elif operator == b"Do":
            # Saved first: whatever happens next, the matching after() restores it.
            self._drawings.append(
                _Drawing(
                    self._mode,
                    self._size,
                    len(self._saved),
                    self._floor,
                    self._resources,
                    self._base,
                )
            )
            self._floor = len(self._saved)
            form = _entry(_entry(self._resources, "/XObject"), operands[0])
            if _entry(form, "/Subtype") == "/Form":
                matrix = multiply(_matrix(_entry(form, "/Matrix")), cm)
                self._base = multiply(matrix, self._base)
                self._resources = _entry(form, "/Resources")
        elif operator in _INLINE_IMAGES:
            self.images = True

    def _drawn(self) -> None:
        if not self._drawings:
            return
        drawing = self._drawings.pop()
        self._mode, self._size = drawing.mode, drawing.size
        self._floor, self._resources, self._base = drawing.floor, drawing.resources, drawing.base
        del self._saved[drawing.depth :]

    def _shown(self, operands: object, cm: Any, tm: Any) -> None:
        count = shown_characters(operands)
        if not count:
            return
        a, b, c, d, x, y = multiply(multiply(tm, cm), self._base)
        if self._mode in INVISIBLE_MODES:
            self.invisible += count
        elif self._size * math.sqrt(abs(a * d - b * c)) < TINY_POINTS:
            self.tiny += count
        box = self._box
        if box is not None and not (
            box[0] - OFFPAGE_MARGIN <= x <= box[2] + OFFPAGE_MARGIN
            and box[1] - OFFPAGE_MARGIN <= y <= box[3] + OFFPAGE_MARGIN
        ):
            self.offpage += count


def read_page(page: Any) -> tuple[str, PageSigns]:
    """A page's extracted text, as pypdf gives it, and the signs of text it does not
    show. Raises whatever pypdf raises on the text."""
    try:
        resources = _resolved(page.get("/Resources"))
    except Exception:  # a broken reference: as if the page had no resources
        resources = None
    try:
        images = has_images(resources)
    except Exception:  # a broken resource: no image, so invisible text stays suspect
        images = False
    signs = PageSigns(resources, visible_box(page), images=images)
    text = page.extract_text(visitor_operand_before=signs.before, visitor_operand_after=signs.after)
    return text or "", signs


def read_pages(pages: Sequence[Any], limit: int) -> tuple[str | None, list[PdfPage]]:
    """The stored text of a PDF's pages (:class:`StoredText`, cut at ``limit``) and the
    facts of every page, also of those after the limit (from their own text, which is
    then dropped). The counts of characters are of the text as pypdf gives it, broken
    characters included; the stored text is cleaned (:func:`clean_text`) before the
    spans are measured in it. Raises whatever pypdf raises on any page."""
    stored = StoredText(limit)
    counts: list[tuple[int, int, int]] = []
    signs: list[tuple[bool, int, int, int]] = []
    for page in pages:
        extracted, found = read_page(page)
        stored.add(clean_text(extracted))
        counts.append(text_counts(extracted))
        signs.append((found.images, found.invisible, found.tiny, found.offpage))
    text, spans = stored.finish()
    return text, [
        PdfPage(
            number=number,
            start=start,
            end=end,
            cut=cut,
            chars=chars,
            letters=letters,
            garbage=garbage,
            images=images,
            invisible=invisible,
            tiny=tiny,
            offpage=offpage,
        )
        for number, (
            (start, end, cut),
            (chars, letters, garbage),
            (images, invisible, tiny, offpage),
        ) in enumerate(zip(spans, counts, signs, strict=True), start=1)
    ]


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
    ``{"text": str | null, "pdf_pages": [PdfPage.to_data(), ...] | null}``: the text
    (:class:`StoredText`) and the facts of every page (:func:`read_pages`), both
    ``null`` if pypdf fails on any page."""
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
    try:
        text, facts = read_pages(reader.pages, max_chars)
    except Exception:
        emit({"text": None, "pdf_pages": None})
        return 0
    emit({"text": text, "pdf_pages": [page.to_data() for page in facts]})
    return 0


_WORKER_BOOT: Final = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from agentic_os.attachments import pdf_worker; raise SystemExit(pdf_worker(sys.argv[2:]))"
)
_PACKAGE_ROOT: Final = str(Path(__file__).resolve().parents[1])
_WORKER_ENV: Final = {"LC_ALL": "C.UTF-8"}
"""The reader's whole environment: nothing of the server's (keys, tokens) reaches it."""
_MAX_WORKER_LINE: Final = 16 * 1024 * 1024
"""Longest line read from the reader: the text line, at most 12 bytes per character of
the text (about 12 MB) and less than 250 bytes of facts per page."""


class PdfReader:
    """Reads the page count, the text and the facts of each page of stored PDFs with
    pypdf, each in a new process of its own: isolated Python (``-I``: no environment
    variables, no user site), with an empty environment of the server's and the resource
    limits of :func:`lower_limits`. At most ``concurrency`` run at once. Cancelling a
    read kills its process."""

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
        """The page count, the text and the facts of each page of the PDF at ``path``.
        Raises :class:`AttachmentError` 422 for an encrypted PDF, one without pages or
        with more than the page limit, or one whose pages could not be counted (not a
        PDF, broken, or the reader ran out of time or memory first). A text that could
        not be extracted in time is ``None``, and so are facts that do not fit it
        (:func:`fitting_pages`)."""
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
        read = second if isinstance(second, dict) else {}
        raw = read.get("text")
        text = clean_text(raw) if isinstance(raw, str) and raw.strip() else None
        facts = fitting_pages(read.get("pdf_pages"), pages, text)
        if text is not None and text != raw:
            facts = None  # the reader's spans are in another text than this one
        return PdfInfo(pages=pages, text=text, pdf_pages=facts)


def fitting_pages(value: object, pages: int, text: str | None) -> tuple[PdfPage, ...] | None:
    """The facts the reader wrote for each page (``pdf_facts.pages_from_data``), if they
    fit the PDF and its text: one per page, and every span within the text (none when
    there is no text). ``None`` otherwise: the PDF is kept, unanalysed."""
    found = pages_from_data(value)
    if found is None or len(found) != pages:
        return None
    for page in found:
        if page.end is not None and (text is None or page.end > len(text)):
            return None
    return found


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
