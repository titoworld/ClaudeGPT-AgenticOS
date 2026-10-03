"""Deterministic fake provider: tests and the no-cost demo (``AOS_*_MODE=fake``).

Replies are canned Markdown built from the question, in the turn's language
(:mod:`agentic_os.locales.demo`), streamed in small chunks. Revisions follow the debate
format (critique, answer or UNCHANGED, agreement) with agreement values taken from a
configurable sequence, which restarts for every new question.

Like a real model it honours ``max_output_tokens`` (about 4 characters per token): a
longer reply is cut and reported as truncated (``finish_reason`` "max_tokens"). Tests can
also ask for truncated replies or refusals per call purpose.

Attachments are recorded (``attachments``, one tuple per call) and never read; answers
and syntheses name them, and their estimated tokens count in the input usage.

Claude's check of a PDF for ChatGPT (purpose "check", docs/adr/0009-attachments.md) replies
with the test's ``check_replies`` in turn, or says every page is right (``{"end": true}``).
A fake can report another ``mode``, so that a test has a ChatGPT that cannot open PDFs
(mode "cli", like Codex) without any process.

A refine turn (docs/adr/0010-refine-mode.md; its prompts carry the owner's
``<brief>``) runs its whole loop: the answers are the canned ones, the merge writes a
first version, every edit applies the changes the reviews proposed (one more line each
time, so no version repeats the previous one) and a shortening keeps the whole lines that
fit the word budget. The reviews of each brief propose one change in the first two rounds
(a defect, then a clarity change scored above the default convergence threshold) and then
nothing (UNCHANGED), or are the test's ``refine_reviews`` in turn. Like a model that
follows those prompts' note, it reads the «&lt;» they write before a tag as «<», and
writes «<» in the document.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import Final

from agentic_os.attachments import attachment_tokens
from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage, other_agent
from agentic_os.i18n import lazy, number, t
from agentic_os.providers.base import (
    Attachment,
    AttachmentKind,
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    RefusalError,
    TextDelta,
)
from agentic_os.providers.prompt_format import AGENT_LABELS, RESERVED_TAGS

DEFAULT_AGREEMENTS: tuple[int, ...] = (72, 90)
UNCHANGED_FROM = 85
"""Agreement from which the fake keeps its previous answer (UNCHANGED)."""

_TAGGED = {
    "question": re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL),
    "user_message": re.compile(r"<user_message>\n(.*?)\n</user_message>", re.DOTALL),
    "brief": re.compile(r"<brief>\n(.*?)\n</brief>", re.DOTALL),
    "previous": re.compile(r"<your_previous_answer>\n(.*?)\n</your_previous_answer>", re.DOTALL),
    "current_version": re.compile(r"<current_version>\n(.*?)\n</current_version>", re.DOTALL),
    "draft": re.compile(r"<draft>\n(.*?)\n</draft>", re.DOTALL),
    "draft_changelog": re.compile(r"<draft_changelog>\n(.*?)\n</draft_changelog>", re.DOTALL),
}
_ATTACHMENTS_SECTION = re.compile(r"\A<attachments>\n.*?\n</attachments>\n+", re.DOTALL)
"""The list of attachments before a solo or duel question (the prompt's own section)."""
_ATTACHMENTS_REFERENCE = re.compile(r"\A\[(?:Attachments|Adjunts): [^\n]*\]\n")
"""The line that stands for a question's attachments in the history (the engine's
``memory.attachments_reference``), in English, or in Catalan as it was written before
the texts for the models were English."""
_WORD_RE = re.compile(r"\S+\s*|\s+")
_CHARS_PER_TOKEN = 4
CHECK_REPLY = '{"end": true}'
"""The default reply of a check call: every page's extracted text is right."""
_ESCAPED_TAG = re.compile(r"&lt;(?=\s*/?\s*([A-Za-z_]\w*))")
"""Where a refine prompt quotes a tag of the prompts (``neutralize_tags``)."""
_PROPOSED = re.compile(r"^- \[([a-z]+)\] (.+)$", re.MULTILINE)
"""A change in the reviews of an edit prompt, or in the changelog of a shortening's."""
_BUDGET = re.compile(r"must have at most (\d+) words")
_PROPOSALS: Final[Mapping[AgentName, tuple[tuple[str, str], ...]]] = {
    "claude": (("defect", "demo.refine.claude.defect"), ("clarity", "demo.refine.claude.clarity")),
    "chatgpt": (
        ("defect", "demo.refine.chatgpt.defect"),
        ("clarity", "demo.refine.chatgpt.clarity"),
    ),
}
"""(kind, text key) of each change in :data:`REFINE_PROPOSALS`."""


class _Proposals(Mapping[AgentName, tuple[str, ...]]):
    """:data:`REFINE_PROPOSALS`: an agent's lines, made in the language in force when
    they are read."""

    def __getitem__(self, agent: AgentName) -> tuple[str, ...]:
        return tuple(f"- [{kind}] {t(key)}" for kind, key in _PROPOSALS[agent])

    def __iter__(self) -> Iterator[AgentName]:
        return iter(_PROPOSALS)

    def __len__(self) -> int:
        return len(_PROPOSALS)


REFINE_PROPOSALS: Final[Mapping[AgentName, tuple[str, ...]]] = _Proposals()
"""The change each agent's review proposes in the first rounds of a refine turn: a
``- [kind] …`` line of the reviews' format, whose text is in the language in force."""
REFINE_SCORES: tuple[int, ...] = (78, 92, 95)
"""The scores of those reviews and then of every UNCHANGED one."""


def _as_written(text: str) -> str:
    """A text a refine prompt quotes, with «<» where the prompt wrote «&lt;» before a tag
    of the prompts, as the prompt asks the model to write it in the document."""
    return _ESCAPED_TAG.sub(
        lambda match: "<" if match.group(1).casefold() in RESERVED_TAGS else match.group(0), text
    )


def _estimate(text: str) -> int:
    return max(1, -(-len(text) // _CHARS_PER_TOKEN)) if text else 0


def _attachment_tokens(attachment: Attachment) -> int:
    """Input tokens of an attachment as the call got it: its card's estimate, or the
    text of a PDF sent as text."""
    if attachment.kind == "pdf" and attachment.mode == "text":
        return _estimate(attachment.text or "")
    return attachment_tokens(attachment)


_CLAUDE_INTROS: Final = ("demo.claude.intro_1", "demo.claude.intro_2", "demo.claude.intro_3")
_CHATGPT_TIPS: Final = ("demo.chatgpt.tip_1", "demo.chatgpt.tip_2", "demo.chatgpt.tip_3")
_KINDS: Final[Mapping[AttachmentKind, str]] = {
    "image": "demo.kind.image",
    "pdf": "demo.kind.pdf",
    "text": "demo.kind.text",
}


def _topic(question: str) -> str:
    line = next((ln.strip() for ln in question.splitlines() if ln.strip()), "")
    if not line:
        return t("demo.topic_default")
    return line if len(line) <= 80 else line[:79].rstrip() + "…"


def _label(attachment: Attachment) -> str:
    """How an answer names an attachment, as the call got it, in the language in force:
    «report.pdf (PDF, 12 pages)», «photo.jpg (image)»."""
    details = t(_KINDS[attachment.kind])
    if attachment.kind == "pdf" and attachment.pages:
        pages = attachment.pages
        count = t("demo.pages.one") if pages == 1 else t("demo.pages.other", count=number(pages))
        details += f", {count}"
    if attachment.kind == "pdf" and attachment.mode == "text":
        details += f"; {t('demo.text_only')}"
    return f"{attachment.name} ({details})"


def _variant(*parts: str) -> int:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).digest()[0]


def _chunks(text: str) -> list[str]:
    """Split into small pieces of one or two words (whitespace preserved)."""
    words = _WORD_RE.findall(text)
    return ["".join(words[i : i + 2]) for i in range(0, len(words), 2)]


class FakeProvider:
    """Provider that never leaves the process. ``requests`` and ``prewarmed`` record
    every call, which tests use to check what the engine sent, and ``attachments`` the
    attachments of each call (with the mode it got them in). Results report the
    requested model (``fake-<agent>`` or ``fake-<agent>-mini`` for fast calls by default).

    ``fail`` makes the calls of those purposes fail; ``truncate`` cuts their reply in
    half (a truncated result); ``refuse`` makes them raise :class:`RefusalError` with
    the billed usage, after streaming ``refuse_after`` chunks.

    ``mode`` is the mode it reports (``"fake"`` by default). ``check_replies`` are the
    replies of its successive "check" calls (the last one repeats; by default
    :data:`CHECK_REPLY`). ``refine_reviews`` are the replies of its successive reviews of
    a refine turn, again from the first for a new brief (the last one repeats; by default
    :data:`REFINE_PROPOSALS`, then UNCHANGED)."""

    def __init__(
        self,
        agent: AgentName,
        *,
        chunk_delay: float = 0.015,
        agreements: Sequence[int] | None = None,
        fail: set[Purpose] | None = None,
        truncate: set[Purpose] | None = None,
        refuse: set[Purpose] | None = None,
        refuse_after: int = 0,
        mode: ProviderMode = "fake",
        check_replies: Sequence[str] | None = None,
        refine_reviews: Sequence[str] | None = None,
    ) -> None:
        self._agent: AgentName = agent
        self._chunk_delay = chunk_delay
        self._agreements = tuple(agreements) if agreements else DEFAULT_AGREEMENTS
        self._fail = frozenset(fail or ())
        self._truncate = frozenset(truncate or ())
        self._refuse = frozenset(refuse or ())
        self._refuse_after = refuse_after
        self._mode: ProviderMode = mode
        self._check_replies = tuple(check_replies) if check_replies else (CHECK_REPLY,)
        self._checks = 0
        self._revisions = 0
        self._revision_question: str | None = None
        self._refine_reviews = tuple(refine_reviews) if refine_reviews else None
        self._reviews = 0
        self._review_brief: str | None = None
        self.requests: list[GenerationRequest] = []
        self.prewarmed: list[GenerationRequest] = []
        self.attachments: list[tuple[Attachment, ...]] = []
        """The attachments of every call, in call order."""
        self.closed = False

    @property
    def agent(self) -> AgentName:
        return self._agent

    @property
    def mode(self) -> ProviderMode:
        return self._mode

    @property
    def model(self) -> str:
        return f"fake-{self._agent}"

    @property
    def fast_model(self) -> str:
        return f"fake-{self._agent}-mini"

    @property
    def models_live(self) -> bool:
        return True

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        self.attachments.append(tuple(request.attachments))
        started = time.monotonic()
        if request.purpose in self._fail:
            await asyncio.sleep(self._chunk_delay)
            raise ProviderError(
                lazy("providers.fake.failed", agent=AGENT_LABELS[self._agent]), kind="unavailable"
            )
        text = self._compose(request)
        truncated = False
        if request.purpose in self._truncate:
            text, truncated = text[: max(1, len(text) // 2)], True
        budget = max(1, request.max_output_tokens) * _CHARS_PER_TOKEN
        if len(text) > budget:
            text, truncated = text[:budget], True
        chunks = _chunks(text)
        refusing = request.purpose in self._refuse
        if refusing:
            chunks = chunks[: self._refuse_after]
        ttft_ms: int | None = None
        for chunk in chunks:
            await asyncio.sleep(self._chunk_delay)
            if ttft_ms is None:
                ttft_ms = int((time.monotonic() - started) * 1000)
            yield TextDelta(chunk)
        prompt_tokens = _estimate(request.system) + _estimate(request.prompt)
        prompt_tokens += sum(_estimate(turn.content) for turn in request.history)
        prompt_tokens += _estimate(request.context_summary or "")
        prompt_tokens += sum(_attachment_tokens(a) for a in request.attachments)
        model = request.model or (self.fast_model if request.fast else self.model)
        if refusing:
            await asyncio.sleep(self._chunk_delay)
            streamed = "".join(chunks)
            raise RefusalError(
                lazy("providers.fake.refused", agent=AGENT_LABELS[self._agent]),
                usage=Usage(input_tokens=prompt_tokens, output_tokens=_estimate(streamed)),
                model=model,
                refusal=t("demo.refusal"),
            )
        yield GenerationResult(
            text=text,
            usage=Usage(input_tokens=prompt_tokens, output_tokens=_estimate(text)),
            model=model,
            latency_ms=int((time.monotonic() - started) * 1000),
            ttft_ms=ttft_ms,
            truncated=truncated,
            finish_reason="max_tokens" if truncated else None,
        )

    async def prewarm(self, request: GenerationRequest) -> None:
        self.prewarmed.append(request)

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            agent=self._agent,
            mode=self._mode,
            available=True,
            model=self.model,
            detail=lazy("providers.fake.status"),
        )

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        agent = AGENT_LABELS[self._agent]
        return (
            ModelInfo(
                id=self.model,
                label=lazy("providers.fake.label", agent=agent),
                description=lazy("providers.fake.description"),
                is_default=True,
            ),
            ModelInfo(
                id=self.fast_model,
                label=lazy("providers.fake.label_mini", agent=agent),
                description=lazy("providers.fake.description_mini"),
            ),
        )

    async def aclose(self) -> None:
        self.closed = True

    # -- canned replies ------------------------------------------------------------

    def _compose(self, request: GenerationRequest) -> str:
        if request.purpose == "summary":
            return self._summary(request)
        if request.purpose == "check":
            reply = self._check_replies[min(self._checks, len(self._check_replies) - 1)]
            self._checks += 1
            return reply
        brief = _TAGGED["brief"].search(request.prompt)
        if (
            brief is not None
            and request.purpose in ("revision", "synthesis")
            and not any(
                _TAGGED[name].search(request.prompt) for name in ("question", "user_message")
            )
        ):
            # A refine prompt: a debate's has its question (which may quote a brief).
            return self._refine(request, _as_written(brief.group(1)))
        question = self._question(request.prompt)
        if request.purpose == "revision":
            return self._revision(request.prompt, question)
        if request.purpose == "synthesis":
            return self._synthesis(question) + self._mention(request.attachments)
        return self._answer(question) + self._mention(request.attachments)

    @staticmethod
    def _mention(attachments: Sequence[Attachment]) -> str:
        """The line that names the attachments of a call ("" without any)."""
        if not attachments:
            return ""
        labels = ", ".join(_label(attachment) for attachment in attachments)
        return f"\n\n{t('demo.attachments_received', labels=labels)}"

    @staticmethod
    def _question(prompt: str) -> str:
        for name in ("question", "user_message", "brief"):
            match = _TAGGED[name].search(prompt)
            if match:
                return match.group(1)
        return _ATTACHMENTS_SECTION.sub("", prompt, count=1)

    def _answer(self, question: str) -> str:
        topic = _topic(question)
        mode = self._agent.upper()
        if self._agent == "claude":
            intro = t(_CLAUDE_INTROS[_variant(question, "claude") % 3])
            return t("demo.claude.answer", topic=topic, intro=intro, mode=mode)
        tip = t(_CHATGPT_TIPS[_variant(question, "chatgpt") % 3])
        return t("demo.chatgpt.answer", topic=topic, tip=tip, mode=mode)

    def _revision(self, prompt: str, question: str) -> str:
        if question != self._revision_question:
            # A new debate: the agreement sequence starts again.
            self._revision_question = question
            self._revisions = 0
        agreement = self._agreements[min(self._revisions, len(self._agreements) - 1)]
        self._revisions += 1
        other = AGENT_LABELS[other_agent(self._agent)]
        if agreement >= UNCHANGED_FROM:
            critique = t("demo.revision.agreed", other=other)
            answer = "UNCHANGED"
        else:
            critique = t("demo.revision.critique", other=other)
            previous_match = _TAGGED["previous"].search(prompt)
            previous = previous_match.group(1) if previous_match else self._answer(question)
            answer = f"{previous}\n\n{t('demo.revision.addition', other=other)}"
        return (
            f"<critique>\n{critique}\n</critique>\n"
            f"<answer>\n{answer}\n</answer>\n"
            f"<agreement>{agreement}</agreement>"
        )

    @staticmethod
    def _synthesis(question: str) -> str:
        return t("demo.synthesis", topic=_topic(question))

    # -- refine turns ----------------------------------------------------------------

    def _refine(self, request: GenerationRequest, brief: str) -> str:
        """A review, an edit, a shortening or the merge of a refine turn, by its prompt."""
        prompt = request.prompt
        if request.purpose == "revision":
            return self._refine_review(brief)
        draft = _TAGGED["draft"].search(prompt)
        if draft is not None:
            return self._shorten(prompt, _as_written(draft.group(1)))
        current = _TAGGED["current_version"].search(prompt)
        if current is not None:
            return self._edit(prompt, _as_written(current.group(1)))
        return self._merge(brief, request.attachments)

    def _refine_review(self, brief: str) -> str:
        if brief != self._review_brief:
            # A new refine turn: the reviews start again.
            self._review_brief = brief
            self._reviews = 0
        count = self._reviews
        self._reviews += 1
        if self._refine_reviews is not None:
            return self._refine_reviews[min(count, len(self._refine_reviews) - 1)]
        proposals = REFINE_PROPOSALS[self._agent]
        changes = proposals[count] if count < len(proposals) else "UNCHANGED"
        score = REFINE_SCORES[min(count, len(REFINE_SCORES) - 1)]
        return f"<changes>\n{changes}\n</changes>\n<score>{score}</score>"

    def _merge(self, brief: str, attachments: Sequence[Attachment]) -> str:
        document = t("demo.refine.document", topic=_topic(brief))
        changelog = (
            f"- [merge] {t('demo.refine.merge_steps')}\n- [merge] {t('demo.refine.merge_table')}"
        )
        document += self._mention(attachments)
        return f"<version>\n{document}\n</version>\n<changelog>\n{changelog}\n</changelog>"

    @staticmethod
    def _edit(prompt: str, current: str) -> str:
        """The current version with one more line, from the first change the reviews
        proposed; its changelog applies every one of them (at most five)."""
        reviews = _as_written(prompt.split("</current_version>", 1)[-1])
        proposed = _PROPOSED.findall(reviews)[:5] or [("clarity", t("demo.refine.general_review"))]
        change = t("demo.refine.change")
        count = current.count(f"**{change} ") + 1
        version = f"{current}\n\n**{change} {count}:** {proposed[0][1]}"
        changelog = "\n".join(f"- [{kind}] {text}" for kind, text in proposed)
        return f"<version>\n{version}\n</version>\n<changelog>\n{changelog}\n</changelog>"

    @staticmethod
    def _shorten(prompt: str, draft: str) -> str:
        """The draft's first whole lines that fit the budget, with its changelog."""
        budgets = _BUDGET.findall(prompt)
        budget = int(budgets[-1]) if budgets else 300
        kept: list[str] = []
        count = 0
        for line in draft.splitlines():
            count += len(line.split())
            if count > budget:
                break
            kept.append(line)
        version = "\n".join(kept).strip() or " ".join(draft.split()[:budget])
        listed = _TAGGED["draft_changelog"].search(prompt)
        changes = _PROPOSED.findall(_as_written(listed.group(1)) if listed else "")[:5]
        changelog = "\n".join(f"- [{kind}] {text}" for kind, text in changes) or (
            f"- [simplification] {t('demo.refine.shortened')}"
        )
        return f"<version>\n{version}\n</version>\n<changelog>\n{changelog}\n</changelog>"

    @staticmethod
    def _summary(request: GenerationRequest) -> str:
        questions = [
            _topic(_ATTACHMENTS_REFERENCE.sub("", turn.content))
            for turn in request.history
            if turn.role == "user"
        ]
        listed = "; ".join(t("demo.quoted", text=q) for q in questions)
        previous = f" {request.context_summary}" if request.context_summary else ""
        return t("demo.summary", previous=previous, listed=listed or t("demo.summary_topics"))
