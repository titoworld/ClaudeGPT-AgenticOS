"""Server -> client events of a turn (WebSocket protocol, see docs/PROTOCOL.md).

The engine yields these events; the web layer adds a per-turn ``seq`` number and
sends ``to_wire()`` as JSON.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from agentic_os.domain import AGENTS, AgentName, MessageKind, RefineStopReason, TurnMode, Usage

Section = Literal["text", "critique", "answer"]
"""text: plain answer/synthesis stream. critique/answer: parts of a debate revision."""

PdfCheckState = Literal["checking", "checked", "unchecked"]
"""Where Claude's check of a PDF for ChatGPT is (docs/adr/0009-adjunts.md): running, done
with at least one page checked, or done with none (it failed, took too long, the PDF was
not analysed or there is no Claude)."""

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
    phase: Literal["answer", "revision", "synthesis", "compaction", "review", "edit"]
    """``review`` and ``edit``: a refine round, where both agents review the current
    version and then the editor writes the next one (docs/adr/0010-mode-perfecciona.md)."""
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
class PdfReading:
    """How ChatGPT, when it cannot open PDFs (Codex), read one PDF of the question: as
    the text the server extracted, with the pages Claude's check read for it
    (docs/adr/0009-adjunts.md). ChatGPT's messages keep it (``meta.pdf_reading``)."""

    attachment_id: int
    name: str
    checked: bool
    """Claude checked at least one page of it."""
    claude_pages: tuple[int, ...] = ()
    """Pages ChatGPT read, all or in part, as Claude read them: their text, or what their
    figures show as Claude described it (``PdfCheck.claude_pages``)."""
    hidden_pages: tuple[int, ...] = ()
    """Pages with text that is not visible, which ChatGPT did not get."""
    unchecked_pages: tuple[int, ...] = ()
    """Pages nobody checked: ChatGPT read the extracted text as it is."""
    reason: str | None = None
    """Why pages remain unchecked (Catalan); None when every page was checked."""

    def to_wire(self) -> Wire:
        return {
            "attachment_id": self.attachment_id,
            "name": self.name,
            "checked": self.checked,
            "claude_pages": list(self.claude_pages),
            "hidden_pages": list(self.hidden_pages),
            "unchecked_pages": list(self.unchecked_pages),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PdfCheckChanged:
    """Claude's check of a PDF of the question for ChatGPT started or ended (it runs
    while Claude answers, before ChatGPT's first call of the turn)."""

    request_id: str
    attachment_id: int
    name: str
    state: PdfCheckState
    claude_pages: tuple[int, ...] = ()
    hidden_pages: tuple[int, ...] = ()
    unchecked_pages: tuple[int, ...] = ()
    reused: bool = False
    """An earlier turn's check, stored: no call was made for it in this turn."""
    usage: Usage | None = None
    """What this turn's calls for the PDF billed (None while checking, or reused)."""
    reason: str | None = None
    """Why pages remain unchecked (Catalan), as :attr:`PdfReading.reason`."""

    def to_wire(self) -> Wire:
        return {
            "type": "pdf.check",
            "request_id": self.request_id,
            "attachment_id": self.attachment_id,
            "name": self.name,
            "state": self.state,
            "claude_pages": list(self.claude_pages),
            "hidden_pages": list(self.hidden_pages),
            "unchecked_pages": list(self.unchecked_pages),
            "reused": self.reused,
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "reason": self.reason,
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
    cost_basis: str | None = None
    """"api" (real cost), "equivalent" (subscription value at API prices) or None."""
    truncated: bool = False
    """The reply was cut off: a usable partial answer. On the wire only when true."""
    finish_reason: str | None = None
    """Why a truncated reply stopped, as the message's ``meta.finish_reason`` (e.g.
    "max_tokens"). On the wire only when set."""
    unchanged_note: str | None = None
    """Debate revisions only: the short note written after UNCHANGED, as the message's
    ``meta.unchanged_note``. On the wire only when set."""
    pdf_reading: tuple[PdfReading, ...] = ()
    """ChatGPT's messages when it cannot open PDFs: how it read each PDF of the question,
    as the message's ``meta.pdf_reading``. On the wire only when there is any."""
    refine: Mapping[str, object] | None = None
    """Refine turns: the message's ``meta.refine`` (a review, or a version with whether it
    was accepted). On the wire only when set."""

    def to_wire(self) -> Wire:
        wire: Wire = {
            "type": "stream.completed",
            "request_id": self.request_id,
            "stream_id": self.stream_id,
            "message_id": self.message_id,
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "agreement": self.agreement,
            "unchanged": self.unchanged,
            "cost_basis": self.cost_basis,
        }
        if self.truncated:
            wire["truncated"] = True
        if self.finish_reason:
            wire["finish_reason"] = self.finish_reason
        if self.unchanged_note:
            wire["unchanged_note"] = self.unchanged_note
        if self.pdf_reading:
            wire["pdf_reading"] = [reading.to_wire() for reading in self.pdf_reading]
        if self.refine is not None:
            wire["refine"] = dict(self.refine)
        return wire


@dataclass(frozen=True, slots=True)
class StreamFailed:
    request_id: str
    stream_id: str
    error: ErrorInfo
    usage: Usage | None = None
    """What the failed call billed (a refusal, an empty reply, an output budget spent
    before any text...), priced; None when nothing is known to be billed. On the wire
    only when set."""

    def to_wire(self) -> Wire:
        wire: Wire = {
            "type": "stream.failed",
            "request_id": self.request_id,
            "stream_id": self.stream_id,
            "error": self.error.to_wire(),
        }
        if self.usage is not None:
            wire["usage"] = self.usage.to_dict()
        return wire


@dataclass(frozen=True, slots=True)
class Savings:
    cache: int = 0
    compaction: int = 0
    early_stop: int = 0
    unchanged: int = 0
    cost_usd: float | None = None
    """Estimated value of the saved tokens: kept answers at the output price of their
    model, compaction at the input price of the calls that carried the context, skipped
    rounds at the average cost of this turn's revisions, a cache hit at the replayed
    turn's cost. None when no saved kind can be priced."""

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
class RefineChange:
    """A change of a refine round: proposed by a review or applied by an edit. ``kind`` is
    "defect", "clarity", "simplification" or "requirement" ("merge" for the lines of the
    first version, which say what it took from each answer)."""

    kind: str
    text: str

    def to_wire(self) -> Wire:
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True, slots=True)
class RefineRound:
    """The end of a refine round (docs/adr/0010-mode-perfecciona.md): round 1 merges the
    answers into version 1; each later round reviews the current version and may write
    the next one."""

    request_id: str
    round: int
    version: int
    """The current version after the round (the last accepted one)."""
    accepted: bool
    """The round wrote a new version that became the current one."""
    words: int
    """Words of the current version."""
    budget_words: int
    """The word limit of every version of the turn."""
    usage: Usage
    """What the round's calls billed."""
    total: Usage
    """What the turn has billed so far."""
    reason: str | None = None
    """Why the round wrote no new version (Catalan); None when it did."""
    changes: Sequence[RefineChange] = ()
    """The changes of the new version (its changelog); none when not accepted."""
    proposals: Mapping[AgentName, int | None] = field(default_factory=dict)
    """Changes each agent's review proposed (None: no review of that agent)."""
    scores: Mapping[AgentName, int | None] = field(default_factory=dict)
    """How fit for the brief each review scored the version, 0-100 (None: none)."""
    converged: bool = False
    """The round completed the convergence rule (the turn stops by itself)."""

    def to_wire(self) -> Wire:
        return {
            "type": "refine.round",
            "request_id": self.request_id,
            "round": self.round,
            "version": self.version,
            "accepted": self.accepted,
            "reason": self.reason,
            "words": self.words,
            "budget_words": self.budget_words,
            "changes": [change.to_wire() for change in self.changes],
            "proposals": {agent: self.proposals.get(agent) for agent in AGENTS},
            "scores": {agent: self.scores.get(agent) for agent in AGENTS},
            "converged": self.converged,
            "usage": self.usage.to_dict(),
            "total": self.total.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TurnStopping:
    """The owner asked a refine turn to stop: it ends after ``round``, the round in
    course, with its last version (``turn.stop``)."""

    request_id: str
    round: int

    def to_wire(self) -> Wire:
        return {"type": "turn.stopping", "request_id": self.request_id, "round": self.round}


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
    stop_reason: RefineStopReason | None = None
    """Refine turns: why the turn ended with its last version. On the wire only when set."""

    def to_wire(self) -> Wire:
        wire: Wire = {
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
        if self.stop_reason is not None:
            wire["stop_reason"] = self.stop_reason
        return wire


@dataclass(frozen=True, slots=True)
class TurnFailed:
    request_id: str
    error: ErrorInfo
    usage: Usage = field(default_factory=Usage)
    """The turn's total until it failed, as its :class:`TurnOutcome` (zero when it failed
    before any call)."""

    def to_wire(self) -> Wire:
        return {
            "type": "turn.failed",
            "request_id": self.request_id,
            "error": self.error.to_wire(),
            "usage": self.usage.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    """Emitted by the web layer when the owner cancels a running turn (or the server
    stops), with what the turn had spent (the engine's :class:`TurnOutcome`)."""

    request_id: str
    usage: Usage = field(default_factory=Usage)

    def to_wire(self) -> Wire:
        return {
            "type": "turn.cancelled",
            "request_id": self.request_id,
            "usage": self.usage.to_dict(),
        }


TurnStatus = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class TurnFailure:
    """A model call of the turn that failed (a ``stream.failed``)."""

    agent: AgentName
    kind: str
    message: str
    round: int

    def to_wire(self) -> Wire:
        return {
            "agent": self.agent,
            "kind": self.kind,
            "message": self.message,
            "round": self.round,
        }


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """How a turn ended (docs/adr/0007-resultat-del-torn.md): what its terminal event
    said, written once on its question (``meta.outcome``) so that a reloaded turn shows
    what the live one did."""

    status: TurnStatus
    usage: Usage
    """The turn's total: every billed call (the compaction summaries, failed calls,
    declined attempts and calls that stored no message included)."""
    savings: Savings
    """What the turn saved (a failed or cancelled turn records no savings: zero)."""
    error: ErrorInfo | None = None
    """Why the turn failed (only when ``status`` is "failed")."""
    failures: Sequence[TurnFailure] = ()
    """The stream failures of the turn, in the order they happened."""
    consensus: Consensus | None = None
    final_message_ids: Sequence[int] = ()
    cached: bool = False
    stop_reason: RefineStopReason | None = None
    """Refine turns: why the turn ended with its last version (a cancelled one keeps it
    too). On the wire only when set."""

    def to_wire(self) -> Wire:
        wire: Wire = {"status": self.status}
        if self.status == "failed" and self.error is not None:
            wire["error"] = self.error.to_wire()
        wire.update(
            failures=[failure.to_wire() for failure in self.failures],
            usage=self.usage.to_dict(),
            savings=self.savings.to_wire(),
            consensus=self.consensus.to_wire() if self.consensus else None,
            final_message_ids=list(self.final_message_ids),
            cached=self.cached,
        )
        if self.stop_reason is not None:
            wire["stop_reason"] = self.stop_reason
        return wire


ServerEvent = (
    TurnStarted
    | PhaseChanged
    | PdfCheckChanged
    | RefineRound
    | TurnStopping
    | StreamStarted
    | StreamDelta
    | StreamCompleted
    | StreamFailed
    | TurnCompleted
    | TurnFailed
    | TurnCancelled
)
