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


# -- tags that are text, not structure (N1) --------------------------------------------------


def random_chunks(text: str, seed: int, cuts: int = 15) -> list[str]:
    rng = random.Random(seed)
    points = sorted(rng.sample(range(1, len(text)), min(cuts, len(text) - 1)))
    return [text[a:b] for a, b in zip([0, *points], [*points, len(text)], strict=True)]


def streamed_like_whole(text: str, seeds: int = 12) -> RevisionParse:
    """Parse ``text`` in one chunk, byte by byte and in random chunks: the result must be
    the same, and what streams for each section must be exactly the stored section."""
    whole = run([text])[1]
    for chunks in (list(text), *(random_chunks(text, seed) for seed in range(seeds))):
        pieces, parsed = run(chunks)
        assert parsed == whole, chunks
        assert joined(pieces, "critique") == parsed.critique
        assert joined(pieces, "answer") == ("" if parsed.unchanged else parsed.answer)
    return whole


def test_tags_in_inline_code_are_text() -> None:
    answer = (
        "Ask the model to wrap its reply in `<answer>` and `</answer>` tags, then extract "
        "the text between them with a regex.\n\nStep 2: validate the output."
    )
    text = (
        "<critique>- Missing the closing tag.</critique>"
        f"<answer>{answer}</answer><agreement>60</agreement>"
    )
    parsed = streamed_like_whole(text)
    assert parsed.critique == "- Missing the closing tag."
    assert parsed.answer == answer
    assert parsed.agreement == 60 and not parsed.unchanged


def test_tags_in_a_fenced_block_are_text() -> None:
    answer = (
        "Here is the XML:\n\n```xml\n<quiz>\n  <question>2+2?</question>\n"
        "  <answer>4</answer>\n  <agreement>100</agreement>\n</quiz>\n```\n\nThat is the format."
    )
    text = f"<critique>None</critique>\n<answer>\n{answer}\n</answer>\n<agreement>85</agreement>"
    parsed = streamed_like_whole(text)
    assert parsed.answer == answer
    assert parsed.agreement == 85


@pytest.mark.parametrize(
    "block",
    [
        "~~~\n</answer>\n<agreement>1</agreement>\n~~~",
        "````md\n```\n</answer>\n<critique>x</critique>\n```\n````",
        "1. Pas:\n\n    ```xml\n    </answer>\n    <agreement>1</agreement>\n    ```",
    ],
)
def test_tilde_long_and_indented_fences(block: str) -> None:
    answer = f"Abans.\n\n{block}\n\nDesprés."
    parsed = streamed_like_whole(f"<answer>{answer}</answer>\n<agreement>70</agreement>")
    assert parsed.answer == answer
    assert parsed.agreement == 70


def test_a_critique_that_mentions_tags_in_code_is_whole() -> None:
    critique = (
        "- ChatGPT forgot to close the `</critique>` tag in its example; also it omits step 3."
    )
    text = (
        f"<critique>{critique}</critique>\n<answer>Full answer.</answer><agreement>70</agreement>"
    )
    parsed = streamed_like_whole(text)
    assert (parsed.critique, parsed.answer, parsed.agreement) == (critique, "Full answer.", 70)


def test_a_critique_that_mentions_tags_in_prose_is_whole() -> None:
    critique = "- The </critique> tag of ChatGPT's example is misplaced.\n- It omits step 3."
    text = f"<critique>{critique}</critique>\n<answer>Fixed.</answer>\n<agreement>55</agreement>"
    parsed = streamed_like_whole(text)
    assert (parsed.critique, parsed.answer, parsed.agreement) == (critique, "Fixed.", 55)


def test_an_answer_that_mentions_its_tags_in_prose_is_whole() -> None:
    answer = "Put the reply between <answer> and </answer> tags, then parse it.\n\nThat's all."
    text = f"<critique>None</critique><answer>{answer}</answer>\n<agreement>65</agreement>"
    parsed = streamed_like_whole(text)
    assert parsed.answer == answer
    assert parsed.agreement == 65


def test_an_unclosed_backtick_does_not_hide_the_closing_tags() -> None:
    parsed = streamed_like_whole(
        "<answer>Use a single ` to quote.</answer>\n<agreement>70</agreement>"
    )
    assert parsed.answer == "Use a single ` to quote."
    assert parsed.agreement == 70


