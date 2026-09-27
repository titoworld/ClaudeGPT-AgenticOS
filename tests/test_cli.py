import asyncio
import stat
from collections.abc import Iterator, Sequence
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

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
    for command in ("serve", "init", "reset-sessions", "doctor"):
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
    assert "AOS_LOG_LEVEL no és vàlid" in capsys.readouterr().err


def test_log_config_adds_the_application_logger() -> None:
    config = log_config("info")
    assert config["loggers"]["agentic_os"]["level"] == "INFO"
    assert config["loggers"]["agentic_os"]["handlers"] == ["app"]
    assert "uvicorn.access" in config["loggers"]


def test_reset_sessions(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["reset-sessions"]) == 0
    assert capsys.readouterr().out.strip() == "No hi havia cap sessió oberta."


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

    async def list_models(self) -> Sequence[ModelInfo]:
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
    assert asyncio.run(cli_version(str(failing))) is None
    assert asyncio.run(cli_version(str(tmp_path / "missing"))) is None
