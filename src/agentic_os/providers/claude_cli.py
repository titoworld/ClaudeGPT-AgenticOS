"""Claude through the official Claude Code CLI, with the owner's subscription (OAuth).

Every call runs one ``claude -p`` process in stream-json mode with no tools, our own
system prompt, no settings files, no MCP servers and no slash commands, in an empty
private directory and with an allow-listed environment. The rendered transcript is
written as a single stdin line (``client_composed`` so the CLI never expands
``@file`` mentions or dispatches ``/commands``) and stdin is closed, so the process
exits after the turn. With attachments the message ``content`` is a list of blocks
(:func:`attachment_blocks`): one per attachment, in order (a base64 ``image``, a base64
PDF ``document`` with its name as ``title``, or a ``[Fitxer: ...]`` text block), then the
transcript; the CLI never reads a file itself.

``prewarm`` starts processes ahead of time (they wait on stdin without calling the
API) to hide the CLI start-up time. asyncio sets a process's return code a moment after
it dies, so the pool can hand out a warm process that has just died. One that dies
before its turn begins never sent a request, and the call goes, once and transparently,
to a fresh process. The turn begins with ``system``/``init``, which the CLI prints after
reading the message and before its API request (then ``system``/``status``
"requesting"); before it, only the start-up events of :data:`STARTUP_EVENTS`, which the
CLI may print while it still waits on stdin. Any other line ends the chance of a
replacement.

Output budget: the CLI has no flag for it, only ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``, which
sets ``max_tokens`` (thinking included) of each API request it makes; the provider
computes it from the request (it is never inherited) and a warm process only serves
calls with the same budget and reasoning.

Replies the CLI would go on with by itself. The CLI 2.1.283 does not accept two kinds of
reply and, about 10 ms after their ``message_stop``, sends another API request with a
message of its own (checked against a local mock of the Messages API): after
``stop_reason: "max_tokens"`` a continuation ("Output token limit hit. Resume
directly...", up to 3 times; the ``result`` then holds only the continuation's text), and
after ``stop_reason: "refusal"`` one retry ("Your response above was stopped by a safety
classifier..."). Each would bill the whole context again. So the provider SIGKILLs the
process group as soon as it reads such a ``message_delta``, before reading on or yielding
anything (SIGTERM is not enough: the CLI shuts down gracefully and still sends the
request), and ends the call with that request's usage: a truncated result for
``max_tokens``, a :class:`RefusalError` for a refusal (text streamed before it never
becomes an answer). A ``result`` with ``is_error`` and ``stop_reason: "refusal"`` is a
refusal as well.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import signal
import time
from collections.abc import AsyncIterator, Awaitable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage
from agentic_os.providers.base import (
    Attachment,
    DeclinedAttempt,
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    RefusalError,
    TextDelta,
    UsageLimit,
)
from agentic_os.providers.prompt_format import attachment_text, read_files, render_transcript

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "opus"
DEFAULT_FAST_MODEL = "haiku"

CLAUDE_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("opus", "Claude Opus", "Raonament profund i tasques llargues."),
    ("sonnet", "Claude Sonnet", "Equilibri entre qualitat i velocitat."),
    ("haiku", "Claude Haiku", "El més ràpid i econòmic; bo per als resums."),
    ("fable", "Claude Fable", "El més capaç; pot no estar inclòs en tots els plans."),
)
"""(CLI alias, label, Catalan description) per model family. The CLI aliases always
point to the newest model of their family (verified on Claude Code 2.1.283)."""

Effort = Literal["low", "medium", "high"]

EFFORT_BY_PURPOSE: dict[Purpose, Effort] = {
    "answer": "high",
    "revision": "medium",
    "synthesis": "high",
    "summary": "low",
    "check": "low",
}
"""Thinking effort per call purpose (shared with the api provider)."""

WARM_TTL_SECONDS = 120.0
MAX_WARM_PROCESSES = 2
STATUS_CACHE_SECONDS = 60.0
STATUS_TIMEOUT_SECONDS = 10.0
KILL_GRACE_SECONDS = 2.0
"""Time between SIGTERM and SIGKILL."""
EXIT_GRACE_SECONDS = 5.0
"""Time a finished process gets to exit on its own before it is terminated."""
STREAM_LIMIT = 8 * 1024 * 1024
"""Maximum length of one stdout line (a stream-json event)."""
STDERR_KEEP = 8 * 1024
MAX_SYSTEM_PROMPT_BYTES = 120_000
"""The system prompt travels as one argv string (Linux caps those at 128 KiB)."""
MAX_OUTPUT_ENV = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"
"""Set by the provider from the request's budget (never inherited from the app)."""
SELF_CONTINUING_STOPS = frozenset({"max_tokens", "refusal"})
"""``stop_reason`` values after which the CLI sends another request on its own."""
STARTUP_EVENTS = frozenset({"active_goal", "autocompact_state"})
"""Event types the CLI 2.1.283, logged in to the real API, printed within a second of
starting, while it still waited on stdin (with no session it printed none): a warm
process may have them in its pipe already. They come before the turn's
``system``/``init`` and carry nothing about the turn (they are ignored)."""
LOGIN_HINT = (
    "posa el token de «claude setup-token» a CLAUDE_CODE_OAUTH_TOKEN (.env) i fes "
    "«docker compose up -d», o executa «claude auth login» al servidor"
)
"""How to log the CLI in (docs/DESPLEGAMENT.md, step 6): ``setup-token`` only prints the
token, which the app gets from .env."""

