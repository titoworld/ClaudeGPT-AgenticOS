"""Turn engine: runs solo, duel and debate turns and streams their events.

See docs/ARQUITECTURA.md (modes, token savings) and docs/PROTOCOL.md (event order).
The turn itself runs in its own task and hands events to :meth:`Engine.run`
through a queue, so concurrent model calls interleave naturally and closing or
cancelling the iterator cancels every task of the turn before returning.

How every turn ends (completed, failed or cancelled) is decided once and stored on its
question before the terminal event (docs/adr/0007-resultat-del-torn.md).

Attachments (docs/adr/0009-adjunts.md): a turn loads the ones its request names before
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
"""

from __future__ import annotations

import asyncio
import logging
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
from typing import Any, Literal

from agentic_os.attachments import snapshot
from agentic_os.domain import (
    AGENTS,
    AgentName,
    MessageKind,
    ProviderMode,
    Purpose,
    Usage,
    other_agent,
)
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
    Savings,
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
    CHECK_FAILED,
    CHECK_TIMED_OUT,
    CHECK_TIMEOUT_SECONDS,
    CheckOutcome,
    check_pdf,
    unchecked_pages,
)
from agentic_os.orchestrator.prompts import (
    answer_prompt,
    debate_answer_prompt,
    pdf_reading_note,
    revision_prompt,
    synthesis_prompt,
    system_prompt,
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
DEFAULT_TITLE = "Conversa nova"
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
INTERNAL_ERROR = ErrorInfo("internal", "S'ha produït un error intern i el torn s'ha aturat.")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def make_title(question: str) -> str:
    """Conversation title: first non-empty line of the question, at most 60 characters."""
    line = next((ln.strip() for ln in question.splitlines() if ln.strip()), "")
    if not line:
        return DEFAULT_TITLE
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
    """A PDF's check ended in a way the next turn retries (an error, a refusal, a reply
    that made no progress, a timeout): the turn is never cached, so asking the same
    question checks it again instead of replaying an unchecked reading."""

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
    store takes to write."""
    if not task.done():
        task.cancel()
    interrupted: asyncio.CancelledError | None = None
    while pending := [t for t in (task, turn.outcome_write) if t is not None and not t.done()]:
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
        return "El model ha retornat una resposta buida."
    if result.finish_reason == "max_tokens":
        return "El model ha esgotat el límit de sortida abans d'escriure cap resposta."
    if result.finish_reason == "content_filter":
        return "El filtre de contingut ha aturat la resposta abans que el model escrivís res."
    return "La resposta del model s'ha interromput abans d'escriure res."


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
        """
        queue: asyncio.Queue[ServerEvent | _End] = asyncio.Queue()
        turn = _Turn(
            request=request,
            emit=queue.put_nowait,
            question=request.text.strip(),
            prices=price_overrides,
            on_outcome=on_outcome,
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
                outcome = await self._settle(turn, "failed", error=INTERNAL_ERROR)
                yield TurnFailed(request.request_id, INTERNAL_ERROR, outcome.usage)
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
                # before the cancellation goes on.
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
                await self._settle(turn, "failed", error=INTERNAL_ERROR)
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
            await self._fail(turn, ErrorInfo("not_found", "La conversa no existeix."))
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
        # that could not learn one neither reads nor writes the turn cache.
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
            if len(known) == len(agents)
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
        if request.mode == "solo":
            ok = await self._solo(turn)
        elif request.mode == "duel":
            ok = await self._duel(turn)
        else:
            consensus = await self._debate(turn)
            ok = consensus is not None
        if ok:
            await self._finish(turn, consensus, cache_key)

    def _validate(self, request: TurnRequest) -> ErrorInfo | None:
        text = request.text
        if not text.strip():
            return ErrorInfo("invalid", "La pregunta és buida.")
        if len(text) > self._config.max_question_chars:
            return ErrorInfo(
                "invalid",
                f"La pregunta és massa llarga (màxim {self._config.max_question_chars} caràcters).",
            )
        if request.mode not in ("solo", "duel", "debate"):
            return ErrorInfo("invalid", "Mode de torn desconegut.")
        if request.mode == "solo" and request.target not in AGENTS:
            return ErrorInfo("invalid", "Agent desconegut.")
        debate = request.options.debate
        if request.mode == "debate":
            if not 0 <= debate.rounds <= MAX_DEBATE_ROUNDS:
                return ErrorInfo(
                    "invalid", f"Les rondes de debat han de ser entre 0 i {MAX_DEBATE_ROUNDS}."
                )
            if not 0 <= debate.consensus_threshold <= 100:
                return ErrorInfo("invalid", "El llindar de consens ha de ser entre 0 i 100.")
            if debate.synthesizer not in AGENTS:
                return ErrorInfo("invalid", "Agent sintetitzador desconegut.")
        for model in (*request.models.values(), *request.fast_models.values()):
            if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
                return ErrorInfo("invalid", "Identificador de model invàlid.")
        attachments = request.attachments
        if len(attachments) > self._config.max_attachments:
            return ErrorInfo(
                "invalid",
                f"Un missatge pot portar com a màxim {self._config.max_attachments} adjunts.",
            )
        if len(set(attachments)) != len(attachments):
            return ErrorInfo("invalid", "Un mateix adjunt no pot anar dues vegades al missatge.")
        if request.pdf_in_revisions not in ("full", "text"):
            return ErrorInfo("invalid", "Opció desconeguda per als PDF de les revisions.")
        for agent in self._agents(request):
            if agent not in self._providers:
                return ErrorInfo("unavailable", f"{AGENT_LABELS[agent]} no està configurat.")
        return None

    @staticmethod
    def _missing_attachment(error: AttachmentNotFoundError) -> ErrorInfo:
        return ErrorInfo("invalid", f"L'adjunt {error.attachment_id} no existeix.")

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
            return ErrorInfo(
                "invalid",
                f"Els adjunts d'un missatge no poden sumar més de {limit / 1_000_000:g} MB.",
            )
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
        self, turn: _Turn, consensus: Consensus | None, cache_key: str | None
    ) -> None:
        await self._record_savings(turn)
        # Only whole, complete turns are replayed: never one with a failed, degraded or
        # cut-off message, nor one whose key does not say which models answered, nor one
        # where ChatGPT read a PDF whose check the next turn would try again.
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
        outcome = await self._settle(turn, "completed", consensus=consensus)
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
    ) -> TurnOutcome:
        """How the turn ended (docs/adr/0007-resultat-del-torn.md), decided once: a later
        call (a cancellation while the outcome is being written) gets the same outcome.
        It is reported to ``on_outcome`` and written on the question, if it exists, in a
        task of its own that the caller waits for through ``asyncio.shield``: cancelling
        the turn meanwhile stops the wait, never the write.

        Only a turn whose messages are all stored records savings, right before it
        completes, so a failed or cancelled turn has none; one cancelled while its saving
        rows were being written keeps them (the rows are written in full, see
        :meth:`_record_savings`), and its outcome is stored after them."""
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
        await self._fail(turn, ErrorInfo(kind, f"{AGENT_LABELS[agent]} no ha pogut respondre."))
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
        await self._fail(turn, ErrorInfo(kind, "Cap dels dos agents ha pogut respondre."))

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
                outcome = CheckOutcome(None, False, spent[index], CHECK_TIMED_OUT, final=False)
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
            reason = CHECK_FAILED.format(message="S'ha produït un error intern.")
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
    ) -> _Outcome:
        """One model call: stream it, retry once if allowed, store and report the message.

        A ChatGPT that cannot open PDFs first waits for Claude's check of the question's
        PDFs (the turn's one check, started by its first call) and gets them with it."""
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
            parser = RevisionStreamParser() if kind == "revision" else None
            progress = _Progress()
            started = time.monotonic()
            try:
                result = await self._stream(turn, provider, request, stream_id, parser, progress)
                break
            except ProviderError as exc:
                error = exc
            except Exception:
                logger.exception("Provider %s failed unexpectedly", agent)
                error = ProviderError("Error inesperat del proveïdor.", kind="internal")
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
        if parser is not None:
            for section, text in parser.close():
                turn.emit(StreamDelta(turn.request_id, stream_id, section, text))
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
        if not content:
            # Billed all the same: it counts in the turn's usage (not in its savings).
            turn.accounting.add_unstored(usage)
            error = ProviderError(_empty_reply_message(result), kind="invalid")
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
        if kind == "revision":
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
        )

    async def _stream(
        self,
        turn: _Turn,
        provider: Provider,
        request: GenerationRequest,
        stream_id: str,
        parser: RevisionStreamParser | None,
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
                "La resposta del model s'ha interromput.", kind="internal", retryable=True
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
        priced at its rates, that stored no message (docs/adr/0008-recompte-de-tokens.md).
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
                error=ProviderError(
                    f"{model} ha declinat la petició i l'ha passada a un altre model.",
                    kind="invalid",
                ),
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
