"""ChatGPT through the official Codex CLI and the owner's ChatGPT subscription (``cli`` mode).

``codex exec --json`` does not stream text, so this provider talks to ``codex app-server``:
one long-lived process (stdio, newline-delimited JSON-RPC) shared by every call. The
protocol is marked experimental upstream; this client is pinned to Codex CLI 0.157.1.

Every call is stateless (the orchestrator owns the context): a new ephemeral, read-only
thread whose base instructions are replaced by ``request.system``, one turn with the
rendered transcript, and the thread is released afterwards. Codex's default agent
harness (a 19 KB prompt, tools, skills, plugins...) is trimmed with config overrides
verified against 0.157.1, and any request the server sends back (approvals, user
input...) is declined so a call can never hang waiting for a human.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import signal
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic_os import __version__
from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
    UsageLimit,
)
from agentic_os.providers.prompt_format import render_transcript

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]

PROTOCOL_VERSION = "0.157."
"""Codex CLI release line whose app-server schema this client implements."""
CLIENT_NAME = "agentic-os"
DEFAULT_FAST_MODEL = "gpt-6-luna"
FALLBACK_MODEL_LABEL = "gpt-6-astra"
"""Shown by ``status()`` until the server reports its default model (bundled default)."""

ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "CODEX_HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
"""The only variables the Codex process inherits: never API keys or ``AOS_*`` secrets."""

EFFORT_BY_PURPOSE: dict[Purpose, str] = {
    "answer": "medium",
    "revision": "low",
    "synthesis": "medium",
    "summary": "low",
}

_DISABLED_FEATURES: tuple[str, ...] = (
    "apps",
    "plugins",
    "multi_agent",
    "goals",
    "sleep_tool",
    "shell_tool",
    "unified_exec",
    "view_image",
    "image_generation",
    "browser_use",
    "computer_use",
    "tool_suggest",
    "skill_search",
    "hooks",
    "unbounded_connection_retries",
)

CONFIG_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("approval_policy", '"never"'),
    ("sandbox_mode", '"read-only"'),
    ("web_search", '"disabled"'),
    ("include_environment_context", "false"),
    ("include_permissions_instructions", "false"),
    ("include_collaboration_mode_instructions", "false"),
    ("include_apps_instructions", "false"),
    ("check_for_update_on_startup", "false"),
    ("history.persistence", '"none"'),
    ("analytics.enabled", "false"),
    ("skills.bundled.enabled", "false"),
    *((f"features.{name}", "false") for name in _DISABLED_FEATURES),
)
"""``-c key=value`` overrides (TOML values), all accepted by ``--strict-config`` in 0.157.1.
Without them every request carries ~50 KB of agent prompt and tool definitions."""

LINE_LIMIT = 32 * 1024 * 1024
"""Longest JSON-RPC line accepted from the server (a turn/completed repeats the answer)."""
INITIALIZE_TIMEOUT = 30.0
CONTROL_TIMEOUT = 30.0
"""thread/start and turn/start answers (local work, but they may touch the network)."""
CLEANUP_TIMEOUT = 10.0
"""turn/interrupt and thread/unsubscribe: no answer means the process is wedged."""
STATUS_REQUEST_TIMEOUT = 10.0
STATUS_TTL = 60.0
STATUS_TTL_UNAVAILABLE = 10.0
"""Short, so a ``codex login`` done meanwhile shows up soon on the dashboard."""
SHUTDOWN_GRACE = 3.0
BACKOFF_BASE = 0.5
BACKOFF_MAX = 30.0
STABLE_UPTIME = 60.0
"""A process that lived this long before dying does not escalate the respawn backoff."""
DETAIL_MAX_CHARS = 300

LOGIN_HINT = "executa «codex login --device-auth» al servidor"

_PLAN_LABELS: dict[str, str] = {
    "free": "Free",
    "go": "Go",
    "plus": "Plus",
    "pro": "Pro",
    "prolite": "Pro Lite",
    "team": "Team",
    "business": "Business",
    "enterprise": "Enterprise",
    "edu": "Edu",
}

_DENIED_REVIEW: JsonObject = {"denied": {"rejection": "Not available in this environment."}}
_DECLINED_REQUESTS: dict[str, JsonObject] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "item/tool/requestUserInput": {"answers": {}},
    "item/tool/call": {"contentItems": [], "success": False},
    "mcpServer/elicitation/request": {"action": "decline", "content": None, "_meta": None},
    "applyPatchApproval": {"decision": _DENIED_REVIEW},
    "execCommandApproval": {"decision": _DENIED_REVIEW},
}
"""Safe answer to every server -> client request of the 0.157.1 schema that has one.
Anything else (token refresh for external auth, attestation...) gets a JSON-RPC error."""

_RETRYABLE_ERROR_INFOS = frozenset(
    {
        "serverOverloaded",
        "internalServerError",
        "httpConnectionFailed",
        "responseStreamConnectionFailed",
        "responseStreamDisconnected",
        "responseTooManyFailedAttempts",
    }
)


class CodexRpcError(Exception):
    """JSON-RPC error answer from the app-server."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProcessGone(Exception):
    """The app-server process exited or closed its stdout."""


