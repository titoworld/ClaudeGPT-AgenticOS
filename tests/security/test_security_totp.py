import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import pyotp

from agentic_os.security import totp

SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
NOW = datetime(2026, 9, 27, 12, 0, 10, tzinfo=UTC)
STEP = int(NOW.timestamp()) // 30


def code_at(step: int) -> str:
    return pyotp.TOTP(SECRET).generate_otp(step)


def test_new_secret_is_random_base32_160_bits() -> None:
    secret = totp.new_secret()
    assert len(secret) == 32
    assert len(base64.b32decode(secret)) == 20
    assert secret != totp.new_secret()


def test_provisioning_uri() -> None:
    uri = totp.provisioning_uri(SECRET, "propietari@example.com")
    parts = urlsplit(uri)
    assert parts.scheme == "otpauth"
    assert parts.netloc == "totp"
    assert unquote(parts.path) == "/ClaudeGPT OS:propietari@example.com"
    query = parse_qs(parts.query)
    assert query["secret"] == [SECRET]
    assert query["issuer"] == ["ClaudeGPT OS"]


def test_time_step_treats_naive_as_utc() -> None:
    assert totp.time_step(NOW) == STEP
    assert totp.time_step(NOW.replace(tzinfo=None)) == STEP


def test_verify_current_code_returns_its_step() -> None:
    assert totp.verify(code_at(STEP), SECRET, 0, now=NOW) == STEP


def test_code_with_spaces_is_accepted() -> None:
    code = code_at(STEP)
    assert totp.verify(f" {code[:3]} {code[3:]} ", SECRET, 0, now=NOW) == STEP


def test_window_accepts_one_step_each_side_only() -> None:
    assert totp.verify(code_at(STEP - 1), SECRET, 0, now=NOW) == STEP - 1
    assert totp.verify(code_at(STEP + 1), SECRET, 0, now=NOW) == STEP + 1
    assert totp.verify(code_at(STEP - 2), SECRET, 0, now=NOW) is None
    assert totp.verify(code_at(STEP + 2), SECRET, 0, now=NOW) is None


def test_replay_is_rejected() -> None:
    code = code_at(STEP)
    step = totp.verify(code, SECRET, 0, now=NOW)
    assert step == STEP
    # Same code again, still inside its validity window.
    assert totp.verify(code, SECRET, step, now=NOW + timedelta(seconds=15)) is None
    # An older code is rejected too once a newer step was accepted.
    assert totp.verify(code_at(STEP - 1), SECRET, step, now=NOW) is None
    # The next code is fine.
    assert totp.verify(code_at(STEP + 1), SECRET, step, now=NOW + timedelta(seconds=30)) == (
        STEP + 1
    )


def test_malformed_codes_are_rejected() -> None:
    for code in (
        "",
        "12345",
        "1234567",
        "12a456",
        "\uff11\uff12\uff13\uff14\uff15\uff16",
        "123-456",
    ):
        assert totp.normalize_code(code) is None
        assert totp.verify(code, SECRET, 0, now=NOW) is None


def test_wrong_code_is_rejected() -> None:
    valid = {code_at(STEP + offset) for offset in (-1, 0, 1)}
    wrong = next(code for code in ("000000", "111111", "222222") if code not in valid)
    assert totp.verify(wrong, SECRET, 0, now=NOW) is None
