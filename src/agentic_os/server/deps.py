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
from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request
from starlette.requests import HTTPConnection

from agentic_os.config import Settings
from agentic_os.security.sessions import SessionManager
from agentic_os.security.throttle import LoginThrottle
from agentic_os.server.catalog import ModelCatalog
from agentic_os.server.fx_rates import FxRefresher
from agentic_os.server.middleware import normalize_origins
from agentic_os.server.status import ProviderMonitor
from agentic_os.server.turns import TurnManager
from agentic_os.storage import SessionRecord, SqliteStore

SECURE_COOKIE_NAME: Final = "__Host-aos_session"
DEV_COOKIE_NAME: Final = "aos_session"
UNAUTHORIZED_DETAIL: Final = "Cal iniciar sessió."


def cookie_name(settings: Settings) -> str:
    """``__Host-aos_session`` with secure cookies, ``aos_session`` for local http."""
    return SECURE_COOKIE_NAME if settings.secure_cookies else DEV_COOKIE_NAME


@dataclass(slots=True)
class AppState:
    """Everything the lifespan opens, stored in ``app.state.aos``."""

    settings: Settings
    store: SqliteStore
    sessions: SessionManager
    throttle: LoginThrottle
    turns: TurnManager
    monitor: ProviderMonitor
    catalog: ModelCatalog
    fx: FxRefresher
    clock: Callable[[], datetime]
    login_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Serializes login attempts so the throttle check and the failure count are
    atomic: parallel requests cannot test more passwords than the lockout allows."""

    @property
    def cookie_name(self) -> str:
        return cookie_name(self.settings)

    @property
    def allowed_origins(self) -> frozenset[str]:
        return normalize_origins(self.settings.allowed_origins)

    async def session_for(self, connection: HTTPConnection) -> SessionRecord | None:
        """The live session of the request's cookie, or ``None``."""
        token = connection.cookies.get(self.cookie_name)
        return await self.sessions.validate(token, self.clock())


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
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL)
    return session


SessionDep = Annotated[SessionRecord, Depends(require_session)]


def client_ip(connection: HTTPConnection) -> str | None:
    """Client address (uvicorn already applied the trusted proxy headers)."""
    return connection.client.host if connection.client else None


async def read_json(request: Request) -> object:
    """The decoded JSON body; 422 if it is not valid JSON."""
    body = await request.body()
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise HTTPException(
            status_code=422, detail="El cos de la petició ha de ser JSON vàlid."
        ) from None