def codex_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment of the Codex process: the allow-list only, never the app's secrets."""
    environ = os.environ if source is None else source
    return {name: environ[name] for name in ENV_ALLOWLIST if name in environ}


def config_arguments() -> list[str]:
    """``-c key=value`` command-line arguments that trim Codex's agent harness."""
    arguments: list[str] = []
    for key, value in CONFIG_OVERRIDES:
        arguments += ["-c", f"{key}={value}"]
    return arguments


class _AppServerConnection:
    """JSON-RPC over the stdio of one ``codex app-server`` process.

    Responses resolve request futures; notifications that carry a ``threadId`` go to the
    queue of that thread (``None`` marks the end of the process); other notifications go
    to ``on_notification``. Server -> client requests are declined.
    """

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        on_notification: Callable[[str, JsonObject], None],
        on_exit: Callable[[_AppServerConnection, bool], None],
    ) -> None:
        self._process = process
        self._on_notification = on_notification
        self._on_exit = on_exit
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._listeners: dict[str, asyncio.Queue[tuple[str, JsonObject] | None]] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._alive = True
        self._closing = False
        self.started_at = time.monotonic()
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self._tasks: set[asyncio.Task[None]] = set()
        self._stdout_task = asyncio.create_task(self._read_stdout(), name="codex-stdout")
        self._stderr_task = asyncio.create_task(self._read_stderr(), name="codex-stderr")

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def pid(self) -> int:
        return self._process.pid

    async def request(self, method: str, params: Any, timeout: float) -> Any:
        """Send a request and wait for its result (``CodexRpcError`` on an error answer).

        The pending entry is always removed, on timeout and cancellation too."""
        if not self._alive:
            raise ProcessGone("codex app-server is not running")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Any = None) -> None:
        message: JsonObject = {"method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    def listen(self, thread_id: str) -> asyncio.Queue[tuple[str, JsonObject] | None]:
        queue: asyncio.Queue[tuple[str, JsonObject] | None] = asyncio.Queue()
        if not self._alive:
            queue.put_nowait(None)
        self._listeners[thread_id] = queue
        return queue

    def stop_listening(self, thread_id: str) -> None:
        self._listeners.pop(thread_id, None)

    async def close(self) -> None:
        """Stop the process: SIGTERM to its process group, SIGKILL after a grace period."""
        self._closing = True
        self._mark_dead()
        await self._terminate()
        tasks = [self._stdout_task, self._stderr_task, *self._tasks]
        for task in tasks:
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(
            *(t for t in tasks if t is not asyncio.current_task()), return_exceptions=True
        )

    # -- internals ---------------------------------------------------------------------

    async def _send(self, message: JsonObject) -> None:
        stdin = self._process.stdin
        if stdin is None or not self._alive:
            raise ProcessGone("codex app-server is not running")
        data = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            async with self._write_lock:
                stdin.write(data)
                await stdin.drain()
        except (ConnectionError, RuntimeError) as exc:
            raise ProcessGone(f"codex app-server stdin closed: {exc}") from exc

    async def _read_stdout(self) -> None:
        stdout = self._process.stdout
        try:
            while stdout is not None:
                try:
                    line = await stdout.readline()
                except ValueError:
                    logger.warning(
                        "Codex app-server sent a line over %d bytes; skipped", LINE_LIMIT
                    )
                    continue
                if not line:
                    break
                self._dispatch(line)
        except Exception:
            logger.exception("Codex app-server reader failed")
        finally:
            self._mark_dead()
            if not self._closing:
                await self._terminate()

    async def _read_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        with contextlib.suppress(Exception):
            while True:
                try:
                    line = await stderr.readline()
                except ValueError:
                    continue
                if not line:
                    return
                text = line.decode("utf-8", "replace").rstrip()
                self.stderr_tail.append(text)
                logger.debug("codex app-server: %s", text)

    def _dispatch(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            logger.debug("Codex app-server sent a non-JSON line: %r", line[:200])
            return
        if not isinstance(message, dict):
            return
        method = message.get("method")
        if isinstance(method, str):
            if "id" in message:
                self._spawn(self._answer_server_request(message["id"], method))
                return
            params = message.get("params")
            params = params if isinstance(params, dict) else {}
            thread_id = params.get("threadId")
            queue = self._listeners.get(thread_id) if isinstance(thread_id, str) else None
            if queue is not None:
                queue.put_nowait((method, params))
            else:
                self._on_notification(method, params)
            return
        request_id = message.get("id")
        future = self._pending.pop(request_id, None) if isinstance(request_id, int) else None
        if future is None or future.done():
            return
        error = message.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            text = error.get("message") if isinstance(error, dict) else None
            future.set_exception(
                CodexRpcError(code if isinstance(code, int) else None, str(text or error))
            )
        else:
            future.set_result(message.get("result"))

    async def _answer_server_request(self, request_id: Any, method: str) -> None:
        result = _DECLINED_REQUESTS.get(method)
        reply: JsonObject
        if result is None:
            logger.warning("Codex app-server request %s is not supported; refused", method)
            reply = {
                "id": request_id,
                "error": {"code": -32601, "message": f"{method} is not supported by this client"},
            }
        else:
            logger.info("Declined Codex app-server request %s", method)
            reply = {"id": request_id, "result": result}
        with contextlib.suppress(ProcessGone):
            await self._send(reply)

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _mark_dead(self) -> None:
        if not self._alive:
            return
        self._alive = False
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ProcessGone("codex app-server exited"))
                # Retrieved here: its requester may already be gone.
                future.exception()
        self._pending.clear()
        for queue in self._listeners.values():
            queue.put_nowait(None)
        self._on_exit(self, self._closing)

    async def _terminate(self) -> None:
        process = self._process
        if process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.close()
        try:
            if process.returncode is None:
                _kill_group(process.pid, signal.SIGTERM)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), SHUTDOWN_GRACE)
        finally:
            # The group may hold children of the npm wrapper even after the leader exits.
            _kill_group(process.pid, signal.SIGKILL)
        if process.returncode is None:
            await process.wait()


