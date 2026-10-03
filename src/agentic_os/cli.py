"""Command-line entry point (``agentic-os``). Its output, help included, is in the
language of the system locale (English, Spanish or Catalan; English for any other).

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
import logging
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import ValidationError
from pydantic_core import ErrorDetails
from pydantic_settings import SettingsError

from agentic_os import __version__, i18n
from agentic_os.config import Settings, get_settings
from agentic_os.domain import AgentName, ProviderMode
from agentic_os.fx import FxRate, manual_rate
from agentic_os.i18n import number, t
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
_SHOWN_VALUE_CHARS: Final = 60
_FIELD_RE: Final = re.compile(r'field "(\w+)"')
_QUOTED_RE: Final = re.compile(r"'([^']*)'")

Output = Callable[[str], None]


class _HelpFormatter(argparse.HelpFormatter):
    def add_usage(
        self,
        usage: str | None,
        actions: Iterable[argparse.Action],
        groups: Iterable[argparse._MutuallyExclusiveGroup],
        prefix: str | None = None,
    ) -> None:
        super().add_usage(
            usage, actions, groups, f"{t('cli.help.usage')} " if prefix is None else prefix
        )


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
    parser._optionals.title = t("cli.help.options")
    parser.add_argument("-h", "--help", action="help", help=t("cli.help.help"))
    return parser


def build_parser() -> argparse.ArgumentParser:
    """The parser, its help in the language in force (build it after choosing one)."""
    parser = _parser(None, "agentic-os", t("cli.help.description"))
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help=t("cli.help.version"),
    )
    commands = parser.add_subparsers(
        dest="command", title=t("cli.help.commands"), metavar=t("cli.help.command")
    )

    serve = _parser(commands, "serve", t("cli.help.serve"))
    serve.add_argument("--host", help=t("cli.help.host"))
    serve.add_argument("--port", type=int, help=t("cli.help.port"))
    serve.add_argument("--dev", action="store_true", help=t("cli.help.dev"))
    _parser(commands, "init", t("cli.help.init"))
    _parser(commands, "reset-sessions", t("cli.help.reset_sessions"))
    _parser(commands, "reset-throttle", t("cli.help.reset_throttle"))
    _parser(commands, "doctor", t("cli.help.doctor"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # The command line speaks the system's language (the server, each client's).
    token = i18n.set_lang(i18n.from_environ(os.environ))
    try:
        return _main(argv)
    finally:
        i18n.reset_lang(token)


def _main(argv: Sequence[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        settings = get_settings()
    except (ValidationError, SettingsError) as exc:
        print(describe_invalid_settings(exc), file=sys.stderr)
        return 2
    except UnicodeError:
        print(t("cli.settings.env_not_utf8"), file=sys.stderr)
        return 2
    except OSError as exc:
        print(t("cli.settings.env_unreadable", error=exc.strerror or exc), file=sys.stderr)
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


def _variable(field: object) -> str:
    """The environment variable of a :class:`Settings` field."""
    name = str(field)
    return name if name.isupper() else f"AOS_{name.upper()}"


def _number(value: object) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def _alternatives(options: Sequence[str]) -> str:
    quoted = [t("cli.quoted", text=option) for option in options]
    if len(quoted) == 1:
        return quoted[0]
    return t("cli.either", first=", ".join(quoted[:-1]), last=quoted[-1])


_BOUNDS: Final = (
    ("ge", "cli.settings.bound.ge"),
    ("gt", "cli.settings.bound.gt"),
    ("le", "cli.settings.bound.le"),
    ("lt", "cli.settings.bound.lt"),
)
"""The bounds of a field and the key of their text."""
_BOUND_ERRORS: Final = frozenset(
    {"greater_than_equal", "greater_than", "less_than_equal", "less_than"}
)


def _range(field: object) -> str:
    """The bounds of a :class:`Settings` field in the language in force (``at least 1
    and at most 8760``), or ``""``."""
    info = Settings.model_fields.get(str(field))
    parts = [
        t(text, value=_number(getattr(item, key)))
        for item in (info.metadata if info is not None else ())
        for key, text in _BOUNDS
        if getattr(item, key, None) is not None
    ]
    if len(parts) < 2:
        return "".join(parts)
    return t("cli.both", first=", ".join(parts[:-1]), last=parts[-1])


def _reason(error: ErrorDetails) -> str:
    """Why a value is invalid, in the language in force (pydantic's own messages are
    English)."""
    kind, ctx = error["type"], error.get("ctx") or {}
    if kind in _BOUND_ERRORS and (bounds := _range(error["loc"][0])):
        return t("cli.settings.must_be", what=bounds)
    if kind in ("int_parsing", "int_from_float", "int_type"):
        return t("cli.settings.integer")
    if kind in ("float_parsing", "float_type", "finite_number"):
        return t("cli.settings.number")
    if kind in ("bool_parsing", "bool_type"):
        return t("cli.settings.boolean")
    if kind == "literal_error" and (options := _QUOTED_RE.findall(str(ctx.get("expected")))):
        return t("cli.settings.must_be", what=_alternatives(options))
    if kind == "value_error" and ctx.get("error") is not None:
        return str(ctx["error"])  # our own validators' messages, already in this language
    return t("cli.settings.invalid")


def _shown(error: ErrorDetails, variable: str) -> str:
    """`` (value: "…")`` for a value read from the environment (never for keys)."""
    value = error.get("input")
    if not isinstance(value, str) or "KEY" in variable:
        return ""
    if len(value) > _SHOWN_VALUE_CHARS:
        value = value[: _SHOWN_VALUE_CHARS - 1] + "…"
    return f" ({t('cli.settings.value', value=value)})"


def describe_invalid_settings(exc: ValidationError | SettingsError) -> str:
    """The invalid ``AOS_*`` variables, one per line, in the language in force."""
    lines = [t("cli.settings.invalid_title")]
    if isinstance(exc, SettingsError):
        # pydantic-settings reads lists (AOS_EXTRA_ORIGINS) as JSON.
        match = _FIELD_RE.search(str(exc))
        variable = _variable(match.group(1)) if match else "AOS_EXTRA_ORIGINS"
        example = f"{variable}='[\"http://localhost:5173\"]'"
        lines.append(f"- {variable}: {t('cli.settings.json', example=example)}")
        return "\n".join(lines)
    for error in exc.errors():
        variable = _variable(error["loc"][0]) if error["loc"] else "?"
        lines.append(f"- {variable}: {_reason(error)}{_shown(error, variable)}.")
    return "\n".join(lines)


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
        print(t("cli.db_unavailable", path=settings.db_path, error=exc), file=sys.stderr)
        return 1


# -- serve -----------------------------------------------------------------------------


class AccessLogWithoutQuery(logging.Filter):
    """Keeps only the path of the requests in uvicorn's access log: the query string can
    name an uploaded file (``PUT /api/attachments?name=...``) or carry a search, and
    neither belongs in the logs (docs/adr/0009-attachments.md)."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn logs '%s - "%s %s HTTP/%s" %d' with (client, method, path?query,
        # version, status); the path is percent-encoded, so its first "?" is the query's.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (args[0], args[1], args[2].partition("?")[0], args[3], args[4])
        return True


