"""Command-line entry point (``agentic-os``). All output is in Catalan.

- ``agentic-os serve [--host] [--port] [--dev]``: run the web server (uvicorn).
- ``agentic-os init``: configure the owner (password + TOTP).
- ``agentic-os reset-sessions``: close every open session and forget known devices.
- ``agentic-os reset-throttle``: lift the login lockouts.
- ``agentic-os doctor``: check the installation and the providers.
- ``agentic-os --version``.
"""

import argparse
import asyncio
import copy
import os
import shutil
import sqlite3
import stat
import sys
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import ValidationError

from agentic_os import __version__
from agentic_os.config import Settings, get_settings
from agentic_os.domain import AgentName, ProviderMode
from agentic_os.fx import FxRate, manual_rate
from agentic_os.providers.base import Provider, ProviderStatus
from agentic_os.providers.prompt_format import AGENT_LABELS
from agentic_os.storage import RuntimeSettings, SchemaVersionError, SqliteStore, utc_now

WS_MAX_BYTES: Final = 1024 * 1024
KEEP_ALIVE_SECONDS: Final = 75
"""Longer than the reverse proxy's upstream keepalive (Caddy: 30 s), so the proxy
never reuses a connection uvicorn has just closed (sporadic 502s)."""
STATUS_TIMEOUT_SECONDS: Final = 30.0
VERSION_TIMEOUT_SECONDS: Final = 15.0
LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
LOG_LEVELS: Final = ("critical", "error", "warning", "info", "debug", "trace")

Output = Callable[[str], None]


class _HelpFormatter(argparse.HelpFormatter):
    def add_usage(
        self,
        usage: str | None,
        actions: Iterable[argparse.Action],
        groups: Iterable[argparse._MutuallyExclusiveGroup],
        prefix: str | None = None,
    ) -> None:
        super().add_usage(usage, actions, groups, "ús: " if prefix is None else prefix)


