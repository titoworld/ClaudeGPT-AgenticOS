"""Turns running on the server, independent of any WebSocket.

Every turn is an asyncio task that consumes the engine's events. Each event gets
a per-turn ``seq`` (1, 2, 3...), is serialized once, appended to the turn's buffer
and handed to the current subscribers. Buffers are kept while the turn runs and
for :data:`RETENTION_SECONDS` after it ends, so a client that reconnects can ask
for what it missed (``turn.subscribe``). Only ``turn.cancel`` (or shutdown) stops a
turn; a subscriber going away does not. Deleting a conversation cancels its turns
and forgets them, so their content can no longer be replayed. A cancelled turn ends
with ``turn.cancelled`` carrying what it spent, as the engine reported it (its
outcome, docs/adr/0007-turn-outcome.md). A turn is cancelled only once: a
repeated ``turn.cancel`` or a shutdown while it stops leaves the engine to finish
stopping it, so ``turn.cancelled`` always follows its stored outcome.

A refine turn can also be asked to stop after the round in course (``turn.stop``,
docs/adr/0010-refine-mode.md): that is no cancellation, only a signal the engine
reads between its calls; it then ends the turn as completed, with its last version.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

from agentic_os.domain import Usage
from agentic_os.i18n import lazy, number, t
from agentic_os.orchestrator.events import (
    ErrorInfo,
    ServerEvent,
    TurnCancelled,
    TurnFailed,
    TurnOutcome,
    TurnStarted,
    Wire,
)
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.pricing import ModelPrice

logger = logging.getLogger(__name__)

MAX_CONCURRENT_TURNS: Final = 3
RETENTION_SECONDS: Final = 300.0
_TERMINAL_TYPES: Final = frozenset({"turn.completed", "turn.failed", "turn.cancelled"})
STOP_ONLY_REFINE: Final = lazy("server.turn.stop_only_refine")
"""The message of :class:`TurnNotStoppableError` (``str()`` makes it)."""


def dumps(message: Wire) -> str:
    """Compact JSON for the WebSocket."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


class Subscriber(Protocol):
    """Receiver of a turn's serialized events (a WebSocket connection)."""

    def send(self, text: str) -> bool:
        """Queue ``text`` without blocking; ``False`` means the subscriber is gone
        (it is then dropped from the turn)."""
        ...

    def send_batch(self, texts: Sequence[str]) -> bool:
        """Like :meth:`send` for several texts at once, which take a single place
        in the subscriber's queue (a replay can be far longer than the queue)."""
        ...


class TurnRunner(Protocol):
    """What the manager needs from :class:`~agentic_os.orchestrator.engine.Engine`."""

    def run(
        self,
        request: TurnRequest,
        *,
        compaction_threshold_tokens: int | None = None,
        price_overrides: Mapping[str, ModelPrice] | None = None,
        on_outcome: Callable[[TurnOutcome], None] | None = None,
        stop: asyncio.Event | None = None,
    ) -> AsyncIterator[ServerEvent]:
        """The turn's events; ``on_outcome`` gets how it ended as soon as that is
        decided, a cancelled turn included (it has no event of its own). Cancelling the
        consumer once stops the turn: its ``CancelledError`` comes after ``on_outcome``
        (the manager never cancels a turn twice). ``stop``, once set, asks a refine turn
        to end after the round in course (``turn.stop``)."""
        ...


