"""Serves the built SPA (``web/dist``) as the router's fallback.

- ``/assets/*`` (content-hashed by Vite): ``Cache-Control: public, max-age=31536000,
  immutable``; a missing asset is a 404, never ``index.html``.
- Other ``GET``/``HEAD`` paths: the file if it exists at the top of the build
  (favicon...), otherwise ``index.html`` (client-side routes), with ``no-cache``.
- ``/api/*`` never falls back: unknown API paths are JSON 404s.
- Without a build, a small page in the client's language explains how to build the
  frontend.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocketClose

from agentic_os import i18n
from agentic_os.config import Settings
from agentic_os.i18n import t
from agentic_os.server.middleware import is_api_path

IMMUTABLE: Final = "public, max-age=31536000, immutable"
NO_CACHE: Final = "no-cache"

_REPO_ROOT: Final = Path(__file__).resolve().parents[3]
DIST_CANDIDATES: Final = (_REPO_ROOT / "web" / "dist", Path("/app/web/dist"))
"""Where the build is looked for when ``AOS_WEB_DIST`` is not set."""


def not_built_page() -> str:
    """The page answered without a build, in the language in force. (Its texts come from
    the catalog, which holds no markup: nothing in them needs escaping.)"""
    return f"""<!doctype html>
<html lang="{i18n.current()}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ClaudeGPT OS</title>
</head>
<body>
<h1>ClaudeGPT OS</h1>
<p>{t("server.not_built.running")}</p>
<p>{t("server.not_built.build")}</p>
<pre>cd web
npm ci
npm run build</pre>
<p>{t("server.not_built.or_variable", variable="<code>AOS_WEB_DIST</code>")}</p>
</body>
</html>
"""


def find_web_dist(settings: Settings) -> Path | None:
    """The build directory (one with an ``index.html``): ``settings.web_dist`` if
    set, otherwise ``<repo>/web/dist`` or ``/app/web/dist``. ``None`` if missing."""
    candidates = (settings.web_dist,) if settings.web_dist is not None else DIST_CANDIDATES
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


class SpaFallback:
    """ASGI app installed as ``app.router.default`` (called when no route matches)."""

    def __init__(self, dist: Path | None) -> None:
        self.dist = dist
        self._files = StaticFiles(directory=dist, check_dir=False) if dist else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await WebSocketClose()(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        path: str = scope["path"]
        if is_api_path(path):
            raise HTTPException(status_code=404)
        if scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=405, headers={"Allow": "GET, HEAD"})
        response = await self._response(path, scope)
        await response(scope, receive, send)

    async def _response(self, path: str, scope: Scope) -> Response:
        if self._files is None:
            return HTMLResponse(
                not_built_page(), status_code=503, headers={"Cache-Control": NO_CACHE}
            )
        relative = os.path.normpath(path.lstrip("/")) if path.strip("/") else "index.html"
        if relative.startswith("assets" + os.sep):
            response = await self._files.get_response(relative, scope)
            response.headers["Cache-Control"] = IMMUTABLE
            return response
        try:
            response = await self._files.get_response(relative, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            response = await self._files.get_response("index.html", scope)
        response.headers["Cache-Control"] = NO_CACHE
        return response
