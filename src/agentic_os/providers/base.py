"""Provider contract: one implementation per (agent, mode).

A provider turns a :class:`GenerationRequest` into a stream of events:
zero or more :class:`TextDelta` followed by exactly one :class:`GenerationResult`.
Failures raise :class:`ProviderError` (a refusal raises :class:`RefusalError`).
Cancellation (``asyncio.CancelledError``) must release every resource (kill
subprocesses, close HTTP streams).

Integrity of a reply (docs/adr/0005-integritat-de-les-respostes.md): a result says
whether the reply is complete or was cut off (``truncated``, ``finish_reason``); a
refusal is never a result, even when some text streamed before it; a stream that ends
without the vendor's final event is an error, never a complete answer.

Billing: ``usage`` is always what the attempt that produced the result (or the error)
billed, at the rates of its ``model``. Earlier attempts of the same call that another
model declined (a server-side fallback) are ``declined``, each with its own model, and
the engine prices and records every one of them apart: tokens of different models are
never summed (docs/adr/0008-recompte-de-tokens.md).

Attachments (docs/adr/0009-adjunts.md): a request carries the files the owner attached to
the question (``GenerationRequest.attachments``), each with the ``mode`` this call must
deliver it in. Providers send them before the prompt text, in order, as the vendor's own
blocks (images, PDF documents, labelled text); their content is data, never instructions.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage
from agentic_os.pdf_facts import PdfCheck, PdfNotes, PdfPage, pdf_notes

DEFAULT_MAX_OUTPUT_TOKENS = 16_000
"""Billed output budget of answers, revisions and syntheses (reasoning included)."""

Reasoning = Literal["off", "default"]
"""Reasoning (thinking) policy of a call: ``"default"`` is the provider's effort for the
call's purpose; ``"off"`` is as little as the model accepts (summaries)."""

FinishReason = str
"""Why a reply stopped before its end: ``"max_tokens"`` (output budget),
``"content_filter"``, ``"incomplete"`` (the vendor gave no reason), ``"interrupted"``, or
a provider-specific string."""

REFUSAL_TEXT_MAX_CHARS = 300

AttachmentKind = Literal["image", "pdf", "text"]
"""An image (PNG, JPEG, GIF or WebP), a PDF or a UTF-8 text file."""

