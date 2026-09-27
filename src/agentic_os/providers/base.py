"""Provider contract: one implementation per (agent, mode).

A provider turns a :class:`GenerationRequest` into a stream of events:
zero or more :class:`TextDelta` followed by exactly one :class:`GenerationResult`.
Failures raise :class:`ProviderError`. Cancellation (``asyncio.CancelledError``)
must release every resource (kill subprocesses, close HTTP streams).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
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
class ProviderStatus:
    agent: AgentName
    mode: ProviderMode
    available: bool
    model: str
    detail: str
    """Human-readable state, e.g. 'Sessió de subscripció activa' or the error cause."""


@runtime_checkable
class Provider(Protocol):
    @property
    def agent(self) -> AgentName: ...

    @property
    def mode(self) -> ProviderMode: ...

    def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]: ...

    async def status(self) -> ProviderStatus: ...

    async def aclose(self) -> None: ...
