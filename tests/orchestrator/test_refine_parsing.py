"""The replies of a refine turn (docs/adr/0010-refine-mode.md), read strictly: a
review's proposed changes and score, and the version an edit writes with its changelog.
A document may quote the tags it is written between, and the live stream of a reply
always ends up as exactly what is stored."""

from __future__ import annotations

import random

import pytest

from agentic_os.orchestrator.events import RefineChange
from agentic_os.orchestrator.refine import (
    EditStream,
    ReviewStream,
    parse_changes,
    parse_edit,
    parse_review,
)

REVIEW = (
    "<changes>\n"
    "- [defect] Pas 2: falta la data de lliurament — l'encàrrec la demana.\n"
    "- [Simplification] Introducció: repeteix el títol — sobra.\n"
    "</changes>\n"
    "<score>82</score>"
)

EDIT = (
    "<version>\n# Pla de llançament\n\n1. Data: 3 de novembre.\n2. Pressupost: 2.000 €.\n"
    "</version>\n"
    "<changelog>\n- [defect] Afegeix la data de lliurament.\n</changelog>"
)

QUOTING = (
    "Aquí la tens:\n"
    "<version>\n"
    "# Format de les respostes\n\n"
    "L'editor respon així:\n\n"
    "```xml\n<version>\nEl document.\n</version>\n"
    "<changelog>\n- [defect] Què.\n</changelog>\n```\n\n"
    "I la revisió acaba amb `</changes>` i `<score>N</score>`.\n"
    "</version>\n"
    "<changelog>\n"
    "- [clarity] Explica el format amb un exemple.\n"
    "</changelog>"
)
"""An edit whose document quotes every tag of the format, inside code and in prose."""


# -- reviews -----------------------------------------------------------------------------


def test_a_review_proposes_changes_with_their_kinds_and_a_score() -> None:
    review = parse_review(REVIEW)
    assert review.ok and not review.unchanged
    assert review.changes == (
        RefineChange("defect", "Pas 2: falta la data de lliurament — l'encàrrec la demana."),
        RefineChange("simplification", "Introducció: repeteix el títol — sobra."),
    )
    assert review.score == 82
    assert review.text == REVIEW.split("<changes>\n")[1].split("\n</changes>")[0]


def test_unchanged_proposes_nothing() -> None:
    review = parse_review("<changes>\nUNCHANGED\n</changes>\n<score>95</score>")
    assert review.ok and review.unchanged
    assert review.changes == () and review.score == 95 and review.text == "UNCHANGED"
    empty = parse_review("<changes>\n</changes>\n<score>91</score>")
    assert empty.ok and empty.unchanged and empty.text == ""


@pytest.mark.parametrize(
    "section",
    [
        "unchanged",
        "**UNCHANGED**",
        "`UNCHANGED`",
        "- UNCHANGED.",
        "«Unchanged»",
        "UNCHANGED — la versió ja compleix l'encàrrec.",
        "UNCHANGED\n\nLa versió ja compleix tot el que demana l'encàrrec.",
    ],
)
def test_unchanged_may_be_written_loosely(section: str) -> None:
    """In any case, wrapped or with punctuation; the exact UNCHANGED of the prompt may
    carry a note, on its line or after it."""
    review = parse_review(f"<changes>\n{section}\n</changes>\n<score>93</score>")
    assert review.ok and review.unchanged and review.changes == () and review.text == section


@pytest.mark.parametrize(
    "section",
    [
        "El pas 2 té un error greu: el total no quadra. Cal corregir-lo.",
        "No hi ha res a canviar.",
        "Sense canvis.",
        "Unchanged parts are fine, but step 2 is wrong: 3 + 4 is not 8.",
        "- [style] Títol: posa'l en negreta — queda millor.\n- Sense tipus de canvi.",
        "UNCHANGEDX",
    ],
)
def test_a_section_that_neither_lists_changes_nor_says_unchanged_is_a_failed_review(
    section: str,
) -> None:
    """Prose, bullets of no known kind or another way of saying it: the review is not in
    the format asked for, and taking it for «nothing to change» could end the turn while
    the reviewer proposed changes."""
    review = parse_review(f"<changes>\n{section}\n</changes>\n<score>40</score>")
    assert not review.ok and not review.unchanged and review.changes == ()
    assert review.text == section and review.score == 40


