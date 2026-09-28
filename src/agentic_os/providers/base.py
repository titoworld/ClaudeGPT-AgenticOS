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
"""

from __future__ import annotations

import unicodedata
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage

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


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    """Full generated text (the concatenation of all deltas)."""
    usage: Usage
    model: str
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


ProviderEvent = TextDelta | GenerationResult

ProviderErrorKind = Literal["auth", "rate_limit", "timeout", "unavailable", "invalid", "internal"]


class ProviderError(Exception):
    """A call that failed. ``usage`` and ``model`` say what it billed when the vendor
    reported it (a refusal, an output budget spent before any text...), so the engine
    records its cost; None when nothing is known to be billed."""

    def __init__(
        self,
        message: str,
        *,
        kind: ProviderErrorKind,
        retryable: bool = False,
        usage: Usage | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind: ProviderErrorKind = kind
        self.retryable = retryable
        self.usage = usage
        self.model = model


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

    The vendor may still bill it: ``usage`` holds the billed tokens (all zero when
    nothing is billed) and ``model`` the model that declined, so the call's cost is
    recorded. ``category`` is the vendor's refusal category when it gives one and
    ``refusal`` the model's own explanation (cleaned with :func:`clean_refusal`)."""

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
    ) -> None:
        super().__init__(message, kind="invalid", usage=usage, model=model)
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
