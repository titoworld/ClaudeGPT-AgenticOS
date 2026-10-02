"""What the PDF reader finds on each page (docs/adr/0009-adjunts.md, P7b), end to end
through the reader's own process (:class:`PdfReader`) on PDFs drawn in the tests: where
each page's text is in the stored text (built page by page, as it always was), its
letters and broken characters, whether it draws an image, and the text it shows
invisibly, smaller than a point or outside its visible box. And what the server does
with what the reader says: facts that do not fit the text are dropped (the PDF is kept,
unanalysed)."""

from __future__ import annotations

import json
import math
import random
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from attachment_files import Form, Page, drawn_pdf, literal, pdf, shown

from agentic_os import attachments
from agentic_os.attachments import (
    PdfInfo,
    PdfReader,
    StoredText,
    cut_notice,
    smallest_scale,
    text_counts,
)
from agentic_os.pdf_facts import PdfPage, pdf_notes

VISIBLE = "Informe de vendes del segon trimestre"
HIDDEN = "Nota oculta per als models"
OCR = "Capa de text reconeguda sobre un escaneig"
TINY = "Text massa petit per llegir-lo"
OFF = "Text fora de la part visible"
FORM = "Text dins d'un formulari"
SCAN = b"q 595 0 0 842 0 0 cm /Im1 Do Q "
"""Draws the one-pixel image over the whole page: a scanned page."""


def write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "document.pdf"
    path.write_bytes(data)
    return path


async def read(tmp_path: Path, pages: Sequence[Page], **options: Any) -> PdfInfo:
    return await PdfReader(**options).read(write(tmp_path, drawn_pdf(pages)))


def analysed(info: PdfInfo) -> tuple[PdfPage, ...]:
    assert info.pdf_pages is not None
    assert len(info.pdf_pages) == info.pages
    return info.pdf_pages


def signs(page: PdfPage) -> tuple[bool, int, int, int]:
    """Whether the page draws an image, and the characters it shows invisibly, smaller
    than a point and off its visible box."""
    return (page.images, page.invisible, page.tiny, page.offpage)


def page_text(info: PdfInfo, page: PdfPage) -> str | None:
    if page.start is None or page.end is None:
        return None
    assert info.text is not None
    return info.text[page.start : page.end]


# -- where each page's text is ---------------------------------------------------------------


async def test_a_page_of_plain_text_has_its_span_and_no_warning(tmp_path: Path) -> None:
    info = await read(tmp_path, [Page(shown("Informe de vendes del segon trimestre del 2025"))])
    assert info.text == "--- Pàgina 1 ---\nInforme de vendes del segon trimestre del 2025"
    assert info.pdf_pages == (
        PdfPage(
            number=1,
            start=17,
            end=63,
            cut=False,
            chars=39,
            letters=35,
            garbage=0,
            images=False,
            invisible=0,
            tiny=0,
            offpage=0,
        ),
    )
    assert pdf_notes(info.pdf_pages).to_wire() == {"no_text": [], "garbled": [], "hidden": []}


async def test_the_spans_are_each_pages_text_in_the_stored_text(tmp_path: Path) -> None:
    info = await PdfReader().read(
        write(tmp_path, pdf(["Primera pàgina", None, "Tercera (i última) pàgina"]))
    )
    first, second, third = analysed(info)
    assert page_text(info, first) == "Primera pàgina"
    assert page_text(info, third) == "Tercera (i última) pàgina"
    # A page without text has no span, and it is a page without text.
    assert (second.start, second.end, second.cut, second.chars, second.letters) == (
        None,
        None,
        False,
        0,
        0,
    )
    assert second.no_text and not first.cut and not third.cut


async def test_a_pdf_without_any_text_is_analysed_all_the_same(tmp_path: Path) -> None:
    """A scanned PDF: no text to store, and every page without text (the pages Claude
    must read for ChatGPT)."""
    info = await read(tmp_path, [Page(SCAN, image=True), Page(SCAN, image=True)])
    assert info.text is None
    pages = analysed(info)
    assert [(p.number, p.start, p.end, p.cut, p.letters, p.images) for p in pages] == [
        (1, None, None, False, 0, True),
        (2, None, None, False, 0, True),
    ]
    assert pdf_notes(pages).no_text == (1, 2)


