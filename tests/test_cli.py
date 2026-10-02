import asyncio
import json
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

import agentic_os
from agentic_os import __version__
from agentic_os.cli import log_config, main, run_doctor
from agentic_os.config import Settings, get_settings
from agentic_os.domain import AgentName, ProviderMode
from agentic_os.fx import FxRate
from agentic_os.providers.base import ModelInfo, Provider, ProviderStatus, UsageLimit
from agentic_os.providers.fake import FakeProvider
from agentic_os.storage import FxSettings, RuntimeSettings, SqliteStore, utc_now


def isolated_settings(**values: Any) -> Settings:
    """Settings from ``values`` only (no ``.env`` file)."""
    return Settings(**{"_env_file": None, **values})


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Process settings from the environment, isolated in ``tmp_path``."""
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setenv("AOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AOS_WEB_DIST", str(tmp_path / "no-dist"))
    monkeypatch.setenv("AOS_CLAUDE_MODE", "fake")
    monkeypatch.setenv("AOS_CHATGPT_MODE", "fake")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_version_matches_package_metadata() -> None:
    assert __version__ == version("agentic-os")


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"agentic-os {__version__}"


def test_bare_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ús: agentic-os")
    for command in ("serve", "init", "reset-sessions", "reset-throttle", "doctor"):
        assert command in out


def test_serve_runs_uvicorn_with_the_app_factory(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))
    monkeypatch.setenv("AOS_TRUSTED_PROXIES", "172.30.0.2")
    monkeypatch.setenv("AOS_LOG_LEVEL", "WARNING")

    assert main(["serve", "--port", "9001"]) == 0
    [(app, kwargs)] = calls
    assert app == "agentic_os.server.app:create_app"
    assert kwargs["factory"] is True
    assert (kwargs["host"], kwargs["port"]) == ("127.0.0.1", 9001)
    assert (kwargs["loop"], kwargs["http"]) == ("uvloop", "httptools")
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == "172.30.0.2"
    assert kwargs["timeout_keep_alive"] == 75
    assert kwargs["ws_ping_interval"] == 20
    assert kwargs["ws_max_size"] == 1024 * 1024
    assert kwargs["log_level"] == "warning"
    assert kwargs["reload"] is False
    assert capsys.readouterr().out == ""

    assert main(["serve", "--dev", "--host", "0.0.0.0"]) == 0
    _, kwargs = calls[-1]
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["log_level"] == "debug"
    out = capsys.readouterr().out
    assert "AOS_SECURE_COOKIES=false" in out
    assert "AOS_EXTRA_ORIGINS" in out


def test_serve_rejects_an_unknown_log_level(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: pytest.fail("must not run"))
    monkeypatch.setenv("AOS_LOG_LEVEL", "verbose")
    assert main(["serve"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("La configuració (variables AOS_*) no és vàlida:")
    assert "- AOS_LOG_LEVEL: ha de ser un d'aquests valors: critical, error, warning" in err


def test_log_config_adds_the_application_logger() -> None:
    config = log_config("info")
    assert config["loggers"]["agentic_os"]["level"] == "INFO"
    assert config["loggers"]["agentic_os"]["handlers"] == ["app"]
    assert "uvicorn.access" in config["loggers"]


def test_the_access_log_keeps_no_query_string() -> None:
    """An upload names its file in the query (``PUT /api/attachments?name=...``) and a
    search sends its text (``?q=...``): the access log keeps only the path. In a process of
    its own, configured as uvicorn configures it, so the tests' logging stays as it is."""
    package_root = Path(agentic_os.__file__).resolve().parents[1]  # the code under test
    line = "'%s - \"%s %s HTTP/%s\" %d', '203.0.113.9:40000'"
    code = (
        f"import sys; sys.path.insert(0, {str(package_root)!r}); "
        "import logging.config; from agentic_os.cli import log_config; "
        "logging.config.dictConfig(log_config('info')); "
        "log = logging.getLogger('uvicorn.access'); "
        f"log.info({line}, 'PUT', '/api/attachments?name=informe%20m%C3%A8dic.pdf', '1.1', 201); "
        f"log.info({line}, 'GET', '/api/conversations?q=diagn%C3%B2stic&limit=20', '1.1', 200); "
        f"log.info({line}, 'GET', '/api/health', '1.1', 200)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    requests = [entry.split('"')[1] for entry in result.stdout.splitlines()]
    assert requests == [
        "PUT /api/attachments HTTP/1.1",
        "GET /api/conversations HTTP/1.1",
        "GET /api/health HTTP/1.1",
    ]
    assert "?" not in result.stdout and not result.stderr


def test_reset_sessions(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reset-sessions"]) == 0
    assert capsys.readouterr().out.strip() == "No hi havia cap sessió oberta."


def test_reset_throttle(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    async def lock() -> None:
        async with await SqliteStore.open(env / "data" / "agentic_os.sqlite3") as store:
            await store.record_throttle_failure(
                "global",
                utc_now(),
                reset_after=timedelta(hours=24),
                lock_for=lambda _: timedelta(minutes=15),
            )

    asyncio.run(lock())
    assert main(["reset-throttle"]) == 0
    out = capsys.readouterr().out
    assert "S'han esborrat els bloquejos d'inici de sessió (1 comptador" in out
    assert main(["reset-throttle"]) == 0
    assert capsys.readouterr().out.strip() == (
        "No hi havia cap bloqueig ni cap intent fallit registrat."
    )


def test_init_runs_the_admin_command(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[SqliteStore, Settings]] = []

    async def fake_init(store: SqliteStore, settings: Settings) -> int:
        seen.append((store, settings))
        return 1

    monkeypatch.setattr("agentic_os.admin.run_init", fake_init)
    assert main(["init"]) == 1
    [(_, settings)] = seen
    assert settings.data_dir == env / "data"


def test_invalid_settings(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AOS_PORT", "not-a-port")
    assert main(["doctor"]) == 2


INVALID_CONFIG = "La configuració (variables AOS_*) no és vàlida:"


@pytest.mark.parametrize("command", ["doctor", "reset-throttle", "serve"])
def test_a_list_that_is_not_json_gets_the_catalan_message(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: pytest.fail("must not run"))
    monkeypatch.setenv("AOS_EXTRA_ORIGINS", "http://localhost:5173")
    assert main([command]) == 2
    err = capsys.readouterr().err
    assert err.startswith(INVALID_CONFIG)
    assert "AOS_EXTRA_ORIGINS" in err and "JSON" in err
    assert '["http://localhost:5173"]' in err
    assert "Traceback" not in err and "SettingsError" not in err


@pytest.mark.parametrize(
    ("variable", "value", "reason"),
    [
        ("AOS_SESSION_IDLE_HOURS", "0", "com a mínim 1"),
        ("AOS_SESSION_IDLE_HOURS", "-3", "com a mínim 1"),
        ("AOS_SESSION_IDLE_HOURS", "9000", "com a màxim 8760"),
        ("AOS_SESSION_MAX_DAYS", "0", "com a mínim 1"),
        ("AOS_SESSION_MAX_DAYS", "1000000000", "com a màxim 3650"),
        ("AOS_LOGIN_MAX_FAILURES", "0", "com a mínim 1"),
        ("AOS_LOGIN_MAX_FAILURES", "5000", "com a màxim 1000"),
        ("AOS_PROVIDER_TIMEOUT_SECONDS", "0", "més gran que 0"),
        ("AOS_PROVIDER_TIMEOUT_SECONDS", "-1", "més gran que 0"),
        ("AOS_PROVIDER_TIMEOUT_SECONDS", "nan", "com a màxim 86400"),
        ("AOS_PROVIDER_TIMEOUT_SECONDS", "1e400", "com a màxim 86400"),
        ("AOS_PORT", "0", "com a mínim 1"),
        ("AOS_PORT", "70000", "com a màxim 65535"),
        ("AOS_PORT", "abc", "ha de ser un nombre enter"),
        ("AOS_SESSION_IDLE_HOURS", "1.5", "ha de ser un nombre enter"),
        ("AOS_CLAUDE_MODE", "web", "ha de ser «cli», «api» o «fake»"),
        ("AOS_SECURE_COOKIES", "potser", "ha de ser true o false"),
        ("AOS_LOG_LEVEL", "verbose", "critical, error, warning, info, debug, trace"),
    ],
)
def test_doctor_reports_values_the_server_could_not_start_with(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    variable: str,
    value: str,
    reason: str,
) -> None:
    monkeypatch.setenv(variable, value)
    assert main(["doctor"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # it stops before the diagnosis
    assert captured.err.startswith(INVALID_CONFIG)
    [line] = [x for x in captured.err.splitlines() if x.startswith("- ")]
    assert line.startswith(f"- {variable}: ")
    assert reason in line
    assert "Input should" not in captured.err and "errors.pydantic.dev" not in captured.err


def test_every_invalid_variable_is_listed(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AOS_SESSION_IDLE_HOURS", "0")
    monkeypatch.setenv("AOS_LOGIN_MAX_FAILURES", "0")
    assert main(["reset-sessions"]) == 2
    lines = [x for x in capsys.readouterr().err.splitlines() if x.startswith("- ")]
    assert [x.split(":")[0] for x in lines] == [
        "- AOS_SESSION_IDLE_HOURS",
        "- AOS_LOGIN_MAX_FAILURES",
    ]


def test_the_limits_themselves_are_valid() -> None:
    low = isolated_settings(
        port=1,
        session_idle_hours=1,
        session_max_days=1,
        login_max_failures=1,
        provider_timeout_seconds=0.1,
        log_level="WARNING",
    )
    assert low.log_level == "warning"
    high = isolated_settings(
        port=65535,
        session_idle_hours=8760,
        session_max_days=3650,
        login_max_failures=1000,
        provider_timeout_seconds=86400,
    )
    assert (high.session_idle_hours, high.session_max_days) == (8760, 3650)


def test_a_dotenv_that_is_not_utf8_gets_a_catalan_message(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (env / ".env").write_bytes(b"AOS_PORT=\xff\xfe9000\n")
    assert main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("No s'ha pogut llegir la configuració")
    assert ".env" in err and "UTF-8" in err
    assert "Traceback" not in err


# -- doctor ----------------------------------------------------------------------------


class StaticProvider:
    def __init__(self, status: ProviderStatus) -> None:
        self._status = status

    @property
    def agent(self) -> AgentName:
        return self._status.agent

    @property
    def mode(self) -> ProviderMode:
        return self._status.mode

    def stream(self, request: Any) -> Any:
        raise NotImplementedError

    async def prewarm(self, request: Any) -> None:
        return None

    async def status(self) -> ProviderStatus:
        return self._status

    @property
    def fast_model(self) -> str:
        return "haiku" if self.agent == "claude" else "gpt-6-luna"

    @property
    def models_live(self) -> bool:
        return True

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        model = self._status.model
        return (ModelInfo(id=model, label=model, is_default=True),)

    async def aclose(self) -> None:
        return None


def fakes() -> dict[AgentName, Provider]:
    return {"claude": FakeProvider("claude"), "chatgpt": FakeProvider("chatgpt")}


async def ready_settings(tmp_path: Path, **overrides: Any) -> Settings:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "web_dist": dist,
        "public_origin": "https://aos.example",
        "claude_mode": "fake",
        "chatgpt_mode": "fake",
    }
    settings = isolated_settings(**{**values, **overrides})
    async with await SqliteStore.open(settings.db_path) as store:
        await store.set_owner(password_hash="$argon2id$x", totp_secret="S", totp_last_step=0)
    return settings


async def doctor(settings: Settings, providers: dict[AgentName, Provider]) -> tuple[int, str]:
    lines: list[str] = []
    code = await run_doctor(settings, providers=providers, out=lines.append, status_timeout=1)
    return code, "\n".join(lines)


async def test_doctor_all_good(tmp_path: Path) -> None:
    settings = await ready_settings(tmp_path)
    code, out = await doctor(settings, fakes())
    assert code == 0, out
    assert "[ OK ] Propietari configurat (contrasenya i TOTP)." in out
    assert f"[ OK ] Interfície web: {(tmp_path / 'dist').resolve()}" in out
    assert "[ -- ] CLI de Claude Code: no cal (mode fake)." in out
    assert "[ OK ] Claude (mode fake, model fake-claude): Mode demostració" in out
    assert "[ OK ] ChatGPT (mode fake, model fake-chatgpt): Mode demostració" in out
    assert "       Model per defecte: fake-claude; per als resums: fake-claude-mini." in out
    assert (
        "[ -- ] Tipus de canvi manual de reserva: 1 $ = 0,86 € (encara no hi ha cap tipus "
        "recent del BCE; el servidor el baixa en arrencar)."
    ) in out
    assert "[AVÍS]" not in out
    assert out.endswith("Tot correcte.")


async def test_doctor_shows_the_chosen_models_and_the_ecb_rate(tmp_path: Path) -> None:
    settings = await ready_settings(tmp_path)
    async with await SqliteStore.open(settings.db_path) as store:
        await store.put_runtime_settings(
            RuntimeSettings(
                models={"claude": "claude-opus-5", "chatgpt": None},
                fast_models={"claude": "claude-haiku-4-5", "chatgpt": None},
            )
        )
        await store.put_ecb_rate(FxRate(0.8547, date(2026, 9, 25), "ecb"), utc_now())
    code, out = await doctor(settings, fakes())
    assert code == 0, out
    assert (
        "       Model per defecte: claude-opus-5 (triat al tauler); per als resums: "
        "claude-haiku-4-5 (triat al tauler)."
    ) in out
    assert "       Model per defecte: fake-chatgpt; per als resums: fake-chatgpt-mini." in out
    assert "[ OK ] Tipus de canvi del BCE del 25/09/2026: 1 $ = 0,8547 €." in out

    async with await SqliteStore.open(settings.db_path) as store:
        await store.put_runtime_settings(RuntimeSettings(fx=FxSettings("manual", 0.9)))
    code, out = await doctor(settings, fakes())
    assert "[ OK ] Tipus de canvi manual: 1 $ = 0,90 €." in out


async def test_doctor_reports_cli_versions_and_limits(tmp_path: Path) -> None:
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\necho '2.1.283 (Claude Code)'\n")
    script.chmod(0o755)
    settings = await ready_settings(tmp_path, claude_mode="cli", claude_cli_path=str(script))
    claude = StaticProvider(
        ProviderStatus(
            "claude",
            "cli",
            True,
            "opus",
            "Subscripció Max activa",
            limits=(UsageLimit("5h", 42.0, None, "allowed"),),
        )
    )
    code, out = await doctor(settings, {"claude": claude, "chatgpt": FakeProvider("chatgpt")})
    assert code == 0, out
    assert f"[ OK ] CLI de Claude Code: {script} (2.1.283 (Claude Code))" in out
    assert "[ OK ] Claude (mode cli, model opus): Subscripció Max activa" in out
    assert "       Límit de 5h: 42% usat (allowed)." in out


async def test_the_cli_version_checks_get_only_the_allowlisted_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``claude --version`` and ``codex --version`` run with the same closed environment
    as the CLIs themselves (CLAUDE.md): never the app's API keys or AOS_* settings."""
    for name, value in (
        ("AOS_ANTHROPIC_API_KEY", "sk-ant-secret"),
        ("OPENAI_API_KEY", "sk-openai-secret"),
        ("AOS_SESSION_SECRET", "session-secret"),
    ):
        monkeypatch.setenv(name, value)
    shown = "${AOS_ANTHROPIC_API_KEY}${OPENAI_API_KEY}${AOS_SESSION_SECRET}"
    for name in ("claude", "codex"):
        script = tmp_path / name
        script.write_text(f'#!/bin/sh\necho "{name} [{shown}] home=${{HOME:+yes}}"\n')
        script.chmod(0o755)
    settings = await ready_settings(
        tmp_path,
        claude_mode="cli",
        claude_cli_path=str(tmp_path / "claude"),
        chatgpt_mode="cli",
        codex_cli_path=str(tmp_path / "codex"),
    )
    _, out = await doctor(settings, fakes())
    assert "secret" not in out
    assert f"[ OK ] CLI de Claude Code: {tmp_path / 'claude'} (claude [] home=yes)" in out
    assert f"[ OK ] CLI de Codex: {tmp_path / 'codex'} (codex [] home=yes)" in out


async def test_doctor_reports_critical_problems(tmp_path: Path) -> None:
    settings = isolated_settings(
        data_dir=tmp_path / "data",
        web_dist=tmp_path / "no-dist",
        public_origin="http://ai.example.com",
        claude_mode="fake",
        chatgpt_mode="cli",
        codex_cli_path=str(tmp_path / "missing-codex"),
    )
    chatgpt = StaticProvider(
        ProviderStatus("chatgpt", "cli", False, "gpt-5", "Sense sessió: executa «codex login»")
    )
    code, out = await doctor(settings, {"claude": FakeProvider("claude"), "chatgpt": chatgpt})
    assert code == 1
    assert "[AVÍS] L'origen és http:// però les cookies són segures" in out
    assert "[AVÍS] El directori de dades" in out
    assert "[ERROR] Encara no hi ha base de dades ni propietari" in out
    assert "[AVÍS] No s'ha trobat la interfície web compilada" in out
    assert "[ERROR] No es troba la CLI de Codex" in out
    assert "[ERROR] ChatGPT (mode cli, model gpt-5): Sense sessió" in out
    assert "       Model per defecte: gpt-5; per als resums: gpt-6-luna." in out
    assert out.endswith("Hi ha 3 problemes crítics.")


async def test_doctor_describes_the_models_of_a_provider_that_does_not_answer(
    tmp_path: Path,
) -> None:
    class Hanging(FakeProvider):
        async def status(self) -> ProviderStatus:
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

    settings = await ready_settings(tmp_path)
    code, out = await doctor(settings, {"claude": Hanging("claude"), "chatgpt": fakes()["chatgpt"]})
    assert code == 1
    assert "[ERROR] Claude (mode fake): el proveïdor no respon." in out
    assert "       Model per defecte: ?; per als resums: fake-claude-mini." in out


async def test_doctor_warns_about_loose_permissions(tmp_path: Path) -> None:
    settings = await ready_settings(tmp_path)
    settings.data_dir.chmod(0o755)
    code, out = await doctor(settings, fakes())
    assert code == 0
    assert f"[AVÍS] Permisos massa oberts a {settings.data_dir} (0755)" in out
    assert out.endswith("Tot correcte (1 avís).")
    assert stat.S_IMODE(settings.db_path.stat().st_mode) == 0o600


async def test_doctor_reports_a_missing_owner(tmp_path: Path) -> None:
    settings = isolated_settings(data_dir=tmp_path / "data", claude_mode="fake")
    async with await SqliteStore.open(settings.db_path):
        pass  # database created, owner never configured
    code, out = await doctor(settings, fakes())
    assert code == 1
    assert "[ERROR] No hi ha cap propietari configurat" in out


FAKE_CODEX = Path(__file__).parent / "providers" / "fixtures" / "codex" / "fake_app_server.py"


def codex_state_dirs(codex_home: Path) -> list[Path]:
    """``sqlite_home`` of every app-server the fake Codex CLI started."""
    log = codex_home / "requests.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    starts = [entry["argv"] for entry in map(json.loads, lines) if "argv" in entry]
    return [
        Path(tomllib.loads(value)["sqlite_home"])
        for argv in starts
        if argv[:1] == ["app-server"]
        for value in argv
        if value.startswith("sqlite_home=")
    ]


@pytest.fixture
def live_codex_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The running server's Codex state directory, with the log its app-server has open."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    (tmp_path / "codex-home").mkdir()
    live = tmp_path / "codex-state"
    live.mkdir()
    live.chmod(0o750)
    (live / "logs_2.sqlite").write_text("LIVE-PROMPT", encoding="utf-8")
    return live


def assert_untouched(live: Path) -> None:
    assert sorted(path.name for path in live.iterdir()) == ["logs_2.sqlite"]
    assert (live / "logs_2.sqlite").read_text(encoding="utf-8") == "LIVE-PROMPT"
    assert stat.S_IMODE(live.stat().st_mode) == 0o750


async def test_doctor_never_touches_the_live_codex_state(
    tmp_path: Path, live_codex_state: Path
) -> None:
    """With the app running, doctor starts a Codex app-server of its own. A start deletes
    the log databases of its state directory, which the server's app-server has open, and
    two app-servers must never share one: doctor's gets a private temporary directory."""
    settings = await ready_settings(
        tmp_path,
        chatgpt_mode="cli",
        codex_cli_path=str(FAKE_CODEX),
        codex_state_dir=live_codex_state,
    )
    lines: list[str] = []
    code = await run_doctor(settings, out=lines.append, status_timeout=10)
    out = "\n".join(lines)
    assert code == 0, out
    assert "[ OK ] ChatGPT (mode cli, model gpt-6-astra): Subscripció ChatGPT activa" in out
    assert_untouched(live_codex_state)
    [private] = codex_state_dirs(tmp_path / "codex-home")
    assert not private.is_relative_to(live_codex_state.resolve())
    assert not private.exists()  # removed when doctor is done


async def test_doctor_skips_codex_without_a_private_state_directory(
    tmp_path: Path, live_codex_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a private state directory doctor does not start Codex at all: it never
    falls back to the live one."""

    def no_space(*args: Any, **kwargs: Any) -> str:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tempfile, "mkdtemp", no_space)
    settings = await ready_settings(
        tmp_path,
        chatgpt_mode="cli",
        codex_cli_path=str(FAKE_CODEX),
        codex_state_dir=live_codex_state,
    )
    lines: list[str] = []
    code = await run_doctor(settings, out=lines.append, status_timeout=10)
    out = "\n".join(lines)
    assert code == 1, out
    assert (
        "[ERROR] ChatGPT (mode cli): no s'ha pogut crear un directori temporal per a l'estat "
        "de Codex ([Errno 28] No space left on device)."
    ) in out
    assert "[ OK ] Claude (mode fake, model fake-claude): Mode demostració" in out
    assert_untouched(live_codex_state)
    assert codex_state_dirs(tmp_path / "codex-home") == []  # no app-server at all


def version_only(tmp_path: Path) -> str:
    """A ``codex`` that only answers ``--version`` (doctor's providers are stubbed)."""
    script = tmp_path / "codex-version"
    script.write_text("#!/bin/sh\necho 'codex-cli 0.157.1'\n")
    script.chmod(0o755)
    return str(script)


class StuckCodex(FakeProvider):
    """ChatGPT whose status and close only end when cancelled (a slow app-server)."""

    def __init__(self) -> None:
        super().__init__("chatgpt")
        self.asked = asyncio.Event()
        self.closing = asyncio.Event()

    async def status(self) -> ProviderStatus:
        self.asked.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closing.set()
        await asyncio.Event().wait()


async def test_doctor_removes_its_codex_state_even_when_cancelled_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second Ctrl+C while doctor's Codex is being closed still removes its temporary
    state directory."""
    from agentic_os.providers import factory

    codex = StuckCodex()
    dirs: list[Path] = []

    def own(
        settings: Settings, *, codex_state_dir: Path | None = None
    ) -> dict[AgentName, Provider]:
        assert codex_state_dir is not None and codex_state_dir.is_dir()
        dirs.append(codex_state_dir)
        return {"claude": FakeProvider("claude"), "chatgpt": codex}

    monkeypatch.setattr(factory, "build_providers", own)
    settings = await ready_settings(
        tmp_path, chatgpt_mode="cli", codex_cli_path=version_only(tmp_path)
    )
    running = asyncio.create_task(run_doctor(settings, out=lambda _: None, status_timeout=60))
    await asyncio.wait_for(codex.asked.wait(), 5)
    running.cancel()  # the first Ctrl+C: doctor closes its providers
    await asyncio.wait_for(codex.closing.wait(), 5)
    running.cancel()  # the second one, while Codex is closing
    with pytest.raises(asyncio.CancelledError):
        await running
    [state] = dirs
    assert not state.exists()


async def test_doctor_removes_its_codex_state_when_the_providers_cannot_be_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentic_os.providers import factory

    dirs: list[Path] = []

    def broken(settings: Settings, *, codex_state_dir: Path | None = None) -> Any:
        assert codex_state_dir is not None
        dirs.append(codex_state_dir)
        raise RuntimeError("boom")

    monkeypatch.setattr(factory, "build_providers", broken)
    settings = await ready_settings(
        tmp_path, chatgpt_mode="cli", codex_cli_path=version_only(tmp_path)
    )
    with pytest.raises(RuntimeError, match="boom"):
        await run_doctor(settings, out=lambda _: None, status_timeout=1)
    [state] = dirs
    assert not state.exists()


def test_doctor_command(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "ClaudeGPT OS" in out
    assert "[ OK ] Claude (mode fake" in out


def test_cli_version_handles_failures(tmp_path: Path) -> None:
    from agentic_os.cli import cli_version

    failing = tmp_path / "failing"
    failing.write_text("#!/bin/sh\nexit 3\n")
    failing.chmod(0o755)
    assert asyncio.run(cli_version(str(failing), {})) is None
    assert asyncio.run(cli_version(str(tmp_path / "missing"), {})) is None
