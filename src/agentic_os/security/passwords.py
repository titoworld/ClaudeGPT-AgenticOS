"""Owner password hashing with argon2id (argon2-cffi defaults: RFC 9106 low-memory
profile, t=3, m=64 MiB, p=4).

Hashing costs ~50 ms of CPU and 64 MiB of RAM: in the server always use the
``*_async`` variants, which run in a worker thread and allow at most
:data:`MAX_CONCURRENT_HASHES` hashes at a time (bounded memory under a login flood).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH: Final = 12
MAX_PASSWORD_LENGTH: Final = 1024
"""Longer inputs are rejected without hashing (cheap denial-of-service guard)."""
MAX_CONCURRENT_HASHES: Final = 2

_hasher: Final = PasswordHasher()
_slots: Final = threading.BoundedSemaphore(MAX_CONCURRENT_HASHES)


def password_policy_error(password: str) -> str | None:
    """Why ``password`` is not acceptable as the owner password (Catalan), or ``None``."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return (
            f"La contrasenya ha de tenir com a mínim {MIN_PASSWORD_LENGTH} caràcters "
            "(millor una frase de pas)."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"La contrasenya no pot tenir més de {MAX_PASSWORD_LENGTH} caràcters."
    if not password.strip():
        return "La contrasenya no pot ser només espais."
    return None


def hash_password(password: str) -> str:
    """argon2id hash in PHC string format (``$argon2id$v=19$...``)."""
    with _slots:
        return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """``True`` if ``password`` matches. Never raises: a mismatch, an oversized
    password, a password that cannot be encoded as UTF-8 (a lone surrogate, which JSON
    allows as ``"\\ud800"``) or a malformed hash all return ``False``, so the login
    counts them as failed attempts."""
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        with _slots:
            return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError, UnicodeError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """``True`` if the hash was made with other parameters than the current ones
    (rehash the password after a successful login)."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)
