"""Turn engine: runs solo, duel and debate turns and streams their events.

See docs/ARQUITECTURA.md (modes, token savings) and docs/PROTOCOL.md (event order).
The turn itself runs in its own task and hands events to :meth:`Engine.run`
through a queue, so concurrent model calls interleave naturally and closing or
cancelling the iterator cancels every task of the turn before returning.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
)
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from agentic_os.domain import AGENTS, AgentName, MessageKind, Purpose, Usage, other_agent
from agentic_os.orchestrator.accounting import TurnAccounting
from agentic_os.orchestrator.cache import context_fingerprint, replayed_message, turn_cache_key
from agentic_os.orchestrator.events import (
    Consensus,
    ErrorInfo,
    PhaseChanged,
    ServerEvent,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamStarted,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from agentic_os.orchestrator.memory import (
    TurnContext,
    build_context,
    compact,
    compaction_cut,
    context_from_history,
)
from agentic_os.orchestrator.prompts import (
    debate_answer_prompt,
    revision_prompt,
    synthesis_prompt,
    system_prompt,
)
from agentic_os.orchestrator.sections import RevisionStreamParser
from agentic_os.orchestrator.store import CachedTurn, JsonValue, NewMessage, Store, UsageRecord
from agentic_os.orchestrator.types import EngineConfig, TurnRequest
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
    TextDelta,
)
from agentic_os.providers.prompt_format import AGENT_LABELS

logger = logging.getLogger(__name__)

MAX_DEBATE_ROUNDS = 4
TITLE_MAX_CHARS = 60
DEFAULT_TITLE = "Conversa nova"
STATUS_TIMEOUT_SECONDS = 2.0
PREWARM_TIMEOUT_SECONDS = 5.0

_DEFAULT_CONFIG = EngineConfig()
_PHASE_OF: dict[MessageKind, Literal["answer", "revision", "synthesis"]] = {
    "answer": "answer",
    "revision": "revision",
    "synthesis": "synthesis",
}

Emit = Callable[[ServerEvent], None]


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
    context: TurnContext = field(default_factory=lambda: build_context(None, ()))
    accounting: TurnAccounting = field(default_factory=TurnAccounting)
    stored: list[NewMessage] = field(default_factory=list)
    """Assistant messages in storage order (what the cache replays)."""
    final_ids: list[int] = field(default_factory=list)
    failed_agents: set[AgentName] = field(default_factory=set)
    degraded: bool = False
    background: set[asyncio.Task[None]] = field(default_factory=set)

    @property
    def request_id(self) -> str:
        return self.request.request_id


async def _cancel_and_wait(tasks: Iterable[asyncio.Task[Any]]) -> None:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)


def _usage_json(usage: Usage) -> dict[str, JsonValue]:
    return {key: value for key, value in usage.to_dict().items()}


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
    ) -> None:
        self._providers = dict(providers)
        self._store = store
        self._config = config
        self._clock = clock
        self._retry_delay = retry_delay
        self._identities: dict[AgentName, str] = {}
        self._models: dict[AgentName, str] = {}
        """Model name per agent for StreamStarted, from ``status()`` or the first result."""

    # -- public API --------------------------------------------------------------

    async def run(
        self, request: TurnRequest, *, compaction_threshold_tokens: int | None = None
    ) -> AsyncIterator[ServerEvent]:
        """Run one turn, yielding its events (see docs/PROTOCOL.md).

        ``compaction_threshold_tokens`` overrides ``EngineConfig`` for this turn (the
        owner's runtime setting). The last event is always ``TurnCompleted`` or
        ``TurnFailed``. Closing the iterator or cancelling its consumer cancels the
        turn and all its model calls.
        """
        queue: asyncio.Queue[ServerEvent | _End] = asyncio.Queue()
        task = asyncio.create_task(
            self._execute(request, queue.put_nowait, compaction_threshold_tokens),
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
                yield TurnFailed(
                    request.request_id,
                    ErrorInfo("internal", "S'ha produït un error intern i el torn s'ha aturat."),
                )
        finally:
            await _cancel_and_wait((task,))

    # -- turn ----------------------------------------------------------------------

    async def _execute(self, request: TurnRequest, emit: Emit, threshold: int | None) -> None:
        turn = _Turn(request=request, emit=emit, question=request.text.strip())
        try:
            await self._run_turn(turn, threshold)
        finally:
            await _cancel_and_wait(turn.background)

    async def _run_turn(self, turn: _Turn, threshold: int | None) -> None:
        request = turn.request
        invalid = self._validate(request)
        if invalid is not None:
            turn.emit(TurnFailed(turn.request_id, invalid))
            return

        new_conversation = request.conversation_id is None
        if request.conversation_id is None:
            turn.conversation_id = await self._store.create_conversation(make_title(turn.question))
        elif await self._store.conversation_exists(request.conversation_id):
            turn.conversation_id = request.conversation_id
        else:
            turn.emit(
                TurnFailed(turn.request_id, ErrorInfo("not_found", "La conversa no existeix."))
            )
            return

        turn.context = await self._prepare_context(turn, threshold)

        agents = self._agents(request)
        identities = dict(
            zip(agents, await asyncio.gather(*(self._identity(a) for a in agents)), strict=True)
        )
        cache_key = turn_cache_key(
            mode=request.mode,
            target=request.target,
            options=request.options,
            question=turn.question,
            context_fingerprint=context_fingerprint(turn.context),
            identities=identities,
        )
        cached: CachedTurn | None = None
        if request.options.use_cache:
            try:
                cached = await self._store.cache_get(cache_key, self._clock())
            except Exception:
                logger.exception("Could not read the turn cache")
            if cached is not None and cached.mode != request.mode:
                cached = None

        turn.turn_id = await self._store.add_message(
            NewMessage(
                conversation_id=turn.conversation_id,
                kind="question",
                content=turn.question,
                final=True,
                meta=self._question_meta(request),
            )
        )
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
        for agent in self._agents(request):
            if agent not in self._providers:
                return ErrorInfo("unavailable", f"{AGENT_LABELS[agent]} no està configurat.")
        return None

    @staticmethod
    def _agents(request: TurnRequest) -> tuple[AgentName, ...]:
        return (request.target,) if request.mode == "solo" else AGENTS

    @staticmethod
    def _question_meta(request: TurnRequest) -> dict[str, JsonValue]:
        debate = request.options.debate
        return {
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

    async def _identity(self, agent: AgentName) -> str:
        """``"<mode>:<model>"`` of an agent's provider (part of the cache key).

        Asked once per process: models only change with the configuration, which
        requires a restart. An unreadable status is remembered as ``?`` so a slow
        provider never delays later turns.
        """
        identity = self._identities.get(agent)
        if identity is None:
            provider = self._providers[agent]
            try:
                status = await asyncio.wait_for(provider.status(), STATUS_TIMEOUT_SECONDS)
                model = status.model
                self._models.setdefault(agent, model)
            except Exception:
                logger.warning("Could not read the status of %s", agent, exc_info=True)
                model = "?"
            identity = self._identities.setdefault(agent, f"{provider.mode}:{model}")
        return identity

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
            max_output_tokens=self._config.max_output_tokens,
        )
        if result is None:
            return context
        turn.accounting.add_summary(result.usage)
        turn.accounting.compaction_per_request = result.tokens_removed
        return result.context

    async def _finish(self, turn: _Turn, consensus: Consensus | None, cache_key: str) -> None:
        accounting = turn.accounting
        await self._record_savings(turn)
        if not turn.degraded and not turn.failed_agents and turn.stored:
            try:
                await self._store.cache_put(
                    cache_key,
                    CachedTurn(
                        mode=turn.request.mode,
                        messages=tuple(turn.stored),
                        tokens=accounting.turn_usage.total_tokens,
                    ),
                    self._clock() + timedelta(seconds=self._config.cache_ttl_seconds),
                )
            except Exception:
                logger.exception("Could not store turn %s in the cache", turn.turn_id)
        turn.emit(
            TurnCompleted(
                turn.request_id,
                turn.conversation_id,
                turn.turn_id,
                tuple(sorted(turn.final_ids)),
                accounting.usage,
                accounting.savings(),
                consensus,
                cached=False,
            )
        )

    async def _record_savings(self, turn: _Turn) -> None:
        for record in turn.accounting.saving_records(turn.conversation_id, turn.turn_id):
            try:
                await self._store.record_saving(record)
            except Exception:
                logger.exception("Could not record a %s saving", record.kind)

    # -- modes -----------------------------------------------------------------------

    async def _solo(self, turn: _Turn) -> bool:
        agent = turn.request.target
        turn.emit(PhaseChanged(turn.request_id, "answer", 0))
        outcome = await self._call(
            turn,
            agent=agent,
            kind="answer",
            round_=0,
            request=self._context_request(turn, agent, turn.question, "answer"),
            final=True,
        )
        if outcome.ok:
            return True
        kind = outcome.error.kind if outcome.error else "unavailable"
        turn.emit(
            TurnFailed(
                turn.request_id,
                ErrorInfo(kind, f"{AGENT_LABELS[agent]} no ha pogut respondre."),
            )
        )
        return False

    async def _duel(self, turn: _Turn) -> bool:
        turn.emit(PhaseChanged(turn.request_id, "answer", 0))
        outcomes = await self._parallel(
            {
                agent: self._call(
                    turn,
                    agent=agent,
                    kind="answer",
                    round_=0,
                    request=self._context_request(turn, agent, turn.question, "answer"),
                    final=True,
                )
                for agent in AGENTS
            }
        )
        if any(outcome.ok for outcome in outcomes.values()):
            return True
        self._fail_all(turn, outcomes)
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
                        turn, agent, debate_answer_prompt(agent, turn.question), "answer"
                    ),
                    final=False,
                )
                for agent in AGENTS
            }
        )
        survivors = [agent for agent in AGENTS if outcomes[agent].ok]
        if not survivors:
            self._fail_all(turn, outcomes)
            return None
        if len(survivors) == 1:
            # One agent is down: its partner's answer becomes the final one, no more calls.
            consensus = Consensus(reached=False, round=0, scores={})
            turn.emit(PhaseChanged(turn.request_id, "synthesis", 0))
            await self._store_degraded_synthesis(
                turn, survivors[0], outcomes[survivors[0]].content, 0, consensus
            )
            return consensus

        answers: dict[AgentName, str] = {agent: outcomes[agent].content for agent in AGENTS}
        critiques: dict[AgentName, str | None] = dict.fromkeys(AGENTS)
        scores: dict[AgentName, int] = {}
        reached = False
        last_round = 0
        for round_ in range(1, rounds + 1):
            turn.emit(PhaseChanged(turn.request_id, "revision", round_))
            self._prewarm_next(turn, revision=round_ < rounds)
            previous = dict(answers)
            outcomes = await self._parallel(
                {
                    agent: self._call(
                        turn,
                        agent=agent,
                        kind="revision",
                        round_=round_,
                        request=self._revision_request(turn, agent, previous),
                        final=False,
                        previous=previous[agent],
                    )
                    for agent in AGENTS
                }
            )
            last_round = round_
            round_scores: dict[AgentName, int] = {}
            for agent, outcome in outcomes.items():
                if not outcome.ok:
                    continue  # keep that agent's last answer
                answers[agent] = outcome.content
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
        answers: Mapping[AgentName, str],
        critiques: Mapping[AgentName, str | None],
        round_: int,
        consensus: Consensus,
    ) -> None:
        synthesizer = turn.request.options.debate.synthesizer
        order = [synthesizer, other_agent(synthesizer)]
        if synthesizer in turn.failed_agents:
            order.reverse()  # it already failed in this turn: try the other one first
        prompt = synthesis_prompt(turn.question, answers, critiques)
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
        await self._store_degraded_synthesis(turn, order[0], answers[order[0]], round_, consensus)

    def _fail_all(self, turn: _Turn, outcomes: Mapping[AgentName, _Outcome]) -> None:
        kinds = {outcome.error.kind for outcome in outcomes.values() if outcome.error}
        kind = kinds.pop() if len(kinds) == 1 else "unavailable"
        turn.emit(
            TurnFailed(turn.request_id, ErrorInfo(kind, "Cap dels dos agents ha pogut respondre."))
        )

    async def _store_degraded_synthesis(
        self,
        turn: _Turn,
        agent: AgentName,
        content: str,
        round_: int,
        consensus: Consensus,
    ) -> None:
        """Store an existing answer as the final one, without calling any model."""
        turn.degraded = True
        stream_id = uuid.uuid4().hex
        model = self._models.get(agent, "")
        turn.emit(StreamStarted(turn.request_id, stream_id, agent, "synthesis", round_, model))
        turn.emit(StreamDelta(turn.request_id, stream_id, "text", content))
        message_id = await self._store.add_message(
            NewMessage(
                conversation_id=turn.conversation_id,
                kind="synthesis",
                content=content,
                turn_id=turn.turn_id,
                agent=agent,
                round=round_,
                final=True,
                meta={
                    "model": model,
                    "usage": _usage_json(Usage()),
                    "latency_ms": 0,
                    "ttft_ms": None,
                    "cached": False,
                    "degraded": True,
                    "consensus": _consensus_json(consensus),
                },
            )
        )
        turn.final_ids.append(message_id)
        turn.emit(StreamCompleted(turn.request_id, stream_id, message_id, Usage(), 0, None))

    # -- cache replay ------------------------------------------------------------------

    async def _replay(self, turn: _Turn, cached: CachedTurn) -> None:
        phase: tuple[str, int] | None = None
        consensus: Consensus | None = None
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
            message_id = await self._store.add_message(message)
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
                )
            )
        if turn.request.mode == "debate" and consensus is None:
            consensus = Consensus(reached=False, round=phase[1] if phase else 0, scores={})
        turn.accounting.cache = cached.tokens
        await self._record_savings(turn)
        turn.emit(
            TurnCompleted(
                turn.request_id,
                turn.conversation_id,
                turn.turn_id,
                tuple(sorted(turn.final_ids)),
                turn.accounting.usage,
                turn.accounting.savings(),
                consensus,
                cached=True,
            )
        )

    # -- model calls -------------------------------------------------------------------

    def _context_request(
        self, turn: _Turn, agent: AgentName, prompt: str, purpose: Purpose
    ) -> GenerationRequest:
        """A request carrying the conversation context (answers and synthesis)."""
        return GenerationRequest(
            system=system_prompt(agent),
            prompt=prompt,
            history=turn.context.history,
            context_summary=turn.context.summary,
            purpose=purpose,
            max_output_tokens=self._config.max_output_tokens,
        )

    def _revision_request(
        self, turn: _Turn, agent: AgentName, answers: Mapping[AgentName, str]
    ) -> GenerationRequest:
        """Self-contained revision request: question + both answers, no history."""
        return GenerationRequest(
            system=system_prompt(agent),
            prompt=revision_prompt(
                agent, turn.question, answers[agent], answers[other_agent(agent)]
            ),
            purpose="revision",
            max_output_tokens=self._config.max_output_tokens,
        )

    def _prewarm_next(self, turn: _Turn, *, revision: bool) -> None:
        """Hint the providers of the next debate phase while the current one streams."""
        if revision:
            for agent in AGENTS:
                request = GenerationRequest(
                    system=system_prompt(agent),
                    prompt="",
                    purpose="revision",
                    max_output_tokens=self._config.max_output_tokens,
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
        """One model call: stream it, retry once if allowed, store and report the message."""
        provider = self._providers[agent]
        stream_id = uuid.uuid4().hex
        turn.emit(
            StreamStarted(
                turn.request_id, stream_id, agent, kind, round_, self._models.get(agent, "")
            )
        )
        carries_context = bool(request.history or request.context_summary)
        attempt = 0
        while True:
            attempt += 1
            if carries_context:
                turn.accounting.add_context_request()
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
            await self._record_usage(
                turn,
                agent,
                request.purpose,
                model=self._models.get(agent, ""),
                usage=Usage(),
                latency_ms=int((time.monotonic() - started) * 1000),
                ttft_ms=None,
                error=error,
            )
            if error.retryable and not progress.emitted and attempt == 1:
                logger.info("Retrying %s after a retryable error: %s", agent, error.message)
                await asyncio.sleep(self._retry_delay)
                continue
            return self._stream_failed(turn, agent, stream_id, error)

        critique: str | None = None
        agreement: int | None = None
        unchanged = False
        if parser is not None:
            for section, text in parser.close():
                turn.emit(StreamDelta(turn.request_id, stream_id, section, text))
            parsed = parser.final()
            critique, agreement = parsed.critique, parsed.agreement
            if parsed.unchanged or not parsed.answer:
                # The agent keeps its answer: show it again so the live view matches the store.
                unchanged = True
                content = previous or ""
                if content:
                    turn.emit(StreamDelta(turn.request_id, stream_id, "answer", content))
            else:
                content = parsed.answer
        else:
            content = result.text.strip()

        if not content:
            error = ProviderError("El model ha retornat una resposta buida.", kind="invalid")
            await self._record_usage(
                turn,
                agent,
                request.purpose,
                model=result.model,
                usage=result.usage,
                latency_ms=result.latency_ms,
                ttft_ms=result.ttft_ms,
                error=error,
            )
            return self._stream_failed(turn, agent, stream_id, error)

        await self._record_usage(
            turn,
            agent,
            request.purpose,
            model=result.model,
            usage=result.usage,
            latency_ms=result.latency_ms,
            ttft_ms=result.ttft_ms,
            error=None,
        )
        turn.accounting.add_call(result.usage, request.purpose)
        if unchanged:
            turn.accounting.add_unchanged(content)
        if not request.fast:
            self._models.setdefault(agent, result.model)

        meta: dict[str, JsonValue] = {
            "model": result.model,
            "usage": _usage_json(result.usage),
            "latency_ms": result.latency_ms,
            "ttft_ms": result.ttft_ms,
            "cached": False,
        }
        if kind == "revision":
            meta.update(critique=critique or "", agreement=agreement, unchanged=unchanged)
        if extra_meta:
            meta.update(extra_meta)
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
                result.usage,
                result.latency_ms,
                result.ttft_ms,
                agreement=agreement,
                unchanged=unchanged,
            )
        )
        return _Outcome(
            agent=agent, ok=True, content=content, critique=critique, agreement=agreement
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
        self, turn: _Turn, agent: AgentName, stream_id: str, error: ProviderError
    ) -> _Outcome:
        turn.failed_agents.add(agent)
        turn.emit(StreamFailed(turn.request_id, stream_id, ErrorInfo(error.kind, error.message)))
        return _Outcome(agent=agent, ok=False, error=error)

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
