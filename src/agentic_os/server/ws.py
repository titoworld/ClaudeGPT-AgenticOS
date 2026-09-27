"""WebSocket ``/api/ws`` (docs/PROTOCOL.md): one persistent connection per tab.

The handshake is accepted first so the browser sees the close codes: ``4403`` for a
foreign ``Origin`` and ``4401`` without a live session. Every connection has a
bounded outgoing queue drained by a writer task; a client that cannot keep up is
disconnected (code 1013) and recovers what it missed with ``turn.subscribe`` after
reconnecting. Invalid messages get an ``error`` answer; they never close the socket.
"""

import asyncio
import contextlib
import json
import logging
import math
from collections.abc import Mapping
from typing import Final

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketState

from agentic_os import __version__
from agentic_os.domain import AGENTS, AgentName, TurnMode, TurnOptions
from agentic_os.orchestrator.events import Wire
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.server.deps import AppState, app_state
from agentic_os.server.middleware import origin_allowed
from agentic_os.server.status import status_to_wire
from agentic_os.server.tasks import cancel_and_wait
from agentic_os.server.turns import TurnRejectedError, dumps
from agentic_os.storage import RuntimeSettings
from agentic_os.storage.models import TURN_MODES

logger = logging.getLogger(__name__)

router = APIRouter()

CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_FORBIDDEN_ORIGIN: Final = 4403
CLOSE_TOO_SLOW: Final = 1013
CLOSE_INTERNAL_ERROR: Final = 1011
MAX_MESSAGE_CHARS: Final = 512 * 1024
SEND_QUEUE_SIZE: Final = 4096
"""Messages waiting to be written; a full queue drops the connection."""
MAX_REQUEST_ID_LENGTH: Final = 128


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


def parse_turn_start(data: Mapping[str, object], runtime: RuntimeSettings) -> TurnRequest:
    """A ``turn.start`` message as a :class:`TurnRequest`; ``mode``, ``target`` and
    ``options`` default to the runtime settings. The text itself (empty, too long)
    is validated by the engine, which answers with ``turn.failed``."""
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
        if raw_conversation is not None and (conversation_id is None or conversation_id < 1):
            raise ProtocolError("«conversation_id» ha de ser un enter positiu o null.")
        options = turn_options(data.get("options"), runtime)
    except ProtocolError as exc:
        raise ProtocolError(exc.message, request_id=request_id) from None
    return TurnRequest(
        request_id=request_id,
        text=text,
        mode=mode,
        target=target,
        conversation_id=conversation_id,
        options=options,
    )


# -- connection ------------------------------------------------------------------------


class ClientConnection:
    """Outgoing side of one WebSocket: a bounded queue and its writer task.

    :meth:`send` never blocks, so turn events can be fanned out synchronously; it is
    the :class:`~agentic_os.server.turns.Subscriber` given to the turn manager."""

    def __init__(self, websocket: WebSocket, *, queue_size: int = SEND_QUEUE_SIZE) -> None:
        self._websocket = websocket
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self.overflowed = asyncio.Event()
        self.closed = False

    def send(self, text: str) -> bool:
        if self.closed:
            return False
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            logger.warning("WebSocket client too slow: dropping the connection")
            self.closed = True
            self.overflowed.set()
            return False
        return True

    def send_json(self, message: Wire) -> bool:
        return self.send(dumps(message))

    def error(self, message: str, *, code: str = "invalid", request_id: str | None = None) -> None:
        wire: Wire = {"type": "error", "message": message, "code": code}
        if request_id is not None:
            wire["request_id"] = request_id
        self.send_json(wire)

    async def write_loop(self) -> None:
        while True:
            text = await self._queue.get()
            await self._websocket.send_text(text)


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
        return {
            "type": "hello",
            "version": __version__,
            "providers": [status_to_wire(s) for s in statuses],
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
            if (
                kind != "ping"
                and await self._state.sessions.validate(self._token, self._state.clock()) is None
            ):
                return CLOSE_UNAUTHORIZED
            try:
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

    async def _dispatch(self, kind: str, data: dict[str, object]) -> None:
        turns = self._state.turns
        if kind == "ping":
            t = data.get("t")
            if isinstance(t, bool) or not isinstance(t, int | float) or not math.isfinite(t):
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
    if token is None or await state.sessions.validate(token, state.clock()) is None:
        await _close(websocket, CLOSE_UNAUTHORIZED)
        return

    connection = ClientConnection(websocket)
    session = ClientSession(state, websocket, connection, token)
    reader = asyncio.create_task(session.read_loop(), name="ws-reader")
    writer = asyncio.create_task(connection.write_loop(), name="ws-writer")
    overflow = asyncio.create_task(connection.overflowed.wait(), name="ws-overflow")
    close_code: int | None = None
    try:
        await asyncio.wait((reader, writer, overflow), return_when=asyncio.FIRST_COMPLETED)
        if overflow.done():
            close_code = CLOSE_TOO_SLOW
        elif reader.done() and not reader.cancelled():
            error = reader.exception()
            if error is not None:
                logger.error("WebSocket reader crashed", exc_info=error)
                close_code = CLOSE_INTERNAL_ERROR
            else:
                close_code = reader.result()
    finally:
        # Turns keep running: only this connection stops receiving their events.
        connection.closed = True
        state.turns.detach(connection)
        await cancel_and_wait((reader, writer, overflow))
    if close_code is not None:
        await _close(websocket, close_code)
