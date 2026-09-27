"""``/api/auth``: login (password + TOTP), logout and session state."""

import logging
import math
from typing import Final

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from agentic_os.security import totp
from agentic_os.security.passwords import (
    hash_password_async,
    needs_rehash,
    verify_password_async,
)
from agentic_os.server.deps import (
    AppState,
    SessionDep,
    StateDep,
    client_ip,
    read_json,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")

LOGIN_FAILED_DETAIL: Final = "Credencials incorrectes."
MAX_TOTP_LENGTH: Final = 32

_dummy_hash: str | None = None


async def _dummy_password_hash() -> str:
    """A hash to verify against when no owner exists, so a login costs the same
    argon2 work either way (computed once, with the current parameters)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await hash_password_async("no owner configured: dummy password")
    return _dummy_hash


def _wait_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} segon" if seconds == 1 else f"{seconds} segons"
    minutes = math.ceil(seconds / 60)
    return f"{minutes} minut" if minutes == 1 else f"{minutes} minuts"


def _too_many_attempts(wait: int) -> JSONResponse:
    return JSONResponse(
        {
            "detail": f"Massa intents fallits. Torna-ho a provar d'aquí a {_wait_text(wait)}.",
            "retry_after": wait,
        },
        status_code=429,
        headers={"Retry-After": str(wait)},
    )


def _credentials(body: object) -> tuple[str, str]:
    if isinstance(body, dict):
        password, code = body.get("password"), body.get("totp")
        if isinstance(password, str) and isinstance(code, str) and len(code) <= MAX_TOTP_LENGTH:
            return password, code
    raise HTTPException(status_code=422, detail="Cal indicar la contrasenya i el codi TOTP.")


def _set_session_cookie(response: Response, state: AppState, token: str) -> None:
    response.set_cookie(
        state.cookie_name,
        token,
        max_age=int(state.sessions.max_age.total_seconds()),
        path="/",
        secure=state.settings.secure_cookies,
        httponly=True,
        samesite="strict",
    )


@router.get("/state")
async def auth_state(request: Request, state: StateDep) -> JSONResponse:
    """Never requires a session: tells the SPA whether to show the login screen."""
    session = await state.session_for(request)
    owner = await state.store.get_owner()
    return JSONResponse({"authenticated": session is not None, "setup_required": owner is None})


@router.post("/login", status_code=204)
async def login(request: Request, state: StateDep) -> Response:
    now = state.clock()
    ip = client_ip(request)
    wait = await state.throttle.retry_after(ip, now)
    if wait > 0:
        return _too_many_attempts(wait)
    password, code = _credentials(await read_json(request))

    owner = await state.store.get_owner()
    step: int | None = None
    if owner is None:
        await verify_password_async(await _dummy_password_hash(), password)
        password_ok = False
    else:
        password_ok = await verify_password_async(owner.password_hash, password)
        step = totp.verify(code, owner.totp_secret, owner.totp_last_step, now=now)
    # The TOTP step is consumed (replay protection) only with the right password.
    ok = password_ok and step is not None and await state.store.consume_totp_step(step)
    if owner is None or not ok:
        await state.throttle.record_failure(ip, now)
        raise HTTPException(status_code=401, detail=LOGIN_FAILED_DETAIL)

    await state.throttle.record_success(ip)
    if needs_rehash(owner.password_hash):
        try:
            await state.store.update_password_hash(await hash_password_async(password))
        except Exception:
            logger.exception("Could not rehash the owner password")
    # Never reuse a session token presented before the login (session fixation).
    await state.sessions.revoke(request.cookies.get(state.cookie_name))
    token = await state.sessions.create(now, ip=ip, user_agent=request.headers.get("user-agent"))
    response = Response(status_code=204)
    _set_session_cookie(response, state, token)
    logger.info("Owner logged in from %s", ip or "?")
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request, state: StateDep, _session: SessionDep) -> Response:
    await state.sessions.revoke(request.cookies.get(state.cookie_name))
    response = Response(status_code=204)
    response.delete_cookie(
        state.cookie_name,
        path="/",
        secure=state.settings.secure_cookies,
        httponly=True,
        samesite="strict",
    )
    return response