ENV_ALLOW_LIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "XDG_CONFIG_HOME",
    # Network plumbing for hosts behind an egress proxy (not credentials).
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "NODE_EXTRA_CA_CERTS",
    "SSL_CERT_FILE",
)
"""Variables the CLI inherits. Everything else is dropped on purpose: ANTHROPIC_API_KEY
and ANTHROPIC_AUTH_TOKEN would silently replace the subscription with API billing,
ANTHROPIC_BASE_URL would send the OAuth token elsewhere, and AOS_* are app secrets."""

_RATE_WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))
_RATE_STATUS = {"allowed": "allowed", "allowed_warning": "warning", "rejected": "rejected"}
_BEARER_TOKEN = r"[A-Za-z0-9._~+/=\-]+"
_SECRET_RE = re.compile(rf"(sk-ant-)[A-Za-z0-9_\-]+|(Bearer\s+){_BEARER_TOKEN}")
_LEADING_TOKEN_RE = re.compile(rf"\A(\s*){_BEARER_TOKEN}")
"""A bearer token at the start of a text, after any blank space (line breaks too)."""


class _Key(NamedTuple):
    """What a process is started with: a warm process can only serve the same key."""

    model: str
    effort_args: tuple[str, ...]
    """--effort and --thinking arguments (the reasoning policy)."""
    system: str
    max_output_tokens: int
    """The billed output budget (its environment's CLAUDE_CODE_MAX_OUTPUT_TOKENS)."""


def is_haiku(model: str) -> bool:
    return "haiku" in model.lower()


def family_description(model: str) -> str:
    """Catalan description of a Claude model id or alias by its family ("" if unknown)."""
    lowered = model.lower()
    return next((text for family, _, text in CLAUDE_FAMILIES if family in lowered), "")


def redact(text: str) -> str:
    """Hide anything that looks like an Anthropic key or a bearer token."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1) or m.group(2)}***", text)


def refusal_error(
    usage: Usage,
    model: str,
    category: str | None,
    declined: Sequence[DeclinedAttempt] = (),
) -> RefusalError:
    """The error of a Claude reply that stopped with ``stop_reason: "refusal"`` (shared
    with the api provider). ``usage`` is what the refusing attempt billed and
    ``declined`` the billed attempts other models declined before it (api fallbacks)."""
    reason = f" (categoria: {category})" if category else ""
    return RefusalError(
        f"Claude ha declinat respondre aquesta petició{reason}.",
        usage=usage,
        model=model,
        category=category,
        declined=declined,
    )


def attachment_blocks(
    attachments: Sequence[Attachment], files: Sequence[bytes | None]
) -> list[dict[str, Any]]:
    """Anthropic content blocks of the attachments, in order (shared with the api
    provider): an ``image`` or a PDF ``document`` (with its name as ``title``) from the
    file's bytes (``files``, from :func:`~agentic_os.providers.prompt_format.read_files`),
    else a text block (a text file, or a PDF in mode "text")."""
    blocks: list[dict[str, Any]] = []
    for attachment, data in zip(attachments, files, strict=True):
        if data is None:
            blocks.append({"type": "text", "text": attachment_text(attachment)})
            continue
        encoded = base64.b64encode(data).decode("ascii")
        if attachment.kind == "image":
            source = {"type": "base64", "media_type": attachment.mime, "data": encoded}
            blocks.append({"type": "image", "source": source})
        else:
            source = {"type": "base64", "media_type": "application/pdf", "data": encoded}
            blocks.append({"type": "document", "source": source, "title": attachment.name})
    return blocks


def _obj(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _timestamp(value: object) -> datetime | None:
    seconds = _number(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _is_startup_line(raw: bytes) -> bool:
    """True for an event of :data:`STARTUP_EVENTS`; any other line may be the turn's."""
    try:
        event = json.loads(raw)
    except ValueError:
        return False
    return isinstance(event, dict) and event.get("type") in STARTUP_EVENTS


