"""Server -> client events of a turn (WebSocket protocol, see docs/PROTOCOL.md).

The engine yields these events; the web layer adds a per-turn ``seq`` number and
sends ``to_wire()`` as JSON.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from agentic_os.domain import AgentName, MessageKind, TurnMode, Usage

Section = Literal["text", "critique", "answer"]
"""text: plain answer/synthesis stream. critique/answer: parts of a debate revision."""

Wire = dict[str, object]


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    kind: str
    message: str

    def to_wire(self) -> Wire:
        return {"kind": self.kind, "message": self.message}


@dataclass(frozen=True, slots=True)
class TurnStarted:
    request_id: str
    conversation_id: int
    turn_id: int
    mode: TurnMode
    new_conversation: bool

    def to_wire(self) -> Wire:
        return {
            "type": "turn.started",
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "mode": self.mode,
            "new_conversation": self.new_conversation,
        }


@dataclass(frozen=True, slots=True)
class PhaseChanged:
    request_id: str
    phase: Literal["answer", "revision", "synthesis", "compaction"]
    round: int

    def to_wire(self) -> Wire:
        return {
            "type": "phase",
            "request_id": self.request_id,
            "phase": self.phase,
            "round": self.round,
        }


@dataclass(frozen=True, slots=True)
class StreamStarted:
    request_id: str
    stream_id: str
    agent: AgentName
    kind: MessageKind
    round: int
    model: str

    def to_wire(self) -> Wire:
        return {
            "type": "stream.started",
            "request_id": self.request_id,
            "stream_id": self.stream_id,
            "agent": self.agent,
            "kind": self.kind,
            "round": self.round,
            "model": self.model,
        }


@dataclass(frozen=True, slots=True)
class StreamDelta:
    request_id: str
    stream_id: str
    section: Section
    text: str

    def to_wire(self) -> Wire:
        return {
            "type": "stream.delta",
            "request_id": self.request_id,
            "stream_id": self.stream_id,
            "section": self.section,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class StreamCompleted:
    request_id: str
    stream_id: str
    message_id: int
    usage: Usage
    latency_ms: int
    ttft_ms: int | None
    agreement: int | None = None
    """Debate revisions only: agreement with the other agent (0-100)."""
    unchanged: bool = False
    """Debate revisions only: the agent kept its previous answer."""

    def to_wire(self) -> Wire:
        return {
            "type": "stream.completed",
            "request_id": self.request_id,
            "stream_id": self.stream_id,
            "message_id": self.message_id,
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "agreement": self.agreement,
            "unchanged": self.unchanged,
        }


@dataclass(frozen=True, slots=True)
class StreamFailed:
    request_id: str
    stream_id: str
    error: ErrorInfo

    def to_wire(self) -> Wire:
        return {
            "type": "stream.failed",
            "request_id": self.request_id,
            "stream_id": self.stream_id,
            "error": self.error.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class Savings:
    cache: int = 0
    compaction: int = 0
    early_stop: int = 0
    unchanged: int = 0
    cost_usd: float | None = None
    """Estimated value of the saved tokens at this turn's average price per token."""

    @property
    def total(self) -> int:
        return self.cache + self.compaction + self.early_stop + self.unchanged

    def to_wire(self) -> Wire:
        return {
            "cache": self.cache,
            "compaction": self.compaction,
            "early_stop": self.early_stop,
            "unchanged": self.unchanged,
            "total": self.total,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True, slots=True)
class Consensus:
    reached: bool
    round: int
    """Round in which the debate stopped."""
    scores: dict[AgentName, int] = field(default_factory=dict)

    def to_wire(self) -> Wire:
        return {"reached": self.reached, "round": self.round, "scores": dict(self.scores)}


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    request_id: str
    conversation_id: int
    turn_id: int
    final_message_ids: Sequence[int]
    usage: Usage
    savings: Savings
    consensus: Consensus | None = None
    cached: bool = False

    def to_wire(self) -> Wire:
        return {
            "type": "turn.completed",
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "final_message_ids": list(self.final_message_ids),
            "usage": self.usage.to_dict(),
            "savings": self.savings.to_wire(),
            "consensus": self.consensus.to_wire() if self.consensus else None,
            "cached": self.cached,
        }


@dataclass(frozen=True, slots=True)
class TurnFailed:
    request_id: str
    error: ErrorInfo

    def to_wire(self) -> Wire:
        return {"type": "turn.failed", "request_id": self.request_id, "error": self.error.to_wire()}


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    """Emitted by the web layer when the owner cancels a running turn."""

    request_id: str

    def to_wire(self) -> Wire:
        return {"type": "turn.cancelled", "request_id": self.request_id}


ServerEvent = (
    TurnStarted
    | PhaseChanged
    | StreamStarted
    | StreamDelta
    | StreamCompleted
    | StreamFailed
    | TurnCompleted
    | TurnFailed
    | TurnCancelled
)