def test_untagged_reply_with_tags_in_code_is_the_answer() -> None:
    reply = "Here is the answer:\n\n```xml\n<answer>4</answer>\n```"
    parsed = streamed_like_whole(reply)
    assert parsed.answer == reply
    assert parsed.critique == ""


def test_a_loose_agreement_line_after_the_answer_is_not_answer_text() -> None:
    pieces, parsed = run(["<answer>a</answer>\nAgreement: 77"])
    assert parsed.answer == "a" and parsed.agreement == 77
    assert joined(pieces, "answer") == "a"


def test_answer_text_streams_while_a_code_block_is_open() -> None:
    parser = RevisionStreamParser()
    parser.feed("<critique>x</critique><answer>Codi:\n```xml\n")
    pieces = parser.feed("print(1)\n")
    # The code line shows at once (after the line break held from the previous chunk).
    assert joined(pieces, "answer") == "\nprint(1)"


def test_a_tag_in_an_open_code_block_waits_for_the_block_to_close() -> None:
    parser = RevisionStreamParser()
    parser.feed("<critique>x</critique><answer>Codi:\n```xml\n")
    # Code if the block closes, structure if it never does: held until then.
    assert parser.feed("<answer>4</answer>\n") == []
    pieces = parser.feed("```\nFi.")
    assert joined(pieces, "answer") == "\n<answer>4</answer>\n```\nFi."
    pieces.extend(parser.close())
    assert parser.final().answer == "Codi:\n```xml\n<answer>4</answer>\n```\nFi."


def test_an_unclosed_fence_in_the_critique_does_not_hide_the_answer() -> None:
    parsed = streamed_like_whole(
        "<critique>- ChatGPT's code:\n```\nfoo()\n</critique>\n"
        "<answer>The fixed answer.</answer>\n<agreement>50</agreement>"
    )
    assert parsed.critique == "- ChatGPT's code:\n```\nfoo()"
    assert (parsed.answer, parsed.agreement) == ("The fixed answer.", 50)


def test_an_unclosed_fence_in_the_answer_does_not_hide_its_end() -> None:
    parsed = streamed_like_whole(
        "<critique>None</critique>\n<answer>Use this:\n```python\nprint('hi')\n"
        "</answer>\n<agreement>80</agreement>"
    )
    assert (parsed.answer, parsed.agreement) == ("Use this:\n```python\nprint('hi')", 80)


def test_a_fence_closed_at_the_very_end_is_still_code() -> None:
    reply = "<critique>None</critique><answer>Format:\n```xml\n<answer>4</answer>\n```"
    parsed = streamed_like_whole(reply)
    assert parsed.answer == "Format:\n```xml\n<answer>4</answer>\n```"


# -- text between and after the sections ------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    ["Here is my revised answer:", "**Revised answer**", "## Resposta millorada", "---"],
)
def test_a_header_between_the_critique_and_the_answer_is_dropped(header: str) -> None:
    for layout in (
        f"<critique>- Misses X.</critique>\n{header}\n<answer>The answer is 42.</answer>",
        f"<critique>\n- Misses X.\n</critique>\n\n{header}\n\n<answer>\nThe answer is 42.\n"
        "</answer>",
    ):
        parsed = streamed_like_whole(f"{layout}\n<agreement>70</agreement>")
        assert (parsed.critique, parsed.answer, parsed.agreement) == (
            "- Misses X.",
            "The answer is 42.",
            70,
        )


@pytest.mark.parametrize(
    "between", ["My agreement with ChatGPT is high.", "**Agreement**", "Acord:\n\n---"]
)
def test_text_between_the_answer_and_the_agreement_is_dropped(between: str) -> None:
    parsed = streamed_like_whole(
        f"<critique>None</critique>\n<answer>The answer is 42.</answer>\n{between}\n"
        "<agreement>85</agreement>"
    )
    assert (parsed.answer, parsed.agreement) == ("The answer is 42.", 85)


@pytest.mark.parametrize("sign_off", ["Let me know if you need more.", "Espero que t'ajudi! 🙂"])
def test_a_sign_off_after_the_answer_is_dropped(sign_off: str) -> None:
    for end in (f"\n\n{sign_off}", f"\n{sign_off}\n<agreement>60</agreement>"):
        parsed = streamed_like_whole(
            f"<critique>None</critique>\n<answer>The answer is 42.</answer>{end}"
        )
        assert parsed.answer == "The answer is 42."


