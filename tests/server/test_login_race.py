"""A login in flight vs ``agentic-os init`` run by another process (audit point 5).

``init`` replaces the owner through its own SQLite connection (``set_owner``) while
the server is checking the old credentials. The login finishes in one write
transaction conditioned on the owner it verified, so it must fail: no session nor
device made with the old credentials survives, a rehash never brings the old
password back, and the new credentials work at once. The pauses are deterministic
(an ``asyncio.Event`` inside a step of the login); FakeProvider only, no network.
"""

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyotp
import pytest
from argon2 import PasswordHasher

from agentic_os import fx
from agentic_os.config import Settings
from agentic_os.domain import AgentName
from agentic_os.providers.base import Provider
from agentic_os.providers.fake import FakeProvider
from agentic_os.security import passwords, totp
from agentic_os.security.passwords import hash_password, needs_rehash, verify_password
from agentic_os.security.sessions import hash_token, new_token
from agentic_os.server import routes_auth
from agentic_os.server.app import create_app
from agentic_os.server.deps import AppState
from agentic_os.storage import SqliteStore

ORIGIN = "https://aos.example"
OLD_PASSWORD = "contrasenya antiga prou llarga"
NEW_PASSWORD = "contrasenya nova també prou llarga"
OLD_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
NEW_SECRET = "KRSXG5CTMVRXEZLUKRSXG5CTMVRXEZLU"
T0 = datetime(2026, 9, 27, 12, 0, 5, tzinfo=UTC)
S0 = totp.time_step(T0)
FAILED = {"detail": "Credencials incorrectes."}


