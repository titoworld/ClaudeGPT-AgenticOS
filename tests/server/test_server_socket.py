"""Real sockets against uvicorn with httptools, as ``agentic-os serve`` runs it.

A response given before the request body has arrived (403 Origin, 401, 413 by
``Content-Length``, 429 login lock, a body sent to a route that ignores it) must
close the connection. Otherwise uvicorn keeps it open and every trickled byte
restarts its keep-alive timer, so an anonymous client could hold connections, and
their file descriptors, forever.
"""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from agentic_os.config import Settings
from agentic_os.domain import AgentName
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

ORIGIN = "https://aos.example"
CLOSE_WITHIN_SECONDS = 3.0
"""Much less than the keep-alive timeout of the server below (75 s)."""


def make_settings(tmp_path: Path) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
    }
    return Settings(**{"_env_file": None, **values})


def fakes() -> dict[AgentName, Provider]:
    return {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }


@dataclass
class Running:
    port: int
    server: uvicorn.Server
    state: AppState

    def open_connections(self) -> int:
        return len(self.server.server_state.connections)


@asynccontextmanager
async def serving(tmp_path: Path) -> AsyncIterator[Running]:
    app = create_app(make_settings(tmp_path), providers=fakes())
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    config = uvicorn.Config(
        app,
        http="httptools",
        lifespan="on",
        timeout_keep_alive=75,  # as in production (cli.KEEP_ALIVE_SECONDS)
        timeout_graceful_shutdown=2,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        yield Running(listener.getsockname()[1], server, app.state.aos)
    finally:
        server.should_exit = True
        await task
        listener.close()


@dataclass
class Answer:
    status: int
    headers: dict[str, str]
    closed_after: float | None
    """Seconds from the answer until the server closed the connection (None: still
    open after :data:`CLOSE_WITHIN_SECONDS` of trickling)."""


async def read_head(reader: asyncio.StreamReader) -> tuple[int, dict[str, str]]:
    status_line = await reader.readline()
    status = int(status_line.split()[1])
    headers: dict[str, str] = {}
    while (line := await reader.readline()) not in (b"\r\n", b""):
        name, _, value = line.decode().partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers


async def trickle(port: int, head: str) -> Answer:
    """Send ``head`` and one byte of the body, read the answer, then keep sending a
    byte every 50 ms until the server closes the connection."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    loop = asyncio.get_running_loop()
    try:
        writer.write(head.encode() + b"{")
        await writer.drain()
        async with asyncio.timeout(5):
            status, headers = await read_head(reader)
        answered = loop.time()
        while loop.time() - answered < CLOSE_WITHIN_SECONDS:
            try:
                writer.write(b" ")
                await writer.drain()
                chunk = await asyncio.wait_for(reader.read(65536), 0.05)
            except TimeoutError:
                continue
            except OSError:  # reset by the server: closed too
                chunk = b""
            if not chunk:
                return Answer(status, headers, loop.time() - answered)
        return Answer(status, headers, None)
    finally:
        writer.close()


def request_head(method: str, path: str, *, length: int = 100_000, origin: bool = True) -> str:
    lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1"]
    if origin:
        lines.append(f"Origin: {ORIGIN}")
    lines += ["Content-Type: application/json", f"Content-Length: {length}"]
    return "\r\n".join(lines) + "\r\n\r\n"


async def wait_no_connections(running: Running) -> int:
    for _ in range(100):
        if running.open_connections() == 0:
            break
        await asyncio.sleep(0.01)
    return running.open_connections()


async def test_an_answer_before_the_body_closes_the_connection(tmp_path: Path) -> None:
    async with serving(tmp_path) as running:
        cases = {
            "403 (no Origin)": (request_head("POST", "/api/auth/login", origin=False), 403),
            "401 (no session)": (request_head("PUT", "/api/settings"), 401),
            "413 (Content-Length)": (
                request_head("POST", "/api/auth/login", length=2 * 1024 * 1024),
                413,
            ),
            "GET with a body": (request_head("GET", "/api/health"), 200),
        }
        for name, (head, status) in cases.items():
            answer = await trickle(running.port, head)
            assert answer.status == status, name
            assert answer.headers.get("connection") == "close", name
            assert answer.closed_after is not None, f"{name}: the connection stayed open"
            # The server side of the socket (its file descriptor) is gone too.
            assert await wait_no_connections(running) == 0, name


async def test_a_login_during_a_lock_closes_the_connection(tmp_path: Path) -> None:
    async with serving(tmp_path) as running:
        state = running.state
        for _ in range(state.throttle.max_failures):
            await state.throttle.record_failure("127.0.0.1", state.clock())
        answer = await trickle(running.port, request_head("POST", "/api/auth/login", length=1000))
        assert answer.status == 429
        assert answer.headers.get("connection") == "close"
        assert answer.closed_after is not None
        assert await wait_no_connections(running) == 0


async def test_requests_whose_body_was_read_keep_the_connection(tmp_path: Path) -> None:
    async with serving(tmp_path) as running:
        state = running.state
        token = await state.sessions.create(state.clock(), ip=None, user_agent=None)
        reader, writer = await asyncio.open_connection("127.0.0.1", running.port)
        try:
            body = b"{}"
            put = (
                f"PUT /api/settings HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: {ORIGIN}\r\n"
                f"Cookie: {state.cookie_name}={token}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode() + body
            get = b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
            for request, expected in ((put, 200), (get, 200), (get, 200)):
                writer.write(request)
                await writer.drain()
                async with asyncio.timeout(5):
                    status, headers = await read_head(reader)
                    await reader.readexactly(int(headers["content-length"]))
                assert status == expected
                assert "connection" not in headers  # kept alive, on the same socket
            assert running.open_connections() == 1
        finally:
            writer.close()


async def test_a_slow_body_that_the_app_reads_gets_408_and_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentic_os.server import middleware

    monkeypatch.setattr(middleware, "BODY_TIMEOUT_SECONDS", 0.3)
    async with serving(tmp_path) as running:
        answer = await trickle(running.port, request_head("POST", "/api/auth/login", length=1000))
        assert answer.status == 408
        assert answer.headers.get("connection") == "close"
        assert answer.closed_after is not None
        assert await wait_no_connections(running) == 0
