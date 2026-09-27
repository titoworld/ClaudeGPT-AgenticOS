"""Web layer: FastAPI app (REST, WebSocket, static SPA), sessions and turn manager.

Entry point: :func:`agentic_os.server.app.create_app` (an app factory for uvicorn).
"""

from agentic_os.server.app import create_app

__all__ = ["create_app"]
