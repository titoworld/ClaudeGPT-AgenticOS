"""Turn engine: runs solo, duel, debate and refine turns and streams their events.

See docs/ARCHITECTURE.md (modes, token savings) and docs/PROTOCOL.md (event order).
The turn itself runs in its own task and hands events to :meth:`Engine.run`
through a queue, so concurrent model calls interleave naturally and closing or
cancelling the iterator cancels every task of the turn before returning.

How every turn ends (completed, failed or cancelled) is decided once and stored on its
question before the terminal event (docs/adr/0007-turn-outcome.md).

Attachments (docs/adr/0009-attachments.md): a turn loads the ones its request names before
anything else (a missing one fails the turn), stores its question linked to them in one
transaction, before ``turn.started`` (one deleted meanwhile fails the turn just the same),
and keeps their metadata there (``meta.attachments``). Answers and the synthesis get
every one whole; the revisions get images and text files whole and the PDFs as
``pdf_in_revisions`` says (their extracted text, or the document), except a PDF without
any text, which goes whole. Later turns only see a reference to them.

A ChatGPT that cannot open PDFs (Codex) reads their text as Claude checked it
(orchestrator/pdf_check.py): its first call of the turn starts the check, which runs while
Claude answers, and every call of ChatGPT waits for that same check before it starts
(``pdf.check`` events). The check's calls are billed to the turn; ChatGPT's messages say
how it read each PDF (``meta.pdf_reading``), and so do the revisions and the synthesis
(``prompts.pdf_reading_note``). A replay from the turn cache checks nothing.

A refine turn («Perfecciona», docs/adr/0010-refine-mode.md) improves one document
until the owner stops it (see :meth:`Engine._refine`): both agents answer (round 0), the
editor merges the answers into version 1 (round 1), and from round 2 on both review the
current version and the editor writes the next one, which the engine checks without any
model (a complete version, within the word budget, not the same as before). It ends when
the owner asks it to stop after the round in course (``turn.stop``), when neither finds
anything left to change, when both score it above the threshold, at its rounds or its
budget, or when both agents fail; its last version is then stored, without any call, as
the turn's final message, which later turns see. Cancelling it stores that version too,
before the outcome. It is never cached.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import uuid
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, Protocol

from agentic_os.attachments import size_text, snapshot
from agentic_os.domain import (
    AGENTS,
    REFINE_BUDGET_FACTOR,
    REFINE_CONVERGENCE_ROUNDS,
    REFINE_MIN_BUDGET_WORDS,
    AgentName,
    MessageKind,
    ProviderMode,
    Purpose,
    RefineOptions,
    RefineReasonCode,
    RefineStopReason,
    Usage,
    other_agent,
    words,
)
from agentic_os.i18n import lazy, number, t
from agentic_os.orchestrator.accounting import (
    TurnAccounting,
    declined_attempts,
    failed_call_usage,
    is_billed,
)
from agentic_os.orchestrator.cache import (
    context_fingerprint,
    replay_cost_usd,
    replay_tokens,
    replayed_message,
    turn_cache_key,
)
from agentic_os.orchestrator.events import (
    Consensus,
    ErrorInfo,
    PdfCheckChanged,
    PdfReading,
    PhaseChanged,
    RefineChange,
    RefineRound,
    Savings,
    Section,
    ServerEvent,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamStarted,
    TurnCompleted,
    TurnFailed,
    TurnFailure,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
    TurnStopping,
)
from agentic_os.orchestrator.memory import (
    TurnContext,
    build_context,
    compact,
    compaction_cut,
    context_from_history,
)
from agentic_os.orchestrator.pdf_check import (
    CHECK_CONCURRENCY,
    CHECK_TIMEOUT_SECONDS,
    CheckOutcome,
    check_pdf,
    unchecked_pages,
)
from agentic_os.orchestrator.prompts import (
    answer_prompt,
    changelog_tail,
    debate_answer_prompt,
    pdf_reading_note,
    refine_answer_prompt,
    refine_edit_prompt,
    refine_merge_prompt,
    refine_review_prompt,
    refine_shorten_prompt,
    revision_prompt,
    synthesis_prompt,
    system_prompt,
)
from agentic_os.orchestrator.refine import (
    EditStream,
    RefinePiece,
    ReviewParse,
    ReviewStream,
)
from agentic_os.orchestrator.sections import RevisionStreamParser
from agentic_os.orchestrator.store import (
    AttachmentNotFoundError,
    CachedTurn,
    JsonValue,
    NewMessage,
    SavingRecord,
    Store,
    UsageRecord,
)
from agentic_os.orchestrator.types import EngineConfig, TurnRequest
from agentic_os.pricing import ModelPrice, estimate_cost_usd, find_price
from agentic_os.providers.base import (
    MODEL_ID_PATTERN,
    Attachment,
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
    TextDelta,
    reads_pdfs,
)
from agentic_os.providers.prompt_format import AGENT_LABELS, has_text

logger = logging.getLogger(__name__)

MAX_DEBATE_ROUNDS = 4
TITLE_MAX_CHARS = 60
STATUS_TIMEOUT_SECONDS = 2.0
"""How long a turn waits for a provider's status (the model in the turn cache key)."""
STATUS_CHECK_TIMEOUT_SECONDS = 30.0
"""A status check still running after this long is abandoned; the next turn asks again."""
PREWARM_TIMEOUT_SECONDS = 5.0

_DEFAULT_CONFIG = EngineConfig()
_MODEL_ID = re.compile(MODEL_ID_PATTERN)
_PHASE_OF: dict[MessageKind, Literal["answer", "revision", "synthesis"]] = {
    "answer": "answer",
    "revision": "revision",
    "synthesis": "synthesis",
}

Emit = Callable[[ServerEvent], None]
OnOutcome = Callable[[TurnOutcome], None]


def _internal_error() -> ErrorInfo:
    """The error of a turn that crashed."""
    return ErrorInfo("internal", t("engine.error.internal_turn"))


REFINE_ROUNDS_RANGE: Final = (2, 50)
REFINE_WORDS_RANGE: Final = (100, 20_000)
REFINE_THRESHOLD_RANGE: Final = (50, 100)
"""The ranges of a refine turn's options (``RefineOptions``), which the server checks too."""


def refine_reason(code: RefineReasonCode) -> str:
    """Why a round of a refine turn wrote no new version, for people, in the language in
    force: ``refine.round``'s ``reason`` and the ``reason`` of a version that was not
    accepted, next to their ``reason_code``. The engine decides on the code, and makes the
    text when it writes the event or the message."""
    return t(f"engine.refine.reason.{code}")


REFINE_OVER_BUDGET: Final = lazy("engine.refine.reason.over_budget")
REFINE_INCOMPLETE: Final = lazy("engine.refine.reason.incomplete")
REFINE_IDENTICAL: Final = lazy("engine.refine.reason.identical")
REFINE_NOTHING_TO_CHANGE: Final = lazy("engine.refine.reason.nothing_to_change")
REFINE_FAILED_ROUND: Final = lazy("engine.refine.reason.failed_round")
"""The texts of :func:`refine_reason`, made in the language in force when they are shown
(``str()``), for whoever quotes them."""
REFINE_NO_CHANGES: Final = lazy("engine.refine.no_changes")
"""The error of a review whose reply has no changes section, or whose section neither
lists a change in the format asked for nor says UNCHANGED: it failed."""
REFINE_UNCHANGED = "UNCHANGED"
"""What a review stores when its changes section is empty."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def make_title(question: str) -> str:
    """Conversation title: first non-empty line of the question, at most 60 characters."""
    line = next((ln.strip() for ln in question.splitlines() if ln.strip()), "")
    if not line:
        return t("engine.default_title")
    if len(line) <= TITLE_MAX_CHARS:
        return line
    return line[: TITLE_MAX_CHARS - 1].rstrip() + "…"


class _End:
    """Queue sentinel: the turn task has finished."""


_END = _End()


@dataclass(slots=True)
class _Outcome:
    """Result of one model call (after the optional retry)."""

    agent: AgentName
    ok: bool
    content: str = ""
    critique: str | None = None
    agreement: int | None = None
    error: ProviderError | None = None
    truncated: bool = False
    """The reply was cut off (its message carries ``truncated`` in its meta)."""
    finish_reason: str | None = None
    kept: bool = False
    """A revision whose content is the previous answer (UNCHANGED, or cut off before
    its answer): the previous answer's completeness still applies."""
    message_id: int | None = None
    """The message the call stored."""
    model: str = ""
    """The model that answered."""


@dataclass(slots=True)
class _Answers:
    """The latest answer of each agent in a debate and whether it was cut off."""

    text: dict[AgentName, str]
    cut: dict[AgentName, str | None]
    """Agents whose latest answer is incomplete, with the finish reason (may be None)."""

    def take(self, outcome: _Outcome) -> None:
        self.text[outcome.agent] = outcome.content
        if not outcome.kept:
            if outcome.truncated:
                self.cut[outcome.agent] = outcome.finish_reason
            else:
                self.cut.pop(outcome.agent, None)


@dataclass(slots=True)
class _Progress:
    emitted: bool = False


class _SectionParser(Protocol):
    """Splits a streaming reply into the sections to show (a debate revision, a refine
    review or edit)."""

    def feed(self, chunk: str) -> Sequence[tuple[Section, str]]: ...


@dataclass(slots=True)
class _Version:
    """A version of a refine turn's document that became the current one."""

    number: int
    text: str
    words: int
    agent: AgentName
    """The agent that wrote it, or whose answer it copies."""
    round: int
    message_id: int
    model: str
    truncated: bool = False
    finish_reason: str | None = None
    """A copy of an answer that was cut off keeps its mark."""


@dataclass(slots=True)
class _Refine:
    """The state of a refine turn (see :meth:`Engine._refine`)."""

    options: RefineOptions
    active: tuple[AgentName, ...] = ()
    """The agents that answered the brief: one whose answer failed takes no further part
    in the turn."""
    round: int = 0
    """The round in course (the last one, once the turn ends)."""
    current: _Version | None = None
    budget_words: int = 0
    """The word limit of every version: the owner's, or derived from version 1."""
    changelog: list[tuple[int, RefineChange]] = field(default_factory=list)
    """Every change the accepted versions applied, with their version, in order."""
    edit_failed: set[AgentName] = field(default_factory=set)
    """Agents whose merge or edit failed in this turn: the other one edits first."""
    unchanged_rounds: int = 0
    """Consecutive rounds where no review proposed any change."""
    converging_rounds: int = 0
    """Consecutive rounds where every agent scored the version at least the threshold
    and nobody proposed a defect."""
    announced: bool = False
    """``turn.stopping`` was emitted."""
    stop_reason: RefineStopReason | None = None
    final_write: asyncio.Task[None] | None = None
    """The write of the last version as the final message (shielded from cancellation)."""


@dataclass(frozen=True, slots=True)
class _Read:
    """What a refine call made of its reply: the message to store and its
    ``meta.refine``, or why the call failed (``error``, or no content)."""

    content: str
    refine: dict[str, JsonValue] = field(default_factory=dict)
    error: str | None = None


def _changes_json(changes: Sequence[RefineChange]) -> list[JsonValue]:
    """``RefineChange.to_wire()`` of each change, as JSON for a message's meta."""
    return [{"kind": change.kind, "text": change.text} for change in changes]


def _budget_words(max_words: int | None, first_words: int) -> int:
    """The word limit of a refine turn's versions: the owner's, else
    ``REFINE_BUDGET_FACTOR`` times the words of version 1, at least
    ``REFINE_MIN_BUDGET_WORDS``."""
    if max_words is not None:
        return max_words
    # Rounded before the ceiling: 1.2 * 5 must give 6, never 6.000000000000001 -> 7.
    budget = math.ceil(round(REFINE_BUDGET_FACTOR * first_words, 6))
    return max(REFINE_MIN_BUDGET_WORDS, budget)


class _ReviewReader:
    """Streams and reads a review of a refine turn (orchestrator/refine.py): a reply
    without its changes, in the format asked for or as UNCHANGED, fails the call; an
    empty list stores UNCHANGED."""

    def __init__(self) -> None:
        self._stream = ReviewStream()
        self.review: ReviewParse | None = None
        """The review, once its call has a reply."""

    def start(self) -> ReviewStream:
        """The stream of a new attempt of the call."""
        self._stream = ReviewStream()
        return self._stream

    def finish(self, truncated: bool) -> tuple[list[RefinePiece], _Read]:
        """The rest of the reply to show, and what to store of it."""
        pieces = self._stream.close()
        review = self.review = self._stream.final()
        if not review.ok:
            return pieces, _Read("", error=str(REFINE_NO_CHANGES))
        content = review.text
        if not content:  # an empty list proposes nothing: shown as it is stored
            content = REFINE_UNCHANGED
            pieces.append(("critique", content))
        meta: dict[str, JsonValue] = {
            "role": "review",
            "score": review.score,
            "unchanged": review.unchanged,
            "changes": _changes_json(review.changes),
        }
        return pieces, _Read(content, meta)


