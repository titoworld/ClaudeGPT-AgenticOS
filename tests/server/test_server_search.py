"""``GET /api/conversations?q=`` (audit point 12): the server searches the titles, so
a conversation beyond the pages the client has loaded is found too.

``q`` is optional and pages with ``limit`` and ``before`` like the plain list; the
answer has the same shape (``ConversationSummary[]``, newest first). It is trimmed; a
blank one is no search, and one longer than 200 characters gets a Catalan 422.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentic_os.config import Settings
from agentic_os.domain import AgentName
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState

ORIGIN = "https://aos.example"
T0 = datetime(2026, 9, 28, 8, 0, 0, tzinfo=UTC)
TOO_LONG_DETAIL = "La cerca no pot tenir més de 200 caràcters."


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Server:
    def __init__(self, client: httpx.AsyncClient, state: AppState, clock: Clock) -> None:
        self.client = client
        self.state = state
        self.clock = clock

    async def create(self, *titles: str) -> list[int]:
        """Create conversations oldest first, one minute apart."""
        ids = []
        for title in titles:
            self.clock.now += timedelta(minutes=1)
            ids.append(await self.state.store.create_conversation(title))
        return ids

    async def ids(self, **params: Any) -> list[int]:
        response = await self.client.get("/api/conversations", params=params)
        assert response.status_code == 200, response.text
        return [c["id"] for c in response.json()]


@pytest.fixture
async def server(tmp_path: Path) -> AsyncIterator[Server]:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
        "claude_mode": "fake",
        "chatgpt_mode": "fake",
    }
    settings = Settings(**values)
    providers: dict[AgentName, Provider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    clock = Clock()
    app = create_app(settings, providers=providers, clock=clock)
    async with app.router.lifespan_context(app):
        state: AppState = app.state.aos
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            token = await state.sessions.create(clock(), ip=None, user_agent=None)
            client.cookies.set(state.cookie_name, token)
            yield Server(client, state, clock)


async def test_a_search_finds_a_conversation_beyond_the_first_page(server: Server) -> None:
    # The audit's case: 60 conversations, and the one looked for is the 56th newest.
    ids = await server.create(
        *(
            "Pressupost zebra irrepetible" if i == 5 else f"Conversa número {i}"
            for i in range(1, 61)
        )
    )
    assert ids[4] not in await server.ids()

    response = await server.client.get("/api/conversations", params={"q": "ZÈBRA"})
    assert response.status_code == 200
    [found] = response.json()
    assert found == {
        "id": ids[4],
        "title": "Pressupost zebra irrepetible",
        "created_at": found["created_at"],
        "updated_at": found["updated_at"],
        "last_mode": None,
        "message_count": 0,
    }
    # Accents and case do not matter on either side.
    assert await server.ids(q="numero 6") == [ids[59], ids[5]]  # «número 60» and «número 6»


async def test_a_search_pages_with_limit_and_before(server: Server) -> None:
    titles = [f"Informe {i}" if i % 2 else f"Nota {i}" for i in range(120)]
    ids = await server.create(*titles)
    expected = [cid for cid, title in zip(ids, titles, strict=True) if "Informe" in title]
    expected.reverse()

    listed: list[int] = []
    pages = []
    before: int | None = None
    while True:
        params: dict[str, Any] = {"q": "informé", "limit": 25}
        if before is not None:
            params["before"] = before
        page = await server.ids(**params)
        if not page:
            break
        pages.append(len(page))
        listed += page
        before = page[-1]
    assert pages == [25, 25, 10]
    assert listed == expected


async def test_the_search_is_literal(server: Server) -> None:
    ids = await server.create("100% natural", "100 natural", "a_b", "axb")
    assert await server.ids(q="100%") == [ids[0]]
    assert await server.ids(q="a_b") == [ids[2]]
    assert await server.ids(q="%") == [ids[0]]


async def test_a_blank_search_is_no_search(server: Server) -> None:
    await server.create("Primera", "Segona")
    everything = await server.ids()
    assert len(everything) == 2
    assert await server.ids(q="") == everything
    assert await server.ids(q="   ") == everything


async def test_the_search_text_has_at_most_200_characters_after_trimming(
    server: Server,
) -> None:
    [conversation_id] = await server.create("y" * 200)
    assert await server.ids(q="  " + "Y" * 200 + "  ") == [conversation_id]
    response = await server.client.get("/api/conversations", params={"q": "y" * 201})
    assert response.status_code == 422
    assert response.json() == {"detail": TOO_LONG_DETAIL}


async def test_the_search_needs_a_session(server: Server) -> None:
    server.client.cookies.clear()
    response = await server.client.get("/api/conversations", params={"q": "x"})
    assert response.status_code == 401
