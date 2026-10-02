import pytest
from argon2 import PasswordHasher

from agentic_os.security.passwords import (
    MAX_PASSWORD_LENGTH,
    hash_password,
    hash_password_async,
    needs_rehash,
    password_policy_error,
    verify_password,
    verify_password_async,
)

PASSWORD = "correct horse battery staple"


def test_hash_is_argon2id_and_verifies() -> None:
    password_hash = hash_password(PASSWORD)
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, PASSWORD)
    assert not needs_rehash(password_hash)


def test_mismatch_and_malformed_input_return_false() -> None:
    password_hash = hash_password(PASSWORD)
    assert verify_password(password_hash, "wrong password!!") is False
    assert verify_password("not-a-hash", PASSWORD) is False
    assert verify_password("", PASSWORD) is False
    assert verify_password(password_hash, "x" * (MAX_PASSWORD_LENGTH + 1)) is False


def test_needs_rehash_for_weaker_parameters_or_invalid_hash() -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    assert verify_password(weak, PASSWORD)
    assert needs_rehash(weak)
    assert needs_rehash("garbage")


async def test_async_variants() -> None:
    password_hash = await hash_password_async(PASSWORD)
    assert await verify_password_async(password_hash, PASSWORD)
    assert not await verify_password_async(password_hash, "nope")


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short", "com a mínim 12"),
        ("x" * 11, "com a mínim 12"),
        (" " * 12, "només espais"),
        ("x" * (MAX_PASSWORD_LENGTH + 1), "més de"),
    ],
)
def test_policy_rejections(password: str, fragment: str) -> None:
    problem = password_policy_error(password)
    assert problem is not None and fragment in problem


def test_policy_accepts_twelve_characters() -> None:
    assert password_policy_error("x" * 12) is None
    assert password_policy_error(PASSWORD) is None


@pytest.mark.parametrize("password", ["\ud800" + PASSWORD, PASSWORD + "\udfff", "\udcff" * 20])
async def test_a_password_that_is_not_utf8_never_matches(password: str) -> None:
    # A lone surrogate (valid JSON as "\ud800") cannot be encoded for argon2: a failed
    # attempt like any other, never an exception.
    password_hash = hash_password(PASSWORD)
    assert verify_password(password_hash, password) is False
    assert await verify_password_async(password_hash, password) is False
