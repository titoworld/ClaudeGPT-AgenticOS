"""Small files built in the tests, byte by byte (never downloaded): image headers of every
accepted format, and PDFs with text pages, blank pages or encryption, or drawn operator by
operator (:func:`drawn_pdf`: invisible, tiny or off-page text, images, forms, broken
characters) for the reader's page analysis.

The images are only as real as the server needs: it reads their headers and never
decodes them. The PDFs are real (pypdf reads them).
"""

from __future__ import annotations

import io
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final


def png(width: int, height: int, *, extra: bytes = b"") -> bytes:
    """A PNG signature and IHDR chunk (plus ``extra`` bytes)."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", 13) + b"IHDR" + ihdr
    return b"\x89PNG\r\n\x1a\n" + chunk + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)) + extra


def jpeg(width: int, height: int, *, app_segments: int = 1, sof: int = 0xC0) -> bytes:
    """SOI, ``app_segments`` APPn segments of about 60 KB (EXIF, ICC...), a stray fill
    byte and a start-of-frame segment with the dimensions."""
    data = bytearray(b"\xff\xd8")
    data += b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    for index in range(app_segments):
        payload = bytes([index % 251]) * 60_000
        data += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    data += b"\xff"  # a fill byte before the next marker
    frame = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    data += bytes([0xFF, sof]) + struct.pack(">H", len(frame) + 2) + frame
    data += b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x00" * 32 + b"\xff\xd9"
    return bytes(data)


def gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00" + b";"


def _riff(chunk: bytes, payload: bytes) -> bytes:
    body = b"WEBP" + chunk + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_lossy(width: int, height: int) -> bytes:
    frame = b"\x9d\x01\x2a" + struct.pack("<HH", width, height) + b"\x00" * 16
    return _riff(b"VP8 ", b"\x10\x02\x00" + frame)


def webp_lossless(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    return _riff(b"VP8L", b"\x2f" + struct.pack("<I", bits) + b"\x00" * 16)


def webp_extended(width: int, height: int) -> bytes:
    canvas = (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return _riff(b"VP8X", b"\x10\x00\x00\x00" + canvas + b"\x00" * 16)


def literal(text: str) -> bytes:
    """``text`` as a PDF string literal, ``(...)``, in Latin-1 (WinAnsi for these
    characters)."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + escaped.encode("latin-1") + b")"


def _stream(data: bytes, *, dictionary: bytes = b"") -> bytes:
    """A compressed stream object (FlateDecode) with these extra dictionary entries."""
    packed = zlib.compress(data)
    head = b"<< %s/Length %d /Filter /FlateDecode >>" % (dictionary, len(packed))
    return head + b"\nstream\n" + packed + b"\nendstream"


def _assemble(objects: list[bytes], kids: Sequence[int]) -> bytes:
    """The file: ``objects`` numbered from 1, the first two being replaced by the catalog
    and the page tree of ``kids``, then the cross-reference table and the trailer."""
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(b"%d 0 R" % kid for kid in kids)
        + b"] /Count %d >>" % len(kids)
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


HELVETICA: Final = (
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
)


def pdf(pages: Sequence[str | None]) -> bytes:
    """A PDF with one page per item: its text (Helvetica, one line per 80 characters)
    or, for ``None``, a page without any text (like a scanned page)."""
    objects: list[bytes] = [b"", b""]  # the catalog and the page tree, filled by _assemble
    objects.append(HELVETICA)
    font = len(objects)
    kids: list[int] = []
    for text in pages:
        lines = [] if text is None else [text[i : i + 80] for i in range(0, len(text), 80)]
        operators = [b"BT /F1 10 Tf 40 800 Td 12 TL"]
        operators.extend(literal(line) + b" '" for line in lines)
        operators.append(b"ET")
        objects.append(_stream(b"\n".join(operators)))
        content = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (content, font)
        )
        kids.append(len(objects))
    return _assemble(objects, kids)


# -- PDFs drawn operator by operator (the reader's page analysis) ----------------------------

