"""Provider contract: one implementation per (agent, mode).

A provider turns a :class:`GenerationRequest` into a stream of events:
zero or more :class:`TextDelta` followed by exactly one :class:`GenerationResult`.
Failures raise :class:`ProviderError`. Cancellation (``asyncio.CancelledError``)
must release every resource (kill subprocesses, close HTTP streams).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage


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
    max_output_tokens: int = 8000


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


ProviderEvent = TextDelta | GenerationResult

ProviderErrorKind = Literal["auth", "rate_limit", "timeout", "unavailable", "invalid", "internal"]


class ProviderError(Exception):
    def __init__(self, message: str, *, kind: ProviderErrorKind, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.kind: ProviderErrorKind = kind
        self.retryable = retryable


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

    async def list_models(self) -> Sequence[ModelInfo]:
        """Models available to this provider, queried live when the vendor allows it
        (cached by the provider). Never raises: falls back to a static list."""
        ...

    async def aclose(self) -> None: ...
