"""WebSocket ``/api/ws`` (docs/PROTOCOL.md): one persistent connection per tab.

The handshake is accepted first so the browser sees the close codes: ``4403`` for a
foreign ``Origin`` and ``4401`` without a live session. Every connection has a
bounded outgoing queue drained by a writer task; a client that cannot keep up is
disconnected (code 1013) and recovers what it missed with ``turn.subscribe`` after
reconnecting. A replay takes a single place in the queue, however long it is, but
its events count towards :data:`MAX_PENDING_EVENTS`, and a connection gets each
turn only once (a repeated ``turn.subscribe`` is ignored), so a client that never
reads cannot make the server hold an unbounded number of events for it.
Invalid messages get an ``error`` answer; they never close the socket. That includes
text that is not valid UTF-8 (a lone surrogate written as a ``\\ud800`` escape),
refused before any part of the message is used or echoed back.

The session is checked in the handshake, again on every message and every
:data:`SESSION_CHECK_SECONDS` even if the client sends nothing: a socket whose
session ended (logout, ``agentic-os reset-sessions``, expiry) is closed with
``4401``. A logout in this process closes its sockets at once. Only the owner's
actions (:data:`ACTIVITY_MESSAGES`) refresh the idle timeout; the handshake, pings,
resubscriptions and the periodic check are read-only, so a tab left open (and
reconnecting) never keeps an idle session alive.
"""

import asyncio
import contextlib
import json
import logging
import math
from collections.abc import Mapping, Sequence
from typing import Final

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketState

from agentic_os import __version__
from agentic_os.attachments import MAX_ATTACHMENTS
from agentic_os.domain import AGENTS, AgentName, TurnMode, TurnOptions
from agentic_os.orchestrator.events import Wire
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.security.sessions import hash_token
from agentic_os.server.deps import MAX_SQLITE_ID, AppState, app_state, has_invalid_text
from agentic_os.server.middleware import origin_allowed
from agentic_os.server.status import status_to_wire
from agentic_os.server.tasks import cancel_and_wait
from agentic_os.server.turns import TurnRejectedError, dumps
from agentic_os.storage import RuntimeSettings
from agentic_os.storage.models import TURN_MODES, optional_model_id

logger = logging.getLogger(__name__)

router = APIRouter()

CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_FORBIDDEN_ORIGIN: Final = 4403
CLOSE_TOO_SLOW: Final = 1013
CLOSE_INTERNAL_ERROR: Final = 1011
MAX_MESSAGE_CHARS: Final = 512 * 1024
SEND_QUEUE_SIZE: Final = 4096
"""Messages (or replay batches) waiting to be written; a full queue drops the
connection."""
MAX_PENDING_EVENTS: Final = 200_000
"""Texts waiting to be written, each text of a replay batch counted: once this many
are waiting, the next message drops the connection (1013). The check comes before
the new message is queued, so a single replay may be longer than this and any turn
can still be recovered (after a 1013 the client resumes from what it got)."""
SESSION_CHECK_SECONDS: Final = 30.0
"""How often an open socket re-checks its session (read-only)."""
MAX_REQUEST_ID_LENGTH: Final = 128
ACTIVITY_MESSAGES: Final = frozenset({"turn.start", "turn.cancel"})
"""Messages that are the owner's activity (they refresh the session's idle timeout).
The others check the session read-only: ``ping`` and ``turn.subscribe`` are sent by
the client by itself (heartbeats, resubscriptions after a reconnection)."""
INVALID_TEXT_MESSAGE: Final = "El missatge conté text que no és UTF-8 vàlid."
ATTACHMENTS_MESSAGE: Final = (
    "«attachments» ha de ser una llista d'identificadors d'adjunt (enters positius)."
)


class ProtocolError(Exception):
    """Invalid client message; the (Catalan) message is sent back as an ``error``."""

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id


# -- parsing ---------------------------------------------------------------------------


def _choice[T: str](value: object, choices: tuple[T, ...], message: str) -> T:
    for choice in choices:
        if value == choice:
            return choice
    raise ProtocolError(message)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_request_id(data: Mapping[str, object]) -> str:
    request_id = data.get("request_id")
    if (
        not isinstance(request_id, str)
        or not 0 < len(request_id) <= MAX_REQUEST_ID_LENGTH
        or not request_id.isprintable()
    ):
        raise ProtocolError(
            f"«request_id» ha de ser un text d'1 a {MAX_REQUEST_ID_LENGTH} caràcters."
        )
    return request_id


def valid_request_id(data: Mapping[str, object]) -> str | None:
    """The message's ``request_id`` if it is valid (to name the request in an
    ``error``), else ``None``."""
    try:
        return parse_request_id(data)
    except ProtocolError:
        return None


