"""Small files built in the tests, byte by byte (never downloaded): image headers of every
accepted format, and PDFs with text pages, blank pages or encryption.

The images are only as real as the server needs: it reads their headers and never
decodes them. The PDFs are real (pypdf reads them).
"""

from __future__ import annotations

import io
import struct
import zlib
from collections.abc import Sequence


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


def pdf(pages: Sequence[str | None]) -> bytes:
    """A PDF with one page per item: its text (Helvetica, one line per 80 characters)
    or, for ``None``, a page without any text (like a scanned page)."""
    objects: list[bytes] = [b"", b""]  # the catalog and the page tree, filled below
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    font = len(objects)
    kids: list[int] = []
    for text in pages:
        lines = [] if text is None else [text[i : i + 80] for i in range(0, len(text), 80)]
        operators = [b"BT /F1 10 Tf 40 800 Td 12 TL"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            operators.append(b"(" + escaped.encode("latin-1") + b") '")
        operators.append(b"ET")
        stream = zlib.compress(b"\n".join(operators))
        objects.append(
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
            + stream
            + b"\nendstream"
        )
        content = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (content, font)
        )
        kids.append(len(objects))
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
