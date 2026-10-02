"""Revision of the runtime settings (ADR 0006): ``GET /api/settings`` returns it, and
``PUT /api/settings`` only saves an edit based on the stored revision (compare-and-swap):
409 with the current settings when another tab or device saved first, 422 without a
valid revision."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
from agentic_os.storage import RuntimeSettings, SqliteStore

ORIGIN = "https://aos.example"
ORIGIN_HEADERS = {"origin": ORIGIN}
T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
CONFLICT_DETAIL = (
    "La configuració ha canviat en una altra pestanya o dispositiu. Revisa-la i torna-la a desar."
)
MISSING_REVISION_DETAIL = (
    "Cal indicar «revision» (la revisió de la configuració en què es basa el canvi). "
    "Torna a carregar la pàgina."
)
INVALID_REVISION_DETAIL = "«revision» ha de ser un enter igual o més gran que 0."


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path / "data",
        "public_origin": ORIGIN,
        "web_dist": tmp_path / "no-dist",
        "claude_mode": "fake",
        "chatgpt_mode": "fake",
    }
    return Settings(**values)


@asynccontextmanager
async def running(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """The app on ``settings`` and a client with a live session."""
    providers: dict[AgentName, Provider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    app = create_app(settings, providers=providers, clock=lambda: T0)
    async with app.router.lifespan_context(app):
        state = app.state.aos
        assert isinstance(state, AppState)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            token = await state.sessions.create(T0, ip=None, user_agent=None)
            client.cookies.set(state.cookie_name, token)
            yield client


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with running(settings) as client:
        yield client


async def load(client: httpx.AsyncClient) -> dict[str, Any]:
    """``GET /api/settings``, as a tab does when it opens the settings."""
    response = await client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


async def save(client: httpx.AsyncClient, body: object) -> httpx.Response:
    return await client.put("/api/settings", json=body, headers=ORIGIN_HEADERS)


def without_revision(settings: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in settings.items() if key != "revision"}


async def test_a_new_database_is_at_revision_0(client: httpx.AsyncClient) -> None:
    assert await load(client) == {**RuntimeSettings().to_wire(), "revision": 0}


async def test_settings_saved_before_revisions_existed_are_at_revision_1(
    settings: Settings,
) -> None:
    """They were saved at least once. Revision 0 is only the one of a database that
    never saved its settings, so a PUT of the built-in defaults from a client that never
    loaded the settings (revision 0) cannot overwrite them, not even before the first
    save under this version."""
    legacy = {
        "default_mode": "solo",
        "default_target": "chatgpt",
        "budgets_eur": {"claude": 30.0, "chatgpt": None},
    }
    async with await SqliteStore.open(settings.db_path) as store, store._db.transaction() as tx:
        await tx.execute(
            "INSERT INTO settings (key, value) VALUES ('runtime', ?)", (json.dumps(legacy),)
        )
    async with running(settings) as client:
        current = await load(client)
        assert current["revision"] == 1
        assert current["default_mode"] == "solo"
        assert current["budgets_eur"] == {"claude": 30.0, "chatgpt": None}

        never_loaded = await save(client, RuntimeSettings().to_wire())
        assert never_loaded.status_code == 409
        assert never_loaded.json() == {"detail": CONFLICT_DETAIL, "settings": current}
        assert await load(client) == current

        response = await save(client, {**current, "use_cache": False})
        assert response.status_code == 200
        assert response.json() == {**current, "use_cache": False, "revision": 2}


async def test_every_successful_put_adds_1(client: httpx.AsyncClient) -> None:
    current = await load(client)
    for revision, mode in enumerate(("solo", "duel", "debate"), start=1):
        response = await save(client, {**current, "default_mode": mode})
        assert response.status_code == 200
        saved = response.json()
        assert saved == {**current, "default_mode": mode, "revision": revision}
        assert await load(client) == saved
        current = saved


async def test_a_put_based_on_an_old_revision_gets_409_and_the_current_settings(
    client: httpx.AsyncClient,
) -> None:
    # Two tabs open the settings; the first one saves.
    first_tab = await load(client)
    second_tab = await load(client)
    budgets = {"claude": 25.0, "chatgpt": None}
    response = await save(client, {**first_tab, "budgets_eur": budgets})
    assert response.status_code == 200
    saved = response.json()
    assert saved["revision"] == 1

    # The second one would undo it: refused, with what is stored now.
    conflict = await save(client, {**second_tab, "default_mode": "solo"})
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": CONFLICT_DETAIL, "settings": saved}
    assert await load(client) == saved

    # The body is still validated first: an invalid edit is a 422, not a 409.
    invalid = await save(client, {**second_tab, "debate": {"rounds": 9}})
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "«debate.rounds» ha de ser un enter entre 0 i 4."}

    # Reloaded from the 409, the edit can be saved.
    reloaded = conflict.json()["settings"]
    retried = await save(client, {**reloaded, "default_mode": "solo"})
    assert retried.status_code == 200
    assert retried.json() == {**saved, "default_mode": "solo", "revision": 2}
    assert await load(client) == retried.json()


async def test_a_revision_ahead_of_the_stored_one_is_a_conflict_too(
    client: httpx.AsyncClient,
) -> None:
    current = await load(client)
    for revision in (1, 7, 2**53, 10**30):
        response = await save(client, {**current, "default_mode": "solo", "revision": revision})
        assert response.status_code == 409, revision
        assert response.json() == {"detail": CONFLICT_DETAIL, "settings": current}
    assert await load(client) == current


async def test_a_put_without_a_valid_revision_is_422(client: httpx.AsyncClient) -> None:
    current = await load(client)
    edited = {**without_revision(current), "default_mode": "solo"}
    for body in (edited, {}):
        response = await save(client, body)
        assert response.status_code == 422
        assert response.json() == {"detail": MISSING_REVISION_DETAIL}
    for revision in (-1, 1.5, 0.0, "0", True, None, [0], {"revision": 0}):
        response = await save(client, {**edited, "revision": revision})
        assert response.status_code == 422, revision
        assert response.json() == {"detail": INVALID_REVISION_DETAIL}
    assert await load(client) == current


async def test_the_revision_survives_a_restart(settings: Settings) -> None:
    async with running(settings) as client:
        current = await load(client)
        for mode in ("solo", "duel"):
            response = await save(client, {**current, "default_mode": mode})
            assert response.status_code == 200
            current = response.json()
    async with running(settings) as client:
        reloaded = await load(client)
        assert reloaded["revision"] == 2
        assert reloaded == current
        stale = await save(client, {**reloaded, "use_cache": False, "revision": 1})
        assert stale.status_code == 409
        saved = await save(client, {**reloaded, "use_cache": False})
        assert saved.status_code == 200
        assert saved.json() == {**reloaded, "use_cache": False, "revision": 3}


async def test_concurrent_puts_based_on_one_revision_let_exactly_one_win(
    client: httpx.AsyncClient,
) -> None:
    current = await load(client)
    bodies = [
        {**current, "compaction_threshold_tokens": 1000 + index, "default_mode": mode}
        for index, mode in enumerate(("solo", "duel", "debate", "solo", "duel"))
    ]
    responses = await asyncio.gather(*(save(client, body) for body in bodies))
    assert sorted(response.status_code for response in responses) == [200, 409, 409, 409, 409]
    [winner] = [response.json() for response in responses if response.status_code == 200]
    assert winner in [{**body, "revision": 1} for body in bodies]
    for response in responses:
        if response.status_code == 409:
            assert response.json() == {"detail": CONFLICT_DETAIL, "settings": winner}
    assert await load(client) == winner
