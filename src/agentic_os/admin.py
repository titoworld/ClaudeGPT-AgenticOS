"""Administration commands run from the terminal on the VPS (``agentic-os init``,
``agentic-os reset-sessions``, ``agentic-os reset-throttle``). Their output and
prompts speak the command line's language (:mod:`agentic_os.i18n`).

All are coroutines taking an open :class:`~agentic_os.storage.SqliteStore`. The
input callables are called directly (blocking) because these commands run alone
in their own event loop.
"""

from __future__ import annotations

import getpass as getpass_module
import io
from collections.abc import Callable
from datetime import datetime
from typing import Final
from urllib.parse import urlsplit

import qrcode  # type: ignore[import-untyped]

from agentic_os.config import Settings
from agentic_os.i18n import number, t
from agentic_os.security import totp
from agentic_os.security.passwords import (
    MIN_PASSWORD_LENGTH,
    hash_password_async,
    password_policy_error,
)
from agentic_os.storage import SqliteStore, utc_now

MAX_ATTEMPTS: Final = 3
_YES: Final = frozenset({"s", "si", "sí", "y", "yes"})
"""Answers that confirm, in any of the three languages."""

Prompt = Callable[[str], str]
Output = Callable[[str], None]


def render_qr(data: str) -> str:
    """``data`` as a QR code drawn with block characters (light on dark terminals)."""
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    buffer = io.StringIO()
    qr.print_ascii(out=buffer, invert=True)
    return buffer.getvalue()


def _counted(key: str, count: int) -> str:
    """The text ``key`` for ``count`` things: its ``_one`` form for 1, else ``_other``."""
    return t(f"{key}_{'one' if count == 1 else 'other'}", count=number(count))


def _closed_sessions(count: int) -> str:
    if count == 0:
        return t("cli.sessions.none")
    return _counted("cli.sessions.closed", count)


def _forgotten_devices(count: int) -> str:
    return _counted("cli.sessions.devices_forgotten", count)


def _cleared_throttle(count: int) -> str:
    if count == 0:
        return t("cli.throttle.none")
    return t("cli.throttle.cleared", counters=_counted("cli.throttle.counters", count))


def _account_label(settings: Settings) -> str:
    """The name of the account in the authenticator app."""
    host = urlsplit(settings.public_origin).hostname
    return t("cli.init.account", host=host) if host else t("cli.init.account_without_host")


def _ask_password(getpass: Prompt, out: Output) -> str | None:
    for _ in range(MAX_ATTEMPTS):
        password = getpass(f"{t('cli.init.new_password')} ")
        problem = password_policy_error(password)
        if problem is not None:
            out(problem)
            continue
        if getpass(f"{t('cli.init.repeat_password')} ") != password:
            out(t("cli.init.passwords_differ"))
            continue
        return password
    return None


def _confirm_totp(
    secret: str, prompt: Prompt, out: Output, clock: Callable[[], datetime]
) -> int | None:
    for _ in range(MAX_ATTEMPTS):
        code = prompt(f"{t('cli.init.totp_code')} ")
        step = totp.verify(code, secret, 0, now=clock())
        if step is not None:
            return step
        out(t("cli.init.wrong_code"))
    return None


async def run_init(
    store: SqliteStore,
    settings: Settings,
    *,
    prompt: Prompt = input,
    getpass: Prompt = getpass_module.getpass,
    out: Output = print,
    clock: Callable[[], datetime] = utc_now,
) -> int:
    """Interactive owner setup: password (asked twice, policy checked), a new TOTP
    secret shown as a QR code and ``otpauth://`` URI, and a current code to confirm
    it. Replacing an existing owner asks for confirmation. Saving revokes every
    session, forgets every known device and clears the login throttling. Nothing
    is saved unless every step succeeds. Returns the exit code (0 saved, 1
    cancelled or failed)."""
    try:
        replacing = await store.get_owner() is not None
        if replacing:
            out(t("cli.init.owner_exists"))
            if prompt(f"{t('cli.init.replace')} ").strip().lower() not in _YES:
                out(t("cli.init.cancelled_unchanged"))
                return 1

        out(t("cli.init.choose_password", min=number(MIN_PASSWORD_LENGTH)))
        password = _ask_password(getpass, out)
        if password is None:
            out(t("cli.init.too_many_attempts"))
            return 1

        secret = totp.new_secret()
        uri = totp.provisioning_uri(secret, _account_label(settings))
        out("")
        for line in t("cli.init.scan").splitlines():
            out(line)
        out("")
        out(render_qr(uri))
        out(t("cli.init.manual_key", secret=secret))
        out(f"URI: {uri}")
        out("")
        step = _confirm_totp(secret, prompt, out, clock)
        if step is None:
            out(t("cli.init.totp_unconfirmed"))
            return 1
    except (EOFError, KeyboardInterrupt):
        out("")
        out(t("cli.init.cancelled_unsaved"))
        return 1

    revoked = await store.set_owner(
        password_hash=await hash_password_async(password), totp_secret=secret, totp_last_step=step
    )
    if replacing:
        out(t("cli.init.replaced", sessions=_closed_sessions(revoked)))
    else:
        out(t("cli.init.done"))
    out(t("cli.init.log_in"))
    return 0


async def run_reset_sessions(store: SqliteStore, *, out: Output = print) -> int:
    """Revoke every session and forget every known device (every browser must log
    in again, as a new device), and clear the login throttling. Returns 0.

    The server runs in another process: its open WebSockets notice the revocation
    at their next periodic session check (``server.ws.SESSION_CHECK_SECONDS``)."""
    revoked = await store.delete_all_sessions()
    devices = await store.delete_all_devices()
    counters = await store.clear_throttle()
    message = _closed_sessions(revoked)
    out(t("cli.sessions.log_in_again", sessions=message) if revoked else message)
    if devices:
        out(_forgotten_devices(devices))
    if counters:
        out(_cleared_throttle(counters))
    return 0


async def run_reset_throttle(store: SqliteStore, *, out: Output = print) -> int:
    """Clear every login throttling counter and lock (the owner can log in again
    from any address right away). Sessions and devices are kept. Returns 0."""
    counters = await store.clear_throttle()
    out(_cleared_throttle(counters))
    if counters:
        out(t("cli.throttle.log_in"))
    return 0
