"""The fake provider in a refine turn (docs/adr/0010-refine-mode.md): deterministic
answers, a merge into a first version, reviews that propose one change for two rounds and
then nothing (or the test's own replies), edits that apply the proposed changes and a
shortening that fits the word budget, so that the engine's tests and the demo run the
whole loop without any model."""

from __future__ import annotations

from collections.abc import Sequence

from agentic_os.domain import AgentName, Purpose, words
from agentic_os.orchestrator.events import RefineChange
from agentic_os.orchestrator.prompts import (
    refine_answer_prompt,
    refine_edit_prompt,
    refine_merge_prompt,
    refine_review_prompt,
    refine_shorten_prompt,
    revision_prompt,
    system_prompt,
)
from agentic_os.orchestrator.refine import ReviewParse, parse_edit, parse_review
from agentic_os.providers.base import GenerationRequest, GenerationResult, TextDelta
from agentic_os.providers.fake import FakeProvider

BRIEF = "Pla de llançament de la botiga en línia"
VERSION = "## Pla\n\n1. Objectiu.\n2. Prototip.\n3. Revisió."


async def reply(provider: FakeProvider, prompt: str, purpose: Purpose) -> str:
    request = GenerationRequest(
        system=system_prompt(provider.agent), prompt=prompt, purpose=purpose
    )
    text = ""
    result: GenerationResult | None = None
    async for event in provider.stream(request):
        if isinstance(event, TextDelta):
            text += event.text
        else:
            result = event
    assert result is not None and result.text == text and not result.truncated
    return text


def review_prompt(agent: AgentName = "claude", brief: str = BRIEF) -> str:
    return refine_review_prompt(agent, brief, VERSION, words(VERSION), 300, [])


async def reviews(provider: FakeProvider, count: int, brief: str = BRIEF) -> list[ReviewParse]:
    return [
        parse_review(await reply(provider, review_prompt(provider.agent, brief), "revision"))
        for _ in range(count)
    ]


async def test_the_answers_of_a_refine_turn_answer_the_brief() -> None:
    provider = FakeProvider("claude", chunk_delay=0)
    text = await reply(provider, refine_answer_prompt("claude", BRIEF), "answer")
    assert text.startswith(f"### {BRIEF}")
    debate = await reply(provider, BRIEF, "answer")
    assert text == debate  # the same canned answer as for the brief alone


async def test_the_merge_writes_a_complete_first_version() -> None:
    provider = FakeProvider("chatgpt", chunk_delay=0)
    prompt = refine_merge_prompt(BRIEF, {"claude": "A", "chatgpt": "B"})
    edit = parse_edit(await reply(provider, prompt, "synthesis"), merge=True)
    assert edit.complete and edit.text.startswith(f"## {BRIEF}")
    assert 50 < words(edit.text) < 300
    assert [change.kind for change in edit.changes] == ["merge", "merge"]


async def test_reviews_propose_one_change_for_two_rounds_and_then_nothing() -> None:
    claude = FakeProvider("claude", chunk_delay=0)
    parsed = await reviews(claude, 4)
    assert [(review.ok, len(review.changes), review.score) for review in parsed] == [
        (True, 1, 78),
        (True, 1, 92),
        (True, 0, 95),
        (True, 0, 95),
    ]
    assert [review.changes[0].kind for review in parsed[:2]] == ["defect", "clarity"]
    assert parsed[2].unchanged and parsed[2].text == "UNCHANGED"
    # A new brief starts the sequence again; each agent proposes its own changes.
    again = await reviews(claude, 1, brief="Un altre encàrrec")
    assert again[0].changes == parsed[0].changes and again[0].score == 78
    chatgpt = await reviews(FakeProvider("chatgpt", chunk_delay=0), 1)
    assert chatgpt[0].changes[0].kind == "defect"
    assert chatgpt[0].changes != parsed[0].changes