BROKEN_MAP: Final = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Trencat def
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
3 beginbfchar
<41> <E000>
<42> <0001>
<43> <FFFD>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end"""
"""The ToUnicode map of the font ``/F2``: «A» is read as a private-use character, «B» as
a control character and «C» as U+FFFD (a font without a usable character map); the
other characters are read as usual."""

ONE_PIXEL: Final = (
    b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
    b"/BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream"
)

Box = tuple[float, float, float, float]
Matrix = tuple[float, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class Form:
    """A form XObject, ``/Fm1`` of a page: its content stream (with the fonts ``/F1``
    and ``/F2`` in its resources, and the image ``/Im1`` if ``image``), its box and its
    own matrix."""

    content: bytes
    image: bool = False
    bbox: Box = (0, 0, 595, 842)
    matrix: Matrix | None = None


@dataclass(frozen=True, slots=True)
class Page:
    """A page of :func:`drawn_pdf` (595 x 842 points): its content stream and what its
    resources have besides the fonts ``/F1`` (Helvetica) and ``/F2`` (:data:`BROKEN_MAP`):
    the one-pixel image ``/Im1`` if ``image``, and ``form`` as ``/Fm1``."""

    content: bytes
    image: bool = False
    form: Form | None = None
    crop_box: Box | None = None


def _numbers(values: Sequence[float]) -> bytes:
    return b" ".join(b"%g" % value for value in values)


def drawn_pdf(pages: Sequence[Page]) -> bytes:
    """A PDF with these pages, drawn exactly as their content streams say."""
    objects: list[bytes] = [b"", b""]  # the catalog and the page tree, filled by _assemble
    objects.append(HELVETICA)
    font = len(objects)
    objects.append(_stream(BROKEN_MAP))
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode %d 0 R >>" % len(objects)
    )
    broken = len(objects)
    objects.append(ONE_PIXEL)
    image = len(objects)
    fonts = b"/Font << /F1 %d 0 R /F2 %d 0 R >>" % (font, broken)
    kids: list[int] = []
    for page in pages:
        xobjects: list[bytes] = []
        if page.image:
            xobjects.append(b"/Im1 %d 0 R" % image)
        if page.form is not None:
            form = page.form
            resources = fonts + (b" /XObject << /Im1 %d 0 R >>" % image if form.image else b"")
            entries = b"/Type /XObject /Subtype /Form /BBox [%s] " % _numbers(form.bbox)
            if form.matrix is not None:
                entries += b"/Matrix [%s] " % _numbers(form.matrix)
            entries += b"/Resources << %s >> " % resources
            objects.append(_stream(form.content, dictionary=entries))
            xobjects.append(b"/Fm1 %d 0 R" % len(objects))
        objects.append(_stream(page.content))
        content = len(objects)
        resources = fonts + (b" /XObject << %s >>" % b" ".join(xobjects) if xobjects else b"")
        crop = b"/CropBox [%s] " % _numbers(page.crop_box) if page.crop_box else b""
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] %s/Contents %d 0 R "
            b"/Resources << %s >> >>" % (crop, content, resources)
        )
        kids.append(len(objects))
    return _assemble(objects, kids)


def shown(
    text: str,
    *,
    x: float = 50,
    y: float = 700,
    size: float = 12,
    mode: int | None = None,
    font: str = "F1",
) -> bytes:
    """A text object that shows ``text`` at ``(x, y)``, in render ``mode`` if one is
    given (0 fills the glyphs, 3 and 7 draw nothing; without one, the mode in force),
    and a line break, so that what follows is another operator."""
    render = b"" if mode is None else b"%d Tr " % mode
    return b"BT /%s %g Tf %s%g %g Td %s Tj ET\n" % (
        font.encode(),
        size,
        render,
        x,
        y,
        literal(text),
    )


def blank_pdf(pages: int) -> bytes:
    """A PDF of ``pages`` blank pages, written by pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(200, 200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def encrypted_pdf() -> bytes:
    """A one-page PDF protected with a password (RC4, which pypdf writes by itself)."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt(user_password="secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