class _VersionReader:
    """Streams and reads a merge, an edit or its shortening, and decides whether the
    version it writes is accepted, before its message is stored with the verdict. The
    guards are the engine's, without any model (docs/adr/0010-refine-mode.md): a
    complete version section, then within the word budget and (an edit) not the same as
    the current version (after stripping). The merge's budget is the owner's limit, or
    the one its version 1 sets (which it always fits)."""

    def __init__(
        self,
        *,
        number: int,
        current: _Version | None,
        budget_words: int | None,
        max_words: int | None,
        keep_over: bool = False,
    ) -> None:
        """``current`` None: the merge, which writes version 1 (or its shortening);
        otherwise an edit or a shortening of ``current`` within ``budget_words``.
        ``keep_over``: a complete version over the budget is accepted all the same (the
        shortening of version 1, which has no earlier version to keep)."""
        self._number = number
        self._current = current
        self._budget = budget_words
        self._max_words = max_words
        self._keep_over = keep_over
        self._stream = EditStream(merge=current is None)
        self.text = ""
        self.words = 0
        self.budget_words = 0
        self.changes: tuple[RefineChange, ...] = ()
        self.reason: RefineReasonCode | None = None
        """Why the version was not accepted, as a code (:func:`refine_reason`)."""
        self.accepted = False

    def start(self) -> EditStream:
        """The stream of a new attempt of the call."""
        self._stream = EditStream(merge=self._current is None)
        return self._stream

    def finish(self, truncated: bool) -> tuple[list[RefinePiece], _Read]:
        """The rest of the reply to show, and what to store of it (a version that was
        cut off is never complete)."""
        pieces = self._stream.close(truncated=truncated)
        edit = self._stream.final()
        self.text, self.changes = edit.text, edit.changes
        if not edit.text:
            return pieces, _Read("")  # nothing written: the call fails as empty
        self.words = words(edit.text)
        self.budget_words = (
            self._budget if self._budget is not None else _budget_words(self._max_words, self.words)
        )
        current = self._current
        if not edit.complete:
            self.reason = "incomplete"
        elif not self._keep_over and self.words > self.budget_words:
            self.reason = "over_budget"
        elif current is not None and edit.text == current.text:
            self.reason = "identical"
        self.accepted = self.reason is None
        meta: dict[str, JsonValue] = {
            "role": "version",
            "version": self._number,
            "words": self.words,
            "budget_words": self.budget_words,
            "accepted": self.accepted,
            "reason": None if self.reason is None else refine_reason(self.reason),
            "reason_code": self.reason,
            "changelog": _changes_json(edit.changes),
        }
        return pieces, _Read(edit.text, meta)


_Reader = _ReviewReader | _VersionReader


def _usage_since(start: Usage, now: Usage) -> Usage:
    """What a turn's calls billed between two of its running totals."""
    cost = None if now.cost_usd is None else now.cost_usd - (start.cost_usd or 0.0)
    return Usage(
        input_tokens=now.input_tokens - start.input_tokens,
        output_tokens=now.output_tokens - start.output_tokens,
        cache_read_tokens=now.cache_read_tokens - start.cache_read_tokens,
        cache_write_tokens=now.cache_write_tokens - start.cache_write_tokens,
        reasoning_tokens=now.reasoning_tokens - start.reasoning_tokens,
        cost_usd=cost,
    )


@dataclass(slots=True)
class _Turn:
    """Mutable state of a running turn."""

    request: TurnRequest
    emit: Emit
    question: str
    conversation_id: int = 0
    turn_id: int = 0
    attachments: tuple[Attachment, ...] = ()
    """The question's attachments, in order, each in mode "full"."""
    prices: Mapping[str, ModelPrice] | None = None
    """Owner price overrides (over the default prices) for this turn's costs."""
    context: TurnContext = field(default_factory=lambda: build_context(None, ()))
    accounting: TurnAccounting = field(default_factory=TurnAccounting)
    compaction_usage: Usage | None = None
    """Priced usage of this turn's compaction summary calls, when any reached a model
    (stored on the question as ``meta.compaction_usage``)."""
    stored: list[NewMessage] = field(default_factory=list)
    """Assistant messages in storage order (what the cache replays)."""
    final_ids: list[int] = field(default_factory=list)
    failed_agents: set[AgentName] = field(default_factory=set)
    degraded: bool = False
    truncated: bool = False
    """A stored message of the turn was cut off: the turn is never cached."""
    background: set[asyncio.Task[None]] = field(default_factory=set)
    failures: list[TurnFailure] = field(default_factory=list)
    """The stream failures of the turn, in order (``outcome.failures``)."""
    on_outcome: OnOutcome | None = None
    outcome: TurnOutcome | None = None
    """How the turn ended, once decided (see :meth:`Engine._settle`)."""
    stop: asyncio.Event | None = None
    """Set when the owner asks the turn to stop after the round in course (``turn.stop``):
    only refine turns read it; ``turn.cancel`` cancels the turn instead."""
    refine: _Refine | None = None
    """A refine turn's state (see :meth:`Engine._refine`)."""
    closing: asyncio.Task[None] | None = None
    """A cancelled refine turn's last writes, its last version as the final message and
    then its outcome (shielded from cancellation, see :meth:`Engine._close_refine`)."""
    outcome_write: asyncio.Task[None] | None = None
    """The write of :attr:`outcome` on the question (shielded from cancellation)."""
    savings_write: asyncio.Task[None] | None = None
    """The write of the turn's saving rows, once started (shielded from cancellation):
    from then on the outcome carries them, even if the turn is cancelled."""
    check_task: asyncio.Task[None] | None = None
    """Claude's check of the question's PDFs for a ChatGPT that cannot open them (see
    :meth:`Engine._check_pdfs`), started by ChatGPT's first call that needs it."""
    pdf_reading: tuple[PdfReading, ...] | None = None
    """How ChatGPT reads each PDF of the question, once that check has ended."""
    recheck: bool = False
    """A PDF's check ended in a way another turn may not repeat (``CheckOutcome.final``):
    the next turn retries it (an error, a refusal, a reply that made no progress, a
    timeout), or nobody could check it (no Claude, or only the demo's) and a Claude
    configured later would. The turn is never cached, so asking the same question checks
    it again instead of replaying an unchecked reading."""

    @property
    def request_id(self) -> str:
        return self.request.request_id


async def _cancel_and_wait(tasks: Iterable[asyncio.Task[Any]]) -> None:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)


async def _end_turn(task: asyncio.Task[None], turn: _Turn) -> None:
    """Cancel the task of ``turn`` (once, if it is still running) and wait until it has
    ended and how it ended is stored. A caller cancelled again meanwhile (the owner
    pressing stop twice, a shutdown while the turn stops) keeps waiting, without
    cancelling the task again, and its ``CancelledError`` goes on afterwards: whatever
    announces the cancellation then comes after the stored outcome, with what the turn
    spent, and no write of the turn outlives :meth:`Engine.run` (nor, at shutdown, the
    store). The wait is bounded by how long the providers take to stop a call and the
    store takes to write. A cancelled refine turn first stores its last version (see
    :attr:`_Turn.closing`), and that is waited for too."""
    if not task.done():
        task.cancel()
    interrupted: asyncio.CancelledError | None = None
    while pending := [
        t for t in (task, turn.closing, turn.outcome_write) if t is not None and not t.done()
    ]:
        try:
            await asyncio.wait(pending)
        except asyncio.CancelledError as exc:
            interrupted = exc
    if interrupted is not None:
        raise interrupted


def _usage_json(usage: Usage) -> dict[str, JsonValue]:
    return {key: value for key, value in usage.to_dict().items()}


def _savings_json(savings: Savings) -> dict[str, JsonValue]:
    """``Savings.to_wire()`` as JSON for the meta of a turn's final messages."""
    return {
        "cache": savings.cache,
        "compaction": savings.compaction,
        "early_stop": savings.early_stop,
        "unchanged": savings.unchanged,
        "total": savings.total,
        "cost_usd": savings.cost_usd,
    }


def _set_unstored(meta: dict[str, JsonValue], accounting: TurnAccounting) -> None:
    """On a final message: ``unstored_usage``, the billed calls of the turn so far that
    stored no message (failed, refused, empty or declined). Every final message carries
    the running total, so it can miss a billed failure that ends after the last final
    message of a duel: the turn's total is its outcome's usage (``meta.outcome`` of the
    question), and this is kept for the turns reconstructed without one."""
    if is_billed(accounting.unstored):
        meta["unstored_usage"] = _usage_json(accounting.unstored)


def _meta_text(meta: Mapping[str, JsonValue], key: str) -> str | None:
    """A non-empty string field of a stored message's meta, else None."""
    value = meta.get(key)
    return value if isinstance(value, str) and value else None


def _set_truncated(meta: dict[str, JsonValue], finish_reason: str | None) -> None:
    """Meta of a message whose reply was cut off: a usable partial answer."""
    meta["truncated"] = True
    if finish_reason:
        meta["finish_reason"] = finish_reason


def _empty_reply_message(result: GenerationResult) -> str:
    """Error message of a billed reply that left nothing to store."""
    if not result.truncated:
        return t("engine.error.empty_reply")
    if result.finish_reason == "max_tokens":
        return t("engine.error.empty_reply_max_tokens")
    if result.finish_reason == "content_filter":
        return t("engine.error.empty_reply_content_filter")
    return t("engine.error.empty_reply_interrupted")


def _cost_basis(mode: ProviderMode, usage: Usage) -> str | None:
    """Meta ``cost_basis`` of a call: ``api`` for a real cost, ``equivalent`` for
    subscription (or priced demo) usage valued at API prices; None without either."""
    if mode == "api":
        return "api"
    if mode == "cli" or usage.cost_usd is not None:
        return "equivalent"
    return None


def _pages_json(pages: Sequence[int]) -> list[JsonValue]:
    return [*pages]


def _pdf_reading_json(readings: Sequence[PdfReading]) -> list[JsonValue]:
    """``meta.pdf_reading`` of ChatGPT's message: :meth:`PdfReading.to_wire` as JSON."""
    return [
        {
            "attachment_id": reading.attachment_id,
            "name": reading.name,
            "checked": reading.checked,
            "claude_pages": _pages_json(reading.claude_pages),
            "hidden_pages": _pages_json(reading.hidden_pages),
            "unchecked_pages": _pages_json(reading.unchecked_pages),
            "reason": reading.reason,
        }
        for reading in readings
    ]


def _pages_from_json(value: JsonValue | None) -> tuple[int, ...]:
    items = value if isinstance(value, list) else []
    return tuple(item for item in items if isinstance(item, int) and not isinstance(item, bool))


def _pdf_reading_from_json(value: JsonValue | None) -> tuple[PdfReading, ...]:
    """The readings of a stored ``meta.pdf_reading`` (a replayed message), skipping any
    entry that is not one."""
    readings: list[PdfReading] = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict):
            continue
        attachment_id, name = entry.get("attachment_id"), entry.get("name")
        checked, reason = entry.get("checked"), entry.get("reason")
        if not isinstance(attachment_id, int) or isinstance(attachment_id, bool):
            continue
        if not isinstance(name, str) or not isinstance(checked, bool):
            continue
        readings.append(
            PdfReading(
                attachment_id,
                name,
                checked,
                _pages_from_json(entry.get("claude_pages")),
                _pages_from_json(entry.get("hidden_pages")),
                _pages_from_json(entry.get("unchecked_pages")),
                reason if isinstance(reason, str) else None,
            )
        )
    return tuple(readings)


def _pdf_reading(attachment_id: int, attachment: Attachment, outcome: CheckOutcome) -> PdfReading:
    """How ChatGPT reads a PDF after its check ended with ``outcome``."""
    check = outcome.check if outcome.check is not None and outcome.check.covered > 0 else None
    return PdfReading(
        attachment_id=attachment_id,
        name=attachment.name,
        checked=check is not None,
        claude_pages=check.claude_pages if check is not None else (),
        hidden_pages=check.hidden_pages if check is not None else (),
        unchecked_pages=unchecked_pages(attachment, check),
        reason=outcome.reason,
    )


def _consensus_json(consensus: Consensus) -> dict[str, JsonValue]:
    scores: dict[str, JsonValue] = {agent: score for agent, score in consensus.scores.items()}
    return {"reached": consensus.reached, "round": consensus.round, "scores": scores}


