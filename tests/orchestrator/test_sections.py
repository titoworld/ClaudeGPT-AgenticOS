import random

import pytest

from agentic_os.orchestrator.sections import Piece, RevisionParse, RevisionStreamParser

WELL_FORMED = (
    "<critique>\n- Falta un exemple.\n- La xifra de 2019 és incorrecta.\n</critique>\n"
    "<answer>\nLa resposta millorada.\n\nAmb `a < b` i una <div> literal.\n</answer>\n"
    "<agreement>72</agreement>"
)


def run(chunks: list[str]) -> tuple[list[Piece], RevisionParse]:
    parser = RevisionStreamParser()
    pieces: list[Piece] = []
    for chunk in chunks:
        pieces.extend(parser.feed(chunk))
    pieces.extend(parser.close())
    return pieces, parser.final()


def joined(pieces: list[Piece], section: str) -> str:
    return "".join(text for sec, text in pieces if sec == section)


def test_well_formed_reply() -> None:
    pieces, parsed = run([WELL_FORMED])
    assert parsed.critique == "- Falta un exemple.\n- La xifra de 2019 és incorrecta."
    assert parsed.answer == "La resposta millorada.\n\nAmb `a < b` i una <div> literal."
    assert parsed.agreement == 72
    assert parsed.unchanged is False
    assert joined(pieces, "critique") == parsed.critique
    assert joined(pieces, "answer") == parsed.answer


def test_byte_by_byte_matches_single_chunk() -> None:
    whole = run([WELL_FORMED])
    pieces, parsed = run(list(WELL_FORMED))
    assert parsed == whole[1]
    assert joined(pieces, "critique") == parsed.critique
    assert joined(pieces, "answer") == parsed.answer
    for _, text in pieces:
        for tag in ("<critique", "</critique", "<answer", "</answer", "<agreement", "72</"):
            assert tag not in text


@pytest.mark.parametrize("seed", range(20))
def test_random_splits_are_equivalent(seed: int) -> None:
    rng = random.Random(seed)
    cuts = sorted(rng.sample(range(1, len(WELL_FORMED)), 12))
    chunks = [WELL_FORMED[a:b] for a, b in zip([0, *cuts], [*cuts, len(WELL_FORMED)], strict=True)]
    pieces, parsed = run(chunks)
    assert parsed == run([WELL_FORMED])[1]
    assert joined(pieces, "answer") == parsed.answer


def test_answer_streams_before_the_end() -> None:
    parser = RevisionStreamParser()
    assert parser.feed("<critique>None</critique><answer>Primera part") == [
        ("critique", "None"),
        ("answer", "Primera part"),
    ]


@pytest.mark.parametrize(
    "answer", ["UNCHANGED", "  UNCHANGED  ", "**UNCHANGED**", "unchanged.", "`UNCHANGED`"]
)
def test_unchanged_is_never_streamed(answer: str) -> None:
    text = f"<critique>None</critique>\n<answer>\n{answer}\n</answer>\n<agreement>95</agreement>"
    pieces, parsed = run(list(text))
    assert parsed.unchanged is True
    assert parsed.answer == "UNCHANGED"
    assert parsed.agreement == 95
    assert joined(pieces, "answer") == ""


def test_prefix_of_unchanged_is_released() -> None:
    pieces, parsed = run(list("<answer>Un pas enrere.</answer><agreement>40</agreement>"))
    assert parsed.unchanged is False
    assert parsed.answer == "Un pas enrere."
    assert joined(pieces, "answer") == "Un pas enrere."


def test_truncated_unchanged_prefix_is_kept_as_answer() -> None:
    pieces, parsed = run(["<answer>UNCH"])
    assert parsed.answer == "UNCH"
    assert parsed.unchanged is False
    assert joined(pieces, "answer") == "UNCH"


def test_missing_closing_tags() -> None:
    _, parsed = run(["<critique>Cap error<answer>Resposta nova<agreement>81"])
    assert parsed.critique == "Cap error"
    assert parsed.answer == "Resposta nova"
    assert parsed.agreement == 81


def test_tags_with_whitespace_and_case() -> None:
    _, parsed = run(["< Critique >x</ CRITIQUE >< answer >y</answer ><AGREEMENT> 60 </agreement>"])
    assert (parsed.critique, parsed.answer, parsed.agreement) == ("x", "y", 60)


def test_text_outside_tags_is_ignored() -> None:
    text = "Aquí tens la revisió:\n" + WELL_FORMED + "\nGràcies!"
    pieces, parsed = run([text])
    assert "Aquí tens" not in parsed.answer + parsed.critique
    assert "Gràcies" not in joined(pieces, "answer")


def test_untagged_reply_falls_back_to_answer() -> None:
    pieces, parsed = run(["Només una ", "resposta sense etiquetes."])
    assert parsed.answer == "Només una resposta sense etiquetes."
    assert parsed.critique == ""
    assert parsed.agreement is None
    assert joined(pieces, "answer") == parsed.answer


def test_long_untagged_reply_streams_as_answer() -> None:
    parser = RevisionStreamParser()
    streamed: list[Piece] = []
    for word in ("paraula " * 60).split(" "):
        streamed.extend(parser.feed(word + " "))
    assert streamed and all(section == "answer" for section, _ in streamed)
    streamed.extend(parser.close())
    assert joined(streamed, "answer") == parser.final().answer


def test_untagged_answer_between_tags() -> None:
    pieces, parsed = run(["<critique>- x</critique>\nResposta sense etiqueta\n<agreement>50"])
    assert parsed.answer == "Resposta sense etiqueta"
    assert joined(pieces, "answer") == parsed.answer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("150", 100), ("-5", 0), ("85/100", 85), ("molt", None), ("", None)],
)
def test_agreement_values(raw: str, expected: int | None) -> None:
    _, parsed = run([f"<answer>a</answer><agreement>{raw}</agreement>"])
    assert parsed.agreement == expected


def test_loose_agreement_without_tag() -> None:
    _, parsed = run(["<answer>a</answer>\nAgreement: 77"])
    assert parsed.agreement == 77


def test_garbage_and_truncated_tag() -> None:
    pieces, parsed = run(["<answer>x <<b>> </ans"])
    assert parsed.answer == "x <<b>>"
    assert all("</ans" not in text for _, text in pieces)


def test_final_is_idempotent_and_feed_after_close_fails() -> None:
    parser = RevisionStreamParser()
    parser.feed(WELL_FORMED)
    assert parser.final() == parser.final()
    assert parser.close() == []
    with pytest.raises(RuntimeError):
        parser.feed("més")