def _signal_group(pid: int, sig: signal.Signals) -> None:
    # The process leads its own session (start_new_session), so its pgid is its pid.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def parse_limits(info: Mapping[str, Any]) -> list[UsageLimit]:
    """Usage windows of a ``rate_limit_event``.

    ``utilization`` is a fraction of the window (0-1, above 1 when usage runs past
    it), so it is converted to a percentage. The top-level ``status`` belongs to the
    window named by ``rateLimitType``; other windows are rejected only when full.
    """
    status = _RATE_STATUS.get(str(info.get("status")), "allowed")
    limit_type = info.get("rateLimitType")
    windows = _obj(info.get("unifiedWindows"))
    if not windows and limit_type in dict(_RATE_WINDOWS):
        windows = {
            str(limit_type): {
                "utilization": info.get("utilization"),
                "resetsAt": info.get("resetsAt"),
            }
        }
    limits: list[UsageLimit] = []
    for key, label in _RATE_WINDOWS:
        window = windows.get(key)
        if not isinstance(window, dict):
            continue
        utilization = _number(window.get("utilization"))
        if key == limit_type:
            window_status = status
        elif utilization is not None and utilization >= 1:
            window_status = "rejected"
        else:
            window_status = "allowed"
        limits.append(
            UsageLimit(
                window=label,
                used_percent=round(utilization * 100, 1) if utilization is not None else None,
                resets_at=_timestamp(window.get("resetsAt")),
                status=window_status,
            )
        )
    return limits