def log_config(level: str) -> dict[str, Any]:
    """uvicorn's logging configuration plus the application's own loggers, with an access
    log that keeps no query string (:class:`AccessLogWithoutQuery`)."""
    from uvicorn.config import LOGGING_CONFIG

    config: dict[str, Any] = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["no_query"] = {"()": AccessLogWithoutQuery}
    config["loggers"]["uvicorn.access"]["filters"] = ["no_query"]
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
    out(t("cli.serve.dev"))
    origin = urlsplit(settings.public_origin)
    if settings.secure_cookies and origin.scheme == "http":
        out(t("cli.serve.http_secure_cookies"))
    if "http://localhost:5173" not in settings.allowed_origins:
        out(t("cli.serve.vite_origin", example="AOS_EXTRA_ORIGINS='[\"http://localhost:5173\"]'"))


def serve(settings: Settings, *, host: str | None, port: int | None, dev: bool) -> int:
    """Run uvicorn with the app factory until interrupted. Its own output speaks the
    language of the command line; the server, the language of each client (and of none:
    :data:`~agentic_os.i18n.DEFAULT_LANG`, for what no client caused)."""
    import uvicorn

    level = "debug" if dev else settings.log_level  # checked by Settings
    if dev:
        _dev_hints(settings, print)
    i18n.set_lang(None)
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
        self.out(f"{t('cli.doctor.warning_tag')} {message}")

    def fail(self, message: str) -> None:
        self.critical += 1
        self.out(f"[ERROR] {message}")

    def detail(self, message: str) -> None:
        self.out(f"       {message}")