def turn_options(options: object, runtime: RuntimeSettings) -> TurnOptions:
    """Client ``options`` over the owner's runtime settings (missing keys, or no
    options at all, take the runtime values); validated like the settings."""
    if options is None:
        return runtime.to_turn_options()
    if not isinstance(options, dict):
        raise ProtocolError("«options» ha de ser un objecte.")
    debate = options.get("debate")
    if debate is not None and not isinstance(debate, dict):
        raise ProtocolError("«options.debate» ha de ser un objecte.")
    merged = runtime.to_wire()
    base_debate = merged["debate"]
    if debate and isinstance(base_debate, dict):
        merged["debate"] = {**base_debate, **debate}
    if "use_cache" in options:
        merged["use_cache"] = options["use_cache"]
    try:
        return RuntimeSettings.from_wire(merged).to_turn_options()
    except ValueError as exc:
        raise ProtocolError(str(exc)) from None


def turn_models(models: object, runtime: RuntimeSettings) -> dict[AgentName, str]:
    """Client ``models`` (``{agent: id}``; a missing agent, ``null`` or ``""`` keeps
    the owner's default) over the runtime settings' models."""
    chosen = runtime.chosen_models()
    if models is None:
        return chosen
    if not isinstance(models, dict):
        raise ProtocolError("«models» ha de ser un objecte (agent → model).")
    for agent, value in models.items():
        name = _choice(agent, AGENTS, "«models» només admet «claude» i «chatgpt».")
        try:
            model = optional_model_id(value, f"models.{name}")
        except ValueError as exc:
            raise ProtocolError(str(exc)) from None
        if model is not None:
            chosen[name] = model
    return chosen


def turn_attachments(value: object) -> tuple[int, ...]:
    """Client ``attachments``: the ids of uploaded files, in order, at most
    :data:`~agentic_os.attachments.MAX_ATTACHMENTS` and each once. Whether they exist
    (and their total size) is checked by the engine, which answers with
    ``turn.failed``."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProtocolError(ATTACHMENTS_MESSAGE)
    ids: list[int] = []
    for item in value:
        attachment_id = _int(item)
        if attachment_id is None or not 1 <= attachment_id <= MAX_SQLITE_ID:
            raise ProtocolError(ATTACHMENTS_MESSAGE)
        ids.append(attachment_id)
    if len(ids) > MAX_ATTACHMENTS:
        raise ProtocolError(f"Un missatge pot portar com a màxim {MAX_ATTACHMENTS} adjunts.")
    if len(set(ids)) != len(ids):
        raise ProtocolError("Un mateix adjunt no pot anar dues vegades al missatge.")
    return tuple(ids)


def parse_turn_start(data: Mapping[str, object], runtime: RuntimeSettings) -> TurnRequest:
    """A ``turn.start`` message as a :class:`TurnRequest`; ``mode``, ``target``,
    ``options`` and ``models`` default to the runtime settings (summaries use the
    owner's ``fast_models``, and the revisions get the PDFs as the owner's
    ``pdf_in_revisions`` says). The text itself (empty, too long) and whether the
    attachments exist are validated by the engine, which answers with
    ``turn.failed``."""
    request_id = parse_request_id(data)
    try:
        text = data.get("text")
        if not isinstance(text, str):
            raise ProtocolError("«text» ha de ser un text.")
        mode_value = data.get("mode")
        mode: TurnMode = (
            runtime.default_mode
            if mode_value is None
            else _choice(mode_value, TURN_MODES, "«mode» ha de ser «solo», «duel» o «debate».")
        )
        target_value = data.get("target")
        target: AgentName = (
            runtime.default_target
            if target_value is None
            else _choice(target_value, AGENTS, "«target» ha de ser «claude» o «chatgpt».")
        )
        raw_conversation = data.get("conversation_id")
        conversation_id = _int(raw_conversation)
        if raw_conversation is not None and (
            conversation_id is None or not 1 <= conversation_id <= MAX_SQLITE_ID
        ):
            raise ProtocolError("«conversation_id» ha de ser un enter positiu o null.")
        options = turn_options(data.get("options"), runtime)
        models = turn_models(data.get("models"), runtime)
        attachments = turn_attachments(data.get("attachments"))
    except ProtocolError as exc:
        raise ProtocolError(exc.message, request_id=request_id) from None
    return TurnRequest(
        request_id=request_id,
        text=text,
        mode=mode,
        target=target,
        conversation_id=conversation_id,
        options=options,
        models=models,
        fast_models=runtime.chosen_fast_models(),
        attachments=attachments,
        pdf_in_revisions=runtime.pdf_in_revisions,
    )


# -- connection ------------------------------------------------------------------------


class ClientConnection:
    """Outgoing side of one WebSocket: a bounded queue and its writer task.

    :meth:`send` and :meth:`send_batch` never block, so turn events can be fanned
    out synchronously; it is the :class:`~agentic_os.server.turns.Subscriber` given
    to the turn manager."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        queue_size: int = SEND_QUEUE_SIZE,
        max_pending: int = MAX_PENDING_EVENTS,
    ) -> None:
        self._websocket = websocket
        self._queue: asyncio.Queue[str | tuple[str, ...]] = asyncio.Queue(maxsize=queue_size)
        self._max_pending = max_pending
        self.pending = 0
        """Texts queued (inside batches too) and not written yet."""
        self.overflowed = asyncio.Event()
        self.revoked = asyncio.Event()
        """Set when the session of the connection ends (see :meth:`revoke`)."""
        self.closed = False

    def _put(self, item: str | tuple[str, ...], count: int) -> bool:
        if self.closed:
            return False
        if self.pending >= self._max_pending:
            return self._give_up()
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            return self._give_up()
        self.pending += count
        return True

    def _give_up(self) -> bool:
        logger.warning("WebSocket client too slow: dropping the connection")
        self.closed = True
        self.overflowed.set()
        return False

    def send(self, text: str) -> bool:
        return self._put(text, 1)

    def send_batch(self, texts: Sequence[str]) -> bool:
        """Queue several texts in one place of the queue (they are written in order)."""
        batch = tuple(texts)
        return self._put(batch, len(batch))

    def revoke(self) -> None:
        """The session ended: the endpoint closes the socket with ``4401``."""
        self.revoked.set()

    def send_json(self, message: Wire) -> bool:
        return self.send(dumps(message))

    def error(self, message: str, *, code: str = "invalid", request_id: str | None = None) -> None:
        wire: Wire = {"type": "error", "message": message, "code": code}
        if request_id is not None:
            wire["request_id"] = request_id
        self.send_json(wire)

    async def write_loop(self) -> None:
        while True:
            item = await self._queue.get()
            for text in (item,) if isinstance(item, str) else item:
                await self._websocket.send_text(text)
                self.pending -= 1


