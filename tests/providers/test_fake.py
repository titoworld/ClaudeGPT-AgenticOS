"""The demo provider (``AOS_*_MODE=fake``) in the turn's language
(docs/adr/0011-internationalization.md): its answers, revisions, syntheses and refine
replies are written in English, Spanish or Catalan and keep every marker the engine
parses; its status and its models are lazy texts, made in the language of whoever reads
them. (The other tests check its Catalan, the default of the tests.)"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_os import i18n
from agentic_os.domain import AgentName, Purpose, words
from agentic_os.orchestrator.events import RefineChange
from agentic_os.orchestrator.prompts import (
    answer_prompt,
    refine_edit_prompt,
    refine_merge_prompt,
    refine_review_prompt,
    revision_prompt,
    synthesis_prompt,
    system_prompt,
)
from agentic_os.orchestrator.refine import parse_edit, parse_review
from agentic_os.orchestrator.sections import RevisionParse, RevisionStreamParser
from agentic_os.providers.base import (
    Attachment,
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    ProviderError,
    RefusalError,
    TextDelta,
)
from agentic_os.providers.fake import REFINE_PROPOSALS, FakeProvider

QUESTION = "How do I plan a product launch?"
BRIEF = "A launch plan for the online shop"


async def reply(
    provider: FakeProvider,
    prompt: str,
    purpose: Purpose = "answer",
    *,
    attachments: tuple[Attachment, ...] = (),
    history: tuple[ChatTurn, ...] = (),
) -> str:
    request = GenerationRequest(
        system=system_prompt(provider.agent),
        prompt=prompt,
        purpose=purpose,
        attachments=attachments,
        history=history,
    )
    text = ""
    result: GenerationResult | None = None
    async for event in provider.stream(request):
        if isinstance(event, TextDelta):
            text += event.text
        else:
            result = event
    assert result is not None and result.text == text
    return text


def attachment(kind: str, name: str, **fields: object) -> Attachment:
    mime = {"pdf": "application/pdf", "image": "image/png"}.get(kind, "text/plain")
    return Attachment(
        kind=kind,  # type: ignore[arg-type]
        name=name,
        mime=mime,
        sha256="0" * 64,
        size=1000,
        path=Path("/nonexistent") / name,  # never read: the fake only names them
        **fields,  # type: ignore[arg-type]
    )


def revision(text: str) -> RevisionParse:
    parser = RevisionStreamParser()
    parser.feed(text)
    parser.close()
    return parser.final()


@pytest.mark.parametrize(
    ("lang", "claude", "chatgpt", "received"),
    [
        (
            "en",
            [
                f"### {QUESTION}\n\n",
                "1. **Define the goal.** Pin down what you want to achieve",
                "> Demo answer: set `AOS_CLAUDE_MODE=cli` or `api` to talk to the real model.",
            ],
            [
                f"**Quick answer:** on “{QUESTION}”, what matters most",
                "| Aspect | Proposal |",
                "*Demo answer (`AOS_CHATGPT_MODE=fake`).*",
            ],
            "**Attachments received:** report.pdf (PDF, 12 pages; only the extracted text), "
            "photo.png (image), notes.md (text file), one.pdf (PDF, 1 page).",
        ),
        (
            "es",
            [
                f"### {QUESTION}\n\n",
                "1. **Define el objetivo.** Concreta qué quieres conseguir",
                "> Respuesta de demostración: configura `AOS_CLAUDE_MODE=cli` o `api` para "
                "hablar con el modelo real.",
            ],
            [
                f"**Respuesta rápida:** sobre «{QUESTION}», lo más importante",
                "| Aspecto | Propuesta |",
                "*Respuesta de demostración (`AOS_CHATGPT_MODE=fake`).*",
            ],
            "**Adjuntos recibidos:** report.pdf (PDF, 12 páginas; solo el texto extraído), "
            "photo.png (imagen), notes.md (archivo de texto), one.pdf (PDF, 1 página).",
        ),
    ],
)
async def test_the_answers_are_written_in_the_language_of_the_turn(
    lang: i18n.Lang, claude: list[str], chatgpt: list[str], received: str
) -> None:
    files = (
        attachment("pdf", "report.pdf", pages=12, text="Report.", mode="text"),
        attachment("image", "photo.png", width=10, height=10),
        attachment("text", "notes.md", text="Notes."),
        attachment("pdf", "one.pdf", pages=1),
    )
    with i18n.use(lang):
        answers = {
            agent: await reply(FakeProvider(agent, chunk_delay=0), answer_prompt(QUESTION))
            for agent in ("claude", "chatgpt")
        }
        mentioned = await reply(
            FakeProvider("claude", chunk_delay=0), answer_prompt(QUESTION), attachments=files
        )
    assert answers["claude"].startswith(claude[0])
    assert all(part in answers["claude"] for part in claude)
    assert all(part in answers["chatgpt"] for part in chatgpt)
    assert mentioned == f"{answers['claude']}\n\n{received}"


@pytest.mark.parametrize(
    ("lang", "critique", "addition", "agreed"),
    [
        (
            "en",
            "- ChatGPT's answer lacks a concrete example.\n"
            "- It does not say how to check that the solution works.",
            "**After reviewing ChatGPT's answer:** I am adding an example.",
            "- No relevant errors: ChatGPT's answer is correct and complete.",
        ),
        (
            "es",
            "- A la respuesta de ChatGPT le falta un ejemplo concreto.\n"
            "- No dice cómo se comprueba que la solución funciona.",
            "**Después de revisar la respuesta de ChatGPT:** añado un ejemplo.",
            "- Ningún error relevante: la respuesta de ChatGPT es correcta y completa.",
        ),
    ],
)
async def test_a_revision_keeps_what_the_engine_parses_in_every_language(
    lang: i18n.Lang, critique: str, addition: str, agreed: str
) -> None:
    provider = FakeProvider("claude", chunk_delay=0)
    prompt = revision_prompt("claude", QUESTION, "My answer.", "Its answer.")
    with i18n.use(lang):
        first = revision(await reply(provider, prompt, "revision"))
        second = revision(await reply(provider, prompt, "revision"))
    assert (first.agreement, first.unchanged, first.critique) == (72, False, critique)
    assert first.answer.startswith("My answer.\n\n" + addition)
    assert (second.agreement, second.unchanged, second.critique) == (90, True, agreed)


@pytest.mark.parametrize(
    ("lang", "synthesis", "summary"),
    [
        (
            "en",
            "Claude and ChatGPT agree on the essentials:",
            "Summary (demo): The user asked about “First question”; “Second question”.",
        ),
        (
            "es",
            "Claude y ChatGPT coinciden en lo esencial:",
            "Resumen (demo): El usuario ha preguntado por «First question»; «Second question».",
        ),
    ],
)
async def test_the_synthesis_and_the_summary_in_every_language(
    lang: i18n.Lang, synthesis: str, summary: str
) -> None:
    provider = FakeProvider("chatgpt", chunk_delay=0)
    prompt = synthesis_prompt(QUESTION, {"claude": "A", "chatgpt": "B"}, {})
    history = (
        # The line that stands for a question's attachments is left out, in English too.
        ChatTurn("user", "[Attachments: a.pdf (PDF)]\nFirst question"),
        ChatTurn("assistant", "An answer.", agent="chatgpt"),
        ChatTurn("user", "[Adjunts: b.pdf (PDF)]\nSecond question"),
    )
    with i18n.use(lang):
        written = await reply(provider, prompt, "synthesis")
        summarized = await reply(provider, "Summarize.", "summary", history=history)
    assert written.startswith(f"## {QUESTION}\n\n{synthesis}\n\n")
    assert summarized == summary


@pytest.mark.parametrize(
    ("lang", "steps", "change"),
    [
        ("en", "The numbered steps come from Claude's answer.", "Change"),
        ("es", "Los pasos numerados vienen de la respuesta de Claude.", "Cambio"),
    ],
)
async def test_a_refine_turn_keeps_what_the_engine_parses_in_every_language(
    lang: i18n.Lang, steps: str, change: str
) -> None:
    claude = FakeProvider("claude", chunk_delay=0)
    with i18n.use(lang):
        merged = parse_edit(
            await reply(
                claude, refine_merge_prompt(BRIEF, {"claude": "A", "chatgpt": "B"}), "synthesis"
            ),
            merge=True,
        )
        document = merged.text
        review_prompt = refine_review_prompt("claude", BRIEF, document, words(document), 300, [])
        reviews = [parse_review(await reply(claude, review_prompt, "revision")) for _ in range(3)]
        proposals = REFINE_PROPOSALS["claude"]
        proposed: dict[AgentName, tuple[RefineChange, ...]] = {"claude": reviews[0].changes}
        edit_prompt = refine_edit_prompt(BRIEF, document, proposed, [], words(document), 300)
        edit = parse_edit(await reply(claude, edit_prompt, "synthesis"))
    assert merged.complete and document.startswith(f"## {BRIEF}\n\n")
    assert merged.changes[0] == RefineChange("merge", steps)
    assert [(review.ok, review.score) for review in reviews] == [(True, 78), (True, 92), (True, 95)]
    assert [review.changes[0].kind for review in reviews[:2]] == ["defect", "clarity"]
    assert reviews[0].changes[0].text == proposals[0].removeprefix("- [defect] ")
    assert reviews[2].unchanged
    assert edit.complete and edit.changes == reviews[0].changes
    assert edit.text == f"{document}\n\n**{change} 1:** {reviews[0].changes[0].text}"
    # Read in another language, the proposals are that language's.
    assert REFINE_PROPOSALS["claude"] != proposals


@pytest.mark.parametrize(
    ("lang", "failed", "refused", "refusal"),
    [
        (
            "en",
            "ChatGPT (demo) failed on purpose.",
            "ChatGPT (demo) declined to answer this request.",
            "I can't help with this request.",
        ),
        (
            "es",
            "ChatGPT (demo) ha fallado a propósito.",
            "ChatGPT (demo) se ha negado a responder a esta petición.",
            "No puedo ayudar con esta petición.",
        ),
    ],
)
async def test_its_errors_in_every_language(
    lang: i18n.Lang, failed: str, refused: str, refusal: str
) -> None:
    provider = FakeProvider("chatgpt", chunk_delay=0, fail={"answer"}, refuse={"synthesis"})
    with i18n.use(lang):
        with pytest.raises(ProviderError) as failure:
            await reply(provider, QUESTION)
        with pytest.raises(RefusalError) as refusing:
            await reply(provider, QUESTION, "synthesis")
    assert failure.value.message == failed
    assert (refusing.value.message, refusing.value.refusal) == (refused, refusal)


async def test_its_status_and_models_are_made_in_the_language_of_whoever_reads_them() -> None:
    provider = FakeProvider("claude", chunk_delay=0)
    with i18n.use("es"):  # who asks first does not choose the language of the others
        status = await provider.status()
        models = await provider.list_models()
    shown = {}
    for lang in i18n.LANGS:
        with i18n.use(lang):
            shown[lang] = (str(status.detail), [model.to_wire() for model in models])
    assert shown["en"][0] == "Demo mode"
    assert shown["es"][0] == "Modo demo"
    assert shown["ca"][0] == "Mode demostració"
    assert [(m["label"], m["description"]) for m in shown["en"][1]] == [
        ("Claude (demo)", "Canned answers, with no real model and no cost."),
        ("Claude mini (demo)", "Fast demo variant, the one used for summaries."),
    ]
    assert [(m["label"], m["description"]) for m in shown["es"][1]] == [
        ("Claude (demo)", "Respuestas predefinidas, sin ningún modelo real ni coste."),
        ("Claude mini (demo)", "Variante rápida de demostración, la de los resúmenes."),
    ]
    assert shown["ca"][1][0]["label"] == "Claude (demostració)"
