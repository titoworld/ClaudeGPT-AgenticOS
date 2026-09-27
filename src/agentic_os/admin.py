"""Administration commands run from the terminal on the VPS (``agentic-os init``,
``agentic-os reset-sessions``, ``agentic-os reset-throttle``). All output is in
Catalan.

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
from agentic_os.security import totp
from agentic_os.security.passwords import hash_password_async, password_policy_error
from agentic_os.storage import SqliteStore, utc_now

MAX_ATTEMPTS: Final = 3
_YES: Final = frozenset({"s", "si", "sí", "y", "yes"})

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


def _closed_sessions(count: int) -> str:
    if count == 0:
        return "No hi havia cap sessió oberta."
    if count == 1:
        return "S'ha tancat 1 sessió."
    return f"S'han tancat {count} sessions."


def _forgotten_devices(count: int) -> str:
    if count == 1:
        return "S'ha oblidat 1 dispositiu conegut."
    return f"S'han oblidat {count} dispositius coneguts."


def _cleared_throttle(count: int) -> str:
    if count == 0:
        return "No hi havia cap bloqueig ni cap intent fallit registrat."
    counters = (
        "1 comptador d'intents fallits" if count == 1 else f"{count} comptadors d'intents fallits"
    )
    return f"S'han esborrat els bloquejos d'inici de sessió ({counters})."


def _account_label(settings: Settings) -> str:
    host = urlsplit(settings.public_origin).hostname
    return f"propietari@{host}" if host else "propietari"


def _ask_password(getpass: Prompt, out: Output) -> str | None:
    for _ in range(MAX_ATTEMPTS):
        password = getpass("Contrasenya nova: ")
        problem = password_policy_error(password)
        if problem is not None:
            out(problem)
            continue
        if getpass("Repeteix la contrasenya: ") != password:
            out("Les contrasenyes no coincideixen.")
            continue
        return password
    return None


def _confirm_totp(
    secret: str, prompt: Prompt, out: Output, clock: Callable[[], datetime]
) -> int | None:
    for _ in range(MAX_ATTEMPTS):
        code = prompt("Codi de 6 xifres que mostra l'aplicació: ")
        step = totp.verify(code, secret, 0, now=clock())
        if step is not None:
            return step
        out("Codi incorrecte. Comprova que l'hora del telèfon i del servidor són correctes.")
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
            out(
                "Ja hi ha un propietari configurat. Si continues, se substituiran la "
                "contrasenya i el TOTP i es tancaran totes les sessions obertes."
            )
            if prompt("Vols substituir-lo? [s/N] ").strip().lower() not in _YES:
                out("Operació cancel·lada. No s'ha canviat res.")
                return 1

        out("Tria la contrasenya del propietari (mínim 12 caràcters; millor una frase de pas).")
        password = _ask_password(getpass, out)
        if password is None:
            out("Massa intents. No s'ha desat res.")
            return 1

        secret = totp.new_secret()
        uri = totp.provisioning_uri(secret, _account_label(settings))
        out("")
        out("Escaneja aquest codi QR amb l'aplicació d'autenticació (Aegis, Google")
        out("Authenticator, 1Password...):")
        out("")
        out(render_qr(uri))
        out(f"Si no el pots escanejar, introdueix aquesta clau manualment: {secret}")
        out(f"URI: {uri}")
        out("")
        step = _confirm_totp(secret, prompt, out, clock)
        if step is None:
            out("No s'ha pogut confirmar el TOTP. No s'ha desat res.")
            return 1
    except (EOFError, KeyboardInterrupt):
        out("")
        out("Operació cancel·lada. No s'ha desat res.")
        return 1

    revoked = await store.set_owner(
        password_hash=await hash_password_async(password), totp_secret=secret, totp_last_step=step
    )
    if replacing:
        out(
            f"Propietari substituït. {_closed_sessions(revoked)} S'han oblidat els "
            "dispositius coneguts i s'han esborrat els bloquejos d'inici de sessió."
        )
    else:
        out("Propietari configurat.")
    out("Ja pots iniciar sessió al navegador amb la contrasenya i el codi TOTP.")
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
    out(f"{message} Caldrà tornar a iniciar sessió." if revoked else message)
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
        out("Ja es pot tornar a iniciar sessió des de qualsevol adreça.")
    return 0
