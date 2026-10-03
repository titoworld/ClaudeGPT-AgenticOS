"""TOTP (RFC 6238: 6 digits, 30 s steps, SHA-1) with replay protection.

:func:`verify` is pure: it returns the matched time step, which the caller must
then record atomically: the login does it in ``SqliteStore.complete_login``, in the
same transaction that creates the session. A ``False`` there means the code was
already used (or the owner changed meanwhile) and the login must be rejected.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Final

import pyotp

from agentic_os.storage.models import as_utc, utc_now

ISSUER: Final = "ClaudeGPT OS"
DIGITS: Final = 6
INTERVAL_SECONDS: Final = 30
VALID_WINDOW: Final = 1
"""Steps accepted on each side of the current one (clock drift tolerance)."""


def new_secret() -> str:
    """A random base32 secret of 32 characters (160 bits)."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str = "owner") -> str:
    """``otpauth://`` URI for authenticator apps (usually shown as a QR code)."""
    return pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL_SECONDS).provisioning_uri(
        name=account, issuer_name=ISSUER
    )


def time_step(now: datetime) -> int:
    """TOTP counter for ``now`` (naive datetimes are taken as UTC)."""
    return int(as_utc(now).timestamp()) // INTERVAL_SECONDS


def normalize_code(code: str) -> str | None:
    """The code without whitespace (``"123 456"`` is accepted), or ``None`` if it is
    not exactly :data:`DIGITS` ASCII digits."""
    compact = "".join(code.split())
    if len(compact) != DIGITS or not compact.isascii() or not compact.isdigit():
        return None
    return compact


def verify(
    code: str,
    secret: str,
    last_step: int,
    *,
    now: datetime | None = None,
    valid_window: int = VALID_WINDOW,
) -> int | None:
    """Check ``code`` against ``secret`` at ``now`` ± ``valid_window`` steps.

    Returns the matched time step, or ``None`` if the code is malformed, wrong, or
    its step is not newer than ``last_step`` (replayed code)."""
    normalized = normalize_code(code)
    if normalized is None:
        return None
    totp = pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL_SECONDS)
    current = time_step(now if now is not None else utc_now())
    matched: int | None = None
    # Compare every candidate (no early exit) in constant time.
    for step in range(current - valid_window, current + valid_window + 1):
        if hmac.compare_digest(totp.generate_otp(step), normalized) and step > last_step:
            matched = step
    return matched
