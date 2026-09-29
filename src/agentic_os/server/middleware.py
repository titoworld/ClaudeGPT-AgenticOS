"""Pure ASGI middlewares (no ``BaseHTTPMiddleware``, so streaming responses and
WebSockets pass through untouched): security headers, ``Connection: close`` for
answers given before the request body arrived, the ``Origin`` check for
state-changing requests and the request body limit.

Order, outermost first: security headers -> unread body -> Origin check -> body
limit -> app, so the 403 and 413 answers of the inner two also carry the security
headers and close the connection when their body was never read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY: Final = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "style-src-attr 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    )
)
"""Same policy as ``web/vite.config.ts`` (``vite preview``)."""

PERMISSIONS_POLICY: Final = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
HSTS: Final = "max-age=63072000; includeSubDomains"
MAX_BODY_BYTES: Final = 1024 * 1024
SMALL_BODY_PATHS: Final[Mapping[str, int]] = {"/api/auth/login": 4096}
"""Routes readable without a session get a tiny limit, so stalled anonymous bodies
cannot pile up memory while they wait for the deadline."""
BODY_TIMEOUT_SECONDS: Final = 15.0
"""Time allowed to receive a whole request body, from the app's first read."""
STATE_CHANGING_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})

FORBIDDEN_ORIGIN_DETAIL: Final = "Origen no permès."
TOO_SLOW_DETAIL: Final = "La petició ha trigat massa a arribar. Torna-ho a provar."


def size_text(size: int) -> str:
    """A number of bytes as the docs write it: ``1 MiB``, ``4 KiB``, ``1000 bytes``."""
    for factor, unit in ((1024 * 1024, "MiB"), (1024, "KiB")):
        if size >= factor and size % factor == 0:
            return f"{size // factor} {unit}"
    return f"{size} bytes"


def too_large_detail(limit: int) -> str:
    """The ``detail`` of a 413: it names the limit that applied (the login's is far
    smaller than the others)."""
    return f"La petició és massa gran (màxim {size_text(limit)})."


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def normalize_origins(origins: Iterable[str]) -> frozenset[str]:
    """Origins as compared by :func:`origin_allowed` (lowercase, no trailing slash)."""
    return frozenset(o.strip().rstrip("/").lower() for o in origins if o.strip())


def origin_allowed(origin: str | None, allowed: frozenset[str]) -> bool:
    """``True`` if the ``Origin`` header is one of ``allowed`` (see
    :func:`normalize_origins`). A missing or ``null`` origin is never allowed."""
    if not origin:
        return False
    return origin.strip().rstrip("/").lower() in allowed