async def test_every_page_is_analysed_past_the_text_limit(tmp_path: Path) -> None:
    """The stored text is cut as before, and each page knows how much of its text was
    kept: the one that straddles the cut ends at it, the later ones have none, and all
    of them have the facts of their own text."""
    path = write(tmp_path, pdf(["a" * 400, "b" * 400, "c" * 400, None]))
    info = await PdfReader(max_chars=500).read(path)
    assert info.text is not None
    kept = info.text.partition("\n\n[Text retallat")[0]
    assert len(kept) == 500
    first, second, third, fourth = analysed(info)
    assert (first.start, first.end, first.cut) == (17, 421, False)
    assert (second.start, second.end, second.cut) == (440, 500, True)
    assert (third.start, third.end, third.cut) == (None, None, True)
    # A page without text lost nothing to the cut.
    assert (fourth.start, fourth.end, fourth.cut) == (None, None, False)
    assert page_text(info, second) == "b" * 60
    assert [(p.chars, p.letters) for p in (first, second, third, fourth)] == [
        (400, 400),
        (400, 400),
        (400, 400),
        (0, 0),
    ]


def stored_before(texts: Sequence[str], limit: int) -> str | None:
    """The stored text as the reader wrote it before the page analysis (P7a)."""
    if not any(text.strip() for text in texts):
        return None
    blocks = [
        f"--- Pàgina {number} ---\n{text.lstrip(chr(13) + chr(10)).rstrip()}"
        for number, text in enumerate(texts, 1)
    ]
    joined = "\n\n".join(blocks)
    if len(joined) > limit:
        joined = f"{joined[:limit].rstrip()}\n\n{cut_notice(limit)}"
    return joined


def test_the_stored_text_is_as_before_and_each_span_is_its_pages_text() -> None:
    """Built page by page, dropping the pages past the limit, the stored text is the one
    the reader always wrote; a span is the page's text, all of it or up to the cut."""
    pieces = ["", " ", "\n", "\r\n", "Hola", "món", "a b", "\t", "x" * 30, "línia\nnova"]
    rng = random.Random(9)
    for _ in range(3000):
        texts = [
            "".join(rng.choices(pieces, k=rng.randint(0, 4))) for _ in range(rng.randint(1, 5))
        ]
        limit = rng.randint(1, 150)
        stored = StoredText(limit)
        for text in texts:
            stored.add(text)
        result, spans = stored.finish()
        assert result == stored_before(texts, limit), (texts, limit)
        for text, (start, end, cut) in zip(texts, spans, strict=True):
            body = text.lstrip("\r\n").rstrip()
            if start is None or end is None:
                assert start is None and end is None
                assert cut == (bool(body) and result is not None)  # all of it cut off
                continue
            assert result is not None
            assert result[start:end] == body[: end - start]
            assert cut == (end - start < len(body))


def test_the_characters_of_a_pages_text() -> None:
    """Letters, and the broken characters of a font without a usable map: U+FFFD,
    private-use characters, lone surrogates and control characters other than tab and
    line breaks (a vertical tab too)."""
    assert text_counts("Àx 12\t\n\r") == (4, 2, 0)
    assert text_counts("\ufffd\ue000\ud800\x01\x0b\x00") == (5, 0, 6)
    assert text_counts("") == (0, 0, 0)


# -- broken characters -------------------------------------------------------------------------


async def test_broken_characters_are_garbage(tmp_path: Path) -> None:
    """A font whose character map gives private-use, control and replacement characters
    (``/F2``: «A», «B» and «C») makes a page garbled; its letters still count."""
    info = await read(tmp_path, [Page(shown(f"AAAAABBBBBCCCCC {VISIBLE}", font="F2"))])
    [page] = analysed(info)
    assert (page.garbage, page.letters, page.chars) == (15, 32, 47)
    assert page.garbled and not page.no_text
    assert page_text(info, page) == "" * 5 + "\x01" * 5 + "�" * 5 + f" {VISIBLE}"