def _check_config(settings: Settings, report: _Report) -> None:
    origin = urlsplit(settings.public_origin)
    if origin.scheme not in ("http", "https") or not origin.hostname:
        report.fail(t("cli.doctor.bad_origin", origin=settings.public_origin))
        return
    report.ok(t("cli.doctor.origin", origin=settings.public_origin))
    if settings.secure_cookies and origin.scheme == "http" and origin.hostname not in LOCAL_HOSTS:
        report.warn(t("cli.doctor.http_secure_cookies"))
    if not settings.secure_cookies:
        report.warn(t("cli.doctor.insecure_cookies"))


def _check_permissions(path: str, mode: int, expected: int, report: _Report) -> None:
    if mode & 0o077:
        report.warn(
            t(
                "cli.doctor.permissions",
                path=path,
                mode=f"{mode:04o}",
                expected=f"{expected:04o}",
                command=f"chmod {expected:o} {path}",
            )
        )


def _check_data_dir(settings: Settings, report: _Report) -> None:
    data_dir = settings.data_dir
    if not data_dir.exists():
        report.warn(t("cli.doctor.no_data_dir", path=data_dir))
        return
    if not data_dir.is_dir():
        report.fail(t("cli.doctor.not_a_directory", path=data_dir))
        return
    if not os.access(data_dir, os.W_OK | os.X_OK):
        report.fail(t("cli.doctor.data_dir_read_only", path=data_dir))
        return
    report.ok(t("cli.doctor.data_dir", path=data_dir.resolve()))
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
        report.fail(t("cli.doctor.no_database"))
        return defaults
    try:
        async with await SqliteStore.open(settings.db_path) as store:
            owner = await store.get_owner()
            stored = _Stored(await store.get_runtime_settings(), await store.current_fx(utc_now()))
    except SchemaVersionError as exc:
        report.fail(str(exc))
        return defaults
    except Exception as exc:
        report.fail(t("cli.doctor.database_failed", error=exc))
        return defaults
    if owner is None:
        report.fail(t("cli.doctor.no_owner"))
    else:
        report.ok(t("cli.doctor.owner"))
    return stored


def _decimal(value: float) -> str:
    """``value`` with 2 to 4 decimals, as the web writes numbers in the language in force
    (``0,86`` and ``0,8547`` in Catalan)."""
    decimals = f"{value:.4f}".rstrip("0").partition(".")[2]
    return number(value, max(2, len(decimals)))


def _report_fx(stored: _Stored, report: _Report) -> None:
    rate = t("cli.doctor.fx_rate", rate=_decimal(stored.fx.eur_per_usd))
    if stored.fx.source == "ecb" and stored.fx.as_of is not None:
        day = stored.fx.as_of.strftime("%d/%m/%Y")
        report.ok(t("cli.doctor.fx_ecb", date=day, rate=rate))
    elif stored.runtime.fx.mode == "manual":
        report.ok(t("cli.doctor.fx_manual", rate=rate))
    else:
        report.skip(t("cli.doctor.fx_fallback", rate=rate))


def _check_web(settings: Settings, report: _Report) -> None:
    from agentic_os.server.static import find_web_dist

    dist = find_web_dist(settings)
    if dist is None:
        report.warn(t("cli.doctor.no_web"))
    else:
        report.ok(t("cli.doctor.web", path=dist))


async def cli_version(path: str, env: Mapping[str, str]) -> str | None:
    """First line of ``<path> --version`` run with ``env`` (the CLI's own closed
    environment, never the app's), or ``None`` if it fails or hangs."""
    try:
        process = await asyncio.create_subprocess_exec(
            path,
            "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=dict(env),
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
    label: str,
    mode: ProviderMode,
    path: str,
    variable: str,
    report: _Report,
    env: Callable[[], Mapping[str, str]],
) -> None:
    if mode != "cli":
        report.skip(t("cli.doctor.cli_not_needed", label=label, mode=mode))
        return
    resolved = shutil.which(path)
    if resolved is None:
        report.fail(t("cli.doctor.cli_missing", label=label, path=path, variable=variable))
        return
    version = await cli_version(resolved, env())
    if version is None:
        report.fail(t("cli.doctor.cli_silent", label=label, path=resolved))
    else:
        report.ok(t("cli.doctor.cli", label=label, path=resolved, version=version))