def _kill_group(pid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


async def _until[T](deadline: float, awaitable: Awaitable[T]) -> T:
    """Await with the time left until ``deadline`` (``TimeoutError`` when it passes)."""
    return await asyncio.wait_for(awaitable, max(0.0, deadline - time.monotonic()))


async def _outcome(task: asyncio.Task[Any]) -> Any | None:
    """Result of a task, or None if it failed or was cancelled (without raising)."""
    await asyncio.wait((task,))
    if task.cancelled() or task.exception() is not None:
        return None
    return task.result()


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_dict(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _nested_id(result: Any, key: str) -> str:
    """``result[key]["id"]`` of a thread/start or turn/start answer."""
    value = _as_dict(_as_dict(result).get(key)).get("id")
    if not isinstance(value, str) or not value:
        raise CodexRpcError(None, f"missing {key}.id in the app-server answer")
    return value


_SECRET_RE = re.compile(
    r"(sk-)[A-Za-z0-9_\-]{8,}|(Bearer\s+)\S+|(eyJ)[A-Za-z0-9_\-]+\.[A-Za-z0-9_.\-]+"
)


def redact(text: str) -> str:
    """Hide anything that looks like an API key, a bearer token or a JWT."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1) or m.group(2) or m.group(3)}***", text)


def _with_detail(text: str, detail: str) -> str:
    """``text`` plus the vendor's own message (redacted, shortened) in parentheses."""
    detail = redact(detail.strip())
    if not detail:
        return text
    if len(detail) > DETAIL_MAX_CHARS:
        detail = detail[: DETAIL_MAX_CHARS - 1] + "…"
    return f"{text} ({detail})"


def usage_from_breakdown(breakdown: Any) -> Usage:
    """Usage from a ``TokenUsageBreakdown``. ``input_tokens`` is the uncached remainder,
    as for Anthropic: Codex's inputTokens include cache reads and writes."""
    data = _as_dict(breakdown)
    total_input = _as_int(data.get("inputTokens"))
    cached = _as_int(data.get("cachedInputTokens"))
    written = _as_int(data.get("cacheWriteInputTokens"))
    return Usage(
        input_tokens=max(0, total_input - cached - written),
        output_tokens=_as_int(data.get("outputTokens")),
        cache_read_tokens=cached,
        cache_write_tokens=written,
        reasoning_tokens=_as_int(data.get("reasoningOutputTokens")),
        cost_usd=None,
    )


def _error_info(info: Any) -> tuple[str, int | None]:
    """``codexErrorInfo`` as (variant name, upstream HTTP status)."""
    if isinstance(info, str):
        return info, None
    if isinstance(info, dict) and len(info) == 1:
        name, payload = next(iter(info.items()))
        status = _as_dict(payload).get("httpStatusCode")
        return str(name), status if isinstance(status, int) else None
    return "other", None


def turn_error(error: Any) -> ProviderError:
    """ProviderError for a failed turn or a final ``error`` notification (``TurnError``)."""
    data = _as_dict(error)
    message = data.get("message")
    detail = message if isinstance(message, str) else ""
    name, status = _error_info(data.get("codexErrorInfo"))
    if name in ("usageLimitExceeded", "sessionBudgetExceeded"):
        return ProviderError(
            _with_detail("Has arribat al límit d'ús de la subscripció de ChatGPT.", detail),
            kind="rate_limit",
        )
    if name == "rateLimitExceeded" or status == 429:
        return ProviderError(
            _with_detail(
                "ChatGPT ha limitat les peticions; torna-ho a provar d'aquí a poc.", detail
            ),
            kind="rate_limit",
            retryable=True,
        )
    if name == "unauthorized" or status in (401, 403):
        return ProviderError(f"La sessió de Codex no és vàlida: {LOGIN_HINT}.", kind="auth")
    if name == "contextWindowExceeded":
        return ProviderError("La conversa és massa llarga per al model de ChatGPT.", kind="invalid")
    if name in ("cyberPolicy", "misalignmentPolicyViolation"):
        return ProviderError(
            _with_detail("ChatGPT ha rebutjat la petició per la seva política d'ús.", detail),
            kind="invalid",
        )
    if name == "badRequest":
        return ProviderError(_with_detail("Codex ha rebutjat la petició.", detail), kind="invalid")
    if name in _RETRYABLE_ERROR_INFOS:
        return ProviderError(
            _with_detail("ChatGPT no està disponible ara mateix.", detail),
            kind="unavailable",
            retryable=True,
        )
    return ProviderError(_with_detail("Codex ha fallat.", detail), kind="internal")


def _rpc_error(exc: CodexRpcError) -> ProviderError:
    lowered = exc.message.lower()
    if "authentication" in lowered or "not logged in" in lowered or "login" in lowered:
        return ProviderError(f"Codex no té sessió: {LOGIN_HINT}.", kind="auth")
    if exc.code == -32602:
        return ProviderError(
            _with_detail("Codex ha rebutjat la petició.", exc.message), kind="invalid"
        )
    return ProviderError(_with_detail("Error del servidor de Codex.", exc.message), kind="internal")


def _window_label(minutes: Any, fallback: str) -> str:
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        return fallback
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _usage_limit(name: str, window: JsonObject) -> UsageLimit:
    used = window.get("usedPercent")
    used_percent = (
        float(used) if isinstance(used, int | float) and not isinstance(used, bool) else None
    )
    resets = window.get("resetsAt")
    resets_at = (
        datetime.fromtimestamp(resets, tz=UTC)
        if isinstance(resets, int | float) and not isinstance(resets, bool)
        else None
    )
    if used_percent is None:
        state = "allowed"
    elif used_percent >= 100:
        state = "rejected"
    elif used_percent >= 80:
        state = "warning"
    else:
        state = "allowed"
    return UsageLimit(
        window=_window_label(window.get("windowDurationMins"), name),
        used_percent=used_percent,
        resets_at=resets_at,
        status=state,
    )


def _prepare_sandbox(path: Path) -> Path:
    """Create the empty working directory of Codex (0700) and return its absolute path."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path.resolve()


class CodexAppServerProvider:
    """ChatGPT in ``cli`` mode: ``codex app-server`` signed in with the owner's ChatGPT plan.

    The process is spawned lazily (or by :meth:`prewarm`), shared by concurrent calls
    and re-spawned with exponential backoff if it dies.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sandbox = settings.data_dir / "sandbox" / "codex"
        self._cwd: Path | None = None
        """Absolute sandbox path, set when the process starts."""
        self._conn: _AppServerConnection | None = None
        self._starting: asyncio.Task[None] | None = None
        self._closed = False
        self._failures = 0
        self._last_failure = 0.0
        self._background: set[asyncio.Task[None]] = set()
        self._active_calls = 0
        self._default_model: str | None = None
        self._windows: dict[str, JsonObject] = {}
        self._status_cache: tuple[float, ProviderStatus] | None = None
        self._status_lock = asyncio.Lock()

    @property
    def agent(self) -> AgentName:
        return "chatgpt"

    @property
    def mode(self) -> ProviderMode:
        return "cli"

    # -- generation ----------------------------------------------------------------------

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        started = time.monotonic()
        deadline = started + self._settings.provider_timeout_seconds
        requested_model = self._requested_model(request)
        conn: _AppServerConnection | None = None
        thread_task: asyncio.Task[Any] | None = None
        turn_task: asyncio.Task[Any] | None = None
        thread_id: str | None = None
        turn_done = False
        self._active_calls += 1
        try:
            conn = await _until(deadline, self._connection(wait_backoff=True))
            thread_task = asyncio.create_task(
                conn.request(
                    "thread/start", self._thread_params(request, requested_model), CONTROL_TIMEOUT
                )
            )
            thread = await _until(deadline, asyncio.shield(thread_task))
            thread_id = _nested_id(thread, "thread")
            model = _as_dict(thread).get("model") or requested_model or FALLBACK_MODEL_LABEL
            if requested_model is None and _as_dict(thread).get("model"):
                self._default_model = str(model)
            events = conn.listen(thread_id)
            turn_task = asyncio.create_task(
                conn.request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [
                            {
                                "type": "text",
                                "text": render_transcript(request),
                                "text_elements": [],
                            }
                        ],
                        "effort": EFFORT_BY_PURPOSE[request.purpose],
                        "summary": "none",
                    },
                    CONTROL_TIMEOUT,
                )
            )
            turn_id = _nested_id(await _until(deadline, asyncio.shield(turn_task)), "turn")

            parts: list[str] = []
            usage = Usage()
            ttft_ms: int | None = None
            commentary: set[Any] = set()
            last_item: Any = None
            while True:
                event = await _until(deadline, events.get())
                if event is None:
                    raise ProcessGone("codex app-server exited during the turn")
                method, params = event
                if params.get("turnId", turn_id) != turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    item_id = params.get("itemId")
                    if not isinstance(delta, str) or not delta or item_id in commentary:
                        continue
                    if parts and item_id != last_item:
                        # A second assistant message in the same turn: keep them apart.
                        parts.append("\n\n")
                        yield TextDelta("\n\n")
                    last_item = item_id
                    if ttft_ms is None:
                        ttft_ms = int((time.monotonic() - started) * 1000)
                    parts.append(delta)
                    yield TextDelta(delta)
                elif method == "item/started":
                    item = _as_dict(params.get("item"))
                    if item.get("type") == "agentMessage" and item.get("phase") == "commentary":
                        commentary.add(item.get("id"))
                elif method == "thread/tokenUsage/updated":
                    # A fresh single-turn thread: its running total is exactly this turn,
                    # including every model request the turn made.
                    usage = usage_from_breakdown(_as_dict(params.get("tokenUsage")).get("total"))
                elif method == "model/rerouted":
                    if isinstance(params.get("toModel"), str):
                        model = params["toModel"]
                elif method == "error":
                    if not params.get("willRetry"):
                        raise turn_error(params.get("error"))
                elif method == "turn/completed":
                    turn = _as_dict(params.get("turn"))
                    if turn.get("id", turn_id) != turn_id:
                        continue
                    turn_done = True
                    status = turn.get("status")
                    if status == "completed":
                        self._failures = 0
                        yield GenerationResult(
                            text="".join(parts),
                            usage=usage,
                            model=str(model),
                            latency_ms=int((time.monotonic() - started) * 1000),
                            ttft_ms=ttft_ms,
                        )
                        return
                    if status == "interrupted":
                        raise asyncio.CancelledError("Codex interrupted the turn")
                    raise turn_error(turn.get("error"))
        except TimeoutError:
            raise ProviderError(
                "ChatGPT (Codex) ha superat el temps màxim de resposta.", kind="timeout"
            ) from None
        except ProcessGone:
            raise ProviderError(
                "El procés de Codex s'ha aturat inesperadament.",
                kind="unavailable",
                retryable=True,
            ) from None
        except CodexRpcError as exc:
            raise _rpc_error(exc) from None
        finally:
            self._active_calls -= 1
            if conn is not None and thread_task is not None:
                if thread_id is not None:
                    conn.stop_listening(thread_id)
                self._spawn(
                    self._release_thread(conn, thread_task, None if turn_done else turn_task)
                )

    def _requested_model(self, request: GenerationRequest) -> str | None:
        if request.model:
            return request.model
        if request.fast:
            return self._settings.chatgpt_fast_model or DEFAULT_FAST_MODEL
        return self._settings.chatgpt_model

    def _thread_params(self, request: GenerationRequest, model: str | None) -> JsonObject:
        params: JsonObject = {
            "cwd": str(self._cwd or self._sandbox),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "baseInstructions": request.system,
            "ephemeral": True,
        }
        if model:
            params["model"] = model
        return params

    async def _release_thread(
        self,
        conn: _AppServerConnection,
        thread_task: asyncio.Task[Any],
        turn_task: asyncio.Task[Any] | None,
    ) -> None:
        """Interrupt an unfinished turn and unsubscribe its thread (in the background, so
        a cancelled call returns at once). A server that does not answer is restarted."""
        thread = await _outcome(thread_task)
        if thread is None:
            return
        thread_id = _as_dict(_as_dict(thread).get("thread")).get("id")
        if not isinstance(thread_id, str):
            return
        try:
            if turn_task is not None:
                turn = await _outcome(turn_task)
                turn_id = _as_dict(_as_dict(turn).get("turn")).get("id")
                if isinstance(turn_id, str):
                    with contextlib.suppress(CodexRpcError):
                        await conn.request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": turn_id},
                            CLEANUP_TIMEOUT,
                        )
            with contextlib.suppress(CodexRpcError):
                await conn.request("thread/unsubscribe", {"threadId": thread_id}, CLEANUP_TIMEOUT)
        except TimeoutError:
            logger.warning("Codex app-server stopped answering; restarting it")
            await self._discard(conn)
        except ProcessGone:
            pass

    # -- process lifecycle ---------------------------------------------------------------

    async def _connection(self, *, wait_backoff: bool) -> _AppServerConnection:
        """The running app-server, started (once, shared by concurrent callers) if needed."""
        while True:
            if self._closed:
                raise ProviderError("El proveïdor de Codex està tancat.", kind="unavailable")
            conn = self._conn
            if conn is not None and conn.alive:
                return conn
            delay = self._backoff_delay()
            if delay > 0 and not wait_backoff:
                raise ProviderError(
                    f"Codex s'ha aturat; es reiniciarà d'aquí a {math.ceil(delay)} s.",
                    kind="unavailable",
                    retryable=True,
                )
            if self._starting is None:
                task = asyncio.create_task(self._start(delay), name="codex-start")
                task.add_done_callback(self._start_done)
                self._starting = task
            # Shielded: a cancelled caller must not abort a start other calls wait for.
            await asyncio.shield(self._starting)

    def _start_done(self, task: asyncio.Task[None]) -> None:
        if self._starting is task:
            self._starting = None
        if not task.cancelled():
            task.exception()  # retrieved by the waiters; avoid "never retrieved" warnings

    async def _start(self, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        cli = self._settings.codex_cli_path
        try:
            cwd = await asyncio.to_thread(_prepare_sandbox, self._sandbox)
        except OSError as exc:
            raise ProviderError(
                f"No s'ha pogut preparar el directori de treball de Codex: {exc}",
                kind="unavailable",
            ) from None
        self._cwd = cwd
        try:
            process = await asyncio.create_subprocess_exec(
                cli,
                "app-server",
                *config_arguments(),
                "--listen",
                "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=codex_environment(),
                start_new_session=True,
                limit=LINE_LIMIT,
            )
        except FileNotFoundError:
            raise ProviderError(f"CLI de Codex no trobada: {cli}", kind="unavailable") from None
        except OSError as exc:
            raise ProviderError(
                f"No s'ha pogut executar la CLI de Codex: {exc}", kind="unavailable"
            ) from None
        conn = _AppServerConnection(
            process, on_notification=self._on_notification, on_exit=self._on_exit
        )
        try:
            result = await conn.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": CLIENT_NAME,
                        "title": "ClaudeGPT OS",
                        "version": __version__,
                    },
                    "capabilities": {"experimentalApi": False, "requestAttestation": False},
                },
                INITIALIZE_TIMEOUT,
            )
            await conn.notify("initialized")
        except BaseException as exc:
            if conn.alive and isinstance(exc, Exception):
                self._record_failure()
            await conn.close()
            if isinstance(exc, Exception):
                tail = " | ".join(list(conn.stderr_tail)[-3:])
                raise ProviderError(
                    _with_detail("No s'ha pogut iniciar Codex.", tail or str(exc)),
                    kind="unavailable",
                    retryable=True,
                ) from exc
            raise
        user_agent = _as_dict(result).get("userAgent")
        if isinstance(user_agent, str) and f"/{PROTOCOL_VERSION}" not in user_agent:
            logger.warning(
                "Codex app-server %r is not the pinned %sx release; the protocol may differ",
                user_agent,
                PROTOCOL_VERSION,
            )
        self._conn = conn
        logger.info("Codex app-server started (pid %d)", conn.pid)

    def _on_exit(self, conn: _AppServerConnection, expected: bool) -> None:
        if self._conn is conn:
            self._conn = None
        if expected:
            return
        if time.monotonic() - conn.started_at >= STABLE_UPTIME:
            self._failures = 0
        self._record_failure()
        logger.warning(
            "Codex app-server (pid %d) exited unexpectedly: %s",
            conn.pid,
            " | ".join(conn.stderr_tail) or "no stderr",
        )

    def _record_failure(self) -> None:
        self._failures += 1
        self._last_failure = time.monotonic()

    def _backoff_delay(self) -> float:
        """Seconds to wait before the next spawn: none after a single failure."""
        if self._failures <= 1:
            return 0.0
        delay = min(BACKOFF_MAX, BACKOFF_BASE * 2.0 ** (self._failures - 2))
        return max(0.0, self._last_failure + delay - time.monotonic())

    async def _discard(self, conn: _AppServerConnection) -> None:
        if self._conn is conn:
            self._conn = None
        await conn.close()

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        try:
            task = asyncio.ensure_future(coroutine)
        except RuntimeError:  # no running loop (interpreter shutdown)
            if asyncio.iscoroutine(coroutine):
                coroutine.close()
            return
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _on_notification(self, method: str, params: JsonObject) -> None:
        if method == "account/rateLimits/updated":
            self._merge_rate_limits(params.get("rateLimits"), sparse=True)
        elif method == "account/updated":
            self._status_cache = None
        elif method in ("configWarning", "warning", "deprecationNotice"):
            logger.info("Codex app-server %s: %s", method, params.get("summary") or params)

    def _merge_rate_limits(self, snapshot: Any, *, sparse: bool) -> None:
        """Keep the latest ``primary``/``secondary`` windows of the ``codex`` limit.

        Updates are sparse: a missing window keeps the last value seen."""
        data = _as_dict(snapshot)
        if data.get("limitId") not in (None, "codex"):
            return
        for name in ("primary", "secondary"):
            window = data.get(name)
            if isinstance(window, dict):
                self._windows[name] = window
            elif not sparse:
                self._windows.pop(name, None)

    def _limits(self) -> list[UsageLimit]:
        return [
            _usage_limit(name, self._windows[name])
            for name in ("primary", "secondary")
            if name in self._windows
        ]

    # -- provider protocol ---------------------------------------------------------------

    async def prewarm(self, request: GenerationRequest) -> None:
        """Start and initialize the app-server if it is not running (never raises)."""
        try:
            await self._connection(wait_backoff=False)
        except Exception:
            logger.debug("Codex prewarm failed", exc_info=True)

    async def status(self) -> ProviderStatus:
        cached = self._status_cache
        if cached is not None and time.monotonic() < cached[0]:
            return replace(cached[1], limits=self._limits())
        async with self._status_lock:
            cached = self._status_cache
            if cached is not None and time.monotonic() < cached[0]:
                return replace(cached[1], limits=self._limits())
            status, ttl = await self._read_status()
            self._status_cache = (time.monotonic() + ttl, status)
            return status

    async def _read_status(self) -> tuple[ProviderStatus, float]:
        model = self._settings.chatgpt_model or self._default_model or FALLBACK_MODEL_LABEL

        def unavailable(detail: str) -> tuple[ProviderStatus, float]:
            status = ProviderStatus(
                agent="chatgpt",
                mode="cli",
                available=False,
                model=model,
                detail=detail,
                limits=self._limits(),
            )
            return status, STATUS_TTL_UNAVAILABLE

        try:
            conn = await self._connection(wait_backoff=False)
            answer = _as_dict(
                await conn.request("account/read", {"refreshToken": False}, STATUS_REQUEST_TIMEOUT)
            )
        except ProviderError as exc:
            return unavailable(exc.message)
        except (TimeoutError, ProcessGone, CodexRpcError):
            logger.warning("Could not read the Codex account", exc_info=True)
            return unavailable("No s'ha pogut llegir l'estat de Codex.")

        account = answer.get("account")
        if not isinstance(account, dict):
            if answer.get("requiresOpenaiAuth") is False:
                detail = "Codex amb un proveïdor de models propi"
            else:
                # A fresh process re-reads auth.json, so a later `codex login` is picked up.
                if self._active_calls == 0:
                    self._spawn(self._discard(conn))
                return unavailable(f"Sense sessió: {LOGIN_HINT}")
        elif account.get("type") == "chatgpt":
            plan = account.get("planType")
            label = _PLAN_LABELS.get(plan, str(plan).replace("_", " ").title()) if plan else ""
            detail = (
                f"Subscripció ChatGPT activa ({label})" if label else "Subscripció ChatGPT activa"
            )
            with contextlib.suppress(TimeoutError, ProcessGone, CodexRpcError):
                limits = await conn.request("account/rateLimits/read", None, STATUS_REQUEST_TIMEOUT)
                self._merge_rate_limits(_as_dict(limits).get("rateLimits"), sparse=False)
        elif account.get("type") == "apiKey":
            detail = "Codex amb clau d'API (es factura per ús)"
        else:
            detail = "Codex amb un compte extern"

        if not self._settings.chatgpt_model and self._default_model is None:
            model = await self._read_default_model(conn) or model
        status = ProviderStatus(
            agent="chatgpt",
            mode="cli",
            available=True,
            model=model,
            detail=detail,
            limits=self._limits(),
        )
        return status, STATUS_TTL

    async def _read_default_model(self, conn: _AppServerConnection) -> str | None:
        """Model a thread gets without an explicit one: ``model`` of the effective config
        (config.toml), else the catalog default."""
        try:
            config = await conn.request("config/read", {}, STATUS_REQUEST_TIMEOUT)
            configured = _as_dict(_as_dict(config).get("config")).get("model")
            if isinstance(configured, str) and configured:
                self._default_model = configured
                return configured
            listing = await conn.request("model/list", {"limit": 100}, STATUS_REQUEST_TIMEOUT)
        except (TimeoutError, ProcessGone, CodexRpcError):
            return None
        for entry in _as_dict(listing).get("data") or []:
            model = _as_dict(entry).get("model")
            if _as_dict(entry).get("isDefault") and isinstance(model, str) and model:
                self._default_model = model
                return model
        return None

    async def aclose(self) -> None:
        """Stop the app-server (process group SIGTERM, then SIGKILL) and background work."""
        self._closed = True
        pending = [t for t in (self._starting, *self._background) if t is not None]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()