def test_the_kinds_may_come_in_catalan_or_spanish() -> None:
    """The prompts ask for the kinds in English, but a reply in the brief's language may
    translate them."""
    review = parse_review(
        "<changes>\n- [defecte] Pas 2: el total no quadra — 3 + 4 no fa 8.\n"
        "- [Claredat] Pas 1: massa llarg — escurça'l.\n"
        "- **[simplificació]** Annex: repeteix el pas 3 — treu-lo.\n"
        "- [Requisit] Pas 4: «en català» — l'encàrrec ho diu.\n"
        "- [defecto] Paso 5: falta la fecha — el encargo la pide.\n</changes>\n<score>40</score>"
    )
    assert review.ok and not review.unchanged
    assert [change.kind for change in review.changes] == [
        "defect",
        "clarity",
        "simplification",
        "requirement",
        "defect",
    ]
    assert review.changes[0].text == "Pas 2: el total no quadra — 3 + 4 no fa 8."
    spanish = parse_changes(
        "- [Claridad] a\n- [simplificación] b\n- [requisito] c\n- [SIMPLIFICACION] d"
    )
    assert [change.kind for change in spanish] == [
        "clarity",
        "simplification",
        "requirement",
        "simplification",
    ]
    edit = parse_edit(
        "<version>\nText.\n</version>\n<changelog>\n- [defecte] Corregeix el total.\n</changelog>"
    )
    assert edit.changes == (RefineChange("defect", "Corregeix el total."),)
    merged = parse_changes("- [fusió] De Claude.\n- [fusión] De ChatGPT.", merge=True)
    assert merged == (RefineChange("merge", "De Claude."), RefineChange("merge", "De ChatGPT."))


def test_a_merge_changelog_keeps_the_kinds_of_change_it_lists() -> None:
    """A version 1 that shortens the merge (over the owner's limit) may list what it
    removed: a kind of change stays that kind; any other line is a merge line."""
    changes = parse_changes(
        "- [merge] L'estructura ve de Claude.\n- [simplification] Treu l'annex.\n"
        "- [style] Negretes.",
        merge=True,
    )
    assert changes == (
        RefineChange("merge", "L'estructura ve de Claude."),
        RefineChange("simplification", "Treu l'annex."),
        RefineChange("merge", "[style] Negretes."),
    )


def test_bullets_that_do_not_match_are_ignored_and_at_most_five_count() -> None:
    lines = [
        "- [style] Títol: posa'l en negreta — queda millor.",  # not a kind of change
        "- Sense tipus de canvi.",
        "[defect] Sense guió: no és una llista.",
        "* [clarity] Pas 1: «objectiu» és vague — concreta'l.",
        "1. [requirement] Pas 3: «ha de ser en català» — l'encàrrec ho diu.",
        "- **[Defect]** Pas 4: el total no quadra — 3 + 4 no fa 8.",
        "- [defect]",  # no text
        *(f"- [simplification] Paràgraf {n}: es repeteix — treu-lo." for n in range(1, 6)),
    ]
    review = parse_review("<changes>\n" + "\n".join(lines) + "\n</changes>\n<score>70</score>")
    assert [(change.kind, change.text[:7]) for change in review.changes] == [
        ("clarity", "Pas 1: "),
        ("requirement", "Pas 3: "),
        ("defect", "Pas 4: "),
        ("simplification", "Paràgra"),
        ("simplification", "Paràgra"),
    ]


def test_a_change_may_go_on_in_the_next_indented_line() -> None:
    review = parse_review(
        "<changes>\n- [defect] Pas 2: el pressupost suma 2.100 €\n  i no 2.000 € — corregeix-lo.\n"
        "Text fora de cap canvi.\n- [clarity] Pas 3: «aviat» — posa-hi una data.\n</changes>"
    )
    assert review.changes == (
        RefineChange("defect", "Pas 2: el pressupost suma 2.100 € i no 2.000 € — corregeix-lo."),
        RefineChange("clarity", "Pas 3: «aviat» — posa-hi una data."),
    )
    assert review.score is None


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        ("<score>90</score>", 90),
        ("<score> 85/100 </score>", 85),
        ("<score>77%</score>", 77),
        ("<SCORE>0</SCORE>", 0),
        ("<score>100</score>", 100),
        ("<score>101</score>", None),
        ("<score>-5</score>", None),
        ("<score>molt bé</score>", None),
        ("<score>8 de 10</score>", None),
        ("Puntuació: 90", None),
        ("", None),
    ],
)
def test_the_score_is_a_number_from_0_to_100_or_none(score: str, expected: int | None) -> None:
    review = parse_review(f"<changes>\nUNCHANGED\n</changes>\n{score}")
    assert review.ok and review.score == expected