def _consensus_from_json(value: JsonValue) -> Consensus | None:
    if not isinstance(value, dict):
        return None
    reached, round_, raw_scores = value.get("reached"), value.get("round"), value.get("scores")
    if not isinstance(reached, bool) or not isinstance(round_, int):
        return None
    scores: dict[AgentName, int] = {}
    if isinstance(raw_scores, dict):
        for agent in AGENTS:
            score = raw_scores.get(agent)
            if isinstance(score, int) and not isinstance(score, bool):
                scores[agent] = score
    return Consensus(reached=reached, round=round_, scores=scores)


class Engine:
    """Runs turns against the configured providers and persists them in ``store``."""

    def __init__(
        self,
        providers: Mapping[AgentName, Provider],
        store: Store,
        config: EngineConfig = _DEFAULT_CONFIG,
        *,
        clock: Callable[[], datetime] = _utc_now,
        retry_delay: float = 0.5,
        check_timeout: float = CHECK_TIMEOUT_SECONDS,
    ) -> None:
        self._providers = dict(providers)
        self._store = store
        self._config = config
        self._clock = clock
        self._retry_delay = retry_delay
        self._check_timeout = check_timeout
        """How long ChatGPT waits for Claude's check of a turn's PDFs (see
        :meth:`_check_pdfs`)."""
        self._identities: dict[AgentName, str] = {}
        self._status_checks: dict[AgentName, asyncio.Task[None]] = {}
        """The latest status check of each agent (see :meth:`_identity`)."""
        self._models: dict[AgentName, str] = {}
        """Model name per agent for StreamStarted, from ``status()`` or the first result."""
        self._writes: set[asyncio.Task[None]] = set()
        """Writes of how a turn ended (its outcome and its saving rows) still running:
        kept here so that one whose turn stopped waiting for it (it is shielded) still
        ends."""

    # -- public API --------------------------------------------------------------

    async def run(
        self,
        request: TurnRequest,
        *,
        compaction_threshold_tokens: int | None = None,
        price_overrides: Mapping[str, ModelPrice] | None = None,
        on_outcome: OnOutcome | None = None,
        stop: asyncio.Event | None = None,
    ) -> AsyncIterator[ServerEvent]:
        """Run one turn, yielding its events (see docs/PROTOCOL.md).

        ``compaction_threshold_tokens`` overrides ``EngineConfig`` for this turn and
        ``price_overrides`` (the owner's prices, over ``pricing.DEFAULT_PRICES``) price
        its calls. The last event is always ``TurnCompleted`` or ``TurnFailed``.
        Closing the iterator or cancelling its consumer cancels the turn and all its
        model calls; the ``CancelledError`` always propagates, once the turn has ended
        (a consumer cancelled again meanwhile still waits for that: see
        :func:`_end_turn`).

        ``on_outcome`` is called once with how the turn ended, as soon as it is decided
        (before the terminal event, and also when the turn is cancelled, which has no
        event here: the web layer announces it with what the turn spent). A cancelled
        consumer gets its ``CancelledError`` after that call and after the outcome is
        stored.

        ``stop``, when set, asks a refine turn to end after the round in course, with its
        last version (the owner's ``turn.stop``); other modes ignore it.
        """
        queue: asyncio.Queue[ServerEvent | _End] = asyncio.Queue()
        turn = _Turn(
            request=request,
            emit=queue.put_nowait,
            question=request.text.strip(),
            prices=price_overrides,
            on_outcome=on_outcome,
            stop=stop,
        )
        task = asyncio.create_task(
            self._execute(turn, compaction_threshold_tokens),
            name=f"turn-{request.request_id}",
        )
        task.add_done_callback(lambda _task: queue.put_nowait(_END))
        finished = False
        try:
            while True:
                item = await queue.get()
                if isinstance(item, _End):
                    break
                finished = finished or isinstance(item, TurnCompleted | TurnFailed)
                yield item
            error = None if task.cancelled() else task.exception()
            if error is not None:
                logger.error("Turn %s crashed", request.request_id, exc_info=error)
            if not finished:
                crashed = _internal_error()
                outcome = await self._settle(turn, "failed", error=crashed)
                yield TurnFailed(request.request_id, crashed, outcome.usage)
        finally:
            await _end_turn(task, turn)

    # -- turn ----------------------------------------------------------------------

    async def _execute(self, turn: _Turn, threshold: int | None) -> None:
        try:
            await self._run_turn(turn, threshold)
        except asyncio.CancelledError:
            # Claude's check of the PDFs runs in a task of its own: it stops first, so
            # that what its calls billed is final when the outcome counts it.
            if turn.check_task is not None:
                await _cancel_and_wait((turn.check_task,))
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                # Cancelled by the owner, a closed iterator or a shutdown: the turn's
                # calls have stopped. How it ended is stored (shielded, see _settle)
                # before the cancellation goes on; a refine turn stores its last
                # version first, so nothing already paid for is lost.
                if turn.refine is not None and turn.refine.current is not None:
                    turn.closing = self._write(
                        self._close_refine(turn), name=f"close-{turn.request_id}"
                    )
                    await asyncio.shield(turn.closing)
                else:
                    await self._settle(turn, "cancelled")
            else:
                # Nobody cancelled the turn: a call raised the error by itself (a bug in
                # a provider or a library). run() reports an internal failure, so the
                # stored outcome says the same, not that the owner stopped the turn.
                logger.error(
                    "Turn %s raised CancelledError without being cancelled",
                    turn.request_id,
                    exc_info=True,
                )
                await self._settle(turn, "failed", error=_internal_error())
            raise
        finally:
            await _cancel_and_wait(turn.background)

    async def _run_turn(self, turn: _Turn, threshold: int | None) -> None:
        request = turn.request
        invalid = self._validate(request)
        if invalid is not None:
            await self._fail(turn, invalid)
            return
        # Before anything is stored: a turn with a missing attachment leaves nothing.
        invalid = await self._load_attachments(turn)
        if invalid is not None:
            await self._fail(turn, invalid)
            return

        new_conversation = request.conversation_id is None
        if request.conversation_id is None:
            turn.conversation_id = await self._store.create_conversation(make_title(turn.question))
        elif await self._store.conversation_exists(request.conversation_id):
            turn.conversation_id = request.conversation_id
        else:
            not_found = t("engine.error.conversation_not_found")
            await self._fail(turn, ErrorInfo("not_found", not_found))
            return

        turn.context = await self._prepare_context(turn, threshold)

        agents = self._agents(request)
        identities = await asyncio.gather(
            *(self._identity(agent, request.models.get(agent)) for agent in agents)
        )
        known = {
            agent: identity for agent, identity in zip(agents, identities, strict=True) if identity
        }
        # A key without the model of every agent would match other models' turns: a turn
        # that could not learn one neither reads nor writes the turn cache. A refine turn
        # never does: it runs until the owner stops it, so the same question is never
        # the same turn.
        cache_key = (
            turn_cache_key(
                mode=request.mode,
                target=request.target,
                options=request.options,
                question=turn.question,
                context_fingerprint=context_fingerprint(turn.context),
                identities=known,
                attachments=turn.attachments,
                pdf_in_revisions=request.pdf_in_revisions,
            )
            if len(known) == len(agents) and request.mode != "refine"
            else None
        )
        cached: CachedTurn | None = None
        if request.options.use_cache and cache_key is not None:
            try:
                cached = await self._store.cache_get(cache_key, self._clock())
            except Exception:
                logger.exception("Could not read the turn cache")
            if cached is not None and cached.mode != request.mode:
                cached = None

        question_meta = self._question_meta(request, agents)
        if turn.attachments:
            # The wire's Attachment of each one (docs/PROTOCOL.md): the turn view shows
            # them from here, and later turns mention them (see memory.to_chat_turn).
            question_meta["attachments"] = [
                snapshot(attachment_id, attachment)
                for attachment_id, attachment in zip(
                    request.attachments, turn.attachments, strict=True
                )
            ]
        if turn.compaction_usage is not None:
            # The summary ran before the question existed: its cost travels with the
            # question (and in the turn's outcome).
            question_meta["compaction_usage"] = _usage_json(turn.compaction_usage)
        # Open until the turn ends (see _settle): a turn that never ends (a crash, a
        # restart) keeps it null, unlike the turns stored before outcomes existed.
        question_meta["outcome"] = None
        try:
            # The question and the links to its attachments, in one transaction: a
            # question never describes attachments that are not kept with it.
            turn.turn_id = await self._store.add_message(
                NewMessage(
                    conversation_id=turn.conversation_id,
                    kind="question",
                    content=turn.question,
                    final=True,
                    meta=question_meta,
                    attachments=request.attachments,
                )
            )
        except AttachmentNotFoundError as exc:
            # Deleted since the turn loaded it (from another tab, or an old orphan swept
            # meanwhile): the turn fails before it starts and leaves nothing, not even
            # the conversation it had just created (nobody has heard of it yet).
            if new_conversation:
                await self._discard_conversation(turn.conversation_id)
            await self._fail(turn, self._missing_attachment(exc))
            return
        turn.emit(
            TurnStarted(
                turn.request_id,
                turn.conversation_id,
                turn.turn_id,
                request.mode,
                new_conversation,
            )
        )

        if cached is not None:
            await self._replay(turn, cached)
            return

        consensus: Consensus | None = None
        stop_reason: RefineStopReason | None = None
        if request.mode == "solo":
            ok = await self._solo(turn)
        elif request.mode == "duel":
            ok = await self._duel(turn)
        elif request.mode == "refine":
            stop_reason = await self._refine(turn)
            ok = stop_reason is not None
        else:
            consensus = await self._debate(turn)
            ok = consensus is not None
        if ok:
            await self._finish(turn, consensus, cache_key, stop_reason)

    def _validate(self, request: TurnRequest) -> ErrorInfo | None:
        text = request.text
        if not text.strip():
            return ErrorInfo("invalid", t("engine.request.question_empty"))
        if len(text) > self._config.max_question_chars:
            most = number(self._config.max_question_chars)
            return ErrorInfo("invalid", t("engine.request.question_too_long", max=most))
        if request.mode not in ("solo", "duel", "debate", "refine"):
            return ErrorInfo("invalid", t("engine.request.mode"))
        if request.mode == "solo" and request.target not in AGENTS:
            return ErrorInfo("invalid", t("engine.request.agent"))
        debate = request.options.debate
        if request.mode == "debate":
            if not 0 <= debate.rounds <= MAX_DEBATE_ROUNDS:
                return ErrorInfo(
                    "invalid", t("engine.request.debate_rounds", max=MAX_DEBATE_ROUNDS)
                )
            if not 0 <= debate.consensus_threshold <= 100:
                return ErrorInfo("invalid", t("engine.request.consensus_threshold"))
            if debate.synthesizer not in AGENTS:
                return ErrorInfo("invalid", t("engine.request.synthesizer"))
        if request.mode == "refine" and (invalid := self._validate_refine(request)):
            return invalid
        for model in (*request.models.values(), *request.fast_models.values()):
            if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
                return ErrorInfo("invalid", t("engine.request.model_id"))
        attachments = request.attachments
        if len(attachments) > self._config.max_attachments:
            most = number(self._config.max_attachments)
            return ErrorInfo("invalid", t("engine.request.too_many_attachments", max=most))
        if len(set(attachments)) != len(attachments):
            return ErrorInfo("invalid", t("engine.request.repeated_attachment"))
        if request.pdf_in_revisions not in ("full", "text"):
            return ErrorInfo("invalid", t("engine.request.pdf_in_revisions"))
        for agent in self._agents(request):
            if agent not in self._providers:
                missing = t("engine.request.not_configured", agent=AGENT_LABELS[agent])
                return ErrorInfo("unavailable", missing)
        return None

    @staticmethod
    def _validate_refine(request: TurnRequest) -> ErrorInfo | None:
        """A refine turn's options in their ranges (the server checks them too) and its
        budget in dollars, when known, a positive number."""
        options = request.options.refine
        low, high = REFINE_ROUNDS_RANGE
        if not low <= options.max_rounds <= high:
            return ErrorInfo("invalid", t("engine.request.refine_rounds", low=low, high=high))
        low, high = REFINE_WORDS_RANGE
        if options.max_words is not None and not low <= options.max_words <= high:
            limits = {"low": number(low), "high": number(high)}
            return ErrorInfo("invalid", t("engine.request.refine_words", **limits))
        low, high = REFINE_THRESHOLD_RANGE
        if not low <= options.convergence_threshold <= high:
            return ErrorInfo("invalid", t("engine.request.refine_threshold", low=low, high=high))
        if options.editor not in AGENTS:
            return ErrorInfo("invalid", t("engine.request.refine_editor"))
        budget = request.refine_budget_usd
        if budget is not None and not (math.isfinite(budget) and budget > 0):
            return ErrorInfo("invalid", t("engine.request.refine_budget"))
        return None

    @staticmethod
    def _missing_attachment(error: AttachmentNotFoundError) -> ErrorInfo:
        return ErrorInfo(
            "invalid",
            t("engine.error.attachment_not_found", id=error.attachment_id),
            attachment_id=error.attachment_id,
        )

    async def _discard_conversation(self, conversation_id: int) -> None:
        """Delete the conversation a turn created and could not store its question in.
        A failure only leaves that empty conversation behind, so it is logged."""
        try:
            await self._store.discard_conversation(conversation_id)
        except Exception:
            logger.exception("Could not delete the empty conversation %d", conversation_id)

    async def _load_attachments(self, turn: _Turn) -> ErrorInfo | None:
        """Load the request's attachments into ``turn.attachments`` (in order, mode
        "full"); the error that fails the turn when one does not exist or together they
        pass the size limit."""
        ids = turn.request.attachments
        if not ids:
            return None
        try:
            loaded = await self._store.get_attachments(ids)
        except AttachmentNotFoundError as exc:
            return self._missing_attachment(exc)
        if len(loaded) != len(ids):
            raise RuntimeError(f"the store returned {len(loaded)} attachments for {len(ids)} ids")
        limit = self._config.max_attachment_bytes
        if sum(attachment.size for attachment in loaded) > limit:
            too_large = t("engine.error.attachments_too_large", size=size_text(limit))
            return ErrorInfo("invalid", too_large)
        turn.attachments = tuple(replace(attachment, mode="full") for attachment in loaded)
        return None

    @staticmethod
    def _agents(request: TurnRequest) -> tuple[AgentName, ...]:
        return (request.target,) if request.mode == "solo" else AGENTS

    def _model_name(self, turn: _Turn, agent: AgentName) -> str:
        """Model shown before a call: the one requested for the turn, else the default."""
        return turn.request.models.get(agent) or self._models.get(agent, "")

    @staticmethod
    def _question_meta(request: TurnRequest, agents: tuple[AgentName, ...]) -> dict[str, JsonValue]:
        debate = request.options.debate
        meta: dict[str, JsonValue] = {
            "mode": request.mode,
            "target": request.target,
            "options": {
                "debate": {
                    "rounds": debate.rounds,
                    "consensus_threshold": debate.consensus_threshold,
                    "synthesizer": debate.synthesizer,
                },
                "use_cache": request.options.use_cache,
            },
        }
        models: dict[str, JsonValue] = {
            agent: request.models[agent] for agent in agents if request.models.get(agent)
        }
        if models:
            meta["models"] = models
        if request.mode == "refine":
            # Its limits, as the options' wire: a reloaded turn shows them.
            refine = request.options.refine
            meta["refine"] = {
                "max_rounds": refine.max_rounds,
                "budget_eur": refine.budget_eur,
                "max_words": refine.max_words,
                "stop_on_convergence": refine.stop_on_convergence,
                "convergence_threshold": refine.convergence_threshold,
                "editor": refine.editor,
            }
        return meta

    async def _identity(self, agent: AgentName, model: str | None = None) -> str | None:
        """``"<mode>:<model>"`` of an agent's provider (part of the cache key), with the
        ``model`` the turn asks for, else the provider's default; None while unknown.

        The default is asked until a status answers, then remembered for the process:
        models only change with the configuration, which requires a restart. A turn
        waits at most STATUS_TIMEOUT_SECONDS; a slower status is not cancelled (the
        provider caches what it learns) and its answer serves the next turns. A failure
        or an unavailable provider is never remembered: a placeholder or a guessed model
        in the key would match another model's turns.
        """
        if model:
            return f"{self._providers[agent].mode}:{model}"
        identity = self._identities.get(agent)
        if identity is None:
            check = self._status_checks.get(agent)
            if check is None or check.done():
                check = asyncio.create_task(self._check_status(agent), name=f"status-{agent}")
                self._status_checks[agent] = check
            await asyncio.wait((check,), timeout=STATUS_TIMEOUT_SECONDS)
            identity = self._identities.get(agent)
            if identity is None and not check.done():
                logger.warning("The status of %s is slow: this turn skips the turn cache", agent)
        return identity

    async def _check_status(self, agent: AgentName) -> None:
        """Ask a provider's status and remember its model identity (never raises). An
        unavailable provider, or one that names no model, is not remembered either: it
        may not have read its configuration yet, so its model would be a guess."""
        provider = self._providers[agent]
        try:
            status = await asyncio.wait_for(provider.status(), STATUS_CHECK_TIMEOUT_SECONDS)
        except Exception:
            logger.warning(
                "Could not read the status of %s (asked again on the next turn)",
                agent,
                exc_info=True,
            )
            return
        if not status.available or not status.model:
            logger.info("The model of %s is not known yet: asked again on the next turn", agent)
            return
        self._models.setdefault(agent, status.model)
        self._identities.setdefault(agent, f"{provider.mode}:{status.model}")

    async def _prepare_context(self, turn: _Turn, threshold: int | None) -> TurnContext:
        """Canonical history of the conversation, compacted if it is too long."""
        history = await self._store.get_history(turn.conversation_id)
        context = context_from_history(history)
        limit = threshold if threshold is not None else self._config.compaction_threshold_tokens
        cut = compaction_cut(
            context, threshold=limit, keep_recent=self._config.keep_recent_messages
        )
        if cut is None:
            return context
        turn.emit(PhaseChanged(turn.request_id, "compaction", 0))
        result = await compact(
            context,
            cut,
            conversation_id=turn.conversation_id,
            providers=self._providers,
            store=self._store,
            max_output_tokens=self._config.summary_max_output_tokens,
            models=turn.request.fast_models,
            price_overrides=turn.prices,
        )
        if result.billed:
            # Billed even when the summary came back empty: part of the turn's usage.
            turn.accounting.add_spent(result.usage)
            turn.compaction_usage = result.usage
        if result.context is None:
            return context
        turn.accounting.compaction_per_request = result.tokens_removed
        return result.context

    async def _finish(
        self,
        turn: _Turn,
        consensus: Consensus | None,
        cache_key: str | None,
        stop_reason: RefineStopReason | None = None,
    ) -> None:
        await self._record_savings(turn)
        # Only whole, complete turns are replayed: never one with a failed, degraded or
        # cut-off message, nor one whose key does not say which models answered, nor one
        # where ChatGPT read a PDF that another turn may check (see _Turn.recheck).
        complete = (
            not turn.degraded and not turn.failed_agents and not turn.truncated and not turn.recheck
        )
        if cache_key is not None and complete and turn.stored:
            try:
                await self._store.cache_put(
                    cache_key,
                    CachedTurn(
                        mode=turn.request.mode,
                        messages=tuple(turn.stored),
                        tokens=replay_tokens(turn.stored),
                    ),
                    self._clock() + timedelta(seconds=self._config.cache_ttl_seconds),
                )
            except Exception:
                logger.exception("Could not store turn %s in the cache", turn.turn_id)
        outcome = await self._settle(
            turn, "completed", consensus=consensus, stop_reason=stop_reason
        )
        turn.emit(
            TurnCompleted(
                turn.request_id,
                turn.conversation_id,
                turn.turn_id,
                outcome.final_message_ids,
                outcome.usage,
                outcome.savings,
                consensus,
                cached=False,
                stop_reason=outcome.stop_reason,
            )
        )

    async def _fail(self, turn: _Turn, error: ErrorInfo) -> None:
        """End the turn as failed: its outcome, then ``turn.failed`` with its total."""
        outcome = await self._settle(turn, "failed", error=error)
        turn.emit(TurnFailed(turn.request_id, error, outcome.usage))

    async def _settle(
        self,
        turn: _Turn,
        status: TurnStatus,
        *,
        error: ErrorInfo | None = None,
        consensus: Consensus | None = None,
        cached: bool = False,
        stop_reason: RefineStopReason | None = None,
    ) -> TurnOutcome:
        """How the turn ended (docs/adr/0007-turn-outcome.md), decided once: a later
        call (a cancellation while the outcome is being written) gets the same outcome.
        It is reported to ``on_outcome`` and written on the question, if it exists, in a
        task of its own that the caller waits for through ``asyncio.shield``: cancelling
        the turn meanwhile stops the wait, never the write.

        Only a turn whose messages are all stored records savings, right before it
        completes, so a failed or cancelled turn has none; one cancelled while its saving
        rows were being written keeps them (the rows are written in full, see
        :meth:`_record_savings`), and its outcome is stored after them.

        ``stop_reason``: why a refine turn ended with its last version (a cancelled one
        too, once that version is stored)."""
        if turn.outcome is None:
            accounting = turn.accounting
            done = status == "completed"
            saved = done or turn.savings_write is not None
            turn.outcome = TurnOutcome(
                status=status,
                usage=accounting.usage,
                savings=accounting.savings() if saved else Savings(),
                error=error if status == "failed" else None,
                failures=tuple(turn.failures),
                consensus=consensus if done else None,
                final_message_ids=tuple(sorted(turn.final_ids)),
                cached=done and cached,
                stop_reason=stop_reason if status != "failed" else None,
            )
            if turn.on_outcome is not None:
                try:
                    turn.on_outcome(turn.outcome)
                except Exception:
                    logger.exception("The outcome callback of turn %s failed", turn.request_id)
            if turn.turn_id:
                turn.outcome_write = self._write(
                    self._write_outcome(turn.turn_id, turn.outcome, after=turn.savings_write),
                    name=f"outcome-{turn.request_id}",
                )
        if turn.outcome_write is not None:
            await asyncio.shield(turn.outcome_write)
        return turn.outcome

    def _write(self, write: Coroutine[Any, Any, None], *, name: str) -> asyncio.Task[None]:
        """Run a write of how a turn ended (its outcome, its saving rows) in a task of
        its own, kept until it ends: the turn waits for it through ``asyncio.shield``,
        so a cancellation meanwhile stops the wait, never the write."""
        task = asyncio.create_task(write, name=name)
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)
        return task

    async def _write_outcome(
        self, question_id: int, outcome: TurnOutcome, *, after: asyncio.Task[None] | None
    ) -> None:
        """Store an outcome, once the saving rows it counts (``after``, if any) are
        written; a failure only leaves the question's outcome open (null)."""
        if after is not None and not after.done():
            await asyncio.wait((after,))
        try:
            await self._store.set_turn_outcome(question_id, outcome)
        except Exception:
            logger.exception("Could not store the outcome of turn %s", question_id)

    async def _record_savings(self, turn: _Turn) -> None:
        """Record the savings of a turn whose messages are all stored (one row per kind),
        right before it completes. The rows are written in a task of their own that the
        turn waits for through ``asyncio.shield``: once started they are all written, and
        a turn cancelled meanwhile keeps them in its outcome, so the outcome and the rows
        the dashboard counts agree."""
        records = turn.accounting.saving_records(turn.conversation_id, turn.turn_id)
        if not records:
            return
        turn.savings_write = self._write(
            self._write_savings(records), name=f"savings-{turn.request_id}"
        )
        await asyncio.shield(turn.savings_write)

    async def _write_savings(self, records: Sequence[SavingRecord]) -> None:
        for record in records:
            try:
                await self._store.record_saving(record)
            except Exception:
                logger.exception("Could not record a %s saving", record.kind)

    # -- modes -----------------------------------------------------------------------

    async def _solo(self, turn: _Turn) -> bool:
        agent = turn.request.target
        turn.emit(PhaseChanged(turn.request_id, "answer", 0))
        prompt = answer_prompt(turn.question, turn.attachments)
        outcome = await self._call(
            turn,
            agent=agent,
            kind="answer",
            round_=0,
            request=self._context_request(turn, agent, prompt, "answer"),
            final=True,
        )
        if outcome.ok:
            return True
        kind = outcome.error.kind if outcome.error else "unavailable"
        failed = t("engine.error.agent_failed", agent=AGENT_LABELS[agent])
        await self._fail(turn, ErrorInfo(kind, failed))
        return False

    async def _duel(self, turn: _Turn) -> bool:
        turn.emit(PhaseChanged(turn.request_id, "answer", 0))
        prompt = answer_prompt(turn.question, turn.attachments)
        outcomes = await self._parallel(
            {
                agent: self._call(
                    turn,
                    agent=agent,
                    kind="answer",
                    round_=0,
                    request=self._context_request(turn, agent, prompt, "answer"),
                    final=True,
                )
                for agent in AGENTS
            }
        )
        if any(outcome.ok for outcome in outcomes.values()):
            return True
        await self._fail_all(turn, outcomes)
        return False

    async def _debate(self, turn: _Turn) -> Consensus | None:
        """Debate turn; returns the consensus, or None if the turn failed."""
        options = turn.request.options.debate
        rounds = options.rounds
        turn.emit(PhaseChanged(turn.request_id, "answer", 0))
        self._prewarm_next(turn, revision=rounds > 0)
        outcomes = await self._parallel(
            {
                agent: self._call(
                    turn,
                    agent=agent,
                    kind="answer",
                    round_=0,
                    request=self._context_request(
                        turn,
                        agent,
                        debate_answer_prompt(agent, turn.question, turn.attachments),
                        "answer",
                    ),
                    final=False,
                )
                for agent in AGENTS
            }
        )
        survivors = [agent for agent in AGENTS if outcomes[agent].ok]
        if not survivors:
            await self._fail_all(turn, outcomes)
            return None
        answers = _Answers(text={}, cut={})
        for agent in survivors:
            answers.take(outcomes[agent])
        if len(survivors) == 1:
            # One agent is down: its partner's answer becomes the final one, no more calls.
            consensus = Consensus(reached=False, round=0, scores={})
            turn.emit(PhaseChanged(turn.request_id, "synthesis", 0))
            await self._store_degraded_synthesis(turn, survivors[0], answers, 0, consensus)
            return consensus

        critiques: dict[AgentName, str | None] = dict.fromkeys(AGENTS)
        scores: dict[AgentName, int] = {}
        reached = False
        last_round = 0
        for round_ in range(1, rounds + 1):
            turn.emit(PhaseChanged(turn.request_id, "revision", round_))
            self._prewarm_next(turn, revision=round_ < rounds)
            previous = _Answers(text=dict(answers.text), cut=dict(answers.cut))
            outcomes = await self._parallel(
                {
                    agent: self._call(
                        turn,
                        agent=agent,
                        kind="revision",
                        round_=round_,
                        request=self._revision_request(turn, agent, previous),
                        final=False,
                        previous=previous.text[agent],
                    )
                    for agent in AGENTS
                }
            )
            last_round = round_
            round_scores: dict[AgentName, int] = {}
            for agent, outcome in outcomes.items():
                if not outcome.ok:
                    continue  # keep that agent's last answer
                answers.take(outcome)
                critiques[agent] = outcome.critique
                if outcome.agreement is not None:
                    round_scores[agent] = outcome.agreement
            scores.update(round_scores)
            if not any(outcome.ok for outcome in outcomes.values()):
                break  # both failed: more rounds are pointless, go to the synthesis
            if len(round_scores) == len(AGENTS) and all(
                score >= options.consensus_threshold for score in round_scores.values()
            ):
                reached = True
                turn.accounting.add_early_stop(rounds - round_)
                break

        consensus = Consensus(reached=reached, round=last_round, scores=scores)
        turn.emit(PhaseChanged(turn.request_id, "synthesis", last_round))
        await self._synthesize(turn, answers, critiques, last_round, consensus)
        return consensus

    async def _synthesize(
        self,
        turn: _Turn,
        answers: _Answers,
        critiques: Mapping[AgentName, str | None],
        round_: int,
        consensus: Consensus,
    ) -> None:
        synthesizer = turn.request.options.debate.synthesizer
        order = [synthesizer, other_agent(synthesizer)]
        if synthesizer in turn.failed_agents:
            order.reverse()  # it already failed in this turn: try the other one first
        prompt = synthesis_prompt(
            turn.question,
            answers.text,
            critiques,
            answers.cut,
            turn.attachments,
            reading_note=self._reading_note(turn),
        )
        extra: dict[str, JsonValue] = {"consensus": _consensus_json(consensus)}
        for agent in order:
            outcome = await self._call(
                turn,
                agent=agent,
                kind="synthesis",
                round_=round_,
                request=self._context_request(turn, agent, prompt, "synthesis"),
                final=True,
                extra_meta=extra,
            )
            if outcome.ok:
                return
        # Nobody could synthesize: the latest answer of the healthier agent is final.
        await self._store_degraded_synthesis(turn, order[0], answers, round_, consensus)

    async def _fail_all(self, turn: _Turn, outcomes: Mapping[AgentName, _Outcome]) -> None:
        kinds = {outcome.error.kind for outcome in outcomes.values() if outcome.error}
        kind = kinds.pop() if len(kinds) == 1 else "unavailable"
        await self._fail(turn, ErrorInfo(kind, t("engine.error.both_failed")))

    async def _store_degraded_synthesis(
        self,
        turn: _Turn,
        agent: AgentName,
        answers: _Answers,
        round_: int,
        consensus: Consensus,
    ) -> None:
        """Store an existing answer as the final one, without calling any model (it keeps
        the mark of an answer that was cut off)."""
        turn.degraded = True
        content = answers.text[agent]
        truncated = agent in answers.cut
        stream_id = uuid.uuid4().hex
        model = self._model_name(turn, agent)
        turn.emit(StreamStarted(turn.request_id, stream_id, agent, "synthesis", round_, model))
        turn.emit(StreamDelta(turn.request_id, stream_id, "text", content))
        meta: dict[str, JsonValue] = {
            "model": model,
            "usage": _usage_json(Usage()),
            "latency_ms": 0,
            "ttft_ms": None,
            "cached": False,
            "degraded": True,
            "consensus": _consensus_json(consensus),
            "savings": _savings_json(turn.accounting.savings()),
        }
        if truncated:
            _set_truncated(meta, answers.cut[agent])
            turn.truncated = True
        # ChatGPT's answer, written from its reading of the PDFs: it keeps saying so.
        pdf_reading = self._reading_of(turn, agent)
        if pdf_reading:
            meta["pdf_reading"] = _pdf_reading_json(pdf_reading)
        _set_unstored(meta, turn.accounting)
        message_id = await self._store.add_message(
            NewMessage(
                conversation_id=turn.conversation_id,
                kind="synthesis",
                content=content,
                turn_id=turn.turn_id,
                agent=agent,
                round=round_,
                final=True,
                meta=meta,
            )
        )
        turn.final_ids.append(message_id)
        turn.emit(
            StreamCompleted(
                turn.request_id,
                stream_id,
                message_id,
                Usage(),
                0,
                None,
                truncated=truncated,
                finish_reason=answers.cut[agent] if truncated else None,
                pdf_reading=pdf_reading,
            )
        )

    # -- refine («Perfecciona») ----------------------------------------------------------

    async def _refine(self, turn: _Turn) -> RefineStopReason | None:
        """Refine turn (docs/adr/0010-refine-mode.md): why it ended, once its last
        version is stored as the final message; None if it failed (no answer came back).

        Round 0: both agents answer the brief, and one whose answer fails takes no
        further part in the turn. Round 1: the editor merges the answers into version 1
        (:meth:`_merge`). Each later round starts unless the owner asked the turn to stop,
        its total reached the budget or the rounds ran out (:meth:`_stop_before`); it may
        end the turn itself (:meth:`_refine_round`)."""
        state = _Refine(turn.request.options.refine)
        turn.refine = state
        turn.emit(PhaseChanged(turn.request_id, "answer", 0))
        self._prewarm_refine(turn, state, "synthesis")
        answers = await self._parallel(
            {
                agent: self._call(
                    turn,
                    agent=agent,
                    kind="answer",
                    round_=0,
                    request=self._context_request(
                        turn,
                        agent,
                        refine_answer_prompt(agent, turn.question, turn.attachments),
                        "answer",
                    ),
                    final=False,
                )
                for agent in AGENTS
            }
        )
        state.active = tuple(agent for agent in AGENTS if answers[agent].ok)
        if not state.active:
            await self._fail_all(turn, answers)
            return None
        self._announce_stop(turn, state)
        await self._merge(turn, state, answers)
        reason: RefineStopReason | None = None
        round_ = 2
        while reason is None:
            reason = self._stop_before(turn, state, round_)
            if reason is None:
                reason = await self._refine_round(turn, state, round_)
            round_ += 1
        state.stop_reason = reason
        await asyncio.shield(self._final_write(turn, state))
        return reason

    @staticmethod
    def _announce_stop(turn: _Turn, state: _Refine) -> bool:
        """Whether the owner asked the refine turn to stop (``turn.stop``, read between
        calls only: a call in course is never cut). The first time it is seen,
        ``turn.stopping`` says the round the turn ends after: the one in course, and at
        least round 1, so that the turn ends with a version."""
        if turn.stop is None or not turn.stop.is_set():
            return False
        if not state.announced:
            state.announced = True
            turn.emit(TurnStopping(turn.request_id, max(state.round, 1)))
        return True

    def _stop_before(self, turn: _Turn, state: _Refine, round_: int) -> RefineStopReason | None:
        """Why a refine turn ends before ``round_`` starts, if it does: the owner asked it
        to stop, its total reached its budget (when both are known: the fake models, or
        a model without a price, have no cost) or the rounds ran out."""
        if self._announce_stop(turn, state):
            return "owner"
        budget, spent = turn.request.refine_budget_usd, turn.accounting.usage.cost_usd
        if budget is not None and spent is not None and spent >= budget:
            return "budget"
        if round_ > state.options.max_rounds:
            return "max_rounds"
        return None

    @staticmethod
    def _editors(state: _Refine) -> list[AgentName]:
        """Who writes a version, in order: the editor and then the other agent, the ones
        that take part in the turn; one whose merge or edit already failed goes last, so
        that a provider that is down is not waited for every round."""
        editor = state.options.editor
        order = [agent for agent in (editor, other_agent(editor)) if agent in state.active]
        return sorted(order, key=lambda agent: agent in state.edit_failed)

    async def _merge(
        self, turn: _Turn, state: _Refine, answers: Mapping[AgentName, _Outcome]
    ) -> None:
        """Round 1: the editors (:meth:`_editors`) in turn merge the answers into version
        1, until one writes it whole. If none does, version 1 is a copy of the editor's
        answer (else the other's), stored without any call (:meth:`_copy_answer`). The
        merge carries the conversation and every attachment whole, like a synthesis.

        A merge over the owner's word limit gets one retry, shorter, by the same agent
        (:meth:`_shorten_first`); with no earlier version to keep, that one is version 1
        even if it still passes the limit, and the later rounds' edits must fit it. That
        retry failing hands the merge to the other agent, as a failed merge does. While
        it runs, the merge is the last complete version: a turn cancelled meanwhile keeps
        it as its final answer."""
        state.round = 1
        turn.emit(PhaseChanged(turn.request_id, "edit", 1))
        self._prewarm_refine(turn, state, "revision")
        start = turn.accounting.usage
        prompt = refine_merge_prompt(
            turn.question,
            {agent: answers[agent].content for agent in state.active},
            turn.attachments,
            incomplete={agent for agent in state.active if answers[agent].truncated},
            max_words=state.options.max_words,
            reading_note=self._reading_note(turn),
        )
        for agent in self._editors(state):
            reader = _VersionReader(
                number=1, current=None, budget_words=None, max_words=state.options.max_words
            )
            outcome = await self._call(
                turn,
                agent=agent,
                kind="revision",
                round_=1,
                request=self._context_request(turn, agent, prompt, "synthesis"),
                final=False,
                reader=reader,
            )
            self._announce_stop(turn, state)
            if outcome.ok and reader.reason == "over_budget":
                # Version 1 while it is shortened, should the turn be cancelled meanwhile;
                # once the shortening ends, it decides what version 1 is (round 1 has
                # written nothing else to the changelog).
                self._accept(state, reader, outcome)
                reader, outcome = await self._shorten_first(turn, state, agent, reader)
                state.current = None
                state.changelog.clear()
            if not outcome.ok:
                state.edit_failed.add(agent)
            elif reader.accepted:
                self._accept(state, reader, outcome)
                break
        if state.current is None:
            await self._copy_answer(turn, state, answers)
        changes = tuple(change for number, change in state.changelog if number == 1)
        self._refine_round_ended(turn, state, start, accepted=True, changes=changes)

    async def _shorten_first(
        self, turn: _Turn, state: _Refine, agent: AgentName, draft: _VersionReader
    ) -> tuple[_VersionReader, _Outcome]:
        """The one retry of a merge over the owner's word limit: the same version, shorter,
        by the same agent, self-contained like a round's shortening. Its reader, and the
        outcome of its call."""
        attachments = self._revision_attachments(turn)
        reader = _VersionReader(
            number=1,
            current=None,
            budget_words=draft.budget_words,
            max_words=state.options.max_words,
            keep_over=True,
        )
        prompt = refine_shorten_prompt(
            turn.question,
            draft.text,
            draft.changes,
            draft.words,
            draft.budget_words,
            attachments,
        )
        outcome = await self._call(
            turn,
            agent=agent,
            kind="revision",
            round_=1,
            request=self._refine_request(turn, agent, prompt, "synthesis", attachments),
            final=False,
            reader=reader,
        )
        self._announce_stop(turn, state)
        return reader, outcome

    async def _copy_answer(
        self, turn: _Turn, state: _Refine, answers: Mapping[AgentName, _Outcome]
    ) -> None:
        """Version 1 when no merge came back whole: the editor's answer (else the other
        agent's) as it is, stored without any call (``meta.refine.copied_from``), even
        over the owner's word limit: there is no earlier version, nor anyone to shorten
        it (both merges failed)."""
        editor = state.options.editor
        agent = editor if editor in state.active else state.active[0]
        answer = answers[agent]
        count = words(answer.content)
        state.budget_words = _budget_words(state.options.max_words, count)
        refine: dict[str, JsonValue] = {
            "role": "version",
            "version": 1,
            "words": count,
            "budget_words": state.budget_words,
            "accepted": True,
            "reason": None,
            "reason_code": None,
            "changelog": [],
            "copied_from": answer.message_id,
        }
        model = answer.model or self._model_name(turn, agent)
        cut = answer.finish_reason if answer.truncated else None
        message_id = await self._store_copy(
            turn,
            agent=agent,
            kind="revision",
            round_=1,
            content=answer.content,
            model=model,
            refine=refine,
            truncated=answer.truncated,
            finish_reason=cut,
        )
        state.current = _Version(
            1, answer.content, count, agent, 1, message_id, model, answer.truncated, cut
        )

    @staticmethod
    def _accept(state: _Refine, reader: _VersionReader, outcome: _Outcome) -> None:
        """The version a call wrote becomes the current one, and its changes go to the
        turn's changelog. Version 1 sets the word budget of the turn."""
        assert outcome.message_id is not None
        number = state.current.number + 1 if state.current is not None else 1
        state.current = _Version(
            number,
            reader.text,
            reader.words,
            outcome.agent,
            state.round,
            outcome.message_id,
            outcome.model,
        )
        if number == 1:
            state.budget_words = reader.budget_words
        state.changelog.extend((number, change) for change in reader.changes)

    async def _refine_round(
        self, turn: _Turn, state: _Refine, round_: int
    ) -> RefineStopReason | None:
        """Round ``round_`` (from 2 on): every agent in the turn reviews the current
        version and, unless none proposes a change, the editor writes the next one
        (:meth:`_edit`). A failed review sits that agent out of the round. Why the round
        ends the turn, if it does: nothing to change ``REFINE_CONVERGENCE_ROUNDS`` rounds
        in a row ("unchanged", always), every agent scoring it at least the threshold
        without a defect as many rounds in a row ("converged", when the owner wants it),
        or no review or no version coming back ("failed")."""
        state.round = round_
        start = turn.accounting.usage
        current = state.current
        assert current is not None
        turn.emit(PhaseChanged(turn.request_id, "review", round_))
        self._prewarm_refine(turn, state, "synthesis")
        attachments = self._revision_attachments(turn)
        tail = changelog_tail(state.changelog)
        note = self._reading_note(turn)
        readers = {agent: _ReviewReader() for agent in state.active}
        outcomes = await self._parallel(
            {
                agent: self._call(
                    turn,
                    agent=agent,
                    kind="revision",
                    round_=round_,
                    request=self._refine_request(
                        turn,
                        agent,
                        refine_review_prompt(
                            agent,
                            turn.question,
                            current.text,
                            current.words,
                            state.budget_words,
                            tail,
                            attachments,
                            reading_note=note,
                        ),
                        "revision",
                        attachments,
                    ),
                    final=False,
                    reader=readers[agent],
                )
                for agent in state.active
            }
        )
        self._announce_stop(turn, state)
        reviews = {
            agent: review
            for agent, reader in readers.items()
            if outcomes[agent].ok and (review := reader.review) is not None
        }
        if not reviews:
            self._refine_round_ended(turn, state, start, accepted=False, reason="failed_round")
            return "failed"
        accepted = False
        reason: RefineReasonCode | None = "nothing_to_change"
        changes: tuple[RefineChange, ...] = ()
        if all(review.unchanged for review in reviews.values()):
            state.unchanged_rounds += 1
        else:
            state.unchanged_rounds = 0
            turn.emit(PhaseChanged(turn.request_id, "edit", round_))
            self._prewarm_refine(turn, state, "revision")
            edited = await self._edit(turn, state, reviews)
            if edited is None:
                self._refine_round_ended(
                    turn, state, start, accepted=False, reason="failed_round", reviews=reviews
                )
                return "failed"
            accepted, reason, changes = edited
        options = state.options
        converging = set(reviews) == set(state.active) and all(
            review.score is not None
            and review.score >= options.convergence_threshold
            and all(change.kind != "defect" for change in review.changes)
            for review in reviews.values()
        )
        state.converging_rounds = state.converging_rounds + 1 if converging else 0
        stop: RefineStopReason | None = None
        if state.unchanged_rounds >= REFINE_CONVERGENCE_ROUNDS:
            stop = "unchanged"
        elif options.stop_on_convergence and state.converging_rounds >= REFINE_CONVERGENCE_ROUNDS:
            stop = "converged"
        self._refine_round_ended(
            turn,
            state,
            start,
            accepted=accepted,
            reason=reason,
            changes=changes,
            reviews=reviews,
            converged=stop == "converged",
        )
        return stop

    async def _edit(
        self, turn: _Turn, state: _Refine, reviews: Mapping[AgentName, ReviewParse]
    ) -> tuple[bool, RefineReasonCode | None, tuple[RefineChange, ...]] | None:
        """The edit of a round: the editors (:meth:`_editors`) in turn, until one replies,
        write the next version from the changes the reviews proposed. Whether it was
        accepted, why not, and its changes; None when every editor failed.

        A version over the word budget gets one retry, shorter, by the same agent;
        still over (or that retry failing), the round keeps the current version. A
        version without a complete section, or the same as the current one, is not
        accepted either, and nobody else edits: it was a reply, not a failure."""
        current = state.current
        assert current is not None
        attachments = self._revision_attachments(turn)
        prompt = refine_edit_prompt(
            turn.question,
            current.text,
            {agent: review.changes for agent, review in reviews.items()},
            changelog_tail(state.changelog),
            current.words,
            state.budget_words,
            attachments,
        )
        for agent in self._editors(state):
            reader = self._version_reader(state)
            outcome = await self._call(
                turn,
                agent=agent,
                kind="revision",
                round_=state.round,
                request=self._refine_request(turn, agent, prompt, "synthesis", attachments),
                final=False,
                reader=reader,
            )
            self._announce_stop(turn, state)
            if not outcome.ok:
                state.edit_failed.add(agent)
                continue
            if reader.reason == "over_budget":
                draft, reader = reader, self._version_reader(state)
                shorten = refine_shorten_prompt(
                    turn.question,
                    draft.text,
                    draft.changes,
                    draft.words,
                    state.budget_words,
                    attachments,
                )
                outcome = await self._call(
                    turn,
                    agent=agent,
                    kind="revision",
                    round_=state.round,
                    request=self._refine_request(turn, agent, shorten, "synthesis", attachments),
                    final=False,
                    reader=reader,
                )
                self._announce_stop(turn, state)
                if not outcome.ok:
                    state.edit_failed.add(agent)
                    return False, "over_budget", ()
            if not reader.accepted:
                return False, reader.reason, ()
            self._accept(state, reader, outcome)
            return True, None, reader.changes
        return None

    @staticmethod
    def _version_reader(state: _Refine) -> _VersionReader:
        """The reader of the next version of a refine turn (an edit or its shortening)."""
        current = state.current
        assert current is not None
        return _VersionReader(
            number=current.number + 1,
            current=current,
            budget_words=state.budget_words,
            max_words=state.options.max_words,
        )

    def _refine_round_ended(
        self,
        turn: _Turn,
        state: _Refine,
        start: Usage,
        *,
        accepted: bool,
        reason: RefineReasonCode | None = None,
        changes: Sequence[RefineChange] = (),
        reviews: Mapping[AgentName, ReviewParse] | None = None,
        converged: bool = False,
    ) -> None:
        """``refine.round``: how the round in course ended, with what its calls billed
        (the turn's total since ``start``) and the turn's total so far; when it wrote no
        version, why (``reason``, a code made into the turn's language here)."""
        version = state.current
        assert version is not None
        reviewed = reviews or {}
        code = None if accepted else reason
        turn.emit(
            RefineRound(
                turn.request_id,
                round=state.round,
                version=version.number,
                accepted=accepted,
                words=version.words,
                budget_words=state.budget_words,
                usage=_usage_since(start, turn.accounting.usage),
                total=turn.accounting.usage,
                reason=None if code is None else refine_reason(code),
                reason_code=code,
                changes=tuple(changes) if accepted else (),
                proposals={agent: len(review.changes) for agent, review in reviewed.items()},
                scores={agent: review.score for agent, review in reviewed.items()},
                converged=converged,
            )
        )

    def _refine_request(
        self,
        turn: _Turn,
        agent: AgentName,
        prompt: str,
        purpose: Purpose,
        attachments: tuple[Attachment, ...],
    ) -> GenerationRequest:
        """A self-contained request of a refine round (a review, an edit, its shortening):
        no history, and the attachments as the revisions get them, round after round."""
        return GenerationRequest(
            system=system_prompt(agent),
            prompt=prompt,
            purpose=purpose,
            model=turn.request.models.get(agent),
            max_output_tokens=self._config.max_output_tokens,
            reasoning="default",
            attachments=attachments,
        )

    def _prewarm_refine(self, turn: _Turn, state: _Refine, purpose: Purpose) -> None:
        """Hint the providers of a refine turn's next calls while the current ones run:
        the reviews (every agent in the turn) or the next version (its first editor)."""
        if purpose == "revision":
            agents = list(state.active or AGENTS)
        else:
            agents = self._editors(state)[:1] if state.active else [state.options.editor]
        for agent in agents:
            request = GenerationRequest(
                system=system_prompt(agent),
                prompt="",
                purpose=purpose,
                model=turn.request.models.get(agent),
                max_output_tokens=self._config.max_output_tokens,
                reasoning="default",
            )
            self._prewarm(turn, agent, request)

    def _final_write(self, turn: _Turn, state: _Refine) -> asyncio.Task[None]:
        """The write of a refine turn's last version as its final message, started once,
        in a task of its own (shielded from cancellation): the turn's last step and a
        cancellation meanwhile wait for the same write."""
        if state.final_write is None:
            state.final_write = self._write(
                self._store_final(turn, state), name=f"final-{turn.request_id}"
            )
        return state.final_write

    async def _store_final(self, turn: _Turn, state: _Refine) -> None:
        """The last version as the final message of a refine turn: a synthesis by the
        agent that wrote it, in the turn's last round, stored without any call
        (``meta.copied_from``)."""
        version = state.current
        assert version is not None
        refine: dict[str, JsonValue] = {
            "role": "final",
            "version": version.number,
            "words": version.words,
            "budget_words": state.budget_words,
            "stop_reason": state.stop_reason or "owner",
        }
        await self._store_copy(
            turn,
            agent=version.agent,
            kind="synthesis",
            round_=state.round,
            content=version.text,
            model=version.model,
            refine=refine,
            copied_from=version.message_id,
            truncated=version.truncated,
            finish_reason=version.finish_reason,
        )

    async def _close_refine(self, turn: _Turn) -> None:
        """A cancelled refine turn's last writes, in a task of their own: its last version
        as the final message (stopped by the owner, unless the turn was already ending for
        another reason), then its outcome, cancelled, with that final message."""
        state = turn.refine
        assert state is not None
        if state.stop_reason is None:
            state.stop_reason = "owner"
        try:
            await asyncio.shield(self._final_write(turn, state))
        except Exception:
            logger.exception("Could not store the last version of turn %s", turn.request_id)
        await self._settle(turn, "cancelled", stop_reason=state.stop_reason)

    async def _store_copy(
        self,
        turn: _Turn,
        *,
        agent: AgentName,
        kind: MessageKind,
        round_: int,
        content: str,
        model: str,
        refine: dict[str, JsonValue],
        copied_from: int | None = None,
        truncated: bool = False,
        finish_reason: str | None = None,
    ) -> int:
        """Store a refine turn's message that copies one already stored, without any call
        (version 1 from an answer, the final message from the last version), and stream it
        like the others: a version as the answer, the final message as text. Only the
        final message (a synthesis) is final; it carries the turn's savings."""
        final = kind == "synthesis"
        stream_id = uuid.uuid4().hex
        turn.emit(StreamStarted(turn.request_id, stream_id, agent, kind, round_, model))
        turn.emit(StreamDelta(turn.request_id, stream_id, "text" if final else "answer", content))
        meta: dict[str, JsonValue] = {
            "model": model,
            "usage": _usage_json(Usage()),
            "latency_ms": 0,
            "ttft_ms": None,
            "cached": False,
        }
        if copied_from is not None:
            meta["copied_from"] = copied_from
        if truncated:
            _set_truncated(meta, finish_reason)
            turn.truncated = True
        # ChatGPT's text, written from its reading of the PDFs: it keeps saying so.
        pdf_reading = self._reading_of(turn, agent)
        if pdf_reading:
            meta["pdf_reading"] = _pdf_reading_json(pdf_reading)
        meta["refine"] = refine
        if final:
            meta["savings"] = _savings_json(turn.accounting.savings())
            _set_unstored(meta, turn.accounting)
        message_id = await self._store.add_message(
            NewMessage(
                conversation_id=turn.conversation_id,
                kind=kind,
                content=content,
                turn_id=turn.turn_id,
                agent=agent,
                round=round_,
                final=final,
                meta=meta,
            )
        )
        if final:
            turn.final_ids.append(message_id)
        turn.emit(
            StreamCompleted(
                turn.request_id,
                stream_id,
                message_id,
                Usage(),
                0,
                None,
                truncated=truncated,
                finish_reason=finish_reason if truncated else None,
                pdf_reading=pdf_reading,
                refine=refine,
            )
        )
        return message_id

    # -- cache replay ------------------------------------------------------------------

    async def _replay(self, turn: _Turn, cached: CachedTurn) -> None:
        phase: tuple[str, int] | None = None
        consensus: Consensus | None = None
        turn.accounting.cache = cached.tokens
        turn.accounting.cache_cost = replay_cost_usd(cached.messages, turn.prices)
        savings = turn.accounting.savings()
        for original in cached.messages:
            message = replayed_message(
                original, conversation_id=turn.conversation_id, turn_id=turn.turn_id
            )
            if message.agent is None or message.kind == "question":
                continue
            if (message.kind, message.round) != phase:
                phase = (message.kind, message.round)
                turn.emit(PhaseChanged(turn.request_id, _PHASE_OF[message.kind], message.round))
            meta = message.meta
            stream_id = uuid.uuid4().hex
            model = meta.get("model")
            turn.emit(
                StreamStarted(
                    turn.request_id,
                    stream_id,
                    message.agent,
                    message.kind,
                    message.round,
                    model if isinstance(model, str) else "",
                )
            )
            if message.kind == "revision":
                critique = meta.get("critique")
                if isinstance(critique, str) and critique:
                    turn.emit(StreamDelta(turn.request_id, stream_id, "critique", critique))
                turn.emit(StreamDelta(turn.request_id, stream_id, "answer", message.content))
            else:
                turn.emit(StreamDelta(turn.request_id, stream_id, "text", message.content))
            if message.final:
                # The replay's own savings (the copied turn's were dropped).
                meta = {**meta, "savings": _savings_json(savings)}
            message_id = await self._store.add_message(replace(message, meta=meta))
            if message.final:
                turn.final_ids.append(message_id)
            if message.kind == "synthesis":
                consensus = _consensus_from_json(meta.get("consensus")) or consensus
            agreement = meta.get("agreement")
            turn.emit(
                StreamCompleted(
                    turn.request_id,
                    stream_id,
                    message_id,
                    Usage(),
                    0,
                    0,
                    agreement=(
                        agreement
                        if isinstance(agreement, int) and not isinstance(agreement, bool)
                        else None
                    ),
                    unchanged=meta.get("unchanged") is True,
                    truncated=meta.get("truncated") is True,
                    finish_reason=_meta_text(meta, "finish_reason"),
                    unchanged_note=_meta_text(meta, "unchanged_note"),
                    # How the original ChatGPT read the PDFs (nothing is checked again).
                    pdf_reading=_pdf_reading_from_json(meta.get("pdf_reading")),
                )
            )
        if turn.request.mode == "debate" and consensus is None:
            consensus = Consensus(reached=False, round=phase[1] if phase else 0, scores={})
        await self._record_savings(turn)
        outcome = await self._settle(turn, "completed", consensus=consensus, cached=True)
        turn.emit(
            TurnCompleted(
                turn.request_id,
                turn.conversation_id,
                turn.turn_id,
                outcome.final_message_ids,
                outcome.usage,
                outcome.savings,
                consensus,
                cached=True,
            )
        )

    # -- Claude's check of the PDFs for ChatGPT -----------------------------------------

    async def _checked_pdfs(self, turn: _Turn) -> tuple[PdfReading, ...]:
        """Start Claude's check of the question's PDFs, once per turn, and wait for it:
        every call of a ChatGPT that cannot open PDFs waits for this same check. It runs
        in a task of its own (the turn's background, cancelled with the turn), so the
        calls that do not wait for it, Claude's answer first, go on meanwhile. How
        ChatGPT reads each PDF once it has ended."""
        if turn.check_task is None:
            task = asyncio.create_task(self._check_pdfs(turn), name=f"check-{turn.request_id}")
            turn.check_task = task
            turn.background.add(task)
            task.add_done_callback(turn.background.discard)
        await asyncio.shield(turn.check_task)
        return turn.pdf_reading or ()

    @staticmethod
    def _with_checks(turn: _Turn, attachments: tuple[Attachment, ...]) -> tuple[Attachment, ...]:
        """A request's attachments (the question's, in order, each in its call's mode) with
        the check of each PDF."""
        return tuple(
            replace(attachment, pdf_check=source.pdf_check)
            if attachment.kind == "pdf"
            else attachment
            for attachment, source in zip(attachments, turn.attachments, strict=True)
        )

    async def _check_pdfs(self, turn: _Turn) -> None:
        """Claude's check of the text of the question's PDFs (pdf_check.check_pdf), for a
        ChatGPT that cannot open them: CHECK_CONCURRENCY PDFs at a time, a file attached
        twice one after the other (the second reuses what the first stored), all within
        the engine's check timeout, past which the checks still running are cancelled and
        their PDFs are read unchecked. The start and the end of each PDF's check are
        events of the turn (``PdfCheckChanged``). Then the question's attachments carry
        their checks, so the later calls of every agent get them (the label notes), and
        :attr:`_Turn.pdf_reading` says how ChatGPT reads each PDF."""
        pdfs = [
            (index, attachment_id, attachment)
            for index, (attachment_id, attachment) in enumerate(
                zip(turn.request.attachments, turn.attachments, strict=True)
            )
            if attachment.kind == "pdf"
        ]
        outcomes: dict[int, CheckOutcome] = {}
        # What each PDF's calls have billed so far: a check cut short by the timeout too.
        spent = {index: Usage() for index, _, _ in pdfs}
        slots = asyncio.Semaphore(CHECK_CONCURRENCY)
        files: dict[str, asyncio.Lock] = {}

        async def check_one(index: int, attachment_id: int, attachment: Attachment) -> None:
            async with files.setdefault(attachment.sha256, asyncio.Lock()), slots:
                outcome = await self._check_pdf(turn, attachment_id, attachment, spent, index)
            outcomes[index] = outcome
            turn.emit(self._check_changed(turn, attachment_id, attachment, outcome))

        try:
            async with asyncio.timeout(self._check_timeout), asyncio.TaskGroup() as group:
                for index, attachment_id, attachment in pdfs:
                    group.create_task(check_one(index, attachment_id, attachment))
        except TimeoutError:
            logger.warning("Claude's check of the PDFs of turn %s took too long", turn.request_id)
        for index, attachment_id, attachment in pdfs:
            if index not in outcomes:
                late = t("engine.pdf_check.timed_out")
                outcome = CheckOutcome(None, False, spent[index], late, final=False)
                outcomes[index] = outcome
                turn.emit(self._check_changed(turn, attachment_id, attachment, outcome))
        turn.attachments = tuple(
            replace(attachment, pdf_check=outcomes[index].check)
            if index in outcomes
            else attachment
            for index, attachment in enumerate(turn.attachments)
        )
        turn.pdf_reading = tuple(
            _pdf_reading(attachment_id, attachment, outcomes[index])
            for index, attachment_id, attachment in pdfs
        )
        turn.recheck = any(not outcome.final for outcome in outcomes.values())

    async def _check_pdf(
        self,
        turn: _Turn,
        attachment_id: int,
        attachment: Attachment,
        spent: dict[int, Usage],
        index: int,
    ) -> CheckOutcome:
        """One PDF's check, with the turn's Claude model: its calls are billed to the turn
        (a usage row each, purpose "check", and the turn's total) and added to
        ``spent[index]``. An unexpected error leaves the PDF unchecked (it is logged)."""
        claude = self._providers.get("claude")

        async def record(
            model: str,
            usage: Usage,
            latency_ms: int,
            ttft_ms: int | None,
            error: ProviderError | None,
        ) -> Usage:
            model = model or self._models.get("claude", "")
            if error is not None and not is_billed(usage):
                priced = Usage()
            else:
                priced = replace(usage, cost_usd=estimate_cost_usd(model, usage, turn.prices))
            spent[index] += priced
            turn.accounting.add_spent(priced)
            await self._record_usage(
                turn,
                "claude",
                "check",
                model=model,
                usage=priced,
                latency_ms=latency_ms,
                ttft_ms=ttft_ms,
                error=error,
            )
            return priced

        def started() -> None:
            turn.emit(PdfCheckChanged(turn.request_id, attachment_id, attachment.name, "checking"))

        try:
            return await check_pdf(
                attachment,
                provider=claude,
                model=turn.request.models.get("claude"),
                store=self._store,
                record=record,
                on_start=started,
            )
        except Exception:
            logger.exception("Claude's check of a PDF of turn %s failed", turn.request_id)
            reason = t("engine.pdf_check.failed", message=t("engine.error.internal"))
            return CheckOutcome(None, False, spent[index], reason, final=False)

    @staticmethod
    def _check_changed(
        turn: _Turn, attachment_id: int, attachment: Attachment, outcome: CheckOutcome
    ) -> PdfCheckChanged:
        """The event of a PDF whose check has ended."""
        reading = _pdf_reading(attachment_id, attachment, outcome)
        return PdfCheckChanged(
            turn.request_id,
            attachment_id,
            attachment.name,
            "checked" if reading.checked else "unchecked",
            claude_pages=reading.claude_pages,
            hidden_pages=reading.hidden_pages,
            unchecked_pages=reading.unchecked_pages,
            reused=outcome.reused,
            usage=None if outcome.reused else outcome.usage,
            reason=outcome.reason,
        )

    def _reading_of(self, turn: _Turn, agent: AgentName) -> tuple[PdfReading, ...]:
        """How ``agent`` read the question's PDFs, when it cannot open them and this turn
        checked them for it (empty otherwise)."""
        if not turn.pdf_reading or reads_pdfs(self._providers[agent]):
            return ()
        return turn.pdf_reading

    @staticmethod
    def _reading_note(turn: _Turn) -> str:
        """What the revisions and the synthesis are told of how ChatGPT read the PDFs, when
        it cannot open them (the turn checked them for it); "" otherwise."""
        return pdf_reading_note(turn.attachments) if turn.pdf_reading else ""

    # -- model calls -------------------------------------------------------------------

    def _context_request(
        self, turn: _Turn, agent: AgentName, prompt: str, purpose: Purpose
    ) -> GenerationRequest:
        """A request carrying the conversation context and every attachment of the
        question whole (answers and synthesis)."""
        return GenerationRequest(
            system=system_prompt(agent),
            prompt=prompt,
            history=turn.context.history,
            context_summary=turn.context.summary,
            purpose=purpose,
            model=turn.request.models.get(agent),
            max_output_tokens=self._config.max_output_tokens,
            reasoning="default",
            attachments=turn.attachments,
        )

    @staticmethod
    def _revision_attachments(turn: _Turn) -> tuple[Attachment, ...]:
        """The attachments as the revisions get them: images and text files whole, the
        PDFs as ``pdf_in_revisions`` says (their extracted text, or the document). A PDF
        without any text (a scanned one) goes whole: as text, the revisions would get
        nothing to check the answers against."""
        mode = turn.request.pdf_in_revisions
        return tuple(
            replace(attachment, mode=mode)
            if attachment.kind == "pdf" and has_text(attachment)
            else attachment
            for attachment in turn.attachments
        )

    def _revision_request(
        self, turn: _Turn, agent: AgentName, answers: _Answers
    ) -> GenerationRequest:
        """Self-contained revision request: question + both answers (marked when cut
        off), no history; the attachments as :meth:`_revision_attachments` says."""
        other = other_agent(agent)
        attachments = self._revision_attachments(turn)
        return GenerationRequest(
            system=system_prompt(agent),
            prompt=revision_prompt(
                agent,
                turn.question,
                answers.text[agent],
                answers.text[other],
                own_incomplete=agent in answers.cut,
                other_incomplete=other in answers.cut,
                attachments=attachments,
                reading_note=self._reading_note(turn),
            ),
            purpose="revision",
            model=turn.request.models.get(agent),
            max_output_tokens=self._config.max_output_tokens,
            reasoning="default",
            attachments=attachments,
        )

    def _prewarm_next(self, turn: _Turn, *, revision: bool) -> None:
        """Hint the providers of the next debate phase while the current one streams."""
        if revision:
            for agent in AGENTS:
                request = GenerationRequest(
                    system=system_prompt(agent),
                    prompt="",
                    purpose="revision",
                    model=turn.request.models.get(agent),
                    max_output_tokens=self._config.max_output_tokens,
                    reasoning="default",
                )
                self._prewarm(turn, agent, request)
        else:
            agent = turn.request.options.debate.synthesizer
            self._prewarm(turn, agent, self._context_request(turn, agent, "", "synthesis"))

    def _prewarm(self, turn: _Turn, agent: AgentName, request: GenerationRequest) -> None:
        provider = self._providers.get(agent)
        if provider is None:
            return
        task = asyncio.create_task(self._prewarm_one(provider, request))
        turn.background.add(task)
        task.add_done_callback(turn.background.discard)

    @staticmethod
    async def _prewarm_one(provider: Provider, request: GenerationRequest) -> None:
        try:
            await asyncio.wait_for(provider.prewarm(request), PREWARM_TIMEOUT_SECONDS)
        except Exception:
            logger.debug("Prewarm of %s failed", provider.agent, exc_info=True)

    async def _parallel(
        self, calls: Mapping[AgentName, Coroutine[Any, Any, _Outcome]]
    ) -> dict[AgentName, _Outcome]:
        """Run calls concurrently; their events interleave as they arrive."""
        tasks = {
            agent: asyncio.create_task(coro, name=f"call-{agent}") for agent, coro in calls.items()
        }
        try:
            await asyncio.wait(tasks.values())
        finally:
            await _cancel_and_wait(tasks.values())
        errors = [task.exception() for task in tasks.values()]  # retrieve them all
        for error in errors:
            if error is not None:
                raise error
        return {agent: task.result() for agent, task in tasks.items()}

    async def _call(
        self,
        turn: _Turn,
        *,
        agent: AgentName,
        kind: MessageKind,
        round_: int,
        request: GenerationRequest,
        final: bool,
        previous: str | None = None,
        extra_meta: Mapping[str, JsonValue] | None = None,
        reader: _Reader | None = None,
    ) -> _Outcome:
        """One model call: stream it, retry once if allowed, store and report the message.

        A ChatGPT that cannot open PDFs first waits for Claude's check of the question's
        PDFs (the turn's one check, started by its first call) and gets them with it.

        A refine turn's review or version comes through its ``reader``, which says what
        each part of the reply streams as, what is stored (with its ``meta.refine``) or
        why the call failed: a review without its changes, billed like an empty reply."""
        provider = self._providers[agent]
        pdf_reading: tuple[PdfReading, ...] = ()
        if not reads_pdfs(provider) and any(a.kind == "pdf" for a in request.attachments):
            pdf_reading = await self._checked_pdfs(turn)
            request = replace(request, attachments=self._with_checks(turn, request.attachments))
        stream_id = uuid.uuid4().hex
        turn.emit(
            StreamStarted(
                turn.request_id, stream_id, agent, kind, round_, self._model_name(turn, agent)
            )
        )
        carries_context = bool(request.history or request.context_summary)
        attempt = 0
        while True:
            attempt += 1
            parser: _SectionParser | None
            if reader is not None:
                parser = reader.start()
            elif kind == "revision":
                parser = RevisionStreamParser()
            else:
                parser = None
            progress = _Progress()
            started = time.monotonic()
            try:
                result = await self._stream(turn, provider, request, stream_id, parser, progress)
                break
            except ProviderError as exc:
                error = exc
            except Exception:
                logger.exception("Provider %s failed unexpectedly", agent)
                error = ProviderError(t("engine.error.provider_unexpected"), kind="internal")
            model, usage = failed_call_usage(
                error, request.model or self._models.get(agent, ""), turn.prices
            )
            await self._record_declined(turn, agent, request.purpose, error, carries_context)
            if is_billed(usage):
                # Billed all the same (a refusal): it counts in the turn's usage, and the
                # context it was billed for was the compacted one.
                turn.accounting.add_unstored(usage)
                if carries_context:
                    turn.accounting.add_context_request(find_price(model, turn.prices))
            await self._record_usage(
                turn,
                agent,
                request.purpose,
                model=model,
                usage=usage,
                latency_ms=int((time.monotonic() - started) * 1000),
                ttft_ms=None,
                error=error,
            )
            if error.retryable and not progress.emitted and attempt == 1:
                logger.info("Retrying %s after a retryable error: %s", agent, error.message)
                await asyncio.sleep(self._retry_delay)
                continue
            billed = usage if is_billed(usage) else None
            return self._stream_failed(turn, agent, stream_id, error, round_, billed)

        declined = await self._record_declined(
            turn, agent, request.purpose, result, carries_context
        )
        price = find_price(result.model, turn.prices)
        if carries_context:
            # Only a call that reached a model and was billed sent the compacted context
            # (an empty reply did too); attempts that failed unbilled saved nothing.
            turn.accounting.add_context_request(price)
        critique: str | None = None
        agreement: int | None = None
        unchanged = False
        note: str | None = None
        kept = False
        refine: dict[str, JsonValue] | None = None
        invalid: str | None = None
        if reader is not None:
            pieces, read = reader.finish(result.truncated)
            for section, text in pieces:
                turn.emit(StreamDelta(turn.request_id, stream_id, section, text))
            content, invalid = read.content, read.error
            refine = read.refine
        elif isinstance(parser, RevisionStreamParser):
            for revision_section, text in parser.close():
                turn.emit(StreamDelta(turn.request_id, stream_id, revision_section, text))
            parsed = parser.final()
            critique, agreement = parsed.critique, parsed.agreement
            if parsed.answer and not parsed.unchanged:
                content = parsed.answer
            elif not parsed.unchanged and not parsed.critique and parsed.agreement is None:
                content = ""  # nothing usable came back: fails as an empty response
            else:
                # UNCHANGED keeps the answer on purpose; a reply cut off before its
                # answer (or without one) keeps it too, so the debate goes on, but it is
                # not reported as unchanged. Show it again so the live view matches.
                unchanged, note, kept = parsed.unchanged, parsed.unchanged_note, True
                content = previous or ""
                if content:
                    turn.emit(StreamDelta(turn.request_id, stream_id, "answer", content))
        else:
            content = result.text.strip()
        if result.truncated:
            logger.info("The reply of %s (%s) was cut off: %s", agent, kind, result.finish_reason)

        usage = replace(
            result.usage, cost_usd=estimate_cost_usd(result.model, result.usage, turn.prices)
        )
        if not content or invalid:
            # Billed all the same: it counts in the turn's usage (not in its savings).
            turn.accounting.add_unstored(usage)
            error = ProviderError(invalid or _empty_reply_message(result), kind="invalid")
            await self._record_usage(
                turn,
                agent,
                request.purpose,
                model=result.model,
                usage=usage,
                latency_ms=result.latency_ms,
                ttft_ms=result.ttft_ms,
                error=error,
            )
            billed = usage if is_billed(usage) else None
            return self._stream_failed(turn, agent, stream_id, error, round_, billed)

        await self._record_usage(
            turn,
            agent,
            request.purpose,
            model=result.model,
            usage=usage,
            latency_ms=result.latency_ms,
            ttft_ms=result.ttft_ms,
            error=None,
        )
        turn.accounting.add_call(usage, request.purpose)
        if unchanged:
            turn.accounting.add_unchanged(content, price)
        if not request.fast and request.model is None:
            self._models.setdefault(agent, result.model)

        meta: dict[str, JsonValue] = {
            "model": result.model,
            "usage": _usage_json(usage),
            "latency_ms": result.latency_ms,
            "ttft_ms": result.ttft_ms,
            "cached": False,
        }
        if basis := _cost_basis(provider.mode, usage):
            meta["cost_basis"] = basis
        if declined:
            # The attempts other models declined before this one: billed calls of their
            # own (the turn's usage has them), kept here so a replay is worth them too.
            meta["declined"] = [
                {"model": model, "usage": _usage_json(attempt)} for model, attempt in declined
            ]
        if result.truncated:
            # A usable partial answer, never a complete one: shown as such, never cached.
            _set_truncated(meta, result.finish_reason)
            turn.truncated = True
        if refine is not None:
            meta["refine"] = refine
        elif kind == "revision":
            meta.update(critique=critique or "", agreement=agreement, unchanged=unchanged)
            if note:
                meta["unchanged_note"] = note
        if pdf_reading:
            meta["pdf_reading"] = _pdf_reading_json(pdf_reading)
        if extra_meta:
            meta.update(extra_meta)
        if final:
            # Final messages are stored by the turn's last call, so these savings are the
            # turn's own; only the first answer of a duel can miss the other one's cost.
            meta["savings"] = _savings_json(turn.accounting.savings())
            _set_unstored(meta, turn.accounting)
        message = NewMessage(
            conversation_id=turn.conversation_id,
            kind=kind,
            content=content,
            turn_id=turn.turn_id,
            agent=agent,
            round=round_,
            final=final,
            meta=meta,
        )
        message_id = await self._store.add_message(message)
        turn.stored.append(message)
        if final:
            turn.final_ids.append(message_id)
        turn.emit(
            StreamCompleted(
                turn.request_id,
                stream_id,
                message_id,
                usage,
                result.latency_ms,
                result.ttft_ms,
                agreement=agreement,
                unchanged=unchanged,
                cost_basis=_cost_basis(provider.mode, usage),
                truncated=result.truncated,
                finish_reason=result.finish_reason if result.truncated else None,
                unchanged_note=note,
                pdf_reading=pdf_reading,
                refine=refine,
            )
        )
        return _Outcome(
            agent=agent,
            ok=True,
            content=content,
            critique=critique,
            agreement=agreement,
            truncated=result.truncated,
            finish_reason=result.finish_reason if result.truncated else None,
            kept=kept,
            message_id=message_id,
            model=result.model,
        )

    async def _stream(
        self,
        turn: _Turn,
        provider: Provider,
        request: GenerationRequest,
        stream_id: str,
        parser: _SectionParser | None,
        progress: _Progress,
    ) -> GenerationResult:
        """Consume one provider stream, forwarding its text as StreamDelta events."""
        stream = provider.stream(request)
        result: GenerationResult | None = None
        chunks: list[str] = []
        try:
            async for event in stream:
                if isinstance(event, TextDelta):
                    if not event.text:
                        continue
                    chunks.append(event.text)
                    if parser is None:
                        turn.emit(StreamDelta(turn.request_id, stream_id, "text", event.text))
                        progress.emitted = True
                        continue
                    for section, text in parser.feed(event.text):
                        turn.emit(StreamDelta(turn.request_id, stream_id, section, text))
                        progress.emitted = True
                else:
                    result = event
        finally:
            if isinstance(stream, AsyncGenerator):
                await stream.aclose()
        if result is None:
            raise ProviderError(
                t("engine.error.reply_interrupted"), kind="internal", retryable=True
            )
        if not result.text and chunks:
            result = replace(result, text="".join(chunks))
        return result

    def _stream_failed(
        self,
        turn: _Turn,
        agent: AgentName,
        stream_id: str,
        error: ProviderError,
        round_: int,
        usage: Usage | None = None,
    ) -> _Outcome:
        """Report a failed call (with what it billed, if anything) and keep it for the
        turn's outcome."""
        turn.failed_agents.add(agent)
        info = ErrorInfo(error.kind, error.message)
        turn.failures.append(TurnFailure(agent, info.kind, info.message, round_))
        turn.emit(StreamFailed(turn.request_id, stream_id, info, usage))
        return _Outcome(agent=agent, ok=False, error=error)

    async def _record_declined(
        self,
        turn: _Turn,
        agent: AgentName,
        purpose: Purpose,
        source: object,
        carries_context: bool,
    ) -> list[tuple[str, Usage]]:
        """Record the billed attempts other models declined before ``source`` (a result or
        a provider error) was served or refused: each is a billed call of its own model,
        priced at its rates, that stored no message (docs/adr/0008-token-accounting.md).
        Returns them priced, in order."""
        declined = declined_attempts(source, turn.prices)
        for model, usage in declined:
            turn.accounting.add_unstored(usage)
            if carries_context:
                turn.accounting.add_context_request(find_price(model, turn.prices))
            await self._record_usage(
                turn,
                agent,
                purpose,
                model=model,
                usage=usage,
                latency_ms=0,
                ttft_ms=None,
                error=ProviderError(t("engine.error.declined", model=model), kind="invalid"),
            )
        return declined

    async def _record_usage(
        self,
        turn: _Turn,
        agent: AgentName,
        purpose: Purpose,
        *,
        model: str,
        usage: Usage,
        latency_ms: int,
        ttft_ms: int | None,
        error: ProviderError | None,
    ) -> None:
        await self._store.record_usage(
            UsageRecord(
                conversation_id=turn.conversation_id,
                turn_id=turn.turn_id,
                agent=agent,
                provider_mode=self._providers[agent].mode,
                model=model,
                purpose=purpose,
                usage=usage,
                latency_ms=latency_ms,
                ttft_ms=ttft_ms,
                ok=error is None,
                error=None if error is None else f"{error.kind}: {error.message}",
            )
        )
