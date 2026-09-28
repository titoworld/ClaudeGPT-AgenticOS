"""``SqliteStore.complete_login``: a login finishes in one write transaction, and only
if the owner is still the one whose credentials were verified (audit point 5)."""

import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_os.storage import DeviceRecord, OwnerRecord, SessionRecord, SqliteStore

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
LATER = T0 + timedelta(minutes=5)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "db.sqlite3"


@pytest.fixture
async def store(db_path: Path, clock: FakeClock) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(db_path, clock=clock) as store:
        yield store


def new_session(
    token_hash: str = "new-session",
    *,
    ip: str | None = "203.0.113.9",
    user_agent: str | None = "Firefox",
) -> SessionRecord:
    return SessionRecord(
        token_hash=token_hash,
        created_at=LATER,
        last_seen_at=LATER,
        expires_at=LATER + timedelta(days=30),
        ip=ip,
        user_agent=user_agent,
    )


def new_device(token_hash: str = "new-device") -> DeviceRecord:
    return DeviceRecord(
        token_hash=token_hash, created_at=LATER, expires_at=LATER + timedelta(days=365)
    )


async def owner_with_old_browser(store: SqliteStore) -> OwnerRecord:
    """An owner at TOTP step 5 plus the session and device an old login left."""
    await store.set_owner(password_hash="hash1", totp_secret="SECRET1", totp_last_step=5)
    await store.create_session(
        "old-session", created_at=T0, expires_at=T0 + timedelta(days=30), ip=None, user_agent=None
    )
    await store.create_device("old-device", created_at=T0, expires_at=T0 + timedelta(days=365))
    owner = await store.get_owner()
    assert owner is not None
    return owner


async def test_a_login_records_the_step_and_swaps_the_session_and_device(
    store: SqliteStore, clock: FakeClock
) -> None:
    owner = await owner_with_old_browser(store)
    clock.now = LATER
    done = await store.complete_login(
        owner,
        step=6,
        session=new_session(),
        device=new_device(),
        ended_session="old-session",
        forgotten_device="old-device",
    )
    assert done is True
    stored = await store.get_owner()
    assert stored is not None
    assert (stored.password_hash, stored.totp_secret, stored.totp_last_step) == (
        "hash1",
        "SECRET1",
        6,
    )
    assert stored.updated_at == owner.updated_at  # no rehash: the credentials did not change
    assert await store.get_session("old-session") is None
    assert await store.get_device("old-device") is None
    assert await store.get_session("new-session") == new_session()
    assert await store.get_device("new-device") == new_device()
    # The code of step 6 cannot be used again.
    replay = await store.complete_login(
        owner, step=6, session=new_session("s2"), device=new_device("d2")
    )
    assert replay is False


async def test_a_rehash_is_stored_in_the_same_transaction(
    store: SqliteStore, clock: FakeClock
) -> None:
    owner = await owner_with_old_browser(store)
    clock.now = LATER
    done = await store.complete_login(
        owner, step=6, session=new_session(), device=new_device(), password_hash="hash2"
    )
    assert done is True
    stored = await store.get_owner()
    assert stored is not None
    assert (stored.password_hash, stored.totp_secret, stored.totp_last_step) == (
        "hash2",
        "SECRET1",
        6,
    )
    assert stored.updated_at == LATER


async def test_an_owner_replaced_by_another_process_makes_the_login_fail(
    store: SqliteStore, db_path: Path
) -> None:
    owner = await owner_with_old_browser(store)
    # `agentic-os init` in another process, on its own connection.
    async with await SqliteStore.open(db_path) as admin:
        await admin.set_owner(password_hash="hash9", totp_secret="SECRET9", totp_last_step=0)
        await admin.create_session(
            "old-session",
            created_at=T0,
            expires_at=T0 + timedelta(days=30),
            ip=None,
            user_agent=None,
        )
        await admin.create_device("old-device", created_at=T0, expires_at=T0 + timedelta(days=365))
    done = await store.complete_login(
        owner,
        step=6,
        session=new_session(),
        device=new_device(),
        password_hash="rehash of the old password",
        ended_session="old-session",
        forgotten_device="old-device",
    )
    assert done is False
    stored = await store.get_owner()
    assert stored is not None
    assert (stored.password_hash, stored.totp_secret, stored.totp_last_step) == (
        "hash9",
        "SECRET9",
        0,
    )
    assert await store.get_session("new-session") is None
    assert await store.get_device("new-device") is None
    assert await store.get_session("old-session") is not None
    assert await store.get_device("old-device") is not None


@pytest.mark.parametrize(
    ("change", "value"),
    [("password_hash", "other hash"), ("totp_secret", "OTHER"), ("totp_last_step", 6)],
)
async def test_any_change_of_the_verified_owner_makes_the_login_fail(
    store: SqliteStore, change: str, value: object
) -> None:
    owner = await owner_with_old_browser(store)
    current: dict[str, object] = {
        "password_hash": owner.password_hash,
        "totp_secret": owner.totp_secret,
        "totp_last_step": owner.totp_last_step,
        change: value,
    }
    async with store._db.transaction() as tx:
        await tx.execute(
            "UPDATE owner SET password_hash = ?, totp_secret = ?, totp_last_step = ? WHERE id = 1",
            (current["password_hash"], current["totp_secret"], current["totp_last_step"]),
        )
    done = await store.complete_login(owner, step=6, session=new_session(), device=new_device())
    assert done is False
    assert await store.get_session("new-session") is None
    assert await store.get_device("new-device") is None


async def test_without_an_owner_nothing_is_written(store: SqliteStore) -> None:
    ghost = OwnerRecord(
        password_hash="hash1",
        totp_secret="SECRET1",
        totp_last_step=0,
        created_at=T0,
        updated_at=T0,
    )
    done = await store.complete_login(ghost, step=6, session=new_session(), device=new_device())
    assert done is False
    assert await store.get_session("new-session") is None
    assert await store.get_device("new-device") is None


async def test_a_login_is_all_or_nothing(store: SqliteStore) -> None:
    owner = await owner_with_old_browser(store)
    with pytest.raises(sqlite3.IntegrityError):
        # The device token hash is already taken: the whole login is rolled back.
        await store.complete_login(
            owner,
            step=6,
            session=new_session(),
            device=new_device("old-device"),
            password_hash="hash2",
            ended_session="old-session",
        )
    stored = await store.get_owner()
    assert stored is not None
    assert (stored.password_hash, stored.totp_last_step) == ("hash1", 5)
    assert await store.get_session("new-session") is None
    assert await store.get_session("old-session") is not None
    # And the step can still be used.
    assert await store.complete_login(owner, step=6, session=new_session(), device=new_device())


async def test_the_new_session_is_stored_like_any_other(store: SqliteStore) -> None:
    owner = await owner_with_old_browser(store)
    session = new_session(ip="2001:db8::1" + "0" * 100, user_agent="x" * 1000)
    assert await store.complete_login(owner, step=6, session=session, device=new_device())
    stored = await store.get_session("new-session")
    assert stored is not None
    assert stored.ip is not None and len(stored.ip) == 64
    assert stored.user_agent is not None and len(stored.user_agent) == 256
    assert stored.created_at == stored.last_seen_at == LATER