# -- text that is not visible --------------------------------------------------------------------


@pytest.mark.parametrize("mode", [3, 7])
async def test_invisible_text_is_suspect_on_a_page_without_images(
    tmp_path: Path, mode: int
) -> None:
    info = await read(tmp_path, [Page(shown(VISIBLE) + shown(HIDDEN, y=680, mode=mode))])
    [page] = analysed(info)
    assert signs(page) == (False, len(HIDDEN), 0, 0)
    assert page.hidden
    # The text layer has both: that is what a model reading the text would get.
    assert page_text(info, page) == f"{VISIBLE}\n{HIDDEN}"


async def test_an_invisible_layer_over_a_scan_is_not_suspect(tmp_path: Path) -> None:
    """A scan with its recognized text in an invisible layer is a normal PDF: an image
    drawn from the page's resources, an inline image or an image drawn by a form."""
    ocr = shown(OCR, mode=3)
    inline = b"q 595 0 0 842 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x80 EI Q "
    info = await read(
        tmp_path,
        [
            Page(SCAN + ocr, image=True),
            Page(inline + ocr),
            Page(b"/Fm1 Do " + ocr, form=Form(SCAN, image=True)),
        ],
    )
    for page in analysed(info):
        assert signs(page) == (True, len(OCR), 0, 0), page.number
        assert not page.hidden


async def test_text_smaller_than_a_point(tmp_path: Path) -> None:
    """The size is the font's times every scale the text goes through: its own size,
    the text matrix, the transformation matrix and the matrix a form is drawn with."""
    visible = shown(VISIBLE, y=600)
    info = await read(
        tmp_path,
        [
            Page(shown(TINY, size=0.4) + visible),
            Page(b"q 0.01 0 0 0.01 0 0 cm " + shown(TINY, x=5000, y=70000) + b" Q " + visible),
            Page(b"BT /F1 12 Tf 0.05 0 0 0.05 50 700 Tm %s Tj ET " % literal(TINY)),
            Page(b"q 0.01 0 0 0.01 0 0 cm /Fm1 Do Q " + visible, form=Form(shown(TINY))),
        ],
    )
    for page in analysed(info):
        assert signs(page) == (False, 0, len(TINY), 0), page.number
        assert page.hidden


async def test_text_outside_the_visible_box(tmp_path: Path) -> None:
    """Off the page is more than a point outside the part of the page a viewer shows:
    its crop box within its media box."""
    visible = shown(VISIBLE, y=600)
    second_line = "Segona línia, sota la pàgina"
    info = await read(
        tmp_path,
        [
            Page(shown(OFF, x=2000, y=2000) + visible),
            Page(shown(OFF, x=400, y=280) + shown(VISIBLE, y=200), crop_box=(0, 0, 300, 300)),
            Page(shown(OFF, x=1000, y=1000) + visible, crop_box=(0, 0, 2000, 2000)),
            # «'» moves to the next line before it shows its text: below the page.
            Page(
                b"BT /F1 12 Tf 14 TL 50 5 Td %s Tj %s ' ET"
                % (literal("Primera línia"), literal(second_line))
            ),
            Page(b"q 1 0 0 1 3000 0 cm /Fm1 Do Q " + visible, form=Form(shown(OFF))),
        ],
    )
    expected = [len(OFF), len(OFF), len(OFF), len(second_line), len(OFF)]
    for page, offpage in zip(analysed(info), expected, strict=True):
        assert signs(page) == (False, 0, 0, offpage), page.number
        assert page.hidden