class SecurityHeadersMiddleware:
    """Adds the security headers to every HTTP response (HSTS only when ``hsts``)
    and ``Cache-Control: no-store`` to ``/api`` responses that set no caching."""

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        headers = [
            ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("Permissions-Policy", PERMISSIONS_POLICY),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
        ]
        if hsts:
            headers.append(("Strict-Transport-Security", HSTS))
        self._headers: Final = tuple(headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        api = is_api_path(scope["path"])

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._headers:
                    if name not in headers:
                        headers[name] = value
                if api and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def expects_body(headers: Headers) -> bool:
    """Whether the request announces a body (``Content-Length`` > 0 or any
    ``Transfer-Encoding``); without either, an HTTP/1.1 request has none."""
    if "transfer-encoding" in headers:
        return True
    declared = headers.get("content-length")
    if declared is None:
        return False
    declared = declared.strip()
    return not declared.isdigit() or int(declared) > 0


class CloseUnreadBodyMiddleware:
    """Adds ``Connection: close`` to a response started while part of the request
    body has not been received yet (403, 401, 413 or 429 answered before reading
    it, or a body sent to a route that ignores it).

    uvicorn discards the rest of such a body as it arrives, and every chunk
    restarts its keep-alive timer, so a client trickling it could otherwise hold the
    connection (and a file descriptor) forever. With ``Connection: close`` uvicorn
    closes the socket right after the answer. Requests whose body was read to the
    end keep their connection."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not expects_body(Headers(raw=scope["headers"])):
            await self.app(scope, receive, send)
            return
        body_done = False

        async def tracking_receive() -> Message:
            nonlocal body_done
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                body_done = True
            return message

        async def closing_send(message: Message) -> None:
            if message["type"] == "http.response.start" and not body_done:
                MutableHeaders(scope=message)["Connection"] = "close"
            await send(message)

        await self.app(scope, tracking_receive, closing_send)


class OriginCheckMiddleware:
    """Rejects ``POST``/``PUT``/``PATCH``/``DELETE`` requests whose ``Origin`` header
    is missing or not allowed with ``403 {"detail"}`` (CSRF defence on top of the
    SameSite=Strict cookie). The WebSocket handshake is checked by the endpoint."""

    def __init__(self, app: ASGIApp, *, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self._allowed = normalize_origins(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in STATE_CHANGING_METHODS:
            origin = Headers(raw=scope["headers"]).get("origin")
            if not origin_allowed(origin, self._allowed):
                response = JSONResponse({"detail": FORBIDDEN_ORIGIN_DETAIL}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class BodyError(HTTPException):
    """Raised from ``receive`` when the body is refused. It is an ``HTTPException``
    so FastAPI re-raises it while parsing a body (instead of turning it into a 400)
    and the exception handler answers with its status."""


class RequestTooLargeError(BodyError):
    def __init__(self, limit: int) -> None:
        super().__init__(status_code=413, detail=too_large_detail(limit))


class RequestTimeoutError(BodyError):
    """The body did not arrive in time. ``Connection: close`` makes uvicorn drop the
    connection after the answer instead of waiting for the rest of the body."""

    def __init__(self) -> None:
        super().__init__(status_code=408, detail=TOO_SLOW_DETAIL, headers={"Connection": "close"})


class BodyLimitMiddleware:
    """Limits HTTP request bodies to ``max_bytes`` (FastAPI ignores Starlette's own
    ``max_body_size``) and ``timeout`` seconds. A larger ``Content-Length`` is
    refused before reading; a chunked body is counted as it arrives. The deadline
    starts with the app's first read of the body, so a client that trickles it (or
    stops sending) cannot hold a connection: it gets ``408`` and is disconnected."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int = MAX_BODY_BYTES,
        timeout: float | None = None,
        small_paths: Mapping[str, int] = SMALL_BODY_PATHS,
    ) -> None:
        self.app = app
        self._max = max_bytes
        self._timeout = BODY_TIMEOUT_SECONDS if timeout is None else timeout
        self._small = small_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._small.get(scope["path"], self._max)
        declared = Headers(raw=scope["headers"]).get("content-length")
        if declared is not None and declared.strip().isdigit() and int(declared) > limit:
            await self._reject(scope, receive, send, RequestTooLargeError(limit))
            return

        received = 0
        deadline: float | None = None
        body_done = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, deadline, body_done
            if body_done:  # e.g. waiting for http.disconnect while streaming
                return await receive()
            if deadline is None:
                deadline = asyncio.get_running_loop().time() + self._timeout
            try:
                async with asyncio.timeout_at(deadline):
                    message = await receive()
            except TimeoutError:
                raise RequestTimeoutError() from None
            if message["type"] != "http.request" or not message.get("more_body", False):
                body_done = True
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestTooLargeError(limit)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyError as exc:
            # Normally the app's exception handler already answered; this is the
            # fallback when the error escaped it.
            if started:
                raise
            await self._reject(scope, receive, send, exc)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, error: BodyError) -> None:
        response = JSONResponse(
            {"detail": error.detail}, status_code=error.status_code, headers=error.headers
        )
        await response(scope, receive, send)