async def test_the_reviews_of_a_test_are_its_own_and_the_last_one_repeats() -> None:
    replies: Sequence[str] = (
        "<changes>\n- [requirement] Pas 1: «en línia» — l'encàrrec ho diu.\n</changes>\n"
        "<score>60</score>",
        "<changes>\nUNCHANGED\n</changes>\n<score>99</score>",
    )
    provider = FakeProvider("claude", chunk_delay=0, refine_reviews=replies)
    parsed = await reviews(provider, 3)
    assert [review.score for review in parsed] == [60, 99, 99]
    assert parsed[0].changes == (
        RefineChange("requirement", "Pas 1: «en línia» — l'encàrrec ho diu."),
    )
    assert (await reviews(provider, 1, brief="Un altre"))[0].score == 60


async def test_an_edit_applies_the_proposed_changes() -> None:
    provider = FakeProvider("claude", chunk_delay=0)
    proposed: dict[AgentName, tuple[RefineChange, ...]] = {
        "claude": (RefineChange("defect", "Pas 2: falta la data — l'encàrrec la demana."),),
        "chatgpt": (RefineChange("simplification", "Pas 3: sobra — treu-lo."),),
    }
    prompt = refine_edit_prompt(BRIEF, VERSION, proposed, [], words(VERSION), 300)
    edit = parse_edit(await reply(provider, prompt, "synthesis"))
    assert edit.complete and edit.text.startswith(VERSION) and edit.text != VERSION
    assert edit.changes == (
        RefineChange("defect", "Pas 2: falta la data — l'encàrrec la demana."),
        RefineChange("simplification", "Pas 3: sobra — treu-lo."),
    )
    # The next edit writes a version of its own again (never the same text twice).
    later = refine_edit_prompt(BRIEF, edit.text, proposed, [], words(edit.text), 300)
    following = parse_edit(await reply(provider, later, "synthesis"))
    assert following.complete and following.text.startswith(edit.text)
    assert following.text != edit.text


async def test_a_shortened_version_fits_the_budget_and_keeps_the_changelog() -> None:
    provider = FakeProvider("chatgpt", chunk_delay=0)
    draft = "\n".join(f"Línia {n} del document, amb sis paraules." for n in range(1, 41))
    changes = (RefineChange("defect", "Corregeix la data."),)
    prompt = refine_shorten_prompt(BRIEF, draft, changes, words(draft), 100)
    edit = parse_edit(await reply(provider, prompt, "synthesis"))
    assert edit.complete and 0 < words(edit.text) <= 100
    assert draft.startswith(edit.text)
    assert edit.changes == changes


CODE = "```xml\n<project>\n  <version>1.2.0</version>\n</project>\n```\n<Review score={5} />"
"""A document whose code writes tags of the refine prompts, which quote it escaped."""


async def test_the_fake_writes_back_the_tags_its_prompts_quote_escaped() -> None:
    """Like a model that follows the prompts' note (orchestrator/prompts.py), the fake
    reads «&lt;» before a tag of the prompts as «<» and writes «<» in the document."""
    provider = FakeProvider("claude", chunk_delay=0)
    proposed: dict[AgentName, tuple[RefineChange, ...]] = {
        "claude": (RefineChange("defect", "El <version> del pom.xml — l'encàrrec en demana un."),),
    }
    prompt = refine_edit_prompt(BRIEF, CODE, proposed, [], words(CODE), 300)
    assert "&lt;version>1.2.0&lt;/version>" in prompt
    edit = parse_edit(await reply(provider, prompt, "synthesis"))
    assert edit.complete and edit.text.startswith(CODE) and "&lt;" not in edit.text
    assert edit.changes == proposed["claude"]
    changes = (RefineChange("defect", "Afegeix el <version>."),)
    shorten = refine_shorten_prompt(BRIEF, CODE, changes, words(CODE), 300)
    shortened = parse_edit(await reply(provider, shorten, "synthesis"))
    assert shortened.complete and shortened.text == CODE and shortened.changes == changes


async def test_a_debate_question_that_quotes_a_brief_is_answered_as_a_debate() -> None:
    """Only a refine turn's prompts escape ``<brief>``: a debate's revision of a question
    that writes one is still a revision of the debate."""
    question = "Revisa aquest format:\n<brief>\nUn encàrrec.\n</brief>"
    provider = FakeProvider("claude", chunk_delay=0)
    text = await reply(provider, revision_prompt("claude", question, "A", "B"), "revision")
    assert "<critique>" in text and "<answer>" in text and "<changes>" not in text