def test_the_size_that_counts_is_the_shortest_axis_of_the_glyphs() -> None:
    """The smallest scale of a matrix in any direction: rotating or mirroring the text
    changes nothing, flattening it in one direction makes it as small as that."""
    assert smallest_scale(12, 0, 0, 12) == pytest.approx(12)
    assert smallest_scale(12 * 0.005, 0, 0, 12) == pytest.approx(0.06)  # 0.5 % wide
    assert smallest_scale(12, 0, 0, 0.12) == pytest.approx(0.12)  # flattened
    cos, sin = math.cos(0.7), math.sin(0.7)
    assert smallest_scale(10 * cos, 10 * sin, -10 * sin, 10 * cos) == pytest.approx(10)
    assert smallest_scale(-12, 0, 0, 12) == pytest.approx(12)  # mirrored
    assert smallest_scale(1, 0, 0.2, 1) == pytest.approx(0.905, abs=0.001)  # slanted
    assert smallest_scale(0, 0, 0, 0) == 0


def hidden_with(operators: bytes, then: bytes = b"") -> bytes:
    """``HIDDEN`` shown at (50, 700) after these text state ``operators``, inside ``q`` and
    ``Q`` (which restore the state), then ``then`` and ``VISIBLE`` as usual."""
    return (
        b"q BT /F1 12 Tf %s 50 700 Td %s Tj ET Q\n" % (operators, literal(HIDDEN))
        + then
        + shown(VISIBLE, y=600)
    )


async def test_text_squeezed_flattened_or_raised_out_of_sight(tmp_path: Path) -> None:
    """Horizontal scaling (``Tz``) and a matrix that flattens the text in one direction
    make glyphs as unreadable as a tiny font, and a text rise (``Ts``) moves them as far as
    a position does: pypdf follows neither the scaling nor the rise, the analysis does.
    The size that counts is the smallest in any direction."""
    info = await read(
        tmp_path,
        [
            Page(hidden_with(b"0 Tz")),  # no width at all
            Page(hidden_with(b"0.5 Tz")),  # a few tenths of a point wide
            Page(  # 12 points wide, a tenth of a point tall
                b"q BT /F1 12 Tf 1 0 0 0.01 50 700 Tm %s Tj ET Q\n" % literal(HIDDEN)
                + shown(VISIBLE, y=600)
            ),
            Page(hidden_with(b"5000 Ts")),  # far above the page
            Page(hidden_with(b"-2000 Ts")),  # far below it
        ],
    )
    pages = analysed(info)
    for page in pages[:3]:
        assert signs(page) == (False, 0, len(HIDDEN), 0), page.number
    for page in pages[3:]:
        assert signs(page) == (False, 0, 0, len(HIDDEN)), page.number
    for page in pages:
        assert page.hidden
        text = page_text(info, page)
        assert text is not None and HIDDEN in text  # what a model reading the text gets


async def test_readable_scaling_and_rise_are_no_warning(tmp_path: Path) -> None:
    """Condensed or mirrored text, a superscript and a scale that keeps the glyphs above
    a point in every direction are readable."""
    info = await read(
        tmp_path,
        [
            Page(hidden_with(b"50 Tz")),
            Page(hidden_with(b"-100 Tz")),
            Page(hidden_with(b"4 Ts")),
            Page(
                b"q BT /F1 12 Tf 2 0 0 0.5 50 700 Tm %s Tj ET Q\n" % literal(HIDDEN)
                + shown(VISIBLE, y=600)
            ),
        ],
    )
    for page in analysed(info):
        assert signs(page) == (False, 0, 0, 0), page.number