@pytest.mark.parametrize(
    "reply",
    [
        "- [defect] Pas 2: falta la data — l'encàrrec la demana.\n<score>80</score>",
        "<changes>\n- [defect] Pas 2: falta la data — tallada",
        "UNCHANGED",
        "",
    ],
)
def test_a_reply_without_a_changes_section_is_a_failed_review(reply: str) -> None:
    review = parse_review(reply)
    assert not review.ok and review.text is None and review.changes == ()


def test_a_review_that_quotes_the_closing_tag_keeps_its_changes() -> None:
    review = parse_review(
        "<changes>\n- [clarity] Secció 2: l'exemple acaba amb </changes> sense dir per què —"
        " explica-ho.\n- [defect] Secció 3: falta el total — l'encàrrec el demana.\n</changes>\n"
        "<score>80</score>"
    )
    assert [change.kind for change in review.changes] == ["clarity", "defect"]
    assert "acaba amb </changes> sense" in review.changes[0].text
    assert review.score == 80


def test_tags_ignore_case_and_inner_spaces() -> None:
    review = parse_review("< Changes >\n- [DEFECT] A: b — c.\n</ changes >\n< score >60</score >")
    assert review.changes == (RefineChange("defect", "A: b — c."),) and review.score == 60


# -- edits ---------------------------------------------------------------------------------


def test_an_edit_writes_a_complete_version_and_its_changelog() -> None:
    edit = parse_edit(EDIT)
    assert edit.complete
    assert edit.text == "# Pla de llançament\n\n1. Data: 3 de novembre.\n2. Pressupost: 2.000 €."
    assert edit.changes == (RefineChange("defect", "Afegeix la data de lliurament."),)
    assert edit.changelog_text == "- [defect] Afegeix la data de lliurament."


def test_a_document_that_quotes_the_tags_is_read_whole() -> None:
    edit = parse_edit(QUOTING)
    assert edit.complete
    assert edit.text.startswith("# Format de les respostes")
    assert edit.text.endswith("I la revisió acaba amb `</changes>` i `<score>N</score>`.")
    assert "```xml\n<version>\nEl document.\n</version>\n<changelog>" in edit.text
    assert edit.changes == (RefineChange("clarity", "Explica el format amb un exemple."),)


def test_a_version_that_ends_the_reply_needs_no_changelog() -> None:
    edit = parse_edit("<version>\nUn text.\n</version>\n")
    assert edit.complete and edit.text == "Un text." and edit.changes == ()


@pytest.mark.parametrize(
    ("reply", "text"),
    [
        ("Un document sense etiquetes.", "Un document sense etiquetes."),
        ("<version>\nUn document que no acaba", "Un document que no acaba"),
        (
            "<version>\nUn text.\n</version>\nI una nota.\n<changelog>\n- [defect] x\n</changelog>",
            "Un text.\n</version>\nI una nota.\n<changelog>\n- [defect] x\n</changelog>",
        ),
    ],
)
def test_a_reply_without_a_complete_version_is_kept_as_written(reply: str, text: str) -> None:
    edit = parse_edit(reply)
    assert not edit.complete and edit.text == text and edit.changes == ()


def test_a_reply_that_was_cut_off_is_never_a_complete_version() -> None:
    edit = parse_edit(EDIT, truncated=True)
    assert not edit.complete
    assert edit.text.startswith("# Pla de llançament") and edit.text.endswith("</changelog>")


def test_the_changelog_has_at_most_five_changes_and_may_be_left_open() -> None:
    bullets = "\n".join(f"- [simplification] Treu el paràgraf {n}." for n in range(1, 8))
    edit = parse_edit(f"<version>\nText.\n</version>\n<changelog>\n{bullets}")
    assert edit.complete and len(edit.changes) == 5
    assert edit.changes[-1] == RefineChange("simplification", "Treu el paràgraf 5.")


def test_the_merge_changelog_says_what_came_from_each_answer() -> None:
    changes = parse_changes(
        "- [merge] L'estructura ve de Claude.\n- La taula ve de ChatGPT.\nText solt.", merge=True
    )
    assert changes == (
        RefineChange("merge", "L'estructura ve de Claude."),
        RefineChange("merge", "La taula ve de ChatGPT."),
    )
    edit = parse_edit(
        "<version>\nV1\n</version>\n<changelog>\n- [merge] De tots dos.\n</changelog>"
    )
    assert edit.changes == ()  # an edit's changelog takes only the kinds of change
    merged = parse_edit(
        "<version>\nV1\n</version>\n<changelog>\n- [merge] De tots dos.\n</changelog>", merge=True
    )
    assert merged.changes == (RefineChange("merge", "De tots dos."),)


