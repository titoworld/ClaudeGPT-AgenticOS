"""Pure ASGI middlewares (no ``BaseHTTPMiddleware``, so streaming responses and
WebSockets pass through untouched): security headers, request body limit and the
``Origin`` check for state-changing requests.

Order, outermost first: security headers -> Origin check -> body limit -> app, so
the 403 and 413 answers of the inner two also carry the security headers.
"""

from __future__ import annotations

from collections.abc import Iterable
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
STATE_CHANGING_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})

FORBIDDEN_ORIGIN_DETAIL: Final = "Origen no permès."
TOO_LARGE_DETAIL: Final = "La petició és massa gran (màxim 1 MiB)."


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


class RequestTooLargeError(HTTPException):
    """Raised from ``receive`` when the body exceeds the limit. It is an
    ``HTTPException`` so FastAPI re-raises it while parsing a body (instead of
    turning it into a 400) and the exception handler answers 413."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=TOO_LARGE_DETAIL)


class BodyLimitMiddleware:
    """Limits HTTP request bodies to ``max_bytes`` (FastAPI ignores Starlette's own
    ``max_body_size``). A larger ``Content-Length`` is refused before reading; a
    chunked body is counted as it arrives."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self._max = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(raw=scope["headers"]).get("content-length")
        if declared is not None and declared.strip().isdigit() and int(declared) > self._max:
            await self._reject(scope, receive, send)
            return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max:
                    raise RequestTooLargeError()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestTooLargeError:
            # Normally the app's exception handler already answered; this is the
            # fallback when the error escaped it.
            if started:
                raise
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": TOO_LARGE_DETAIL}, status_code=413)
        await response(scope, receive, send)
