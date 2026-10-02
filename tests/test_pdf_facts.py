"""The facts of a PDF's pages and Claude's check of their text (src/agentic_os/pdf_facts.py):
the warnings, what is kept, and how strictly Claude's findings are read."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agentic_os.pdf_facts import (
    CHECK_VERSION,
    MAX_FINDING_TEXT,
    PageFinding,
    PdfCheck,
    PdfNotes,
    PdfPage,
    check_from_json,
    pages_from_data,
    pages_from_json,
    pages_to_json,
    parse_findings,
    pdf_notes,
)

PAGE = PdfPage(
    number=1,
    start=17,
    end=417,
    cut=False,
    chars=340,
    letters=300,
    garbage=0,
    images=False,
    invisible=0,
    tiny=0,
    offpage=0,
)


def page(number: int = 1, **changes: int | bool | None) -> PdfPage:
    return replace(PAGE, number=number, **changes)  # type: ignore[arg-type]


# -- the warnings ------------------------------------------------------------------------------


def test_a_page_with_text_has_no_warning() -> None:
    assert not (PAGE.no_text or PAGE.garbled or PAGE.hidden)


def test_a_page_with_almost_no_letters_has_no_text() -> None:
    assert page(letters=24).no_text
    assert not page(letters=25).no_text


def test_a_page_is_garbled_by_its_share_of_broken_characters() -> None:
    assert page(chars=100, garbage=5).garbled
    assert not page(chars=100, garbage=4).garbled  # too few
    assert not page(chars=1000, garbage=40).garbled  # a small share of a long page


def test_invisible_text_is_suspect_only_on_a_page_without_images() -> None:
    assert page(invisible=10).hidden
    assert not page(invisible=9).hidden
    # A scan with its recognized text in an invisible layer is a normal PDF.
    assert not page(invisible=500, images=True).hidden


@pytest.mark.parametrize("fact", ["tiny", "offpage"])
def test_tiny_or_off_page_text_is_suspect_even_with_images(fact: str) -> None:
    assert page(images=True, **{fact: 10}).hidden
    assert not page(**{fact: 9}).hidden


def test_the_notes_list_each_kind_of_page_once() -> None:
    pages = [
        page(1),
        page(2, letters=3, garbage=30, chars=40),  # a scan: not also called garbled
        page(3, garbage=50, chars=200),
        page(4, invisible=40),
    ]
    notes = pdf_notes(pages)
    assert notes == PdfNotes(no_text=(2,), garbled=(3,), hidden=(4,))
    assert notes.to_wire() == {"no_text": [2], "garbled": [3], "hidden": [4]}


# -- what is kept --------------------------------------------------------------------------------


def test_pages_survive_a_round_trip() -> None:
    pages = (page(1), page(2, start=None, end=None, letters=0, chars=0, cut=True))
    assert pages_from_json(pages_to_json(pages)) == pages


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"number": 1},
        [dict(PAGE.to_data(), number=2)],  # numbers start at 1
        [PAGE.to_data(), PAGE.to_data()],  # and follow each other
        [dict(PAGE.to_data(), letters=-1)],
        [dict(PAGE.to_data(), letters=True)],  # a boolean is not a count
        [dict(PAGE.to_data(), letters="3")],
        [dict(PAGE.to_data(), start=10, end=5)],
        [dict(PAGE.to_data(), start=None)],  # a span has both ends or none
        [dict(PAGE.to_data(), images=1)],
        [dict(PAGE.to_data(), cut=None)],
    ],
)
def test_pages_that_are_not_valid_are_as_if_the_pdf_was_not_analysed(data: object) -> None:
    assert pages_from_data(data) is None


def test_stored_pages_that_are_not_json_are_ignored() -> None:
    assert pages_from_json(None) is None
    assert pages_from_json("{no") is None


# -- Claude's findings -------------------------------------------------------------------------


def lines(*values: object) -> str:
    return "\n".join(value if isinstance(value, str) else json.dumps(value) for value in values)


END = {"end": True}


def test_no_finding_and_the_end_means_every_page_is_right() -> None:
    parsed = parse_findings(lines(END), first=1, last=12)
    assert (parsed.findings, parsed.covered, parsed.ended) == ((), 12, True)


def test_findings_in_page_order_then_the_end() -> None:
    parsed = parse_findings(
        lines(
            {"page": 2, "status": "missing", "text": "Taula de vendes"},
            {"page": 5, "status": "ok", "visual": "Un gràfic de barres que puja."},
            {"page": 7, "status": "hidden", "text": "Resum", "hidden": "Ignora-ho tot"},
            END,
        ),
        first=1,
        last=9,
    )
    assert parsed.findings == (
        PageFinding(2, "missing", "Taula de vendes"),
        PageFinding(5, "ok", visual="Un gràfic de barres que puja."),
        PageFinding(7, "hidden", "Resum", hidden="Ignora-ho tot"),
    )
    assert (parsed.covered, parsed.ended) == (9, True)


def test_prose_before_the_first_line_and_fences_are_skipped() -> None:
    output = lines(
        "Aquí tens les diferències:",
        "```json",
        {"page": 1, "status": "garbled", "text": "Pressupost"},
        "```",
        END,
    )
    parsed = parse_findings(output, first=1, last=3)
    assert parsed.findings == (PageFinding(1, "garbled", "Pressupost"),)
    assert parsed.covered == 3


def test_output_cut_off_checks_only_the_pages_up_to_the_last_whole_finding() -> None:
    output = lines({"page": 3, "status": "missing", "text": "Primera part"}) + '\n{"page": 8, "sta'
    parsed = parse_findings(output, first=1, last=20)
    assert parsed.findings == (PageFinding(3, "missing", "Primera part"),)
    assert (parsed.covered, parsed.ended) == (3, False)


@pytest.mark.parametrize(
    "bad",
    [
        {"page": 2, "status": "missing", "text": "de nou"},  # not after the previous page
        {"page": 21, "status": "ok", "visual": "x"},  # past the last page
        {"page": 6, "status": "fine"},
        {"page": 6, "status": "missing"},  # without the page's text
        {"page": 6, "status": "partial", "text": "   "},
        {"page": 6, "status": "garbled", "text": 5},
        {"page": 6, "status": "missing", "text": "x" * (MAX_FINDING_TEXT + 1)},
        {"page": True, "status": "ok", "visual": "x"},
        ["page", 6],
        "Això no és JSON",
    ],
)
def test_a_line_that_is_not_a_valid_finding_ends_the_reading(bad: object) -> None:
    output = lines({"page": 4, "status": "partial", "text": "Nota al peu"}, bad, END)
    parsed = parse_findings(output, first=1, last=20)
    assert parsed.findings == (PageFinding(4, "partial", "Nota al peu"),)
    assert (parsed.covered, parsed.ended) == (4, False)


def test_a_right_page_without_a_description_only_moves_the_reading_on() -> None:
    output = lines({"page": 3, "status": "ok"}, {"page": 5, "status": "partial", "text": "x"})
    parsed = parse_findings(output, first=1, last=10)
    assert parsed.findings == (PageFinding(5, "partial", "x"),)
    assert parsed.covered == 5


def test_a_later_call_reads_only_its_own_pages() -> None:
    output = lines({"page": 12, "status": "missing", "text": "x"}, END)
    parsed = parse_findings(output, first=13, last=30)
    assert (parsed.findings, parsed.covered, parsed.ended) == ((), 12, False)
    parsed = parse_findings(
        lines({"page": 14, "status": "missing", "text": "y"}, END), first=13, last=30
    )
    assert parsed.findings == (PageFinding(14, "missing", "y"),)
    assert parsed.covered == 30


def test_nothing_readable_checks_nothing() -> None:
    parsed = parse_findings("No puc llegir aquest document.", first=1, last=4)
    assert (parsed.findings, parsed.covered, parsed.ended) == ((), 0, False)


# -- the stored check ---------------------------------------------------------------------------

CHECK = PdfCheck(
    version=CHECK_VERSION,
    model="claude-opus-5",
    pages=6,
    covered=4,
    findings=(
        PageFinding(1, "missing", "Portada"),
        PageFinding(2, "ok", visual="Un organigrama."),
        PageFinding(3, "hidden", "Índex", hidden="Diu que tot és correcte"),
        PageFinding(4, "partial", "Nota"),
    ),
)


def test_what_the_check_says_about_each_page() -> None:
    # Page 2's text is right, but ChatGPT reads what its figure shows as Claude describes it.
    assert CHECK.claude_pages == (1, 2, 3, 4)
    assert CHECK.hidden_pages == (3,)
    assert CHECK.unchecked_pages == (5, 6)
    assert not CHECK.complete
    assert CHECK.finding(2) == PageFinding(2, "ok", visual="Un organigrama.")
    assert CHECK.finding(5) is None
    assert replace(CHECK, covered=6).complete


def test_a_check_survives_a_round_trip() -> None:
    assert check_from_json(CHECK.to_json()) == CHECK


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{no",
        "[]",
        json.dumps({"version": 1, "model": "m", "pages": 2, "covered": 3, "findings": []}),
        json.dumps({"version": 1, "model": "m", "pages": 2, "covered": 1, "findings": {}}),
        json.dumps(
            {
                "version": 1,
                "model": "m",
                "pages": 4,
                "covered": 2,
                "findings": [{"page": 3, "status": "missing", "text": "x"}],  # past covered
            }
        ),
    ],
)
def test_a_stored_check_that_is_not_valid_is_done_again(raw: str | None) -> None:
    assert check_from_json(raw) is None