async def test_the_scaling_and_the_rise_are_saved_and_restored_like_the_mode(
    tmp_path: Path,
) -> None:
    """``Q`` restores them (``hidden_with`` above), a form inherits them from the page and
    keeps its own to itself, like the render mode and the size."""
    info = await read(
        tmp_path,
        [
            # The page's text after a form that squeezes or raises its own.
            Page(b"/Fm1 Do\n" + shown(VISIBLE, y=600), form=Form(b"0 Tz " + shown(FORM))),
            Page(b"/Fm1 Do\n" + shown(VISIBLE, y=600), form=Form(b"5000 Ts " + shown(FORM))),
            # A form drawn with the page's scaling or rise.
            Page(b"q 0 Tz /Fm1 Do Q\n" + shown(VISIBLE, y=600), form=Form(shown(FORM))),
            Page(b"q 5000 Ts /Fm1 Do Q\n" + shown(VISIBLE, y=600), form=Form(shown(FORM))),
            # Text after an unbalanced form that tries to restore the page's scaling.
            Page(
                b"q 0 Tz q 100 Tz /Fm1 Do Q %s Q\n" % b"BT /F1 12 Tf 50 650 Td (x) Tj ET"
                + shown(VISIBLE, y=600),
                form=Form(b"Q Q " + shown(FORM, y=500)),
            ),
        ],
    )
    first, second, third, fourth, fifth = analysed(info)
    assert signs(first) == (False, 0, len(FORM), 0)
    assert signs(second) == (False, 0, 0, len(FORM))
    assert signs(third) == (False, 0, len(FORM), 0)
    assert signs(fourth) == (False, 0, 0, len(FORM))
    assert signs(fifth) == (False, 0, 1, 0)  # only the page's «x», squeezed by its own Tz


async def test_text_at_the_edge_or_drawn_into_the_page_is_on_it(tmp_path: Path) -> None:
    """Within a point of the edge is on the page, and so is a form's text that its own
    matrix brings onto the page from far coordinates."""
    info = await read(
        tmp_path,
        [
            Page(shown(OFF, x=-0.5, y=842.5) + shown(VISIBLE, x=595.9, y=0)),
            Page(
                b"/Fm1 Do",
                form=Form(
                    shown(FORM, x=1050, y=1050),
                    bbox=(1000, 1000, 1200, 1200),
                    matrix=(1, 0, 0, 1, -1000, -1000),
                ),
            ),
        ],
    )
    for page in analysed(info):
        assert signs(page) == (False, 0, 0, 0), page.number


async def test_a_form_keeps_its_state_to_itself(tmp_path: Path) -> None:
    """A form is drawn as if between ``q`` and ``Q``: its render mode and font size stay
    inside it, and it cannot restore more states than it saved (a way to make the
    page's own invisible text count as visible)."""
    after = b"BT /F1 12 Tf 50 600 Td %s Tj ET" % literal("Visible després del formulari")
    info = await read(
        tmp_path,
        [
            Page(b"/Fm1 Do " + after, form=Form(shown(FORM, mode=3))),
            Page(
                b"BT /F1 12 Tf ET /Fm1 Do BT 50 600 Td %s Tj ET" % literal("Mida de la pàgina"),
                form=Form(shown(FORM, size=0.2)),
            ),
            Page(
                b"q 3 Tr q 0 Tr /Fm1 Do Q BT /F1 12 Tf 50 600 Td %s Tj ET Q" % literal(HIDDEN),
                form=Form(b"Q Q " + shown(FORM, y=500)),
            ),
            Page(b"q BT 3 Tr ET Q " + after),
        ],
    )
    first, second, third, fourth = analysed(info)
    assert signs(first) == (False, len(FORM), 0, 0)
    assert signs(second) == (False, 0, len(FORM), 0)
    assert signs(third) == (False, len(HIDDEN), 0, 0)
    assert signs(fourth) == (False, 0, 0, 0)


async def test_odd_operands_never_stop_the_reading(tmp_path: Path) -> None:
    """Operands the analysis cannot use (a mode that is a name, a size that is a string,
    a form that does not exist) are skipped: the text is read and the page analysed."""
    content = b"BT /F1 12 Tf /Nom Tr /F1 (gran) Tf 50 700 Td %s Tj ET /NoHiEs Do" % literal(VISIBLE)
    info = await read(tmp_path, [Page(content)])
    [page] = analysed(info)
    assert page_text(info, page) == VISIBLE
    assert signs(page) == (False, 0, 0, 0)


# -- what the server keeps of what the reader says ----------------------------------------------


