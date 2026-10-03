"""Application state shared by the routes and FastAPI dependencies.

No ``from __future__ import annotations`` in the modules that declare FastAPI
parameters: FastAPI resolves string annotations late and can get them wrong
(research pitfall with ``Annotated[..., Cookie(alias=...)]``). Cookies are read
from ``request.cookies`` with the name from the settings instead.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Final, Protocol

from fastapi import Depends, HTTPException, Request
from starlette.requests import ClientDisconnect, HTTPConnection

from agentic_os.attachments import PdfReader
from agentic_os.config import Settings
from agentic_os.i18n import t
from agentic_os.security.devices import DeviceManager
from agentic_os.security.sessions import SessionManager, hash_token, is_well_formed
from agentic_os.security.throttle import LoginThrottle
from agentic_os.server.catalog import ModelCatalog
from agentic_os.server.fx_rates import FxRefresher
from agentic_os.server.middleware import normalize_origins
from agentic_os.server.status import ProviderMonitor
from agentic_os.server.turns import TurnManager
from agentic_os.storage import SessionRecord, SqliteStore

SECURE_COOKIE_NAME: Final = "__Host-aos_session"
DEV_COOKIE_NAME: Final = "aos_session"
SECURE_DEVICE_COOKIE_NAME: Final = "__Host-aos_device"
DEV_DEVICE_COOKIE_NAME: Final = "aos_device"
BACKGROUND_HEADER: Final = "X-AOS-Background"
"""With the value ``1``, marks a request the client makes by itself, not because the
owner did something (refreshes after ``hello`` or a reconnection, periodic refreshes,
retries): its session check is read-only (docs/PROTOCOL.md)."""
MAX_SQLITE_ID: Final = 2**63 - 1
"""The largest INTEGER SQLite stores: no row id can be larger, and Python's sqlite3
refuses to even send one (``OverflowError``)."""


def cookie_name(settings: Settings) -> str:
    """``__Host-aos_session`` with secure cookies, ``aos_session`` for local http."""
    return SECURE_COOKIE_NAME if settings.secure_cookies else DEV_COOKIE_NAME


def device_cookie_name(settings: Settings) -> str:
    """``__Host-aos_device`` with secure cookies, ``aos_device`` for local http."""
    return SECURE_DEVICE_COOKIE_NAME if settings.secure_cookies else DEV_DEVICE_COOKIE_NAME


class Revocable(Protocol):
    """A long-lived connection that must stop when its session ends."""

    def revoke(self) -> None: ...


class SessionConnections:
    """Open WebSockets by session (token hash), so that ending a session in this
    process (logout) closes its sockets at once. Revocations by the CLI, which runs
    in another process, are noticed by each socket's periodic check instead."""

    def __init__(self) -> None:
        self._by_session: dict[str, set[Revocable]] = {}

    def add(self, token_hash: str, connection: Revocable) -> None:
        self._by_session.setdefault(token_hash, set()).add(connection)

    def discard(self, token_hash: str, connection: Revocable) -> None:
        connections = self._by_session.get(token_hash)
        if connections is not None:
            connections.discard(connection)
            if not connections:
                del self._by_session[token_hash]

    def revoke(self, token_hash: str) -> int:
        """Close every connection of the session; returns how many there were."""
        connections = self._by_session.pop(token_hash, set())
        for connection in connections:
            connection.revoke()
        return len(connections)


@dataclass(slots=True)
class AppState:
    """Everything the lifespan opens, stored in ``app.state.aos``."""

    settings: Settings
    store: SqliteStore
    sessions: SessionManager
    devices: DeviceManager
    throttle: LoginThrottle
    turns: TurnManager
    monitor: ProviderMonitor
    catalog: ModelCatalog
    fx: FxRefresher
    clock: Callable[[], datetime]
    login_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Serializes login attempts so the throttle check and the failure count are
    atomic: parallel requests cannot test more passwords than the lockout allows."""
    connections: SessionConnections = field(default_factory=SessionConnections)
    """Open WebSockets by session, closed when the session ends (logout)."""
    pdf: PdfReader = field(default_factory=PdfReader)
    """Reads the page count and the text of uploaded PDFs, in subprocesses."""

    @property
    def cookie_name(self) -> str:
        return cookie_name(self.settings)

    @property
    def device_cookie_name(self) -> str:
        return device_cookie_name(self.settings)

    @property
    def allowed_origins(self) -> frozenset[str]:
        return normalize_origins(self.settings.allowed_origins)

    async def session_for(self, connection: HTTPConnection) -> SessionRecord | None:
        """The live session of the request's cookie, or ``None``. The check is the
        owner's activity (it refreshes the idle timeout) unless the client marked the
        request as its own (:func:`is_background`): then it is read-only, so a tab
        left open does not keep the session alive by refreshing things."""
        token = connection.cookies.get(self.cookie_name)
        if is_background(connection):
            return await self.sessions.peek(token, self.clock())
        return await self.sessions.validate(token, self.clock())

    async def end_session(self, token: str | None) -> None:
        """Revoke the session of ``token`` and close its open WebSockets."""
        await self.sessions.revoke(token)
        if is_well_formed(token):
            self.connections.revoke(hash_token(token))


def app_state(connection: HTTPConnection) -> AppState:
    """The :class:`AppState` of a request or WebSocket (set by the lifespan)."""
    state = getattr(connection.app.state, "aos", None)
    if not isinstance(state, AppState):
        raise RuntimeError("The application lifespan has not started")
    return state


def get_state(request: Request) -> AppState:
    return app_state(request)


StateDep = Annotated[AppState, Depends(get_state)]


async def require_session(request: Request, state: StateDep) -> SessionRecord:
    """Dependency of every authenticated route: 401 without a live session."""
    session = await state.session_for(request)
    if session is None:
        raise HTTPException(status_code=401, detail=t("server.login_required"))
    return session


SessionDep = Annotated[SessionRecord, Depends(require_session)]


def is_background(connection: HTTPConnection) -> bool:
    """Whether the client made the request by itself (``X-AOS-Background: 1``)."""
    return connection.headers.get(BACKGROUND_HEADER, "").strip() == "1"


def client_ip(connection: HTTPConnection) -> str | None:
    """Client address (uvicorn already applied the trusted proxy headers)."""
    return connection.client.host if connection.client else None


def is_utf8(text: str) -> bool:
    """Whether ``text`` can be encoded as UTF-8 (it has no lone surrogate)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def has_invalid_text(value: object) -> bool:
    """Whether decoded JSON holds a string, as a key or a value at any depth, that
    cannot be encoded as UTF-8: a lone surrogate, which JSON allows as ``"\\ud800"``.
    Such text could be neither stored nor sent back to the client."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if not is_utf8(item):
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


async def read_json(request: Request, *, utf8_only: bool = True) -> object:
    """The decoded JSON body. 422 if it is not valid JSON or, with ``utf8_only``, if
    it holds text that is not valid UTF-8 (:func:`has_invalid_text`); 400 (without a
    traceback in the logs) if the client disconnects before sending all of it."""
    try:
        body = await request.body()
    except ClientDisconnect:
        raise HTTPException(status_code=400, detail=t("server.client_disconnected")) from None
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        raise HTTPException(status_code=422, detail=t("server.invalid_json")) from None
    if utf8_only and has_invalid_text(value):
        raise HTTPException(status_code=422, detail=t("server.invalid_text"))
    return value
