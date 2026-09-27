from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_os.security.devices import DEVICE_MAX_AGE, DeviceManager
from agentic_os.security.sessions import hash_token, new_token
from agentic_os.storage import SqliteStore

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "db.sqlite3") as store:
        yield store


@pytest.fixture
def devices(store: SqliteStore) -> DeviceManager:
    return DeviceManager(store)


async def test_only_the_hash_is_stored(store: SqliteStore, devices: DeviceManager) -> None:
    token = await devices.create(T0)
    assert await store.get_device(token) is None
    record = await store.get_device(hash_token(token))
    assert record is not None
    assert (record.created_at, record.expires_at) == (T0, T0 + DEVICE_MAX_AGE)
    assert devices.max_age == timedelta(days=365)
    async with store._db.transaction(write=False) as tx:
        rows = await tx.fetchall("SELECT * FROM devices")
    assert all(token not in str(tuple(row)) for row in rows)


async def test_check_recognizes_known_unexpired_devices(devices: DeviceManager) -> None:
    token = await devices.create(T0)
    assert await devices.check(token, T0 + timedelta(days=364)) == hash_token(token)
    assert await devices.check(token, T0 + DEVICE_MAX_AGE) is None  # expired
    assert await devices.check(new_token(), T0) is None  # unknown


@pytest.mark.parametrize("token", [None, "", "short", "bad token with spaces!!", "x" * 129])
async def test_malformed_tokens_are_unknown(devices: DeviceManager, token: str | None) -> None:
    assert await devices.check(token, T0) is None
    await devices.revoke(token)  # never raises


async def test_revoke_revoke_all_and_purge(devices: DeviceManager) -> None:
    first = await devices.create(T0)
    second = await devices.create(T0)
    await devices.revoke(first)
    assert await devices.check(first, T0) is None
    assert await devices.check(second, T0) is not None
    assert await devices.revoke_all() == 1
    assert await devices.check(second, T0) is None

    await devices.create(T0)
    alive = await devices.create(T0 + timedelta(days=10))
    assert await devices.purge_expired(T0 + DEVICE_MAX_AGE) == 1
    assert await devices.check(alive, T0 + DEVICE_MAX_AGE) is not None


def test_max_age_must_be_positive(store: SqliteStore) -> None:
    with pytest.raises(ValueError):
        DeviceManager(store, max_age=timedelta(0))