def _parser(
    parent: "argparse._SubParsersAction[argparse.ArgumentParser] | None",
    name: str,
    description: str,
) -> argparse.ArgumentParser:
    kwargs: dict[str, Any] = {
        "description": description,
        "formatter_class": _HelpFormatter,
        "add_help": False,
    }
    if parent is None:
        parser = argparse.ArgumentParser(prog=name, **kwargs)
    else:
        parser = parent.add_parser(name, help=description, **kwargs)
    parser._optionals.title = "opcions"
    parser.add_argument("-h", "--help", action="help", help="mostra aquesta ajuda i surt")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = _parser(
        None,
        "agentic-os",
        "ClaudeGPT OS: Claude i ChatGPT responen, es critiquen i sintetitzen una resposta millor.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="mostra la versió i surt",
    )
    commands = parser.add_subparsers(dest="command", title="ordres", metavar="ORDRE")

    serve = _parser(commands, "serve", "Arrenca el servidor web.")
    serve.add_argument("--host", help="adreça on escoltar (per defecte, AOS_HOST)")
    serve.add_argument("--port", type=int, help="port on escoltar (per defecte, AOS_PORT)")
    serve.add_argument(
        "--dev",
        action="store_true",
        help=(
            "desenvolupament: registre detallat i consells de configuració. No relaxa "
            "les cookies: per a http local defineix AOS_SECURE_COOKIES=false"
        ),
    )
    _parser(commands, "init", "Configura el propietari: contrasenya i codi TOTP.")
    _parser(
        commands,
        "reset-sessions",
        "Tanca totes les sessions obertes, oblida els dispositius coneguts i esborra els "
        "bloquejos d'inici de sessió.",
    )
    _parser(
        commands,
        "reset-throttle",
        "Esborra els bloquejos per intents d'inici de sessió fallits (no tanca cap sessió).",
    )
    _parser(commands, "doctor", "Comprova la instal·lació, la configuració i els proveïdors.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        settings = get_settings()
    except ValidationError as exc:
        print(f"La configuració (variables AOS_*) no és vàlida:\n{exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        return serve(settings, host=args.host, port=args.port, dev=args.dev)
    if args.command == "doctor":
        return asyncio.run(run_doctor(settings))
    if args.command == "init":
        from agentic_os.admin import run_init

        return _with_store(settings, lambda store: run_init(store, settings))
    if args.command == "reset-throttle":
        from agentic_os.admin import run_reset_throttle

        return _with_store(settings, run_reset_throttle)
    from agentic_os.admin import run_reset_sessions

    return _with_store(settings, run_reset_sessions)


def _with_store(settings: Settings, command: Callable[[SqliteStore], Awaitable[int]]) -> int:
    async def run() -> int:
        async with await SqliteStore.open(settings.db_path) as store:
            return await command(store)

    try:
        return asyncio.run(run())
    except SchemaVersionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error) as exc:
        print(f"No s'ha pogut obrir la base de dades {settings.db_path}: {exc}", file=sys.stderr)
        return 1


# -- serve -----------------------------------------------------------------------------


def log_config(level: str) -> dict[str, Any]:
    """uvicorn's logging configuration plus the application's own loggers."""
    from uvicorn.config import LOGGING_CONFIG

    config: dict[str, Any] = copy.deepcopy(LOGGING_CONFIG)
    config["formatters"]["app"] = {
        "()": "uvicorn.logging.DefaultFormatter",
        "fmt": "%(levelprefix)s %(name)s: %(message)s",
        "use_colors": None,
    }
    config["handlers"]["app"] = {
        "formatter": "app",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stderr",
    }
    config["loggers"]["agentic_os"] = {
        "handlers": ["app"],
        "level": "DEBUG" if level == "trace" else level.upper(),
        "propagate": False,
    }
    return config


def _dev_hints(settings: Settings, out: Output) -> None:
    out("Mode de desenvolupament: registre detallat.")
    origin = urlsplit(settings.public_origin)
    if settings.secure_cookies and origin.scheme == "http":
        out(
            "Avís: AOS_PUBLIC_ORIGIN és http:// i les cookies són segures. Si el navegador "
            "no desa la sessió, defineix AOS_SECURE_COOKIES=false (només en local)."
        )
    if "http://localhost:5173" not in settings.allowed_origins:
        out(
            "Si fas servir el servidor de Vite (npm run dev), afegeix el seu origen: "
            "AOS_EXTRA_ORIGINS='[\"http://localhost:5173\"]'."
        )


def serve(settings: Settings, *, host: str | None, port: int | None, dev: bool) -> int:
    """Run uvicorn with the app factory until interrupted."""
    import uvicorn

    level = "debug" if dev else settings.log_level.lower()
    if level not in LOG_LEVELS:
        print(
            f"AOS_LOG_LEVEL no és vàlid: «{settings.log_level}» (valors: {', '.join(LOG_LEVELS)}).",
            file=sys.stderr,
        )
        return 2
    if dev:
        _dev_hints(settings, print)
    uvicorn.run(
        "agentic_os.server.app:create_app",
        factory=True,
        host=host if host is not None else settings.host,
        port=port if port is not None else settings.port,
        loop="uvloop",
        http="httptools",
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=10,
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
        ws_max_size=WS_MAX_BYTES,
        log_level=level,
        log_config=log_config(level),
        server_header=False,
        reload=False,
    )
    return 0


# -- doctor ----------------------------------------------------------------------------


@dataclass(slots=True)
class _Report:
    out: Output
    critical: int = 0
    warnings: int = 0

    def ok(self, message: str) -> None:
        self.out(f"[ OK ] {message}")

    def skip(self, message: str) -> None:
        self.out(f"[ -- ] {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        self.out(f"[AVÍS] {message}")

    def fail(self, message: str) -> None:
        self.critical += 1
        self.out(f"[ERROR] {message}")

    def detail(self, message: str) -> None:
        self.out(f"       {message}")


def _check_config(settings: Settings, report: _Report) -> None:
    origin = urlsplit(settings.public_origin)
    if origin.scheme not in ("http", "https") or not origin.hostname:
        report.fail(f"AOS_PUBLIC_ORIGIN no és un origen vàlid: «{settings.public_origin}».")
        return
    report.ok(f"Origen públic: {settings.public_origin}")
    if settings.secure_cookies and origin.scheme == "http" and origin.hostname not in LOCAL_HOSTS:
        report.warn(
            "L'origen és http:// però les cookies són segures: el navegador no desarà la "
            "sessió. Fes servir https o, només en local, AOS_SECURE_COOKIES=false."
        )
    if not settings.secure_cookies:
        report.warn("AOS_SECURE_COOKIES=false: correcte només per a desenvolupament local.")


def _check_permissions(path: str, mode: int, expected: int, report: _Report) -> None:
    if mode & 0o077:
        report.warn(
            f"Permisos massa oberts a {path} ({mode:04o}); recomanat {expected:04o}: "
            f"chmod {expected:o} {path}"
        )


def _check_data_dir(settings: Settings, report: _Report) -> None:
    data_dir = settings.data_dir
    if not data_dir.exists():
        report.warn(
            f"El directori de dades {data_dir} no existeix: es crearà amb «agentic-os init»."
        )
        return
    if not data_dir.is_dir():
        report.fail(f"{data_dir} no és un directori.")
        return
    if not os.access(data_dir, os.W_OK | os.X_OK):
        report.fail(f"No es pot escriure al directori de dades {data_dir}.")
        return
    report.ok(f"Directori de dades: {data_dir.resolve()}")
    _check_permissions(str(data_dir), stat.S_IMODE(data_dir.stat().st_mode), 0o700, report)
    db = settings.db_path
    if db.exists():
        _check_permissions(str(db), stat.S_IMODE(db.stat().st_mode), 0o600, report)


@dataclass(frozen=True, slots=True)
class _Stored:
    """What the dashboard stored: the owner's settings and the current exchange rate."""

    runtime: RuntimeSettings
    fx: FxRate


async def _check_database(settings: Settings, report: _Report) -> _Stored:
    """Check the owner; returns the stored settings (the defaults without a database)."""
    defaults = _Stored(RuntimeSettings(), manual_rate(RuntimeSettings().fx.eur_per_usd))
    if not settings.db_path.exists():
        report.fail("Encara no hi ha base de dades ni propietari: executa «agentic-os init».")
        return defaults
    try:
        async with await SqliteStore.open(settings.db_path) as store:
            owner = await store.get_owner()
            stored = _Stored(await store.get_runtime_settings(), await store.current_fx(utc_now()))
    except SchemaVersionError as exc:
        report.fail(str(exc))
        return defaults
    except Exception as exc:
        report.fail(f"No s'ha pogut obrir la base de dades: {exc}")
        return defaults
    if owner is None:
        report.fail("No hi ha cap propietari configurat: executa «agentic-os init».")
    else:
        report.ok("Propietari configurat (contrasenya i TOTP).")
    return stored


def _decimal(value: float) -> str:
    """Catalan decimal notation with 2 to 4 decimals (``0,86``, ``0,8547``)."""
    whole, _, decimals = f"{value:.4f}".rstrip("0").partition(".")
    return f"{whole},{decimals.ljust(2, '0')}"


def _report_fx(stored: _Stored, report: _Report) -> None:
    rate = f"1 $ = {_decimal(stored.fx.eur_per_usd)} €"
    if stored.fx.source == "ecb" and stored.fx.as_of is not None:
        report.ok(f"Tipus de canvi del BCE del {stored.fx.as_of.strftime('%d/%m/%Y')}: {rate}.")
    elif stored.runtime.fx.mode == "manual":
        report.ok(f"Tipus de canvi manual: {rate}.")
    else:
        report.skip(
            f"Tipus de canvi manual de reserva: {rate} (encara no hi ha cap tipus recent del "
            "BCE; el servidor el baixa en arrencar)."
        )


def _check_web(settings: Settings, report: _Report) -> None:
    from agentic_os.server.static import find_web_dist

    dist = find_web_dist(settings)
    if dist is None:
        report.warn(
            "No s'ha trobat la interfície web compilada: executa «npm ci && npm run build» a "
            "web/ o defineix AOS_WEB_DIST."
        )
    else:
        report.ok(f"Interfície web: {dist}")


async def cli_version(path: str) -> str | None:
    """First line of ``<path> --version``, or ``None`` if it fails or hangs."""
    try:
        process = await asyncio.create_subprocess_exec(
            path,
            "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        return None
    try:
        output, _ = await asyncio.wait_for(process.communicate(), VERSION_TIMEOUT_SECONDS)
    except TimeoutError:
        return None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        return None
    lines = output.decode("utf-8", errors="replace").strip().splitlines()
    return lines[0].strip()[:200] if lines else "?"


async def _check_cli(
    label: str, mode: ProviderMode, path: str, variable: str, report: _Report
) -> None:
    if mode != "cli":
        report.skip(f"CLI de {label}: no cal (mode {mode}).")
        return
    resolved = shutil.which(path)
    if resolved is None:
        report.fail(
            f"No es troba la CLI de {label} («{path}»): instal·la-la o defineix {variable}."
        )
        return
    version = await cli_version(resolved)
    if version is None:
        report.fail(f"La CLI de {label} ({resolved}) no respon a --version.")
    else:
        report.ok(f"CLI de {label}: {resolved} ({version})")


def _describe_limits(status: ProviderStatus, report: _Report) -> None:
    for limit in status.limits:
        used = "?" if limit.used_percent is None else f"{limit.used_percent:.0f}%"
        resets = (
            f", es renova el {limit.resets_at.strftime('%Y-%m-%d %H:%M')} UTC"
            if limit.resets_at
            else ""
        )
        report.detail(f"Límit de {limit.window}: {used} usat{resets} ({limit.status}).")


async def _provider_status(provider: Provider, timeout: float) -> ProviderStatus:
    return await asyncio.wait_for(provider.status(), timeout)


def _describe_models(
    agent: AgentName,
    provider: Provider,
    provider_model: str,
    settings: Settings,
    runtime: RuntimeSettings,
    report: _Report,
) -> None:
    from agentic_os.server.catalog import effective_models

    default, fast = effective_models(agent, provider, provider_model, runtime)
    chosen = " (triat al tauler)" if runtime.models.get(agent) else ""
    chosen_fast = " (triat al tauler)" if runtime.fast_models.get(agent) else ""
    report.detail(
        f"Model per defecte: {default or '?'}{chosen}; per als resums: {fast}{chosen_fast}."
    )


def _report_provider(
    agent: AgentName,
    provider: Provider,
    result: ProviderStatus | BaseException,
    report: _Report,
    settings: Settings,
    runtime: RuntimeSettings,
) -> None:
    label = AGENT_LABELS[agent]
    if isinstance(result, BaseException):
        if isinstance(result, TimeoutError):
            report.fail(f"{label} (mode {provider.mode}): el proveïdor no respon.")
        else:
            report.fail(f"{label} (mode {provider.mode}): error en consultar l'estat: {result}")
        _describe_models(agent, provider, "", settings, runtime, report)
        return
    line = f"{label} (mode {result.mode}, model {result.model or '?'}): {result.detail}"
    if result.available:
        report.ok(line)
    else:
        report.fail(line)
    _describe_models(agent, provider, result.model, settings, runtime, report)
    _describe_limits(result, report)


async def run_doctor(
    settings: Settings,
    *,
    providers: Mapping[AgentName, Provider] | None = None,
    out: Output = print,
    status_timeout: float = STATUS_TIMEOUT_SECONDS,
) -> int:
    """Check the installation; returns 1 if something critical fails, else 0.
    ``providers`` replaces the ones built from ``settings`` (and is not closed)."""
    report = _Report(out)
    out(f"ClaudeGPT OS {__version__}: diagnosi")
    _check_config(settings, report)
    _check_data_dir(settings, report)
    stored = await _check_database(settings, report)
    _report_fx(stored, report)
    _check_web(settings, report)
    await _check_cli(
        "Claude Code",
        settings.claude_mode,
        settings.claude_cli_path,
        "AOS_CLAUDE_CLI_PATH",
        report,
    )
    await _check_cli(
        "Codex", settings.chatgpt_mode, settings.codex_cli_path, "AOS_CODEX_CLI_PATH", report
    )

    if providers is None:
        from agentic_os.providers.factory import build_providers

        owned = build_providers(settings)
    else:
        owned = {}
    active = providers if providers is not None else owned
    try:
        results = await asyncio.gather(
            *(_provider_status(provider, status_timeout) for provider in active.values()),
            return_exceptions=True,
        )
        for (agent, provider), result in zip(active.items(), results, strict=True):
            _report_provider(agent, provider, result, report, settings, stored.runtime)
    finally:
        await asyncio.gather(*(p.aclose() for p in owned.values()), return_exceptions=True)

    out("")
    if report.critical:
        problems = "problema crític" if report.critical == 1 else "problemes crítics"
        out(f"Hi ha {report.critical} {problems}.")
        return 1
    if report.warnings:
        warnings = "avís" if report.warnings == 1 else "avisos"
        out(f"Tot correcte ({report.warnings} {warnings}).")
    else:
        out("Tot correcte.")
    return 0
