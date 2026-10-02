from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_os.config import Settings
from agentic_os.security.throttle import GLOBAL_KEY, LoginThrottle, client_key, device_key
from agentic_os.storage import SqliteStore

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
IP = "198.51.100.7"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "db.sqlite3"


@pytest.fixture
async def store(db_path: Path) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(db_path) as store:
        yield store


@pytest.fixture
def throttle(store: SqliteStore) -> LoginThrottle:
    return LoginThrottle(store, max_failures=3, global_max_failures=10)


@pytest.mark.parametrize(
    ("ip", "key"),
    [
        ("198.51.100.7", "ip:198.51.100.7"),
        (" 198.51.100.7 ", "ip:198.51.100.7"),
        ("::ffff:198.51.100.7", "ip:198.51.100.7"),
        ("2001:db8:1:2:aaaa::1", "ip:2001:db8:1:2::/64"),
        ("2001:db8:1:2:bbbb::9", "ip:2001:db8:1:2::/64"),
        ("fe80::1%eth0", "ip:fe80::/64"),
        ("not-an-ip", "ip:unknown"),
        ("", "ip:unknown"),
        (None, "ip:unknown"),
    ],
)
def test_client_key(ip: str | None, key: str) -> None:
    assert client_key(ip) == key


def test_lock_duration_is_exponential_and_capped(throttle: LoginThrottle) -> None:
    durations = [throttle.lock_duration(n, 3).total_seconds() for n in range(1, 12)]
    assert durations == [0, 0, 5, 10, 20, 40, 80, 160, 320, 640, 900]
    assert throttle.lock_duration(10_000, 3) == timedelta(minutes=15)


def test_defaults_from_settings(store: SqliteStore) -> None:
    throttle = LoginThrottle.from_settings(store, Settings(login_max_failures=5))
    assert throttle.max_failures == 5
    assert throttle.global_max_failures == 20


async def test_backoff_and_reset_on_success(throttle: LoginThrottle) -> None:
    assert await throttle.retry_after(IP, T0) == 0
    assert await throttle.record_failure(IP, T0) == 0
    assert await throttle.record_failure(IP, T0) == 0
    assert await throttle.record_failure(IP, T0) == 5
    assert await throttle.retry_after(IP, T0) == 5
    assert await throttle.retry_after(IP, T0 + timedelta(seconds=4.2)) == 1
    assert await throttle.retry_after(IP, T0 + timedelta(seconds=5)) == 0
    # Another client is not affected.
    assert await throttle.retry_after("203.0.113.1", T0) == 0

    now = T0 + timedelta(seconds=5)
    assert await throttle.record_failure(IP, now) == 10
    now += timedelta(seconds=10)
    assert await throttle.record_failure(IP, now) == 20

    await throttle.record_success(IP)
    assert await throttle.retry_after(IP, now) == 0
    assert await throttle.record_failure(IP, now) == 0  # counting starts again


async def test_global_lock_across_clients(throttle: LoginThrottle, store: SqliteStore) -> None:
    for index in range(9):
        assert await throttle.record_failure(f"203.0.113.{index}", T0) == 0
    assert await throttle.record_failure("203.0.113.200", T0) == 5
    assert await throttle.retry_after("192.0.2.1", T0) == 5  # a fresh client is blocked too
    state = await store.get_throttle(GLOBAL_KEY)
    assert state is not None and state.failures == 10
    await throttle.record_success("192.0.2.1")
    assert await store.get_throttle(GLOBAL_KEY) is None


async def test_a_known_device_has_only_its_own_counter(
    throttle: LoginThrottle, store: SqliteStore
) -> None:
    device = "d" * 64
    for index in range(30):  # the client and global keys are at their maximum lock
        await throttle.record_failure(f"203.0.113.{index % 3}", T0)
    assert await throttle.retry_after(IP, T0) > 0
    assert await throttle.retry_after("203.0.113.1", T0, device=device) == 0

    global_before = await store.get_throttle(GLOBAL_KEY)
    assert await throttle.record_failure(IP, T0, device=device) == 0
    assert await throttle.record_failure(IP, T0, device=device) == 0
    assert await throttle.record_failure(IP, T0, device=device) == 5  # per-client policy
    assert await throttle.retry_after(IP, T0, device=device) == 5
    assert await throttle.retry_after(IP, T0, device="e" * 64) == 0  # another device
    assert await store.get_throttle(GLOBAL_KEY) == global_before  # not counted globally
    assert await store.get_throttle(client_key(IP)) is None

    await throttle.record_success(IP, device=device)
    assert await store.get_throttle(device_key(device)) is None
    assert await store.get_throttle(GLOBAL_KEY) == global_before  # still locked for others


async def test_counter_restarts_after_quiet_period(store: SqliteStore) -> None:
    throttle = LoginThrottle(store, max_failures=2, reset_after=timedelta(hours=1))
    assert await throttle.record_failure(IP, T0) == 0
    later = T0 + timedelta(hours=2)
    assert await throttle.record_failure(IP, later) == 0
    assert await throttle.record_failure(IP, later) == 5


async def test_lockout_survives_restart(db_path: Path) -> None:
    async with await SqliteStore.open(db_path) as store:
        throttle = LoginThrottle(store, max_failures=1)
        assert await throttle.record_failure(IP, T0) == 5
    async with await SqliteStore.open(db_path) as store:
        throttle = LoginThrottle(store, max_failures=1)
        assert await throttle.retry_after(IP, T0 + timedelta(seconds=1)) == 4


async def test_purge_removes_stale_counters(throttle: LoginThrottle, store: SqliteStore) -> None:
    await throttle.record_failure(IP, T0)
    assert await throttle.purge(T0 + timedelta(hours=1)) == 0
    assert await throttle.purge(T0 + timedelta(hours=25)) == 2  # client + global
    assert await store.get_throttle(client_key(IP)) is None


def test_max_failures_must_be_positive(store: SqliteStore) -> None:
    with pytest.raises(ValueError):
        LoginThrottle(store, max_failures=0)