def test_a_critique_followed_by_an_untagged_answer() -> None:
    parsed = streamed_like_whole(
        "<critique>- Misses X.</critique>\n\nThe improved answer is that X matters because Y."
    )
    assert parsed.critique == "- Misses X."
    assert parsed.answer == "The improved answer is that X matters because Y."
    assert parsed.agreement is None


def test_a_long_text_after_a_closing_tag_in_prose_is_answer_text() -> None:
    # Past a few hundred characters the tag was text: the answer streams on.
    answer = "Close it with </answer> and go on. " + "More explanation here. " * 20
    text = f"<critique>None</critique><answer>{answer}</answer>\n<agreement>65</agreement>"
    parsed = streamed_like_whole(text)
    assert (parsed.answer, parsed.agreement) == (answer.strip(), 65)
    parser = RevisionStreamParser()
    shown = parser.feed(text[: text.index("</answer>\n<agreement>")])
    assert joined(shown, "answer") == answer.strip()


# -- UNCHANGED with a short note (N2) --------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "note"),
    [
        (
            "UNCHANGED (my previous answer already covers this)",
            "my previous answer already covers this",
        ),
        ("**UNCHANGED** — sense canvis", "sense canvis"),
        ("UNCHANGED: la resposta ja és completa.", "la resposta ja és completa."),
        ("`UNCHANGED` - res a afegir", "res a afegir"),
        (
            "UNCHANGED. My previous answer already covers this.",
            "My previous answer already covers this.",
        ),
        ("UNCHANGED.", None),
        ("Unchanged", None),
        ("*unchanged.*", None),
    ],
)
def test_unchanged_with_a_short_note(answer: str, note: str | None) -> None:
    text = f"<critique>None</critique>\n<answer>\n{answer}\n</answer>\n<agreement>80</agreement>"
    parsed = streamed_like_whole(text)
    assert parsed.unchanged is True
    assert parsed.answer == "UNCHANGED"
    assert parsed.unchanged_note == note
    assert parsed.agreement == 80


@pytest.mark.parametrize(
    "answer",
    [
        "UNCHANGED: " + "la nota és massa llarga " * 12,
        "UNCHANGED\n\n" + "Una resposta completa que es repeteix sencera. " * 8,
        "Unchanged in substance, but here is a tighter version.",
        "UNCHANGEDLY wrong.",
        # A note needs the exact marker the prompt asks for, and stays on its line.
        "Unchanged: the ECB kept its deposit rate at 2%.",
        "unchanged (the rate is still 2%)",
        "UNCHANGED\n\nCorrection: it was 1998, not 1997.",
        "UNCHANGED\nMy previous answer already covers this.",
    ],
)
def test_anything_longer_is_a_normal_answer(answer: str) -> None:
    text = f"<critique>- x</critique>\n<answer>\n{answer}\n</answer>\n<agreement>40</agreement>"
    parsed = streamed_like_whole(text)
    assert parsed.unchanged is False and parsed.unchanged_note is None
    assert parsed.answer == answer.strip()


FUZZ_FRAGMENTS = (
    "<critique>",
    "</critique>",
    "<answer>",
    "</answer>",
    "<agreement>",
    "</agreement>",
    "`",
    "``",
    "```",
    "~~~",
    "\n",
    " ",
    "text",
    "UNCHANGED",
    " (nota)",
    "`<answer>`",
    "```xml\n<answer>4</answer>\n```",
    "< answer >",
    "</ans",
    "<",
    "Agreement: 70",
    "70",
    "**",
    "    ",
    "a < b",
    "x" * 150,
    "Here is my revised answer:",
    "Let me know!",
    "Unchanged: x",
    "UNCHANGED. Nota.",
    "\n```",
)


@pytest.mark.parametrize("seed", range(4))
def test_random_replies_parse_the_same_in_any_chunking(seed: int) -> None:
    """Tags, code and UNCHANGED notes mixed at random: the parse never depends on how the
    reply was split, and what streams is always what is stored."""
    rng = random.Random(seed)
    for _ in range(150):
        text = "".join(rng.choice(FUZZ_FRAGMENTS) for _ in range(rng.randint(1, 30)))
        if len(text) > 1:
            streamed_like_whole(text, seeds=2)