def stand_in(code: str) -> list[str]:
    """A stand-in for the reader's process (it gets the same arguments)."""
    return [sys.executable, "-c", code]


def says(*lines: object) -> list[str]:
    """A stand-in reader that writes these JSON lines."""
    output = "".join(json.dumps(line) + "\n" for line in lines)
    return stand_in(f"import sys; sys.stdout.write({output!r})")


FACTS: dict[str, Any] = {
    "number": 1,
    "start": 17,
    "end": 21,
    "cut": False,
    "chars": 4,
    "letters": 4,
    "garbage": 0,
    "images": False,
    "invisible": 0,
    "tiny": 0,
    "offpage": 0,
}
TEXT = "--- Pàgina 1 ---\nHola"


async def test_the_facts_that_fit_the_text_are_kept(tmp_path: Path) -> None:
    command = says({"pages": 1}, {"text": TEXT, "pdf_pages": [FACTS]})
    info = await PdfReader(command=command).read(write(tmp_path, b"%PDF-"))
    assert info == PdfInfo(
        pages=1,
        text=TEXT,
        pdf_pages=(PdfPage(1, 17, 21, False, 4, 4, 0, False, 0, 0, 0),),
    )
    # A scanned page: no text, and no span.
    no_span = {**FACTS, "start": None, "end": None, "chars": 0, "letters": 0}
    command = says({"pages": 1}, {"text": None, "pdf_pages": [no_span]})
    info = await PdfReader(command=command).read(write(tmp_path, b"%PDF-"))
    assert info.text is None
    assert info.pdf_pages is not None and info.pdf_pages[0].no_text


@pytest.mark.parametrize(
    ("text", "facts"),
    [
        (TEXT, None),  # a reader that did not analyse the pages
        (TEXT, "not a list"),
        (TEXT, []),
        (TEXT, [FACTS, {**FACTS, "number": 2}]),  # more pages than the PDF has
        (TEXT, [{**FACTS, "letters": -1}]),
        (TEXT, [{**FACTS, "end": len(TEXT) + 1}]),  # past the end of the text
        (None, [FACTS]),  # a span in a text that is not there
        ("   ", [FACTS]),
        ("--- Pàgina 1 ---\nHo\ud800la", [FACTS]),  # the text had to be cleaned
    ],
)
async def test_facts_that_do_not_fit_the_text_are_dropped(
    tmp_path: Path, text: str | None, facts: object
) -> None:
    """The PDF is kept with its text, unanalysed, as if the reader had not looked."""
    command = says({"pages": 1}, {"text": text, "pdf_pages": facts})
    info = await PdfReader(command=command).read(write(tmp_path, b"%PDF-"))
    assert info.pages == 1
    assert info.pdf_pages is None
    if text is not None and text.strip():
        assert info.text == attachments.clean_text(text)
    else:
        assert info.text is None


async def test_a_reading_that_fails_gives_neither_text_nor_facts(tmp_path: Path) -> None:
    """If pypdf fails on any page, the reader says so for the whole PDF (as before):
    no text and no facts. A stand-in pypdf fails in the real reader's code."""
    fake = tmp_path / "fake" / "pypdf"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(
        "class _Page(dict):\n"
        "    cropbox = (0, 0, 595, 842)\n\n"
        "    def extract_text(self, **visitors):\n"
        "        raise RuntimeError('a page pypdf cannot read')\n\n"
        "class PdfReader:\n"
        "    is_encrypted = False\n\n"
        "    def __init__(self, path):\n"
        "        self.pages = [_Page()]\n"
    )
    package_root = Path(attachments.__file__).resolve().parents[1]
    boot = (
        f"import sys; sys.path[:0] = [{str(fake.parent)!r}, {str(package_root)!r}]; "
        "from agentic_os.attachments import pdf_worker; raise SystemExit(pdf_worker(sys.argv[1:]))"
    )
    info = await PdfReader(command=[sys.executable, "-c", boot]).read(write(tmp_path, b"%PDF-"))
    assert info == PdfInfo(pages=1, text=None, pdf_pages=None)
