"""Turns running on the server, independent of any WebSocket.

Every turn is an asyncio task that consumes the engine's events. Each event gets
a per-turn ``seq`` (1, 2, 3...), is serialized once, appended to the turn's buffer
and handed to the current subscribers. Buffers are kept while the turn runs and
for :data:`RETENTION_SECONDS` after it ends, so a client that reconnects can ask
for what it missed (``turn.subscribe``). Only ``turn.cancel`` (or shutdown) stops a
turn; a subscriber going away does not.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

from agentic_os.orchestrator.events import (
    ErrorInfo,
    ServerEvent,
    TurnCancelled,
    TurnFailed,
    TurnStarted,
    Wire,
)
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.pricing import ModelPrice
from agentic_os.server.tasks import cancel_and_wait

logger = logging.getLogger(__name__)

MAX_CONCURRENT_TURNS: Final = 3
RETENTION_SECONDS: Final = 300.0
_TERMINAL_TYPES: Final = frozenset({"turn.completed", "turn.failed", "turn.cancelled"})


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
    ) -> AsyncIterator[ServerEvent]: ...


class TurnRejectedError(Exception):
    """The turn cannot start. ``code``: ``busy`` (limits), ``duplicate`` (request id
    already used) or ``unavailable`` (shutting down); the message is Catalan."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


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
    terminal: bool = False
    """A terminal event (completed, failed or cancelled) was published."""
    finished: bool = False
    """The task has ended (no more events)."""
    expiry: asyncio.TimerHandle | None = None

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
        (``conversation_id`` is ``None`` until a new conversation is created)."""
        return [
            {
                "request_id": turn.request_id,
                "conversation_id": turn.conversation_id,
                "last_seq": len(turn.events),
            }
            for turn in self._running()
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
            raise TurnRejectedError("El servidor s'està aturant.", code="unavailable")
        if request.request_id in self._turns:
            raise TurnRejectedError(
                "Aquest identificador de petició ja s'ha fet servir.", code="duplicate"
            )
        running = self._running()
        if len(running) >= self._max_concurrent:
            raise TurnRejectedError(
                f"Ja hi ha {self._max_concurrent} torns en curs. Espera que n'acabi algun.",
                code="busy",
            )
        if request.conversation_id is not None and any(
            turn.conversation_id == request.conversation_id for turn in running
        ):
            raise TurnRejectedError("Aquesta conversa ja té un torn en curs.", code="busy")

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
        turn is unknown (never existed, or ended more than the retention ago).

        A subscriber that already got this turn (it started it or subscribed before)
        is left as it is: it already has every event from its first ``after_seq`` on,
        and replaying the buffer again would only queue it once more (a client that
        does not read could otherwise make the server hold any number of copies)."""
        turn = self._turns.get(request_id)
        if turn is None:
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
        that already ended is left alone. ``False`` if the turn is unknown."""
        turn = self._turns.get(request_id)
        if turn is None:
            return False
        if not turn.terminal and turn.task is not None and not turn.task.done():
            turn.task.cancel()
        return True

    def cancel_conversation(self, conversation_id: int) -> int:
        """Cancel the running turns of a conversation (before deleting it)."""
        cancelled = 0
        for turn in self._running():
            if turn.conversation_id == conversation_id and self.cancel(turn.request_id):
                cancelled += 1
        return cancelled

    def detach(self, subscriber: Subscriber) -> None:
        """Stop sending events to ``subscriber`` (its connection closed)."""
        for turn in self._turns.values():
            turn.subscribers.discard(subscriber)
            turn.receivers.discard(subscriber)

    async def aclose(self) -> None:
        """Cancel every running turn, wait for them and drop all buffers."""
        self._closed = True
        await cancel_and_wait(turn.task for turn in self._turns.values() if turn.task is not None)
        for turn in self._turns.values():
            if turn.expiry is not None:
                turn.expiry.cancel()
        self._turns.clear()

    # -- internals -------------------------------------------------------------------

    async def _run(self, turn: _Turn) -> None:
        events = self._runner.run(
            turn.request,
            compaction_threshold_tokens=turn.compaction_threshold_tokens,
            price_overrides=turn.price_overrides,
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
                self._publish(turn, TurnCancelled(turn.request_id).to_wire())
            else:
                error = task.exception()
                if error is not None:
                    logger.error("Turn %s crashed", turn.request_id, exc_info=error)
                message = "S'ha produït un error intern i el torn s'ha aturat."
                self._publish(
                    turn, TurnFailed(turn.request_id, ErrorInfo("internal", message)).to_wire()
                )
        elif not task.cancelled() and task.exception() is not None:
            logger.error("Turn %s failed after ending", turn.request_id, exc_info=task.exception())
        turn.finished = True
        turn.subscribers.clear()
        if self._closed:
            return
        turn.expiry = asyncio.get_running_loop().call_later(
            self._retention, self._forget, turn.request_id, turn
        )

    def _forget(self, request_id: str, turn: _Turn) -> None:
        if self._turns.get(request_id) is turn:
            del self._turns[request_id]