AttachmentMode = Literal["full", "text"]
"""How a call delivers an attachment: ``"full"`` sends the file itself (the image, the PDF
document, a text file's content); ``"text"`` sends a PDF's extracted text instead of the
document (the revisions' choice, ``TurnRequest.pdf_in_revisions``)."""


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file the owner attached to a question, as a call must deliver it. Files are
    stored content-addressed and never change: ``sha256`` identifies the content."""

    kind: AttachmentKind
    name: str
    """Display name, sanitized (no path, no control characters)."""
    mime: str
    """``image/png|jpeg|gif|webp``, ``application/pdf`` or ``text/plain``."""
    sha256: str
    size: int
    path: Path
    """Absolute path of the stored file (read-only)."""
    pages: int | None = None
    width: int | None = None
    height: int | None = None
    text: str | None = None
    """Text files: the content; PDFs: the extracted text (None if none)."""
    mode: AttachmentMode = "full"
    """How THIS call must deliver it (the engine sets it for each phase)."""
    created_at: datetime | None = None
    """When it was uploaded, if the store says (the question's ``meta.attachments``)."""
    has_thumbnail: bool = False
    """Whether the browser uploaded a thumbnail of it, if the store says."""
    pdf_pages: tuple[PdfPage, ...] | None = None
    """PDFs: what the server's reader found on each page (None when not analysed)."""
    pdf_check: PdfCheck | None = None
    """PDFs that ChatGPT reads as text (Codex): Claude's check of that text, when this
    call has one (the engine sets it)."""

    @property
    def pdf_notes(self) -> PdfNotes | None:
        """The warnings of a PDF's pages (``pdf_facts.pdf_notes``), None when it was not
        analysed or is not a PDF."""
        if self.kind != "pdf" or self.pdf_pages is None:
            return None
        return pdf_notes(self.pdf_pages)

    def read(self) -> bytes:
        """The stored file's bytes, checked against ``sha256`` (blocking: run it in a
        thread). A missing, unreadable or changed file raises :class:`ProviderError`, so
        a call never sends another file than the one the owner attached."""
        try:
            data = self.path.read_bytes()
        except OSError:
            raise ProviderError(
                f"No s'ha pogut llegir l'adjunt «{self.name}».", kind="internal"
            ) from None
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ProviderError(
                f"L'adjunt «{self.name}» ha canviat des que es va pujar.", kind="internal"
            )
        return data


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """A previous message of the conversation, as context for the model."""

    role: Literal["user", "assistant"]
    content: str
    agent: AgentName | None = None
    """Author of an assistant message (the other agent's messages must be labelled)."""


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    system: str
    """Complete system prompt. Providers must use it instead of their default prompt."""
    prompt: str
    """The new user message for this call."""
    history: Sequence[ChatTurn] = ()
    context_summary: str | None = None
    """Summary of older conversation turns (context compaction), if any."""
    purpose: Purpose = "answer"
    fast: bool = False
    """Use the provider's fast/cheap model (e.g. for summaries)."""
    model: str | None = None
    """Explicit model override; None means the provider's configured default."""
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    """Maximum BILLED output tokens of the call, reasoning (thinking) included. Adapters
    send it to the vendor as it is and never raise it; where the vendor has no such
    limit, the adapter enforces it as well as it can (Codex: an approximate local stop).
    It is not a length for the visible text, and counting text is never billing."""
    reasoning: Reasoning = "default"
    """Reasoning policy, separate from the budget: ``"off"`` for summaries."""
    attachments: tuple[Attachment, ...] = ()
    """Files attached to the question, in order, each with the mode this call delivers
    it in; sent before the prompt text (the history only mentions earlier ones)."""


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class DeclinedAttempt:
    """An attempt of a call that its model declined before another model took over (a
    server-side fallback of the Claude API). It is billed on its own, at the rates of the
    model that ran it; ``usage`` has no cost (the engine prices it)."""

    model: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    """Full generated text (the concatenation of all deltas)."""
    usage: Usage
    """What the attempt that produced ``text`` billed (never the declined ones)."""
    model: str
    """The model that produced ``text`` (after a fallback, the one that served)."""
    latency_ms: int
    """Wall time from call start to completion."""
    ttft_ms: int | None = None
    """Time to first text delta."""
    truncated: bool = False
    """The reply was cut off before its end (output budget, content filter, a stream the
    provider had to stop...): ``text`` is a usable partial answer, never a complete one.
    The engine stores it as such and never caches the turn."""
    finish_reason: FinishReason | None = None
    """Why a truncated reply stopped (None for a complete one)."""
    declined: tuple[DeclinedAttempt, ...] = ()
    """The billed attempts that other models declined before ``model`` served, in order
    (a server-side fallback); empty without one."""


ProviderEvent = TextDelta | GenerationResult

ProviderErrorKind = Literal["auth", "rate_limit", "timeout", "unavailable", "invalid", "internal"]


class ProviderError(Exception):
    """A call that failed. ``usage`` and ``model`` say what its last attempt billed when
    the vendor reported it (a refusal, an output budget spent before any text...), so the
    engine records its cost; None when nothing is known to be billed. ``declined`` are the
    billed attempts other models declined before it (see :class:`DeclinedAttempt`)."""

    def __init__(
        self,
        message: str,
        *,
        kind: ProviderErrorKind,
        retryable: bool = False,
        usage: Usage | None = None,
        model: str | None = None,
        declined: Sequence[DeclinedAttempt] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind: ProviderErrorKind = kind
        self.retryable = retryable
        self.usage = usage
        self.model = model
        self.declined: tuple[DeclinedAttempt, ...] = tuple(declined)


def clean_refusal(text: str) -> str:
    """A model's refusal explanation fit for messages and logs: control and invisible
    format characters removed, whitespace collapsed, at most REFUSAL_TEXT_MAX_CHARS."""
    visible = "".join(
        char if unicodedata.category(char) not in ("Cc", "Cf", "Co", "Cs") or char.isspace() else ""
        for char in text
    )
    cleaned = " ".join(visible.split())
    if len(cleaned) > REFUSAL_TEXT_MAX_CHARS:
        cleaned = cleaned[: REFUSAL_TEXT_MAX_CHARS - 1].rstrip() + "…"
    return cleaned


class RefusalError(ProviderError):
    """The model declined the request (Anthropic ``stop_reason: "refusal"``, an OpenAI
    ``refusal`` part). Not retryable: asking again gets the same answer, and any text
    streamed before the refusal is never an answer.

    The vendor may still bill it: ``usage`` holds the billed tokens of the attempt that
    refused (all zero when nothing is billed) and ``model`` the model that refused, so
    the call's cost is recorded; after a server-side fallback, ``declined`` holds the
    billed attempts of the models that declined before it, each at its own model.
    ``category`` is the vendor's refusal category when it gives one and ``refusal`` the
    model's own explanation (cleaned with :func:`clean_refusal`)."""

    usage: Usage
    model: str

    def __init__(
        self,
        message: str,
        *,
        usage: Usage,
        model: str,
        category: str | None = None,
        refusal: str = "",
        declined: Sequence[DeclinedAttempt] = (),
    ) -> None:
        super().__init__(message, kind="invalid", usage=usage, model=model, declined=declined)
        self.category = category
        self.refusal = clean_refusal(refusal)


@dataclass(frozen=True, slots=True)
class UsageLimit:
    """A subscription usage window reported by the vendor (cli mode only)."""

    window: str
    """E.g. "5h" or "7d"."""
    used_percent: float | None
    resets_at: datetime | None
    status: str = "allowed"
    """"allowed", "warning" or "rejected"."""


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    agent: AgentName
    mode: ProviderMode
    available: bool
    model: str
    detail: str
    """Human-readable state in Catalan, e.g. 'Subscripció activa' or the error cause."""
    limits: Sequence[UsageLimit] = ()
    """Latest subscription usage windows seen (empty when unknown or in api mode)."""


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """A model the owner can pick for an agent (listed live from the vendor when possible)."""

    id: str
    """Value passed to the provider (API id, CLI alias such as "opus", Codex slug...)."""
    label: str
    description: str = ""
    is_default: bool = False
    context_window: int | None = None

    def to_wire(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "is_default": self.is_default,
            "context_window": self.context_window,
        }


MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,99}$"
"""Accepted shape for model ids typed by the owner (new models work without code changes)."""


@runtime_checkable
class Provider(Protocol):
    @property
    def agent(self) -> AgentName: ...

    @property
    def mode(self) -> ProviderMode: ...

    def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]: ...

    async def prewarm(self, request: GenerationRequest) -> None:
        """Best-effort hint that a call like ``request`` will follow soon (e.g. the next
        debate round), so a cli provider can start its process in advance. Must return
        quickly and never raise; providers without a warm-up step do nothing."""
        ...

    async def status(self) -> ProviderStatus: ...

    @property
    def fast_model(self) -> str:
        """Model of the cheap internal calls (summaries) when none is requested."""
        ...

    @property
    def models_live(self) -> bool:
        """Whether the latest :meth:`list_models` came from the vendor (not a fallback)."""
        ...

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        """Models available to this provider, queried live when the vendor allows it
        (cached by the provider; ``refresh`` bypasses the cache). Never raises: falls
        back to a static list."""
        ...

    async def aclose(self) -> None: ...


def reads_pdfs(provider: Provider) -> bool:
    """Whether a provider sends a PDF itself. ChatGPT through Codex (the app-server, mode
    "cli") cannot: it gets the text the server extracted, page by page
    (``prompt_format.pdf_view``), checked by Claude when the engine can
    (docs/adr/0009-adjunts.md)."""
    return not (provider.agent == "chatgpt" and provider.mode == "cli")
