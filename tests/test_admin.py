import re
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyotp
import pytest

from agentic_os.admin import render_qr, run_init, run_reset_sessions
from agentic_os.config import Settings
from agentic_os.security.passwords import verify_password
from agentic_os.storage import SqliteStore

NOW = datetime(2026, 9, 27, 12, 0, 10, tzinfo=UTC)
PASSWORD = "una frase de pas prou llarga"
SETTINGS = Settings(public_origin="https://ai.example.com")
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
VALID_CODES = {pyotp.TOTP(SECRET).at(NOW, offset) for offset in (-1, 0, 1)}
WRONG_CODE = next(code for code in ("000000", "111111", "222222") if code not in VALID_CODES)


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentic_os.security.totp.new_secret", lambda: SECRET)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteStore]:
    async with await SqliteStore.open(tmp_path / "db.sqlite3") as store:
        yield store


class Console:
    """Scripted terminal: answers come from lists; ``"<totp>"`` is replaced by the
    current code of the secret printed so far."""

    def __init__(self, answers: list[str], passwords: list[str]) -> None:
        self.answers: Iterator[str] = iter(answers)
        self.passwords: Iterator[str] = iter(passwords)
        self.lines: list[str] = []
        self.prompts: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def out(self, line: str) -> None:
        self.lines.append(line)

    def prompt(self, message: str) -> str:
        self.prompts.append(message)
        answer = next(self.answers)
        if answer == "<totp>":
            match = re.search(r"manualment: ([A-Z2-7]{32})", self.text)
            assert match is not None
            return pyotp.TOTP(match.group(1)).at(NOW)
        return answer

    def getpass(self, message: str) -> str:
        self.prompts.append(message)
        return next(self.passwords)


def clock() -> datetime:
    return NOW


async def init(store: SqliteStore, console: Console) -> int:
    return await run_init(
        store,
        SETTINGS,
        prompt=console.prompt,
        getpass=console.getpass,
        out=console.out,
        clock=clock,
    )


async def test_first_setup_saves_owner(store: SqliteStore) -> None:
    console = Console(answers=["<totp>"], passwords=[PASSWORD, PASSWORD])
    assert await init(store, console) == 0
    owner = await store.get_owner()
    assert owner is not None
    assert verify_password(owner.password_hash, PASSWORD)
    assert owner.totp_secret == SECRET
    assert f"manualment: {SECRET}" in console.text
    assert "otpauth://totp/ClaudeGPT%20OS:propietari%40ai.example.com" in console.text
    assert "Propietari configurat." in console.text
    # The confirmation code cannot be replayed at login.
    assert owner.totp_last_step == int(NOW.timestamp()) // 30
    assert not await store.consume_totp_step(owner.totp_last_step)


async def test_retries_bad_passwords_and_codes(store: SqliteStore) -> None:
    console = Console(
        answers=[WRONG_CODE, "abc", "<totp>"],
        passwords=["curta", PASSWORD, "una altra de diferent", PASSWORD, PASSWORD],
    )
    assert await init(store, console) == 0
    assert "com a mínim 12 caràcters" in console.text
    assert "Les contrasenyes no coincideixen." in console.text
    assert console.text.count("Codi incorrecte") == 2
    assert await store.get_owner() is not None


async def test_too_many_password_attempts_saves_nothing(store: SqliteStore) -> None:
    console = Console(answers=[], passwords=["curta", "curta", "curta"])
    assert await init(store, console) == 1
    assert "Massa intents" in console.text
    assert await store.get_owner() is None


async def test_unconfirmed_totp_saves_nothing(store: SqliteStore) -> None:
    console = Console(answers=["12345", "abcdef", WRONG_CODE], passwords=[PASSWORD, PASSWORD])
    assert await init(store, console) == 1
    assert "No s'ha pogut confirmar el TOTP" in console.text
    assert await store.get_owner() is None


async def test_existing_owner_is_kept_unless_confirmed(store: SqliteStore) -> None:
    await store.set_owner(password_hash="old", totp_secret="OLD", totp_last_step=0)
    console = Console(answers=["n"], passwords=[])
    assert await init(store, console) == 1
    assert "Ja hi ha un propietari configurat" in console.text
    owner = await store.get_owner()
    assert owner is not None and owner.password_hash == "old"


async def test_replacing_owner_revokes_sessions(store: SqliteStore) -> None:
    await store.set_owner(password_hash="old", totp_secret="OLD", totp_last_step=0)
    for index in range(2):
        await store.create_session(
            f"hash{index}",
            created_at=NOW,
            expires_at=NOW + timedelta(days=1),
            ip=None,
            user_agent=None,
        )
    console = Console(answers=["Sí", "<totp>"], passwords=[PASSWORD, PASSWORD])
    assert await init(store, console) == 0
    assert "Propietari substituït. S'han tancat 2 sessions." in console.text
    assert await store.get_session("hash0") is None
    owner = await store.get_owner()
    assert owner is not None and owner.totp_secret != "OLD"


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
async def test_interrupted_input_cancels(
    store: SqliteStore, error: Callable[[], BaseException]
) -> None:
    def interrupted(message: str) -> str:
        raise error()

    result = await run_init(store, SETTINGS, prompt=interrupted, getpass=interrupted, out=print)
    assert result == 1
    assert await store.get_owner() is None


async def test_reset_sessions(store: SqliteStore) -> None:
    await store.create_session(
        "hash", created_at=NOW, expires_at=NOW + timedelta(days=1), ip=None, user_agent=None
    )
    lines: list[str] = []
    assert await run_reset_sessions(store, out=lines.append) == 0
    assert lines == ["S'ha tancat 1 sessió. Caldrà tornar a iniciar sessió."]
    assert await store.get_session("hash") is None
    assert await run_reset_sessions(store, out=lines.append) == 0
    assert lines[-1] == "No hi havia cap sessió oberta."


def test_render_qr_draws_blocks() -> None:
    art = render_qr("otpauth://totp/x?secret=ABC")
    lines = art.splitlines()
    assert len(lines) > 10
    assert all(len(line) == len(lines[0]) for line in lines)
    assert set("".join(lines)) <= {" ", "\u00a0", "▀", "▄", "█"}