class _CliProcess:
    """One CLI process: waiting warm, or serving a call."""

    def __init__(self, proc: asyncio.subprocess.Process, key: _Key) -> None:
        self.proc = proc
        self.key = key
        self.expiry: asyncio.TimerHandle | None = None
        self._stderr = bytearray()
        self._stderr_cut = False
        """The start of stderr was dropped (:data:`STDERR_KEEP`): the first line kept
        is only the end of a line."""
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._terminate_lock = asyncio.Lock()
        self._terminated = False

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def _drain_stderr(self) -> None:
        # Always read stderr so a chatty CLI can never block on a full pipe.
        stream = self.proc.stderr
        if stream is None:
            return
        while chunk := await stream.read(4096):
            self._stderr += chunk
            if len(self._stderr) > STDERR_KEEP:
                del self._stderr[:-STDERR_KEEP]
                self._stderr_cut = True

    def stderr_tail(self, limit: int = 400) -> str:
        """The end of stderr, at most ``limit`` characters, with secrets hidden.

        They are hidden before anything is cut: a cut could leave the body of a token
        without the prefix that gives it away. :meth:`_drain_stderr` cuts earlier, so
        after its cut the partial first line goes as well, and so does the token after
        that line when the line could be the end of a "Bearer" prefix (only the end of
        the word and blank space: the space after "Bearer" may span lines).
        """
        text = redact(self._stderr.decode("utf-8", "replace"))
        if self._stderr_cut:
            partial, _, text = text.partition("\n")
            if "Bearer".endswith(partial.rstrip()):
                text = _LEADING_TOKEN_RE.sub(r"\1***", text, count=1)
        return text.strip()[-limit:]

    async def send(self, line: bytes) -> None:
        """Write the only user message and close stdin (the CLI exits after the turn)."""
        stdin = self.proc.stdin
        if stdin is None:
            return
        try:
            stdin.write(line)
            await stdin.drain()
            stdin.close()
            await stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            # The process died: the read loop sees EOF and reports its stderr.
            pass

    async def readline(self) -> bytes:
        stdout = self.proc.stdout
        return await stdout.readline() if stdout is not None else b""

    def kill(self) -> None:
        """SIGKILL the whole process group now, with no graceful shutdown (it is reaped
        later by :meth:`terminate`)."""
        _signal_group(self.proc.pid, signal.SIGKILL)

    async def finish(self, grace: float) -> None:
        """Let a process whose turn is over exit on its own, then clean up."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.proc.wait(), grace)
        await self.terminate()

    async def terminate(self) -> None:
        """SIGTERM the whole process group, SIGKILL it after a grace period, reap it.

        Idempotent: warm expiry, a failed call and ``aclose`` may all ask for it.
        """
        if self.expiry is not None:
            self.expiry.cancel()
        async with self._terminate_lock:
            if self._terminated:
                return
            proc = self.proc
            if proc.returncode is None:
                _signal_group(proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), KILL_GRACE_SECONDS)
                except TimeoutError:
                    _signal_group(proc.pid, signal.SIGKILL)
                    await proc.wait()
                except BaseException:
                    # Cancelled while cleaning up: never leave the group running.
                    _signal_group(proc.pid, signal.SIGKILL)
                    raise
            # Anything the CLI started in its group must not outlive it.
            _signal_group(proc.pid, signal.SIGKILL)
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            if not self._stderr_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(self._stderr_task), KILL_GRACE_SECONDS)
                except TimeoutError:
                    self._stderr_task.cancel()
            self._terminated = True


@dataclass(frozen=True, slots=True)
class _Stop:
    """A ``message_delta`` whose ``stop_reason`` is in :data:`SELF_CONTINUING_STOPS`."""

    reason: str
    usage: dict[str, Any]
    """Usage of that API request."""
    details: dict[str, Any]
    """Its ``stop_details`` (a refusal's ``category``)."""
    at: float


@dataclass(slots=True)
class _Turn:
    """What the event stream of one call has shown so far."""

    model: str
    started: float
    model_reported: bool = False
    """system/init named the resolved model (message_start is only a fallback)."""
    chunks: list[str] = field(default_factory=list)
    ttft_ms: int | None = None
    result: dict[str, Any] | None = None
    result_at: float = 0.0
    assistant_error: str | None = None
    rate_limited: bool = False
    rate_resets_at: datetime | None = None
    stop: _Stop | None = None
    """A reply the CLI would go on with by itself: the call ends there."""
    refusal_category: str | None = None
    """Category of a refusal the CLI gave up on (``model_refusal_no_fallback``)."""


def cli_environment() -> dict[str, str]:
    """Environment of every ``claude`` process (also ``claude --version``): the
    allow-list only, never the app's secrets, plus the values the app computes."""
    env = {name: os.environ[name] for name in ENV_ALLOW_LIST if name in os.environ}
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


class ClaudeCliProvider:
    """Provider for agent ``claude`` in mode ``cli``: the official Claude Code CLI."""

    def __init__(self, settings: Settings, *, warm_ttl_seconds: float = WARM_TTL_SECONDS) -> None:
        self._settings = settings
        self._warm_ttl = warm_ttl_seconds
        self._warm: list[_CliProcess] = []
        """Idle processes, oldest first."""
        self._processes: set[_CliProcess] = set()
        """Every process not reaped yet: warm, serving a call or finishing."""
        self._tasks: set[asyncio.Task[None]] = set()
        self._limits: dict[str, UsageLimit] = {}
        self._resolved: dict[str, str] = {}
        """Model each requested name (e.g. the alias "opus") resolved to in real calls."""
        self._status_cache: tuple[float, bool, str] | None = None
        self._status_lock = asyncio.Lock()
        self._sandbox: Path | None = None
        self._closed = False

    @property
    def agent(self) -> AgentName:
        return "claude"

    @property
    def mode(self) -> ProviderMode:
        return "cli"

    @property
    def default_model(self) -> str:
        return self._settings.claude_model or DEFAULT_MODEL

    @property
    def fast_model(self) -> str:
        """Model of the cheap internal calls (summaries) when none is requested."""
        return self._settings.claude_fast_model or DEFAULT_FAST_MODEL

    # -- Provider API ----------------------------------------------------------

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        if self._closed:
            raise ProviderError("El proveïdor de Claude s'està aturant.", kind="unavailable")
        started = time.monotonic()
        deadline = asyncio.get_running_loop().time() + self._settings.provider_timeout_seconds
        key = self._key(request)
        line = self._user_line(request, await read_files(request.attachments))
        worker = self._take_warm(key)
        # A warm process that dies before its turn begins is replaced, once.
        replaceable = worker is not None
        if worker is None:
            worker = await self._spawn(key)
        turn = _Turn(model=key.model, started=started)
        finished = False
        try:
            await self._with_deadline(worker.send(line), deadline)
            while turn.result is None and turn.stop is None:
                raw = await self._with_deadline(worker.readline(), deadline)
                if not raw and replaceable and not self._closed:
                    # It died before its turn began: no request left (see the module
                    # docstring). A fresh process serves the call instead.
                    logger.info("A warm Claude CLI process had died: starting a fresh one")
                    dead, worker = worker, await self._spawn(key)
                    self._background(self._retire(dead))
                    replaceable = False
                    await self._with_deadline(worker.send(line), deadline)
                    continue
                if not raw:
                    raise await self._crash_error(worker)
                if replaceable and not _is_startup_line(raw):
                    replaceable = False  # its turn began: the request may have left
                text = self._handle_line(raw, turn)
                if turn.stop is not None:
                    # The CLI sends its own next request ~10 ms after this reply: kill it
                    # before that, not after yielding (see the module docstring).
                    worker.kill()
                elif text:
                    yield TextDelta(text)
            finished = True
        finally:
            if finished and turn.stop is not None:
                self._background(self._retire(worker))  # killed: only reap it
            elif finished:
                self._background(self._retire(worker, EXIT_GRACE_SECONDS))
            else:
                await self._retire(worker)

        if turn.model_reported and turn.model != key.model:
            self._resolved[key.model] = turn.model
        stop = turn.stop
        if stop is not None:
            usage = self._usage_from(stop.usage)
            if stop.reason == "refusal":
                category = _text(stop.details.get("category")) or None
                raise refusal_error(usage, turn.model, category)
            yield GenerationResult(
                text="".join(turn.chunks),
                usage=usage,
                model=turn.model,
                latency_ms=int((stop.at - started) * 1000),
                ttft_ms=turn.ttft_ms,
                truncated=True,
                finish_reason="max_tokens",
            )
            return
        result = turn.result
        if result is None:  # pragma: no cover - the loop only ends with a result
            raise ProviderError("La CLI de Claude no ha enviat cap resultat.", kind="internal")
        if result.get("is_error"):
            raise self._result_error(result, turn)
        text = "".join(turn.chunks)
        if not text and isinstance(result.get("result"), str) and result["result"]:
            # No partial deltas arrived (older CLI?): fall back to the final text.
            text = result["result"]
            turn.ttft_ms = int((turn.result_at - started) * 1000)
            yield TextDelta(text)
        truncated = result.get("stop_reason") == "max_tokens"
        yield GenerationResult(
            text=text,
            usage=self._usage_from(_obj(result.get("usage"))),
            model=turn.model,
            latency_ms=int((turn.result_at - started) * 1000),
            ttft_ms=turn.ttft_ms,
            truncated=truncated,
            finish_reason="max_tokens" if truncated else None,
        )

    async def prewarm(self, request: GenerationRequest) -> None:
        if self._closed:
            return
        for dead in [worker for worker in self._warm if not worker.alive]:
            self._warm.remove(dead)
            self._background(self._retire(dead))
        try:
            key = self._key(request)
            for worker in self._warm:
                if worker.key == key and worker.alive:
                    self._arm_expiry(worker)
                    return
            worker = await self._spawn(key)
        except ProviderError as exc:
            logger.debug("Claude CLI prewarm skipped: %s", exc.message)
            return
        except Exception:
            logger.warning("Claude CLI prewarm failed", exc_info=True)
            return
        if self._closed:
            self._background(self._retire(worker))
            return
        while len(self._warm) >= MAX_WARM_PROCESSES:
            self._background(self._retire(self._warm.pop(0)))
        self._warm.append(worker)
        self._arm_expiry(worker)

    async def status(self) -> ProviderStatus:
        async with self._status_lock:
            now = time.monotonic()
            cached = self._status_cache
            if cached is None or now - cached[0] > STATUS_CACHE_SECONDS:
                available, detail = await self._auth_status()
                cached = self._status_cache = (time.monotonic(), available, detail)
        return ProviderStatus(
            agent="claude",
            mode="cli",
            available=cached[1],
            model=self.default_model,
            detail=cached[2],
            limits=tuple(self._limits.values()),
        )

    @property
    def models_live(self) -> bool:
        """The alias list is authoritative: each alias follows its family's newest model."""
        return True

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        """The CLI aliases (Claude Code has no command to list models), with the model
        each one resolved to in the latest real call. A configured full id goes first."""
        default = self.default_model
        models: list[ModelInfo] = []
        if default not in (alias for alias, _, _ in CLAUDE_FAMILIES):
            models.append(
                ModelInfo(
                    id=default,
                    label=default,
                    description=family_description(default) or "Model configurat al servidor.",
                    is_default=True,
                )
            )
        for alias, label, text in CLAUDE_FAMILIES:
            resolved = self._resolved.get(alias)
            description = f"Sempre la versió més nova. {text}"
            if resolved:
                description += f" Ara: {resolved}"
            is_default = alias == default
            models.append(ModelInfo(alias, label, description, is_default=is_default))
        return models

    async def aclose(self) -> None:
        self._closed = True
        self._warm.clear()
        workers = list(self._processes)
        await asyncio.gather(*(w.terminate() for w in workers), return_exceptions=True)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- processes ---------------------------------------------------------------

    def _key(self, request: GenerationRequest) -> _Key:
        if request.model:
            model = request.model
        elif request.fast:
            model = self.fast_model
        else:
            model = self.default_model
        if is_haiku(model):
            # Haiku ignores --effort; turning thinking off is its cost lever.
            effort_args: tuple[str, ...] = ("--thinking", "disabled")
        elif request.reasoning == "off":
            effort_args = ("--effort", EFFORT_BY_PURPOSE[request.purpose], "--thinking", "disabled")
        else:
            effort_args = ("--effort", EFFORT_BY_PURPOSE[request.purpose])
        system = request.system.replace("\x00", "")
        if len(system.encode("utf-8")) > MAX_SYSTEM_PROMPT_BYTES:
            raise ProviderError(
                "El prompt de sistema és massa llarg per a la CLI de Claude.", kind="invalid"
            )
        return _Key(model, effort_args, system, max(1, request.max_output_tokens))

    def _command(self, key: _Key) -> list[str]:
        return [
            self._settings.claude_cli_path,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--tools",
            "",
            f"--system-prompt={key.system}",
            "--setting-sources=",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--max-turns",
            "1",
            f"--model={key.model}",
            *key.effort_args,
        ]

    @staticmethod
    def _user_line(request: GenerationRequest, files: Sequence[bytes | None] = ()) -> bytes:
        """The only stdin line: the transcript as the message ``content``, or with
        attachments a list of their blocks (``files`` are their bytes, see
        :func:`attachment_blocks`) followed by the transcript as a text block."""
        transcript = render_transcript(request)
        content: str | list[dict[str, Any]] = transcript
        if request.attachments:
            content = [
                *attachment_blocks(request.attachments, files),
                {"type": "text", "text": transcript},
            ]
        message = {
            "type": "user",
            "message": {"role": "user", "content": content},
            # Deliver the text verbatim: no @file expansion, no slash commands.
            "client_composed": True,
        }
        return json.dumps(message).encode("utf-8") + b"\n"

    @staticmethod
    def _env(key: _Key | None = None) -> dict[str, str]:
        """The allow-listed environment plus the values the provider computes itself."""
        env = cli_environment()
        if key is not None:
            env[MAX_OUTPUT_ENV] = str(key.max_output_tokens)
        return env

    def _sandbox_dir(self) -> Path:
        """Empty private working directory: the CLI never sees the app's files."""
        if self._sandbox is None:
            path = (self._settings.data_dir / "sandbox" / "claude").absolute()
            try:
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.chmod(0o700)
            except OSError as exc:
                raise ProviderError(
                    f"No s'ha pogut preparar el directori de treball de Claude ({exc.strerror}).",
                    kind="internal",
                ) from None
            self._sandbox = path
        return self._sandbox

    async def _spawn(self, key: _Key) -> _CliProcess:
        cwd = self._sandbox_dir()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command(key),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self._env(key),
                start_new_session=True,
                limit=STREAM_LIMIT,
            )
        except FileNotFoundError:
            raise ProviderError(
                "CLI de Claude no trobada: revisa AOS_CLAUDE_CLI_PATH.", kind="unavailable"
            ) from None
        except PermissionError:
            raise ProviderError(
                "No es pot executar la CLI de Claude (permisos).", kind="unavailable"
            ) from None
        worker = _CliProcess(proc, key)
        self._processes.add(worker)
        return worker

    async def _retire(self, worker: _CliProcess, grace: float = 0.0) -> None:
        """Stop a process (after ``grace`` seconds to exit on its own) and forget it."""
        try:
            if grace:
                await worker.finish(grace)
            else:
                await worker.terminate()
        finally:
            self._processes.discard(worker)

    async def _with_deadline[T](self, operation: Awaitable[T], deadline: float) -> T:
        try:
            async with asyncio.timeout_at(deadline):
                return await operation
        except TimeoutError:
            seconds = self._settings.provider_timeout_seconds
            raise ProviderError(
                f"Claude no ha respost a temps ({seconds:g} s).", kind="timeout"
            ) from None
        except ValueError:
            # StreamReader.readline: one event exceeded STREAM_LIMIT.
            raise ProviderError(
                "La CLI de Claude ha enviat un esdeveniment massa gran.", kind="internal"
            ) from None

    def _take_warm(self, key: _Key) -> _CliProcess | None:
        for worker in self._warm:
            if worker.key == key and worker.alive:
                self._warm.remove(worker)
                if worker.expiry is not None:
                    worker.expiry.cancel()
                return worker
        return None

    def _arm_expiry(self, worker: _CliProcess) -> None:
        if worker.expiry is not None:
            worker.expiry.cancel()
        worker.expiry = asyncio.get_running_loop().call_later(self._warm_ttl, self._expire, worker)

    def _expire(self, worker: _CliProcess) -> None:
        if worker in self._warm:
            self._warm.remove(worker)
            self._background(self._retire(worker))

    def _background(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- stream-json events --------------------------------------------------------

    def _handle_line(self, raw: bytes, turn: _Turn) -> str | None:
        """Update ``turn`` with one stdout line; return its text delta, if any."""
        try:
            event = json.loads(raw)
        except ValueError:
            logger.debug("Ignoring a non-JSON line from the Claude CLI")
            return None
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        if kind == "stream_event":
            inner = _obj(event.get("event"))
            if inner.get("type") == "message_start" and not turn.model_reported:
                model = _obj(inner.get("message")).get("model")
                if isinstance(model, str) and model:
                    turn.model = model
            elif inner.get("type") == "content_block_delta":
                delta = _obj(inner.get("delta"))
                text = delta.get("text")
                if delta.get("type") == "text_delta" and isinstance(text, str) and text:
                    if turn.ttft_ms is None:
                        turn.ttft_ms = int((time.monotonic() - turn.started) * 1000)
                    turn.chunks.append(text)
                    return text
            elif inner.get("type") == "message_delta":
                delta = _obj(inner.get("delta"))
                reason = _text(delta.get("stop_reason"))
                if reason in SELF_CONTINUING_STOPS:
                    turn.stop = _Stop(
                        reason=reason,
                        usage=_obj(inner.get("usage")),
                        details=_obj(delta.get("stop_details")),
                        at=time.monotonic(),
                    )
        elif kind == "system" and event.get("subtype") == "init":
            model = event.get("model")
            if isinstance(model, str) and model:
                turn.model = model
                turn.model_reported = True
        elif kind == "system" and event.get("subtype") == "model_refusal_no_fallback":
            turn.refusal_category = _text(event.get("api_refusal_category")) or None
        elif kind == "rate_limit_event":
            info = _obj(event.get("rate_limit_info"))
            for limit in parse_limits(info):
                self._limits[limit.window] = limit
            if info.get("status") == "rejected":
                turn.rate_limited = True
                turn.rate_resets_at = _timestamp(info.get("resetsAt"))
        elif kind == "assistant":
            error = event.get("error")
            if isinstance(error, str):
                turn.assistant_error = error
        elif kind == "result":
            turn.result = event
            turn.result_at = time.monotonic()
        return None

    @staticmethod
    def _usage_from(usage: Mapping[str, Any]) -> Usage:
        """Usage of a ``result`` (per turn) or of a ``message_delta`` (per request).
        modelUsage/total_cost_usd are cumulative list-price estimates, meaningless for a
        subscription, so no cost is reported."""
        details = _obj(usage.get("output_tokens_details"))
        return Usage(
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
            cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
            cache_write_tokens=_int(usage.get("cache_creation_input_tokens")),
            reasoning_tokens=_int(details.get("thinking_tokens")),
            cost_usd=None,
        )

    @classmethod
    def _result_error(cls, result: Mapping[str, Any], turn: _Turn) -> ProviderError:
        """Map a ``result`` with ``is_error`` (its ``subtype`` can still be "success")."""
        if result.get("stop_reason") == "refusal":
            # Its usage covers every request of the CLI's turn: all of them were billed.
            usage = cls._usage_from(_obj(result.get("usage")))
            return refusal_error(usage, turn.model, turn.refusal_category)
        status = _int(result.get("api_error_status"))
        detail = result.get("result")
        if not isinstance(detail, str) or not detail:
            errors = result.get("errors")
            detail = "; ".join(str(e) for e in errors) if isinstance(errors, list) else ""
        detail = redact(detail.strip())[:300]
        suffix = f" ({detail})" if detail else ""
        error = turn.assistant_error
        if status in (401, 403) or error == "authentication_failed" or "Not logged in" in detail:
            return ProviderError(
                f"La sessió de Claude no és vàlida: {LOGIN_HINT}.{suffix}", kind="auth"
            )
        if status == 429 or turn.rate_limited or error == "rate_limit":
            when = ""
            if turn.rate_resets_at is not None:
                when = f" Es restableix el {turn.rate_resets_at:%d/%m a les %H:%M} UTC."
            return ProviderError(
                f"S'ha arribat al límit d'ús de la subscripció de Claude.{when}",
                kind="rate_limit",
            )
        if (
            status >= 500
            or error in ("overloaded", "server_error")
            or "overloaded" in detail.lower()
        ):
            return ProviderError(
                f"Claude no està disponible ara mateix.{suffix}", kind="unavailable", retryable=True
            )
        if result.get("terminal_reason") == "prompt_too_long":
            return ProviderError("La conversa és massa llarga per a Claude.", kind="invalid")
        return ProviderError(f"La CLI de Claude ha retornat un error.{suffix}", kind="internal")

    @staticmethod
    async def _crash_error(worker: _CliProcess) -> ProviderError:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(worker.proc.wait(), KILL_GRACE_SECONDS)
        code = worker.proc.returncode
        tail = worker.stderr_tail()
        message = "La CLI de Claude s'ha aturat sense respondre"
        message += f" (codi {code})" if code is not None else ""
        message += f": {tail}" if tail else "."
        return ProviderError(message, kind="unavailable", retryable=True)

    # -- status ----------------------------------------------------------------------

    async def _auth_status(self) -> tuple[bool, str]:
        """Run ``claude auth status --json`` (it exits 1 when not logged in)."""
        try:
            cwd = self._sandbox_dir()
        except ProviderError as exc:
            return False, exc.message
        try:
            proc = await asyncio.create_subprocess_exec(
                self._settings.claude_cli_path,
                "auth",
                "status",
                "--json",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self._env(),
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError):
            return False, "CLI de Claude no trobada"
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), STATUS_TIMEOUT_SECONDS)
        except TimeoutError:
            return False, "La CLI de Claude no respon"
        finally:
            if proc.returncode is None:
                _signal_group(proc.pid, signal.SIGKILL)
                await proc.wait()
        try:
            data = json.loads(stdout)
        except ValueError:
            return False, "No s'ha pogut llegir l'estat de la sessió de la CLI de Claude"
        info = _obj(data)
        if not info.get("loggedIn"):
            return False, f"Sense sessió: {LOGIN_HINT}"
        method = info.get("authMethod")
        if method == "claude.ai":
            plan = info.get("subscriptionType")
            return True, f"Subscripció activa ({plan})" if isinstance(plan, str) and plan else (
                "Subscripció activa"
            )
        if method == "oauth_token":
            return True, "Subscripció activa (token OAuth)"
        if method in ("api_key", "api_key_helper"):
            return True, "Sessió amb clau d'API: es factura per ús, no amb la subscripció"
        return True, "Sessió activa"