def _describe_limits(status: ProviderStatus, report: _Report) -> None:
    for limit in status.limits:
        used = "?" if limit.used_percent is None else f"{limit.used_percent:.0f}%"
        if limit.resets_at:
            renews = limit.resets_at.strftime("%Y-%m-%d %H:%M")
            report.detail(
                t(
                    "cli.doctor.limit_renews",
                    window=limit.window,
                    used=used,
                    date=renews,
                    status=limit.status,
                )
            )
        else:
            report.detail(
                t("cli.doctor.limit", window=limit.window, used=used, status=limit.status)
            )


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
    chosen = f" {t('cli.doctor.chosen')}" if runtime.models.get(agent) else ""
    chosen_fast = f" {t('cli.doctor.chosen')}" if runtime.fast_models.get(agent) else ""
    report.detail(
        t(
            "cli.doctor.models",
            default=default or "?",
            chosen=chosen,
            fast=fast,
            chosen_fast=chosen_fast,
        )
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
            report.fail(t("cli.doctor.provider_timeout", label=label, mode=provider.mode))
        else:
            report.fail(
                t("cli.doctor.provider_failed", label=label, mode=provider.mode, error=result)
            )
        _describe_models(agent, provider, "", settings, runtime, report)
        return
    line = t(
        "cli.doctor.provider",
        label=label,
        mode=result.mode,
        model=result.model or "?",
        detail=str(result.detail),
    )
    if result.available:
        report.ok(line)
    else:
        report.fail(line)
    _describe_models(agent, provider, result.model, settings, runtime, report)
    _describe_limits(result, report)


def _own_providers(
    settings: Settings, report: _Report
) -> tuple[dict[AgentName, Provider], Path | None]:
    """Doctor's own providers, and the temporary Codex state directory to remove after.

    With the app running, its Codex app-server uses the configured state directory: a
    start deletes the log databases there, which that app-server has open, and two
    app-servers must never share one. Doctor's Codex gets a private directory instead.
    """
    from agentic_os.providers.factory import build_provider, build_providers

    if settings.chatgpt_mode != "cli":
        return build_providers(settings), None
    try:
        state_dir = Path(tempfile.mkdtemp(prefix="aos-doctor-codex-"))
    except OSError as exc:
        report.fail(t("cli.doctor.codex_state_dir", label=AGENT_LABELS["chatgpt"], error=exc))
        return {"claude": build_provider("claude", settings.claude_mode, settings)}, None
    try:
        return build_providers(settings, codex_state_dir=state_dir), state_dir
    except BaseException:
        shutil.rmtree(state_dir, ignore_errors=True)
        raise


async def run_doctor(
    settings: Settings,
    *,
    providers: Mapping[AgentName, Provider] | None = None,
    out: Output = print,
    status_timeout: float = STATUS_TIMEOUT_SECONDS,
) -> int:
    """Check the installation; returns 1 if something critical fails, else 0.
    ``providers`` replaces the ones built from ``settings`` (and is not closed). The
    ones doctor builds never touch the running server's Codex state directory."""
    report = _Report(out)
    out(t("cli.doctor.title", version=__version__))
    _check_config(settings, report)
    _check_data_dir(settings, report)
    stored = await _check_database(settings, report)
    _report_fx(stored, report)
    _check_web(settings, report)
    from agentic_os.providers.claude_cli import cli_environment
    from agentic_os.providers.codex_appserver import codex_environment

    await _check_cli(
        "Claude Code",
        settings.claude_mode,
        settings.claude_cli_path,
        "AOS_CLAUDE_CLI_PATH",
        report,
        cli_environment,
    )
    await _check_cli(
        "Codex",
        settings.chatgpt_mode,
        settings.codex_cli_path,
        "AOS_CODEX_CLI_PATH",
        report,
        codex_environment,
    )

    state_dir: Path | None = None
    if providers is None:
        owned, state_dir = _own_providers(settings, report)
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
        try:
            await asyncio.gather(*(p.aclose() for p in owned.values()), return_exceptions=True)
        finally:
            # Once its app-server has stopped nothing writes there; also removed when a
            # second interrupt cuts the close short.
            if state_dir is not None:
                shutil.rmtree(state_dir, ignore_errors=True)

    out("")
    if report.critical:
        form = "one" if report.critical == 1 else "other"
        out(t(f"cli.doctor.critical_{form}", count=number(report.critical)))
        return 1
    if report.warnings:
        form = "one" if report.warnings == 1 else "other"
        out(t(f"cli.doctor.all_good_warnings_{form}", count=number(report.warnings)))
    else:
        out(t("cli.doctor.all_good"))
    return 0