def test_a_preamble_before_the_version_is_dropped() -> None:
    edit = parse_edit("Aquí tens la nova versió:\n\n" + EDIT)
    assert edit.complete and edit.text.startswith("# Pla de llançament")


# -- streaming -----------------------------------------------------------------------------


def chunked(text: str, sizes: list[int]) -> list[str]:
    pieces: list[str] = []
    position = 0
    for size in sizes:
        if position >= len(text):
            break
        pieces.append(text[position : position + size])
        position += size
    if position < len(text):
        pieces.append(text[position:])
    return pieces


def splits(text: str) -> list[list[str]]:
    """The reply in one piece, character by character, in threes and at random."""
    generator = random.Random(len(text))
    return [
        [text],
        list(text),
        chunked(text, [3] * len(text)),
        chunked(text, [generator.randint(1, 12) for _ in text]),
    ]


def streamed(stream: ReviewStream | EditStream, chunks: list[str], **close: bool) -> dict[str, str]:
    texts: dict[str, str] = {"answer": "", "critique": "", "text": ""}
    for chunk in chunks:
        for section, text in stream.feed(chunk):
            texts[section] += text
    closing = stream.close(**close) if isinstance(stream, EditStream) else stream.close()
    for section, text in closing:
        texts[section] += text
    return texts


@pytest.mark.parametrize(
    "reply",
    [
        EDIT,
        QUOTING,
        "Preàmbul.\n" + EDIT,
        "<version>\nUn document que no acaba",
        "Un document sense etiquetes.",
        "<version>\nA < B i <vers no és cap etiqueta.\n</version>\n<changelog>\n- [clarity] x\n",
    ],
)
def test_an_edit_streams_its_version_as_the_answer_and_its_changelog_as_the_critique(
    reply: str,
) -> None:
    final = parse_edit(reply)
    for chunks in splits(reply):
        stream = EditStream()
        texts = streamed(stream, chunks)
        assert stream.final() == final
        assert texts["answer"] == final.text
        assert texts["critique"] == (final.changelog_text if final.complete else "")
        assert texts["text"] == ""


def test_a_cut_off_edit_streams_what_it_keeps() -> None:
    final = parse_edit(EDIT, truncated=True)
    for chunks in splits(EDIT):
        stream = EditStream()
        texts = streamed(stream, chunks, truncated=True)
        assert stream.final() == final
        assert texts["answer"] == final.text and texts["critique"] == ""


def test_the_version_streams_while_it_is_written() -> None:
    stream = EditStream()
    shown = [
        text
        for chunk in ("<version>\n# Pla\n\nPrimer ", "pas.\n\nSegon")
        for _, text in stream.feed(chunk)
    ]
    # Everything but the trailing whitespace is shown before the reply ends.
    assert "".join(shown) == "# Pla\n\nPrimer pas.\n\nSegon"
    # From the first closing tag on, the text waits for the end of the reply.
    assert stream.feed(" pas.\n</version>\n<changelog>\n- [defect] x\n") == [("answer", " pas.")]
    assert stream.close() == [("critique", "- [defect] x")]


@pytest.mark.parametrize(
    "reply",
    [
        REVIEW,
        "<changes>\nUNCHANGED\n</changes>\n<score>95</score>",
        "Preàmbul. <changes>\n- [clarity] A: </changes> citat — b.\n</changes>\n<score>1</score>",
        "<changes>\n- [defect] tallada",
    ],
)
def test_a_review_streams_its_changes_as_the_critique(reply: str) -> None:
    final = parse_review(reply)
    for chunks in splits(reply):
        stream = ReviewStream()
        texts = streamed(stream, chunks)
        assert stream.final() == final
        if final.ok:
            assert texts["critique"] == final.text
        assert texts["answer"] == "" and texts["text"] == ""


def test_no_piece_of_a_stream_carries_a_tag() -> None:
    for reply, stream_type in ((EDIT, EditStream), (REVIEW, ReviewStream)):
        for chunks in splits(reply):
            stream = stream_type()
            pieces = [piece for chunk in chunks for piece in stream.feed(chunk)]
            pieces += stream.close()
            for _section, text in pieces:
                for tag in ("version", "changelog", "changes", "score"):
                    assert f"<{tag}>" not in text and f"</{tag}>" not in text