class ClientSession:
    """Incoming side of one WebSocket: reads and dispatches client messages."""

    def __init__(
        self, state: AppState, websocket: WebSocket, connection: ClientConnection, token: str
    ) -> None:
        self._state = state
        self._websocket = websocket
        self._connection = connection
        self._token = token

    async def hello(self) -> Wire:
        statuses = await self._state.monitor.statuses()
        fx = await self._state.store.current_fx(self._state.clock())
        return {
            "type": "hello",
            "version": __version__,
            "providers": [status_to_wire(s) for s in statuses],
            "fx": fx.to_wire(),
            "active_turns": self._state.turns.active_turns(),
        }

    async def read_loop(self) -> int | None:
        """Handle messages until the client leaves (``None``) or the session ends
        (returns the close code)."""
        self._connection.send_json(await self.hello())
        while True:
            message = await self._websocket.receive()
            if message["type"] == "websocket.disconnect":
                return None
            text = message.get("text")
            if not isinstance(text, str):
                self._connection.error("Només s'accepten missatges de text JSON.")
                continue
            if len(text) > MAX_MESSAGE_CHARS:
                self._connection.error("El missatge és massa gran.", code="too_large")
                continue
            try:
                data = json.loads(text)
            except (ValueError, RecursionError):
                self._connection.error("El missatge no és JSON vàlid.")
                continue
            if not isinstance(data, dict) or not isinstance(data.get("type"), str):
                self._connection.error("El missatge ha de ser un objecte amb un camp «type».")
                continue
            kind: str = data["type"]
            if not await self._session_alive(touch=kind in ACTIVITY_MESSAGES):
                return CLOSE_UNAUTHORIZED
            try:
                if has_invalid_text(data):
                    # Nothing of it is used, nor echoed: it could not even be sent back.
                    raise ProtocolError(INVALID_TEXT_MESSAGE, request_id=valid_request_id(data))
                await self._dispatch(kind, data)
            except ProtocolError as exc:
                self._connection.error(exc.message, request_id=exc.request_id)
            except Exception:
                logger.exception("Error handling a %r WebSocket message", kind[:40])
                self._connection.error(
                    "Error intern en processar el missatge.",
                    code="internal",
                    request_id=data.get("request_id")
                    if isinstance(data.get("request_id"), str)
                    else None,
                )

    async def _session_alive(self, *, touch: bool) -> bool:
        """Whether the session is still live. Only ``touch`` checks count as activity
        (they refresh the idle timeout); the others and the watchdog are read-only."""
        sessions, now = self._state.sessions, self._state.clock()
        if touch:
            return await sessions.validate(self._token, now) is not None
        return await sessions.peek(self._token, now) is not None

    async def watch_session(self) -> int:
        """Return ``4401`` once the session ends: at once when it is revoked in this
        process, otherwise within :data:`SESSION_CHECK_SECONDS` (revoked by the CLI,
        idle or expired) even if the client sends nothing."""
        while True:
            try:
                await asyncio.wait_for(self._connection.revoked.wait(), SESSION_CHECK_SECONDS)
            except TimeoutError:
                if await self._session_alive(touch=False):
                    continue
            return CLOSE_UNAUTHORIZED

    async def _dispatch(self, kind: str, data: dict[str, object]) -> None:
        turns = self._state.turns
        if kind == "ping":
            t = data.get("t")
            # A finite number within the range of JavaScript's safe integers keeps the
            # pong as small as the ping (math.isfinite would overflow on a huge int).
            if (
                isinstance(t, bool)
                or not isinstance(t, int | float)
                or (isinstance(t, float) and not math.isfinite(t))
                or abs(t) > 2**53
            ):
                raise ProtocolError("«t» ha de ser un número.")
            self._connection.send_json({"type": "pong", "t": t})
        elif kind == "turn.start":
            runtime = await self._state.store.get_runtime_settings()
            request = parse_turn_start(data, runtime)
            try:
                turns.start(
                    request,
                    self._connection,
                    compaction_threshold_tokens=runtime.compaction_threshold_tokens,
                    price_overrides=runtime.prices,
                )
            except TurnRejectedError as exc:
                self._connection.error(exc.message, code=exc.code, request_id=request.request_id)
        elif kind == "turn.cancel":
            request_id = parse_request_id(data)
            if not turns.cancel(request_id):
                self._connection.send_json({"type": "turn.unknown", "request_id": request_id})
        elif kind == "turn.subscribe":
            request_id = parse_request_id(data)
            after_seq = _int(data.get("after_seq", 0))
            if after_seq is None or after_seq < 0:
                raise ProtocolError("«after_seq» ha de ser un enter ≥ 0.", request_id=request_id)
            # A turn this connection already gets is not replayed again (see
            # TurnManager.subscribe): the message is ignored.
            if not turns.subscribe(request_id, self._connection, after_seq):
                self._connection.send_json({"type": "turn.unknown", "request_id": request_id})
        else:
            raise ProtocolError(f"Tipus de missatge desconegut: «{kind[:40]}».")