class TurnRejectedError(Exception):
    """The turn cannot start. ``code``: ``busy`` (limits), ``duplicate`` (request id
    already used) or ``unavailable`` (shutting down); the message is in the language in
    force (the connection's)."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class TurnNotStoppableError(Exception):
    """``turn.stop`` of a running turn that is not a refine one: only a refine turn has
    rounds to end after; the others stop at once with ``turn.cancel``. The message is
    for the owner, in the language in force."""

    def __init__(self) -> None:
        message = str(STOP_ONLY_REFINE)
        super().__init__(message)
        self.message = message


@dataclass(slots=True, eq=False)
class _Turn:
    request: TurnRequest
    conversation_id: int | None
    compaction_threshold_tokens: int | None = None
    price_overrides: Mapping[str, ModelPrice] | None = None
    events: list[str] = field(default_factory=list)
    """Serialized events; ``events[i]`` has ``seq == i + 1``."""
    subscribers: set[Subscriber] = field(default_factory=set)
    """Subscribers receiving the live events."""
    receivers: set[Subscriber] = field(default_factory=set)
    """Every subscriber that got this turn's events (it started the turn or
    subscribed to it), live or not: a repeated ``subscribe`` is ignored."""
    task: asyncio.Task[None] | None = None
    usage: Usage = field(default_factory=Usage)
    """What the turn spent, from its outcome (for a ``turn.cancelled``)."""
    terminal: bool = False
    """A terminal event (completed, failed or cancelled) was published."""
    stopping: bool = False
    """Its task was cancelled (``turn.cancel``, a deleted conversation or a shutdown).
    It is never cancelled again: that would interrupt the engine while it stops the
    turn's calls and stores how the turn ended, and ``turn.cancelled`` would go out
    before that, without what the turn spent (see :meth:`TurnManager.cancel`)."""
    finished: bool = False
    """The task has ended (no more events)."""
    forgotten: bool = False
    """Its conversation was deleted: unknown to ``subscribe`` and dropped as soon as
    it ends (see :meth:`TurnManager.forget_conversation`)."""
    expiry: asyncio.TimerHandle | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    """Set by ``turn.stop``: a refine turn ends after the round in course."""

    @property
    def request_id(self) -> str:
        return self.request.request_id


class TurnManager:
    """Starts, tracks, replays and cancels turns. All methods except :meth:`aclose`
    are synchronous (no awaits), so replaying a buffer and subscribing to live
    events is atomic: a subscriber never misses or duplicates an event."""

    def __init__(
        self,
        runner: TurnRunner,
        *,
        max_concurrent: int = MAX_CONCURRENT_TURNS,
        retention_seconds: float = RETENTION_SECONDS,
    ) -> None:
        self._runner = runner
        self._max_concurrent = max_concurrent
        self._retention = retention_seconds
        self._turns: dict[str, _Turn] = {}
        self._closed = False

    # -- queries ---------------------------------------------------------------------

    def _running(self) -> list[_Turn]:
        return [turn for turn in self._turns.values() if not turn.finished]

    def active_turns(self) -> list[Wire]:
        """Running turns for ``hello``: ``{request_id, conversation_id, last_seq}``
        (``conversation_id`` is ``None`` until a new conversation is created). The
        turns of a deleted conversation, still ending, are left out."""
        return [
            {
                "request_id": turn.request_id,
                "conversation_id": turn.conversation_id,
                "last_seq": len(turn.events),
            }
            for turn in self._running()
            if not turn.forgotten
        ]

    def is_running(self, request_id: str) -> bool:
        turn = self._turns.get(request_id)
        return turn is not None and not turn.finished

    # -- commands --------------------------------------------------------------------

    def start(
        self,
        request: TurnRequest,
        subscriber: Subscriber | None = None,
        *,
        compaction_threshold_tokens: int | None = None,
        price_overrides: Mapping[str, ModelPrice] | None = None,
    ) -> None:
        """Start ``request`` in a new task, with ``subscriber`` receiving its events.
        ``compaction_threshold_tokens`` and ``price_overrides`` (the owner's runtime
        settings) are passed on to the engine.

        Raises :class:`TurnRejectedError` if the request id is already known, if
        :data:`MAX_CONCURRENT_TURNS` turns are running or if the conversation
        already has a running turn."""
        if self._closed:
            raise TurnRejectedError(t("server.turn.shutting_down"), code="unavailable")
        if request.request_id in self._turns:
            raise TurnRejectedError(t("server.turn.duplicate"), code="duplicate")
        running = self._running()
        if len(running) >= self._max_concurrent:
            raise TurnRejectedError(
                t("server.turn.too_many", count=number(self._max_concurrent)),
                code="busy",
            )
        if request.conversation_id is not None and any(
            turn.conversation_id == request.conversation_id for turn in running
        ):
            raise TurnRejectedError(t("server.turn.conversation_busy"), code="busy")

        turn = _Turn(
            request=request,
            conversation_id=request.conversation_id,
            compaction_threshold_tokens=compaction_threshold_tokens,
            price_overrides=price_overrides,
        )
        if subscriber is not None:
            turn.subscribers.add(subscriber)
            turn.receivers.add(subscriber)
        self._turns[request.request_id] = turn
        turn.task = asyncio.create_task(self._run(turn), name=f"turn-{request.request_id}")
        turn.task.add_done_callback(lambda task: self._on_done(turn, task))

    def subscribe(self, request_id: str, subscriber: Subscriber, after_seq: int = 0) -> bool:
        """Replay the buffered events with ``seq > after_seq`` to ``subscriber`` and,
        if the turn is still running, keep sending it live events. ``False`` if the
        turn is unknown (never existed, ended more than the retention ago, or its
        conversation was deleted).

        A subscriber that already got this turn (it started it or subscribed before)
        is left as it is: it already has every event from its first ``after_seq`` on,
        and replaying the buffer again would only queue it once more (a client that
        does not read could otherwise make the server hold any number of copies)."""
        turn = self._turns.get(request_id)
        if turn is None or turn.forgotten:
            return False
        if subscriber in turn.receivers:
            return True
        turn.receivers.add(subscriber)
        backlog = turn.events[max(after_seq, 0) :]
        if backlog and not subscriber.send_batch(backlog):
            return True
        if not turn.finished:
            turn.subscribers.add(subscriber)
        return True

    def cancel(self, request_id: str) -> bool:
        """Cancel a running turn (its last event will be ``turn.cancelled``). A turn
        that already ended is left alone, and so is one that is already stopping (the
        owner pressing stop again): its ``turn.cancelled`` comes once the engine has
        stopped its calls and stored how it ended, with what it spent. ``False`` if the
        turn is unknown."""
        turn = self._turns.get(request_id)
        if turn is None:
            return False
        if not turn.terminal:
            self._stop(turn)
        return True

    def stop(self, request_id: str) -> bool:
        """Ask a running refine turn to end after the round in course (``turn.stop``):
        its stop event is set, the engine finishes the round (its calls are never cut)
        and ends the turn with its last version, announcing it once with
        ``turn.stopping``; asking again changes nothing. A turn that has ended, or is
        being cancelled, is left alone, whatever its mode. ``False`` if the turn is
        unknown. Raises :class:`TurnNotStoppableError` for a running turn of another
        mode, which has no round to end after."""
        turn = self._turns.get(request_id)
        if turn is None:
            return False
        if turn.terminal or turn.finished or turn.stopping:
            return True
        if turn.request.mode != "refine":
            raise TurnNotStoppableError
        turn.stop_event.set()
        return True

    def cancel_conversation(self, conversation_id: int) -> int:
        """Cancel the running turns of a conversation (before deleting it)."""
        cancelled = 0
        for turn in self._running():
            if turn.conversation_id == conversation_id and self.cancel(turn.request_id):
                cancelled += 1
        return cancelled

    def forget_conversation(self, conversation_id: int) -> int:
        """Forget the turns of a deleted conversation, so their content can no longer
        be replayed: finished turns at once, running ones as soon as they end (their
        own subscribers still get the rest). ``subscribe`` treats them as unknown from
        now on. Returns how many turns there were."""
        forgotten = 0
        for request_id, turn in list(self._turns.items()):
            if turn.conversation_id != conversation_id:
                continue
            forgotten += 1
            turn.forgotten = True
            if turn.finished:
                self._drop(request_id, turn)
        return forgotten

    def detach(self, subscriber: Subscriber) -> None:
        """Stop sending events to ``subscriber`` (its connection closed)."""
        for turn in self._turns.values():
            turn.subscribers.discard(subscriber)
            turn.receivers.discard(subscriber)

    async def aclose(self) -> None:
        """Cancel every running turn (a turn already stopping is not cancelled again),
        wait for them, so that no turn outlives the store it writes to, and drop all
        buffers."""
        self._closed = True
        for turn in self._turns.values():
            self._stop(turn)
        pending = [
            turn.task
            for turn in self._turns.values()
            if turn.task is not None and not turn.task.done()
        ]
        if pending:
            # asyncio.wait, like server.tasks.cancel_and_wait: a caller cancelled
            # meanwhile gets its own CancelledError. _on_done has retrieved the tasks'
            # errors by the time the wait ends.
            await asyncio.wait(pending)
        for turn in self._turns.values():
            if turn.expiry is not None:
                turn.expiry.cancel()
        self._turns.clear()

    # -- internals -------------------------------------------------------------------

    @staticmethod
    def _stop(turn: _Turn) -> None:
        """Cancel the task of ``turn`` unless it has ended or was already cancelled."""
        if turn.task is not None and not turn.task.done() and not turn.stopping:
            turn.stopping = True
            turn.task.cancel()

    async def _run(self, turn: _Turn) -> None:
        def on_outcome(outcome: TurnOutcome) -> None:
            turn.usage = outcome.usage

        events = self._runner.run(
            turn.request,
            compaction_threshold_tokens=turn.compaction_threshold_tokens,
            price_overrides=turn.price_overrides,
            on_outcome=on_outcome,
            stop=turn.stop_event,
        )
        # Cancelling this task cancels the engine at its current await; the engine
        # then stops every model call of the turn before the error propagates.
        async for event in events:
            if isinstance(event, TurnStarted):
                turn.conversation_id = event.conversation_id
            self._publish(turn, event.to_wire())

    def _publish(self, turn: _Turn, wire: Wire) -> None:
        text = dumps({**wire, "seq": len(turn.events) + 1})
        turn.events.append(text)
        if wire.get("type") in _TERMINAL_TYPES:
            turn.terminal = True
        for subscriber in list(turn.subscribers):
            if not subscriber.send(text):
                turn.subscribers.discard(subscriber)

    def _on_done(self, turn: _Turn, task: asyncio.Task[None]) -> None:
        if not turn.terminal:
            if task.cancelled():
                self._publish(turn, TurnCancelled(turn.request_id, turn.usage).to_wire())
            else:
                error = task.exception()
                if error is not None:
                    logger.error("Turn %s crashed", turn.request_id, exc_info=error)
                message = t("server.turn.internal_error")  # the turn's language
                failed = TurnFailed(turn.request_id, ErrorInfo("internal", message), turn.usage)
                self._publish(turn, failed.to_wire())
        elif not task.cancelled() and task.exception() is not None:
            logger.error("Turn %s failed after ending", turn.request_id, exc_info=task.exception())
        turn.finished = True
        turn.subscribers.clear()
        if self._closed:
            return
        if turn.forgotten:
            self._drop(turn.request_id, turn)
            return
        turn.expiry = asyncio.get_running_loop().call_later(
            self._retention, self._drop, turn.request_id, turn
        )

    def _drop(self, request_id: str, turn: _Turn) -> None:
        """Forget ``turn`` (if ``request_id`` still names it) and its expiry timer."""
        if turn.expiry is not None:
            turn.expiry.cancel()
            turn.expiry = None
        if self._turns.get(request_id) is turn:
            del self._turns[request_id]
