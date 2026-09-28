import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_os.config import Settings
from agentic_os.security.sessions import SessionManager, hash_token, new_token
from agentic_os.storage import SqliteStore

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "db.sqlite3") as store:
        yield store


@pytest.fixture
def sessions(store: SqliteStore) -> SessionManager:
    return SessionManager(store, idle_timeout=timedelta(hours=12), max_age=timedelta(days=7))


def test_tokens_are_random_and_hashed_with_sha256() -> None:
    token = new_token()
    assert len(token) == 43  # 32 bytes, URL-safe base64 without padding
    assert token != new_token()
    assert hash_token(token) == hashlib.sha256(token.encode()).hexdigest()


def test_from_settings(store: SqliteStore) -> None:
    settings = Settings(session_idle_hours=3, session_max_days=2)
    manager = SessionManager.from_settings(store, settings)
    assert manager.idle_timeout == timedelta(hours=3)
    assert manager.max_age == timedelta(days=2)


async def test_only_the_hash_is_stored(store: SqliteStore, sessions: SessionManager) -> None:
    token = await sessions.create(T0, ip="203.0.113.5", user_agent="Firefox")
    assert await store.get_session(token) is None
    record = await store.get_session(hash_token(token))
    assert record is not None
    assert record.expires_at == T0 + timedelta(days=7)
    assert (record.ip, record.user_agent) == ("203.0.113.5", "Firefox")
    async with store._db.transaction(write=False) as tx:
        rows = await tx.fetchall("SELECT * FROM sessions")
    assert all(token not in str(tuple(row)) for row in rows)


async def test_validate_and_touch_at_most_once_per_minute(
    store: SqliteStore, sessions: SessionManager
) -> None:
    token = await sessions.create(T0, ip=None, user_agent=None)
    record = await sessions.validate(token, T0 + timedelta(seconds=30))
    assert record is not None and record.last_seen_at == T0
    stored = await store.get_session(hash_token(token))
    assert stored is not None and stored.last_seen_at == T0  # not written yet

    later = T0 + timedelta(minutes=5)
    record = await sessions.validate(token, later)
    assert record is not None and record.last_seen_at == later
    stored = await store.get_session(hash_token(token))
    assert stored is not None and stored.last_seen_at == later


async def test_idle_timeout(store: SqliteStore, sessions: SessionManager) -> None:
    token = await sessions.create(T0, ip=None, user_agent=None)
    assert await sessions.validate(token, T0 + timedelta(hours=11, minutes=59)) is not None
    # Activity at 11:59 extends the idle deadline.
    assert await sessions.validate(token, T0 + timedelta(hours=23)) is not None
    assert await sessions.validate(token, T0 + timedelta(hours=35, minutes=1)) is None
    assert await store.get_session(hash_token(token)) is None  # deleted


async def test_absolute_expiry_despite_activity(
    store: SqliteStore, sessions: SessionManager
) -> None:
    token = await sessions.create(T0, ip=None, user_agent=None)
    now = T0
    while now < T0 + timedelta(days=7) - timedelta(hours=6):
        now += timedelta(hours=6)
        assert await sessions.validate(token, now) is not None
    assert await sessions.validate(token, T0 + timedelta(days=7)) is None
    assert await store.get_session(hash_token(token)) is None


async def test_peek_is_read_only(store: SqliteStore, sessions: SessionManager) -> None:
    token = await sessions.create(T0, ip=None, user_agent=None)
    record = await sessions.peek(token, T0 + timedelta(hours=11))
    assert record is not None and record.last_seen_at == T0
    stored = await store.get_session(hash_token(token))
    assert stored is not None and stored.last_seen_at == T0  # never touched
    # Idle since T0 (peeks are no activity): ended, but not deleted by a peek.
    assert await sessions.peek(token, T0 + timedelta(hours=12)) is None
    assert await store.get_session(hash_token(token)) is not None
    assert await sessions.validate(token, T0 + timedelta(hours=12)) is None

    token = await sessions.create(T0, ip=None, user_agent=None)
    assert await sessions.peek(token, T0 + timedelta(days=7)) is None  # absolute expiry
    await sessions.revoke(token)
    assert await sessions.peek(token, T0) is None


@pytest.mark.parametrize("token", [None, "", "short", "bad token with spaces!!", "x" * 129])
async def test_malformed_tokens_are_rejected(sessions: SessionManager, token: str | None) -> None:
    assert await sessions.validate(token, T0) is None
    assert await sessions.peek(token, T0) is None
    await sessions.revoke(token)  # never raises


async def test_unknown_token_is_rejected(sessions: SessionManager) -> None:
    assert await sessions.validate(new_token(), T0) is None


async def test_revoke_revoke_all_and_purge(sessions: SessionManager) -> None:
    first = await sessions.create(T0, ip=None, user_agent=None)
    second = await sessions.create(T0, ip=None, user_agent=None)
    await sessions.revoke(first)
    assert await sessions.validate(first, T0) is None
    assert await sessions.validate(second, T0) is not None
    assert await sessions.revoke_all() == 1
    assert await sessions.validate(second, T0) is None

    await sessions.create(T0, ip=None, user_agent=None)
    alive = await sessions.create(T0 + timedelta(hours=10), ip=None, user_agent=None)
    assert await sessions.purge_expired(T0 + timedelta(hours=13)) == 1
    assert await sessions.validate(alive, T0 + timedelta(hours=13)) is not None


def test_timeouts_must_be_positive(store: SqliteStore) -> None:
    with pytest.raises(ValueError):
        SessionManager(store, idle_timeout=timedelta(0), max_age=timedelta(days=1))


async def test_issue_prepares_a_session_without_storing_it(
    store: SqliteStore, sessions: SessionManager
) -> None:
    token, record = sessions.issue(T0, ip="203.0.113.5", user_agent="Firefox")
    assert record.token_hash == hash_token(token)
    assert record.created_at == record.last_seen_at == T0
    assert record.expires_at == T0 + timedelta(days=7)
    assert (record.ip, record.user_agent) == ("203.0.113.5", "Firefox")
    assert await store.get_session(record.token_hash) is None  # a login stores it
    assert sessions.issue(T0, ip=None, user_agent=None)[0] != token