async def no_ecb() -> fx.FxRate:
    """The ECB rate is never downloaded in tests."""
    raise fx.FxError("Sense xarxa als tests.")


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Rig:
    client: httpx.AsyncClient
    state: AppState
    admin: SqliteStore
    """A second connection to the same file: the ``agentic-os init`` process."""
    clock: Clock
    db_path: Path

    def count(self, table: str) -> int:
        """Rows of ``table``, read through yet another connection."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])


@asynccontextmanager
async def rig(tmp_path: Path, owner_hash: str) -> AsyncIterator[Rig]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        public_origin=ORIGIN,
        web_dist=tmp_path / "no-dist",
        claude_mode="fake",
        chatgpt_mode="fake",
    )
    clock = Clock()
    providers: dict[AgentName, Provider] = {
        "claude": FakeProvider("claude", chunk_delay=0),
        "chatgpt": FakeProvider("chatgpt", chunk_delay=0),
    }
    app = create_app(settings, providers=providers, clock=clock, fx_fetcher=no_ecb)
    async with app.router.lifespan_context(app):
        admin = await SqliteStore.open(settings.db_path)
        try:
            await admin.set_owner(
                password_hash=owner_hash, totp_secret=OLD_SECRET, totp_last_step=0
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield Rig(c, app.state.aos, admin, clock, settings.db_path)
        finally:
            await admin.close()


def gate(
    original: Callable[..., Awaitable[Any]], reached: asyncio.Event, release: asyncio.Event
) -> Callable[..., Awaitable[Any]]:
    """``original`` that first signals ``reached`` and waits for ``release``."""

    async def paused(*args: Any, **kwargs: Any) -> Any:
        reached.set()
        await release.wait()
        return await original(*args, **kwargs)

    return paused


async def login(r: Rig, password: str, secret: str) -> httpx.Response:
    code = pyotp.TOTP(secret).at(r.clock())
    return await r.client.post(
        "/api/auth/login", json={"password": password, "totp": code}, headers={"origin": ORIGIN}
    )


async def admin_init(r: Rig, *, confirmed_step: int) -> int:
    """What ``agentic-os init`` does at the end: a new hash and a new TOTP secret."""
    return await r.admin.set_owner(
        password_hash=hash_password(NEW_PASSWORD),
        totp_secret=NEW_SECRET,
        totp_last_step=confirmed_step,
    )


def weak_hash(password: str) -> str:
    """A hash with weaker parameters than the current ones (it needs a rehash)."""
    return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(password)


async def assert_only_the_new_credentials_work(r: Rig) -> None:
    owner = await r.admin.get_owner()
    assert owner is not None
    assert owner.totp_secret == NEW_SECRET
    assert verify_password(owner.password_hash, NEW_PASSWORD)
    assert not verify_password(owner.password_hash, OLD_PASSWORD)
    r.client.cookies.clear()
    r.clock.now = T0 + timedelta(seconds=30)
    assert (await login(r, OLD_PASSWORD, NEW_SECRET)).status_code == 401
    r.clock.now = T0 + timedelta(seconds=60)
    response = await login(r, NEW_PASSWORD, NEW_SECRET)
    assert response.status_code == 204
    assert r.count("sessions") == 1 and r.count("devices") == 1


async def test_control_an_init_before_the_login_refuses_the_old_credentials(
    tmp_path: Path,
) -> None:
    async with rig(tmp_path, hash_password(OLD_PASSWORD)) as r:
        await admin_init(r, confirmed_step=S0)
        response = await login(r, OLD_PASSWORD, OLD_SECRET)
        assert response.status_code == 401
        assert response.json() == FAILED


@pytest.mark.parametrize("confirmed_step", [S0 - 1, S0], ids=["older-step", "same-step"])
async def test_an_init_while_the_password_is_checked_fails_the_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confirmed_step: int
) -> None:
    reached, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(
        routes_auth,
        "verify_password_async",
        gate(passwords.verify_password_async, reached, release),
    )
    async with rig(tmp_path, hash_password(OLD_PASSWORD)) as r:
        pending = asyncio.create_task(login(r, OLD_PASSWORD, OLD_SECRET))
        await asyncio.wait_for(reached.wait(), 10)  # the old owner row was already read
        await admin_init(r, confirmed_step=confirmed_step)
        release.set()
        response = await pending

        assert response.status_code == 401
        assert response.json() == FAILED
        assert "set-cookie" not in response.headers
        assert r.count("sessions") == 0 and r.count("devices") == 0
        owner = await r.admin.get_owner()
        assert owner is not None
        # The old secret's code was not recorded on the new owner either.
        assert owner.totp_last_step == confirmed_step
        await assert_only_the_new_credentials_work(r)


@pytest.mark.parametrize("confirmed_step", [S0 - 1, S0], ids=["older-step", "same-step"])
async def test_an_init_during_the_rehash_keeps_the_new_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confirmed_step: int
) -> None:
    old = weak_hash(OLD_PASSWORD)
    assert needs_rehash(old)
    reached, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(
        routes_auth,
        "hash_password_async",
        gate(passwords.hash_password_async, reached, release),
    )
    async with rig(tmp_path, old) as r:
        pending = asyncio.create_task(login(r, OLD_PASSWORD, OLD_SECRET))
        await asyncio.wait_for(reached.wait(), 10)  # computing the rehash
        await admin_init(r, confirmed_step=confirmed_step)
        release.set()
        response = await pending

        assert response.status_code == 401
        assert "set-cookie" not in response.headers
        assert r.count("sessions") == 0 and r.count("devices") == 0
        await assert_only_the_new_credentials_work(r)


async def test_a_login_that_loses_the_race_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not even the session and the device presented with the login are ended: that
    happens only in the transaction of a login that works."""
    reached, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(
        routes_auth,
        "verify_password_async",
        gate(passwords.verify_password_async, reached, release),
    )
    async with rig(tmp_path, hash_password(OLD_PASSWORD)) as r:
        token, device = new_token(), new_token()
        r.client.cookies.set(r.state.cookie_name, token)
        r.client.cookies.set(r.state.device_cookie_name, device)
        pending = asyncio.create_task(login(r, OLD_PASSWORD, OLD_SECRET))
        await asyncio.wait_for(reached.wait(), 10)
        await admin_init(r, confirmed_step=S0 - 1)  # ends every session and device
        # The tokens the login presented now belong to the new owner's browser.
        await r.admin.create_session(
            hash_token(token),
            created_at=T0,
            expires_at=T0 + timedelta(days=1),
            ip=None,
            user_agent=None,
        )
        await r.admin.create_device(
            hash_token(device), created_at=T0, expires_at=T0 + timedelta(days=365)
        )
        release.set()
        assert (await pending).status_code == 401
        assert await r.admin.get_session(hash_token(token)) is not None
        assert await r.admin.get_device(hash_token(device)) is not None
        assert r.count("sessions") == 1 and r.count("devices") == 1


async def test_a_login_that_needs_a_rehash_stores_it_with_the_session(tmp_path: Path) -> None:
    async with rig(tmp_path, weak_hash(OLD_PASSWORD)) as r:
        response = await login(r, OLD_PASSWORD, OLD_SECRET)
        assert response.status_code == 204
        owner = await r.admin.get_owner()
        assert owner is not None
        assert not needs_rehash(owner.password_hash)
        assert verify_password(owner.password_hash, OLD_PASSWORD)
        assert owner.totp_last_step == S0
        assert r.count("sessions") == 1 and r.count("devices") == 1
