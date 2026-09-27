"""FastAPI application factory (``uvicorn agentic_os.server.app:create_app --factory``)."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime
from http import HTTPStatus
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from agentic_os import __version__
from agentic_os.config import Settings, get_settings
from agentic_os.domain import AgentName
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.types import EngineConfig
from agentic_os.providers.base import Provider
from agentic_os.providers.factory import build_providers
from agentic_os.security.sessions import SessionManager
from agentic_os.security.throttle import LoginThrottle
from agentic_os.server import routes_api, routes_auth, ws
from agentic_os.server.catalog import ModelCatalog
from agentic_os.server.deps import AppState
from agentic_os.server.fx_rates import FxFetcher, FxRefresher
from agentic_os.server.middleware import (
    BodyLimitMiddleware,
    OriginCheckMiddleware,
    SecurityHeadersMiddleware,
)
from agentic_os.server.static import SpaFallback, find_web_dist
from agentic_os.server.status import ProviderMonitor
from agentic_os.server.tasks import cancel_and_wait
from agentic_os.server.turns import TurnManager
from agentic_os.storage import SqliteStore, utc_now

logger = logging.getLogger(__name__)

MAINTENANCE_INTERVAL_SECONDS: Final = 3600.0

_DEFAULT_DETAILS: Final[dict[int, str]] = {
    400: "Petició incorrecta.",
    401: "Cal iniciar sessió.",
    403: "Accés denegat.",
    404: "No s'ha trobat.",
    405: "Mètode no permès.",
    413: "La petició és massa gran.",
    422: "Dades no vàlides.",
    429: "Massa intents.",
    500: "Error intern del servidor.",
    503: "Servei no disponible.",
}


async def _http_error(request: Request, exc: Exception) -> Response:
    """``{"detail"}`` in Catalan for every HTTP error (Starlette's defaults are the
    English status phrases)."""
    if not isinstance(exc, HTTPException):  # pragma: no cover - registered for it
        raise exc
    detail: object = exc.detail
    if detail == HTTPStatus(exc.status_code).phrase:
        detail = _DEFAULT_DETAILS.get(exc.status_code, detail)
    if exc.status_code in (204, 304) or exc.status_code < 200:
        return Response(status_code=exc.status_code, headers=exc.headers)
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)


async def _validation_error(request: Request, exc: Exception) -> Response:
    """422 with a Catalan string ``detail`` naming the invalid fields."""
    fields: list[str] = []
    if isinstance(exc, RequestValidationError):
        for error in exc.errors():
            location = [str(p) for p in error.get("loc", ()) if p not in ("body", "query", "path")]
            name = ".".join(location)
            if name and name not in fields:
                fields.append(name)
    listed = ", ".join(f"«{name}»" for name in fields)
    detail = f"Dades no vàlides: {listed}." if listed else "Dades no vàlides."
    return JSONResponse({"detail": detail}, status_code=422)


async def _internal_error(request: Request, exc: Exception) -> Response:
    return JSONResponse({"detail": _DEFAULT_DETAILS[500]}, status_code=500)


async def _maintenance(state: AppState) -> None:
    """Hourly purge of expired sessions, cached turns and stale throttle counters."""
    while True:
        now = state.clock()
        try:
            await state.store.purge_expired_cache(now)
            await state.sessions.purge_expired(now)
            await state.throttle.purge(now)
        except Exception:
            logger.exception("Periodic maintenance failed")
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)


async def _close_providers(providers: Mapping[AgentName, Provider]) -> None:
    results = await asyncio.gather(
        *(provider.aclose() for provider in providers.values()), return_exceptions=True
    )
    for agent, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("Could not close the %s provider", agent, exc_info=result)


def create_app(
    settings: Settings | None = None,
    *,
    providers: Mapping[AgentName, Provider] | None = None,
    clock: Callable[[], datetime] = utc_now,
    fx_fetcher: FxFetcher | None = None,
) -> FastAPI:
    """Build the application.

    ``settings`` defaults to the process settings (``AOS_*``). ``providers``
    replaces the ones built from the settings (tests, demos); either way the app
    closes them on shutdown. ``clock`` (aware UTC) drives sessions, throttling,
    stats and the timestamps of stored rows. ``fx_fetcher`` replaces the download
    of the ECB exchange rate (tests).
    """
    settings = settings if settings is not None else get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as stack:
            store = await SqliteStore.open(settings.db_path, clock=clock)
            stack.push_async_callback(store.close)
            active = dict(providers) if providers is not None else build_providers(settings)
            stack.push_async_callback(_close_providers, active)

            runtime = await store.get_runtime_settings()
            config = EngineConfig(compaction_threshold_tokens=runtime.compaction_threshold_tokens)
            turns = TurnManager(Engine(active, store, config))
            stack.push_async_callback(turns.aclose)
            monitor = ProviderMonitor(active)
            stack.push_async_callback(monitor.aclose)
            catalog = ModelCatalog(active, settings)
            stack.push_async_callback(catalog.aclose)
            fx = FxRefresher(store, clock, fetcher=fx_fetcher)

            state = AppState(
                settings=settings,
                store=store,
                sessions=SessionManager.from_settings(store, settings),
                throttle=LoginThrottle.from_settings(store, settings),
                turns=turns,
                monitor=monitor,
                catalog=catalog,
                fx=fx,
                clock=clock,
            )
            maintenance = asyncio.create_task(_maintenance(state), name="maintenance")
            fx_refresh = asyncio.create_task(fx.run(), name="fx-refresh")

            stack.push_async_callback(cancel_and_wait, (maintenance, fx_refresh))
            monitor.refresh()
            app.state.aos = state
            logger.info(
                "ClaudeGPT OS %s ready (claude: %s, chatgpt: %s)",
                __version__,
                active["claude"].mode if "claude" in active else "-",
                active["chatgpt"].mode if "chatgpt" in active else "-",
            )
            try:
                yield
            finally:
                del app.state.aos

    app = FastAPI(
        title="ClaudeGPT OS",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _internal_error)

    # Last added = outermost.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.secure_cookies)

    app.include_router(routes_api.public_router)
    app.include_router(routes_auth.router)
    app.include_router(routes_api.router)
    app.include_router(ws.router)

    dist = find_web_dist(settings)
    if dist is None:
        logger.warning("Frontend build not found: / will explain how to build it")
    app.router.default = SpaFallback(dist)
    return app