async def _close(websocket: WebSocket, code: int) -> None:
    if websocket.application_state != WebSocketState.DISCONNECTED:
        with contextlib.suppress(Exception):
            await websocket.close(code=code)


@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    state = app_state(websocket)
    await websocket.accept()
    if not origin_allowed(websocket.headers.get("origin"), state.allowed_origins):
        await _close(websocket, CLOSE_FORBIDDEN_ORIGIN)
        return
    token = websocket.cookies.get(state.cookie_name)
    # Read-only: a (re)connection is not the owner's activity.
    if token is None or await state.sessions.peek(token, state.clock()) is None:
        await _close(websocket, CLOSE_UNAUTHORIZED)
        return

    connection = ClientConnection(websocket)
    session = ClientSession(state, websocket, connection, token)
    token_hash = hash_token(token)
    state.connections.add(token_hash, connection)
    reader = asyncio.create_task(session.read_loop(), name="ws-reader")
    writer = asyncio.create_task(connection.write_loop(), name="ws-writer")
    overflow = asyncio.create_task(connection.overflowed.wait(), name="ws-overflow")
    watchdog = asyncio.create_task(session.watch_session(), name="ws-session-watchdog")
    close_code: int | None = None
    try:
        await asyncio.wait(
            (reader, writer, overflow, watchdog), return_when=asyncio.FIRST_COMPLETED
        )
        if overflow.done():
            close_code = CLOSE_TOO_SLOW
        elif reader.done() and not reader.cancelled():
            close_code = _outcome(reader, "reader")
        elif watchdog.done() and not watchdog.cancelled():
            close_code = _outcome(watchdog, "session watchdog")
    finally:
        # Turns keep running: only this connection stops receiving their events.
        connection.closed = True
        state.connections.discard(token_hash, connection)
        state.turns.detach(connection)
        await cancel_and_wait((reader, writer, overflow, watchdog))
    if close_code is not None:
        await _close(websocket, close_code)


def _outcome(task: asyncio.Task[int | None], name: str) -> int | None:
    """Close code returned by a finished task; ``1011`` if it crashed."""
    error = task.exception()
    if error is not None:
        logger.error("WebSocket %s crashed", name, exc_info=error)
        return CLOSE_INTERNAL_ERROR
    return task.result()
