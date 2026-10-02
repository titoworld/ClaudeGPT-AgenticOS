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

0.157.1 still offers the model its sub-agent tools (the bundled model catalog enables
them whatever the config says), so a prompt injection can make ChatGPT spawn a
sub-agent: a thread no call listens to, which would keep spending the owner's plan
after the call ends. ``agents.max_threads=1`` caps them and any turn on a thread that
no call owns is interrupted as soon as it shows up. Interrupting a sub-agent frees its
slot, so a call counts the sub-agent runs (spawns and follow-ups) its turn asks for and
is stopped when there are more than :data:`MAX_SUB_AGENT_RUNS`. The sub-agents' threads
are unsubscribed (0.157.1 cannot archive or delete ephemeral threads) and a process that
ran any is replaced as soon as no call uses it: their memory is never given back.

Codex's SQLite state, whose log records every prompt, lives in a private directory
outside ``CODEX_HOME``; the log databases are deleted before every start, so prompts do
not outlive the process and a full state tmpfs cannot prevent a restart.

Attachments go before the transcript as items of the turn's input, in order
(:func:`input_items`): an image as a ``localImage`` (Codex reads the file itself, so no
image travels through the JSON-RPC pipe) at a link to the stored file whose extension
gives its type, because Codex guesses a local image's type from the file name
(:func:`named_image`); a text file as text; and a
PDF, which Codex cannot take, as the text the server extracted from it, page by page,
with Claude's reading of the pages whose text it found missing or unreliable when the
engine had Claude check it, and every page marked checked or not
(``prompt_format.pdf_view``). Codex joins the text items into one message: a file's text
is enclosed and neutralized (``prompt_format.enclosed``), so it cannot pass for the
transcript that follows.

Integrity of the reply: the text is kept per ``agentMessage`` item and the final text is
made only of the (non-commentary) items that completed. When Codex retries a dropped
model stream after some text was already shown (an ``error`` with ``willRetry``), it
samples the whole answer again in a new item and cannot take back what was streamed, so
the call fails as interrupted instead of returning the answer twice. 0.157.1 has no
protocol field for the output budget: the call stops the turn itself (``turn/interrupt``)
once the visible text passes ``max_output_tokens`` by the usual estimate (about 4
characters per token) and returns a truncated result. It is approximate: the reasoning
is not counted and a few more tokens may be generated before the interrupt lands; the
usage stays the one Codex reports.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import secrets
import signal
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from agentic_os import __version__
from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage
from agentic_os.orchestrator.tokens import tokens_for_chars
from agentic_os.providers.base import (
    Attachment,
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
    UsageLimit,
)
from agentic_os.providers.prompt_format import (
    attachment_text,
    pdf_view,
    render_transcript,
)

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
    "check": "low",
}
LOWEST_EFFORT = "low"
"""Effort of a request with reasoning "off": the lowest level every model of the 0.157.1
catalog accepts (none of them lists "none" or "minimal")."""

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
    # At most one sub-agent at a time (0 is rejected): see _on_stray_activity.
    ("agents.max_threads", "1"),
    # Unload a thread as soon as its last subscriber leaves: by default every call's
    # thread stayed loaded (~1.2 MB each) long after thread/unsubscribe.
    ("thread_unload_delay_secs", "0"),
    *((f"features.{name}", "false") for name in _DISABLED_FEATURES),
)
"""``-c key=value`` overrides (TOML values), all accepted by ``--strict-config`` in 0.157.1.
Without them every request carries ~50 KB of agent prompt and tool definitions."""

LINE_LIMIT = 32 * 1024 * 1024
"""Longest JSON-RPC line accepted from the server (a turn/completed repeats the answer)."""
INITIALIZE_TIMEOUT = 30.0
CONTROL_TIMEOUT = 30.0
"""thread/start and turn/start (local work, but they may touch the network). Like every
request timeout it covers the write too: a process that stops reading its stdin fills
the pipe. A process that does not answer thread/start is replaced."""
CLEANUP_TIMEOUT = 10.0
"""turn/interrupt and thread/unsubscribe, and the answers to the server's requests: no
answer (or no room in its stdin) means the process is wedged."""
STATUS_REQUEST_TIMEOUT = 10.0
MODELS_TTL = 600.0
MAX_MODEL_PAGES = 5
STATUS_TTL = 60.0
STATUS_TTL_UNAVAILABLE = 10.0
"""Short, so a ``codex login`` done meanwhile shows up soon on the dashboard."""
SHUTDOWN_GRACE = 3.0
BACKOFF_BASE = 0.5
BACKOFF_MAX = 30.0
STABLE_UPTIME = 60.0
"""A process that lived this long before dying does not escalate the respawn backoff."""
DETAIL_MAX_CHARS = 300
MAX_SUB_AGENT_RUNS = 3
"""Sub-agent runs (spawns and follow-ups) one call tolerates; the next one stops it."""
SUB_AGENT_LIMIT_MESSAGE = "ChatGPT ha intentat obrir massa subagents; s'ha aturat la resposta."
INTERRUPTED_MESSAGE = "La resposta de ChatGPT s'ha interromput."
_SUB_AGENT_RUN_KINDS = frozenset({"started", "interacted"})
"""``subAgentActivity`` kinds that start a turn on a sub-agent's thread."""
_COLLAB_RUN_TOOLS = frozenset({"spawnAgent", "sendInput", "resumeAgent", "followupTask"})
"""``collabAgentToolCall`` tools (multi-agent v1) that start a turn on another thread."""
_LOG_DATABASE_RE = re.compile(r"logs_\w+\.sqlite(?:-wal|-shm|-journal)?")
"""Codex's log databases (``logs_2.sqlite`` and its WAL files): every prompt, at DEBUG."""

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

MODEL_DESCRIPTIONS: dict[str, str] = {
    "gpt-6-astra": "El més capaç, per a la feina més exigent.",
    "gpt-6-sol": "Equilibrat, per a la feina de cada dia.",
    "gpt-6-luna": "Ràpid i econòmic, per a tasques senzilles.",
}
"""Catalan descriptions of known models (other models keep Codex's own description)."""

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


def config_arguments(state_dir: Path) -> list[str]:
    """``-c key=value`` command-line arguments that trim Codex's agent harness and keep
    its SQLite state (``logs_2.sqlite`` records every prompt) in ``state_dir``."""
    arguments: list[str] = []
    # A JSON string is also a valid TOML basic string.
    for key, value in (*CONFIG_OVERRIDES, ("sqlite_home", json.dumps(str(state_dir)))):
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
        on_notification: Callable[[_AppServerConnection, str, JsonObject], None],
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
        self._terminated = asyncio.Event()
        self.started_at = time.monotonic()
        self.tainted: str | None = None
        """Why this process may hold threads nobody releases (sub-agents ran in it, or a
        thread/start was answered too late), if it does: it is replaced as soon as no call
        uses it."""
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

    async def wait_terminated(self) -> None:
        """Until the process group has been stopped (SIGKILL sent, leader reaped)."""
        await self._terminated.wait()

    async def request(self, method: str, params: Any, timeout: float) -> Any:
        """Send a request and wait for its result (``CodexRpcError`` on an error answer).

        ``timeout`` covers the write as well as the answer: a process that stops reading
        its stdin fills the pipe, and the write would wait for it forever, holding the
        write lock of every other request. The pending entry is always removed, on
        timeout and cancellation too."""
        if not self._alive:
            raise ProcessGone("codex app-server is not running")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(timeout):
                await self._send({"id": request_id, "method": method, "params": params})
                return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Any = None, *, timeout: float) -> None:
        """Send a notification; ``timeout`` bounds the write (``TimeoutError``)."""
        message: JsonObject = {"method": method}
        if params is not None:
            message["params"] = params
        async with asyncio.timeout(timeout):
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
        """Write one line. The transport takes the whole line at once (what the pipe does
        not accept waits in its buffer), so a caller that gives up while it waits for the
        lock or for ``drain()`` never leaves part of a frame: the line is written whole,
        before any later one, or not at all."""
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
        """Keep and log the process's stderr, redacted line by line as it is read: the
        tail reaches error messages (and so the browser, the database and the logs)."""
        stderr = self._process.stderr
        if stderr is None:
            return
        bearer_before = False
        with contextlib.suppress(Exception):
            while True:
                try:
                    line = await stderr.readline()
                except ValueError:
                    continue
                if not line:
                    return
                text = line.decode("utf-8", "replace").rstrip()
                if bearer_before:  # the token of a "Bearer" that ended the previous line
                    text = _FIRST_WORD_RE.sub("***", text, count=1)
                bearer_before = _BEARER_AT_END_RE.search(text) is not None
                text = redact(text)
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
                self._on_notification(self, method, params)
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
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT):
                await self._send(reply)
        except ProcessGone:
            pass
        except TimeoutError:
            # The process has stopped reading its stdin: the requests that release the
            # call find it stuck as well, and replace it.
            logger.warning(
                "Codex app-server did not take the answer to %s: it has stopped reading", method
            )

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
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
        if process.returncode is None:
            await process.wait()
        self._terminated.set()


def _kill_group(pid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


async def _until[T](deadline: float, awaitable: Awaitable[T]) -> T:
    """Await with the time left until ``deadline`` (``TimeoutError`` when it passes)."""
    return await asyncio.wait_for(awaitable, max(0.0, deadline - time.monotonic()))


def _outcome(task: asyncio.Task[Any]) -> Any | None:
    """Result of a task, or None if it is still running, failed or was cancelled."""
    if not task.done() or task.cancelled() or task.exception() is not None:
        return None
    return task.result()


def _timed_out(task: asyncio.Task[Any]) -> bool:
    """Whether a finished request task failed for lack of an answer in time."""
    return task.done() and not task.cancelled() and isinstance(task.exception(), TimeoutError)


def _retrieved(task: asyncio.Task[Any]) -> None:
    """Done callback of a task that nobody awaits any more: its exception is expected."""
    if not task.cancelled():
        task.exception()


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
_BEARER_AT_END_RE = re.compile(r"\bBearer\s*$")
_FIRST_WORD_RE = re.compile(r"^\s*\S+")


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


def _private_dir(path: Path) -> Path:
    """Create a directory only the app's user can enter (0700); return its absolute path."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path.resolve()


def remove_log_databases(state_dir: Path) -> list[str]:
    """Delete Codex's log databases (``logs_*.sqlite`` and their ``-wal``/``-shm``) from
    ``state_dir``; the ``state_*`` and other databases stay. Returns the names removed.

    Only while no app-server uses the directory: they hold every prompt of the previous
    process and, on a full tmpfs, a new process cannot even start."""
    removed: list[str] = []
    with os.scandir(state_dir) as entries:
        for entry in entries:
            if not _LOG_DATABASE_RE.fullmatch(entry.name) or entry.is_dir(follow_symlinks=False):
                continue
            try:
                os.unlink(entry.path)
            except FileNotFoundError:
                continue
            except OSError as exc:  # keep going: the other files may still free space
                logger.warning("Could not delete the Codex log file %s: %s", entry.name, exc)
                continue
            removed.append(entry.name)
    return sorted(removed)


def _text_item(text: str) -> JsonObject:
    return {"type": "text", "text": text, "text_elements": []}


IMAGE_EXTENSIONS: Final[Mapping[str, str]] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
"""The extension that makes Codex's type guess (mime_guess, from the file name) right."""


def named_image(attachment: Attachment, directory: Path) -> Path:
    """A path to ``attachment`` whose name gives its type: Codex tells a local image's
    type from the file name, and the stored files are named after their content only. A
    symbolic link ``<sha256><extension>`` in ``directory`` (which must exist), replaced
    atomically when it points elsewhere. Blocking (file system calls)."""
    extension = IMAGE_EXTENSIONS.get(attachment.mime)
    if extension is None:
        return attachment.path
    target = attachment.path.resolve()
    link = directory / f"{attachment.sha256}{extension}"
    with contextlib.suppress(OSError):
        if Path(os.readlink(link)) == target:
            return link
    temporary = directory / f".{link.name}.{secrets.token_hex(8)}"
    os.symlink(target, temporary)
    try:
        os.replace(temporary, link)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return link


def input_items(
    request: GenerationRequest, images: Mapping[str, Path] | None = None
) -> list[JsonObject]:
    """``UserInput`` items of a call's turn: one per attachment, in order (an image as a
    ``localImage``, at its path in ``images`` (by SHA-256, see :func:`named_image`) or
    else its stored path; a PDF as ``prompt_format.pdf_view``; a text file as its ``[Fitxer: ...]``
    text), then the rendered transcript."""
    items: list[JsonObject] = []
    for attachment in request.attachments:
        if attachment.kind == "image":
            path = (images or {}).get(attachment.sha256, attachment.path)
            items.append({"type": "localImage", "path": str(path)})
        elif attachment.kind == "pdf":
            items.append(_text_item(pdf_view(attachment)))
        else:
            items.append(_text_item(attachment_text(attachment)))
    items.append(_text_item(render_transcript(request)))
    return items


async def check_images(attachments: Sequence[Attachment]) -> None:
    """Check that every image Codex will read is the file the owner attached (a
    ``ProviderError`` otherwise): Codex would put a note in its place and ChatGPT would
    answer without it."""
    images = [attachment for attachment in attachments if attachment.kind == "image"]
    if images:
        await asyncio.to_thread(lambda: [image.read() for image in images])


def sub_agent_run(item: JsonObject) -> tuple[str, list[str]] | None:
    """``(item id, thread ids)`` when a turn item starts a turn on a sub-agent's thread
    (a spawn or a follow-up), else None. ``item/started`` and ``item/completed`` repeat
    the same item, so callers count distinct ids."""
    kind = item.get("type")
    if kind == "subAgentActivity" and item.get("kind") in _SUB_AGENT_RUN_KINDS:
        threads = [item.get("agentThreadId")]
    elif kind == "collabAgentToolCall" and item.get("tool") in _COLLAB_RUN_TOOLS:
        receivers = item.get("receiverThreadIds")
        threads = receivers if isinstance(receivers, list) else []
    else:
        return None
    item_id = item.get("id")
    ids = [thread for thread in threads if isinstance(thread, str) and thread]
    return (item_id if isinstance(item_id, str) and item_id else ",".join(ids)), ids


class CodexAppServerProvider:
    """ChatGPT in ``cli`` mode: ``codex app-server`` signed in with the owner's ChatGPT plan.

    The process is spawned lazily (or by :meth:`prewarm`), shared by concurrent calls
    and re-spawned with exponential backoff if it dies. One that stops answering, or
    stops reading its stdin, is replaced when a call that used it is released.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sandbox = settings.data_dir / "sandbox" / "codex"
        self._cwd: Path | None = None
        """Absolute sandbox path, set when the process starts."""
        self._state_dir = settings.codex_state_dir or settings.data_dir / "sandbox" / "codex-state"
        self._threads: set[str] = set()
        """Threads of this provider's calls, from thread/start until they are released."""
        self._stray_turns: set[str] = set()
        """Turns on threads no call owns (sub-agents) with an interrupt sent."""
        self._dying: set[_AppServerConnection] = set()
        """Processes that exited or are being stopped, until a new start has waited for
        them (two app-servers must not share the state directory)."""
        self._conn: _AppServerConnection | None = None
        self._starting: asyncio.Task[None] | None = None
        self._closed = False
        self._failures = 0
        self._last_failure = 0.0
        self._background: set[asyncio.Task[None]] = set()
        self._active_calls = 0
        self._releasing = 0
        """Calls that ended but whose thread is still being released."""
        self._default_model: str | None = None
        self._windows: dict[str, JsonObject] = {}
        self._limits_at = float("-inf")
        """When the usage windows were last updated (monotonic)."""
        self._status_cache: tuple[float, ProviderStatus] | None = None
        self._status_lock = asyncio.Lock()
        self._models_cache: tuple[float, tuple[ModelInfo, ...], bool] | None = None
        """(expiry on the monotonic clock, models, live)."""
        self._models_lock = asyncio.Lock()

    @property
    def agent(self) -> AgentName:
        return "chatgpt"

    @property
    def mode(self) -> ProviderMode:
        return "cli"

    @property
    def fast_model(self) -> str:
        """Model of the cheap internal calls (summaries) when none is requested."""
        return self._settings.chatgpt_fast_model or DEFAULT_FAST_MODEL

    # -- generation ----------------------------------------------------------------------

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        started = time.monotonic()
        deadline = started + self._settings.provider_timeout_seconds
        await check_images(request.attachments)
        images = await self._named_images(request.attachments)
        requested_model = self._requested_model(request)
        conn: _AppServerConnection | None = None
        thread_task: asyncio.Task[Any] | None = None
        turn_task: asyncio.Task[Any] | None = None
        thread_id: str | None = None
        turn_done = False
        sub_agent_runs: set[str] = set()
        sub_agent_threads: set[str] = set()
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
            self._threads.add(thread_id)
            model = _as_dict(thread).get("model") or requested_model or FALLBACK_MODEL_LABEL
            if requested_model is None and _as_dict(thread).get("model"):
                self._default_model = str(model)
            events = conn.listen(thread_id)
            turn_task = asyncio.create_task(
                conn.request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": input_items(request, images),
                        "effort": (
                            LOWEST_EFFORT
                            if request.reasoning == "off"
                            else EFFORT_BY_PURPOSE[request.purpose]
                        ),
                        "summary": "none",
                    },
                    CONTROL_TIMEOUT,
                )
            )
            turn_id = _nested_id(await _until(deadline, asyncio.shield(turn_task)), "turn")

            parts: list[str] = []
            """Everything streamed: the text of each item, with the separators."""
            items: dict[Any, list[str]] = {}
            """Text of each agentMessage item, in the order they started streaming."""
            completed: set[Any] = set()
            visible = 0
            usage = Usage()
            ttft_ms: int | None = None
            commentary: set[Any] = set()
            last_item: Any = None
            stopped_at: float | None = None
            """When the call interrupted the turn at the output budget."""
            while True:
                if stopped_at is None:
                    event = await _until(deadline, events.get())
                    if event is None:
                        raise ProcessGone("codex app-server exited during the turn")
                else:
                    # Stopped at the budget: wait a little for the turn to end (and for
                    # the usage Codex reports), but the text is already final.
                    try:
                        event = await _until(
                            min(deadline, stopped_at + CLEANUP_TIMEOUT), events.get()
                        )
                    except TimeoutError:
                        event = None
                    if event is None:
                        yield self._truncated(parts, usage, model, started, ttft_ms)
                        return
                method, params = event
                if params.get("turnId", turn_id) != turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    item_id = params.get("itemId")
                    if (
                        not isinstance(delta, str)
                        or not delta
                        or item_id in commentary
                        or stopped_at is not None
                    ):
                        continue
                    if parts and item_id != last_item:
                        # A second assistant message in the same turn: keep them apart.
                        parts.append("\n\n")
                        yield TextDelta("\n\n")
                    last_item = item_id
                    if ttft_ms is None:
                        ttft_ms = int((time.monotonic() - started) * 1000)
                    parts.append(delta)
                    items.setdefault(item_id, []).append(delta)
                    visible += len(delta)
                    yield TextDelta(delta)
                    if tokens_for_chars(visible) > request.max_output_tokens:
                        stopped_at = time.monotonic()
                        await self._interrupt(conn, thread_id, turn_id)
                elif method in ("item/started", "item/completed"):
                    item = _as_dict(params.get("item"))
                    if item.get("type") == "agentMessage":
                        if item.get("phase") == "commentary":
                            commentary.add(item.get("id"))
                        elif method == "item/completed":
                            completed.add(item.get("id"))
                    run = sub_agent_run(item)
                    if run is not None:
                        conn.tainted = "ChatGPT started sub-agents in it"
                        sub_agent_threads.update(run[1])
                        if run[0] not in sub_agent_runs:
                            sub_agent_runs.add(run[0])
                            logger.warning(
                                "ChatGPT started sub-agent run %d of a call (thread %s)",
                                len(sub_agent_runs),
                                ", ".join(run[1]) or "?",
                            )
                        if len(sub_agent_runs) > MAX_SUB_AGENT_RUNS:
                            # Each interrupted sub-agent frees its slot: an injected loop
                            # would start dozens of threads in a single call.
                            raise ProviderError(SUB_AGENT_LIMIT_MESSAGE, kind="invalid")
                elif method == "thread/tokenUsage/updated":
                    # A fresh single-turn thread: its running total is exactly this turn,
                    # including every model request the turn made.
                    usage = usage_from_breakdown(_as_dict(params.get("tokenUsage")).get("total"))
                elif method == "model/rerouted":
                    if isinstance(params.get("toModel"), str):
                        model = params["toModel"]
                elif method == "error":
                    if stopped_at is not None:
                        continue  # the turn is being stopped: its text is final
                    if not params.get("willRetry"):
                        raise turn_error(params.get("error"))
                    if parts:
                        # Codex samples the whole answer again in a new item, and what
                        # was streamed cannot be taken back: fail instead of repeating.
                        raise ProviderError(INTERRUPTED_MESSAGE, kind="unavailable", retryable=True)
                elif method == "turn/completed":
                    turn = _as_dict(params.get("turn"))
                    if turn.get("id", turn_id) != turn_id:
                        continue
                    turn_done = True
                    status = turn.get("status")
                    if stopped_at is not None:  # our own interrupt, at the output budget
                        self._failures = 0
                        yield self._truncated(parts, usage, model, started, ttft_ms)
                        return
                    if status == "completed":
                        self._failures = 0
                        yield GenerationResult(
                            text="\n\n".join(
                                "".join(text) for item, text in items.items() if item in completed
                            ),
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
                # No one else may await the call's requests: the release can be cancelled
                # by aclose() even before it starts. Their exceptions are expected.
                for task in (thread_task, turn_task):
                    if task is not None:
                        task.add_done_callback(_retrieved)
                self._releasing += 1
                release = self._spawn(
                    self._release_thread(
                        conn,
                        thread_task,
                        None if turn_done else turn_task,
                        tuple(sorted(sub_agent_threads)),
                    )
                )
                if release is None:
                    self._releasing -= 1
                else:  # also when it is cancelled before it starts
                    release.add_done_callback(lambda _: self._released(conn))

    @staticmethod
    def _truncated(
        parts: Sequence[str], usage: Usage, model: object, started: float, ttft_ms: int | None
    ) -> GenerationResult:
        """The result of a turn stopped at the output budget: everything streamed so far,
        with the usage Codex reported (never an estimate of the text)."""
        return GenerationResult(
            text="".join(parts),
            usage=usage,
            model=str(model),
            latency_ms=int((time.monotonic() - started) * 1000),
            ttft_ms=ttft_ms,
            truncated=True,
            finish_reason="max_tokens",
        )

    @staticmethod
    async def _interrupt(conn: _AppServerConnection, thread_id: str, turn_id: str) -> None:
        """Stop a turn at the output budget (best effort: it may be over or the process
        gone; the release of the thread interrupts it again if it never ends)."""
        with contextlib.suppress(CodexRpcError, TimeoutError, ProcessGone):
            await conn.request(
                "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, CLEANUP_TIMEOUT
            )

    async def _named_images(self, attachments: Sequence[Attachment]) -> dict[str, Path]:
        """Links with the right extension to the call's images (:func:`named_image`), by
        SHA-256, in a private directory outside Codex's working directory."""
        images = [attachment for attachment in attachments if attachment.kind == "image"]
        if not images:
            return {}

        def make() -> dict[str, Path]:
            directory = _private_dir(self._settings.data_dir / "codex-images")
            return {image.sha256: named_image(image, directory) for image in images}

        return await asyncio.to_thread(make)

    def _requested_model(self, request: GenerationRequest) -> str | None:
        if request.model:
            return request.model
        if request.fast:
            return self.fast_model
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
        sub_agents: Sequence[str] = (),
    ) -> None:
        """Interrupt an unfinished turn and unsubscribe its thread and the sub-agent
        threads it started (in the background, so a cancelled call returns at once).
        A server that does not answer is restarted, and so is one that ran sub-agents,
        once no call uses it (see :meth:`_released`)."""
        await self._release(conn, thread_task, turn_task, sub_agents)

    def _released(self, conn: _AppServerConnection) -> None:
        """Done callback of a release, however it ended."""
        self._releasing -= 1
        self._recycle_if_idle(conn)

    async def _release(
        self,
        conn: _AppServerConnection,
        thread_task: asyncio.Task[Any],
        turn_task: asyncio.Task[Any] | None,
        sub_agents: Sequence[str],
    ) -> None:
        # Bounded: the call's own requests end by themselves (their timeout covers the
        # write too), so one still running after this long means the process is stuck.
        requests = [task for task in (thread_task, turn_task) if task is not None]
        _, running = await asyncio.wait(requests, timeout=CONTROL_TIMEOUT + CLEANUP_TIMEOUT)
        thread_id = _as_dict(_as_dict(_outcome(thread_task)).get("thread")).get("id")
        try:
            if running:
                raise TimeoutError("codex app-server did not answer the call's requests")
            if _timed_out(thread_task):
                # No thread/start answer leaves no thread to release, so nothing else
                # would find out whether the process still answers. A cheap request
                # tells a stuck process (replaced now) from a busy one: that one keeps
                # serving its other calls and is replaced once idle, since the thread
                # may still be created and could never be released.
                with contextlib.suppress(CodexRpcError):  # an error is an answer too
                    await conn.request("config/read", {}, CLEANUP_TIMEOUT)
                logger.warning("Codex answered too late; its app-server is replaced once idle")
                conn.tainted = "a thread/start was answered too late"
                return
            if not isinstance(thread_id, str):
                return
            if turn_task is not None:
                turn_id = _as_dict(_as_dict(_outcome(turn_task)).get("turn")).get("id")
                if isinstance(turn_id, str):
                    with contextlib.suppress(CodexRpcError):
                        await conn.request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": turn_id},
                            CLEANUP_TIMEOUT,
                        )
            # The call's own thread first: once its turn is over no new sub-agent run can
            # start. Their turns were interrupted as they showed up (_on_stray_activity).
            for released in (thread_id, *sub_agents):
                with contextlib.suppress(CodexRpcError):
                    await conn.request(
                        "thread/unsubscribe", {"threadId": released}, CLEANUP_TIMEOUT
                    )
        except TimeoutError:
            logger.warning("Codex app-server stopped answering; restarting it")
            self._retire(conn)
        except ProcessGone:
            pass
        finally:
            if isinstance(thread_id, str):
                self._threads.discard(thread_id)

    def _recycle_if_idle(self, conn: _AppServerConnection) -> None:
        """Replace a tainted process once no call uses it. Sub-agent threads, even
        interrupted and unsubscribed, still hold memory (~2 MB each) that 0.157.1 never
        gives back, and a turn that starts after the last check would go unnoticed; a
        thread whose thread/start was answered too late is never released at all."""
        idle = self._active_calls == 0 and self._releasing == 0
        if conn.tainted and idle and self._conn is conn and not self._closed:
            logger.warning("Restarting the Codex app-server (pid %d): %s", conn.pid, conn.tainted)
            self._retire(conn)

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
            try:
                # Shielded: a cancelled caller must not abort a start other calls wait for.
                await asyncio.shield(self._starting)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if self._closed and current is not None and not current.cancelling():
                    # The start was cancelled by aclose(), not this caller.
                    raise ProviderError(
                        "El proveïdor de Codex està tancat.", kind="unavailable"
                    ) from None
                raise

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
            cwd = await asyncio.to_thread(_private_dir, self._sandbox)
            state_dir = await asyncio.to_thread(_private_dir, self._state_dir)
        except OSError as exc:
            raise ProviderError(
                f"No s'han pogut preparar els directoris de Codex: {exc}",
                kind="unavailable",
            ) from None
        self._cwd = cwd
        await self._wait_dying()
        try:
            removed = await asyncio.to_thread(remove_log_databases, state_dir)
        except OSError as exc:
            logger.warning("Could not delete the Codex log databases in %s: %s", state_dir, exc)
        else:
            if removed:
                logger.info("Deleted the Codex log databases %s", ", ".join(removed))
        try:
            process = await asyncio.create_subprocess_exec(
                cli,
                "app-server",
                *config_arguments(state_dir),
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
            await conn.notify("initialized", timeout=INITIALIZE_TIMEOUT)
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

    async def _wait_dying(self) -> None:
        """Wait (bounded) until the previous processes are gone: a process still shutting
        down may write, checkpoint or unlink the log databases a new start deletes."""
        dying = list(self._dying)
        if dying:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*(conn.wait_terminated() for conn in dying)),
                    SHUTDOWN_GRACE + 2.0,
                )
        self._dying.difference_update(dying)

    def _on_exit(self, conn: _AppServerConnection, expected: bool) -> None:
        self._dying.add(conn)
        if self._conn is conn:
            self._conn = None
            self._stray_turns.clear()
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
        # The exponent is capped before the power, which overflows (OverflowError) from
        # 1026 failures in a row on, long after the delay has reached BACKOFF_MAX.
        exponent = min(self._failures - 2, math.ceil(math.log2(BACKOFF_MAX / BACKOFF_BASE)))
        delay = min(BACKOFF_MAX, BACKOFF_BASE * 2.0**exponent)
        return max(0.0, self._last_failure + delay - time.monotonic())

    def _retire(self, conn: _AppServerConnection) -> None:
        """Detach ``conn`` so the next call starts a fresh process, and close it."""
        if self._conn is conn:
            self._conn = None
            self._stray_turns.clear()
        self._spawn(conn.close())

    def _spawn(self, coroutine: Awaitable[None]) -> asyncio.Future[None] | None:
        """Run ``coroutine`` in the background (cancelled by :meth:`aclose`); None when
        there is no running loop (interpreter shutdown)."""
        try:
            task = asyncio.ensure_future(coroutine)
        except RuntimeError:
            if asyncio.iscoroutine(coroutine):
                coroutine.close()
            return None
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def _on_notification(self, conn: _AppServerConnection, method: str, params: JsonObject) -> None:
        """Notifications no call listens to: account-wide ones, and the activity of
        threads that no running call owns."""
        thread_id = params.get("threadId")
        if isinstance(thread_id, str):
            self._on_stray_activity(conn, method, thread_id, params)
        elif method == "account/rateLimits/updated":
            self._merge_rate_limits(params.get("rateLimits"), sparse=True)
        elif method == "account/updated":
            self._status_cache = None
        elif method in ("configWarning", "warning", "deprecationNotice"):
            logger.info("Codex app-server %s: %s", method, params.get("summary") or params)

    def _on_stray_activity(
        self, conn: _AppServerConnection, method: str, thread_id: str, params: JsonObject
    ) -> None:
        """Interrupt a turn on a thread that no call owns, and count nothing of it.

        Such a turn is a sub-agent that ChatGPT started (a prompt injection can ask for
        one): nobody sees its output and it would go on spending the owner's plan after
        the call ends or is stopped. Threads of our own calls that are being released
        are left to :meth:`_release_thread`, and a process being stopped is left alone."""
        if thread_id in self._threads or conn is not self._conn:
            return
        turn_id = params.get("turnId") or _as_dict(params.get("turn")).get("id")
        if not isinstance(turn_id, str) or not turn_id:
            return
        if method == "turn/completed":
            self._stray_turns.discard(turn_id)
            return
        if turn_id in self._stray_turns:
            return
        self._stray_turns.add(turn_id)
        conn.tainted = "ChatGPT started sub-agents in it"
        logger.warning(
            "Codex is running a turn outside any call (thread %s, turn %s), probably a "
            "sub-agent started by the model; interrupting it",
            thread_id,
            turn_id,
        )
        self._spawn(self._interrupt_stray(conn, thread_id, turn_id))

    async def _interrupt_stray(
        self, conn: _AppServerConnection, thread_id: str, turn_id: str
    ) -> None:
        try:
            await conn.request(
                "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, CLEANUP_TIMEOUT
            )
        except CodexRpcError as exc:
            # Not retried: a turn that cannot be interrupted must not flood the server.
            # The process still hosted a sub-agent, so it is replaced when idle.
            logger.warning("Could not interrupt Codex turn %s: %s", turn_id, exc.message)
            self._recycle_if_idle(conn)
        except TimeoutError:
            logger.warning("Codex app-server stopped answering; restarting it")
            self._retire(conn)
        except ProcessGone:
            pass
        else:
            # A sub-agent that showed up after its call had ended.
            self._recycle_if_idle(conn)

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
                self._limits_at = time.monotonic()
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
        """Account state (cached ~60 s) with the latest subscription usage windows."""
        cached = self._status_cache
        if cached is None or time.monotonic() >= cached[0]:
            async with self._status_lock:
                cached = self._status_cache
                if cached is None or time.monotonic() >= cached[0]:
                    return await self._refresh_status()
        return replace(cached[1], limits=self._limits())

    async def _refresh_status(self) -> ProviderStatus:
        """Read the account and cache the status before the slower (networked) usage
        read, so a caller that gives up early still leaves a status behind. The model is
        the one a thread gets, or "" while Codex cannot say which: never a guess, since
        the engine keys its turn cache on it."""
        model = self._settings.chatgpt_model or self._default_model or ""

        def remember(available: bool, detail: str, ttl: float) -> ProviderStatus:
            status = ProviderStatus(
                agent="chatgpt",
                mode="cli",
                available=available,
                model=model,
                detail=detail,
                limits=self._limits(),
            )
            self._status_cache = (time.monotonic() + ttl, status)
            return status

        try:
            conn = await self._connection(wait_backoff=False)
            answer = _as_dict(
                await conn.request("account/read", {"refreshToken": False}, STATUS_REQUEST_TIMEOUT)
            )
        except ProviderError as exc:
            return remember(False, exc.message, STATUS_TTL_UNAVAILABLE)
        except (TimeoutError, ProcessGone, CodexRpcError):
            logger.warning("Could not read the Codex account", exc_info=True)
            return remember(False, "No s'ha pogut llegir l'estat de Codex.", STATUS_TTL_UNAVAILABLE)

        account = answer.get("account")
        subscription = False
        if not isinstance(account, dict):
            if answer.get("requiresOpenaiAuth") is not False:
                # A fresh process re-reads auth.json, so a later `codex login` is picked up.
                if self._active_calls == 0:
                    self._retire(conn)
                return remember(False, f"Sense sessió: {LOGIN_HINT}", STATUS_TTL_UNAVAILABLE)
            detail = "Codex amb un proveïdor de models propi"
        elif account.get("type") == "chatgpt":
            subscription = True
            plan = account.get("planType")
            label = _PLAN_LABELS.get(plan, str(plan).replace("_", " ").title()) if plan else ""
            detail = (
                f"Subscripció ChatGPT activa ({label})" if label else "Subscripció ChatGPT activa"
            )
        elif account.get("type") == "apiKey":
            detail = "Codex amb clau d'API (es factura per ús)"
        else:
            detail = "Codex amb un compte extern"

        if not self._settings.chatgpt_model and self._default_model is None:
            model = await self._read_default_model(conn) or model
        status = remember(True, detail, STATUS_TTL)
        if subscription and time.monotonic() - self._limits_at >= STATUS_TTL:
            with contextlib.suppress(TimeoutError, ProcessGone, CodexRpcError):
                limits = await conn.request("account/rateLimits/read", None, STATUS_REQUEST_TIMEOUT)
                self._merge_rate_limits(_as_dict(limits).get("rateLimits"), sparse=False)
        return replace(status, limits=self._limits())

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

    @property
    def models_live(self) -> bool:
        """Whether the latest :meth:`list_models` came from the app-server catalog."""
        return self._models_cache is not None and self._models_cache[2]

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        """Visible models of the app-server catalog (``model/list``, cached ~10 min), or
        only the configured default when Codex cannot be queried."""
        async with self._models_lock:
            cached = self._models_cache
            if refresh or cached is None or time.monotonic() >= cached[0]:
                models, live = await self._fetch_models()
                ttl = MODELS_TTL if live else STATUS_TTL_UNAVAILABLE
                cached = self._models_cache = (time.monotonic() + ttl, models, live)
        return cached[1]

    async def _fetch_models(self) -> tuple[tuple[ModelInfo, ...], bool]:
        entries: list[JsonObject] = []
        try:
            conn = await self._connection(wait_backoff=False)
            if not self._settings.chatgpt_model and self._default_model is None:
                await self._read_default_model(conn)
            cursor: str | None = None
            for _ in range(MAX_MODEL_PAGES):
                params: JsonObject = {"limit": 100, "includeHidden": False}
                if cursor:
                    params["cursor"] = cursor
                page = _as_dict(await conn.request("model/list", params, STATUS_REQUEST_TIMEOUT))
                entries += [_as_dict(entry) for entry in page.get("data") or []]
                next_cursor = page.get("nextCursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    break
                cursor = next_cursor
        except Exception as exc:  # never raise: the owner can still type any model id
            reason = exc.message if isinstance(exc, ProviderError) else type(exc).__name__
            logger.warning("Could not list the Codex models (%s); using the default", reason)
        models: list[ModelInfo] = []
        catalog_default: str | None = None
        for entry in entries:
            slug = entry.get("model") or entry.get("id")
            if entry.get("hidden") is True or not isinstance(slug, str) or not slug:
                continue
            if entry.get("isDefault") is True:
                catalog_default = catalog_default or slug
            label, description = entry.get("displayName"), entry.get("description")
            models.append(
                ModelInfo(
                    id=slug,
                    label=label if isinstance(label, str) and label else slug,
                    description=MODEL_DESCRIPTIONS.get(slug)
                    or (description if isinstance(description, str) else ""),
                )
            )
        default = (
            self._settings.chatgpt_model
            or self._default_model
            or catalog_default
            or FALLBACK_MODEL_LABEL
        )
        if not any(model.id == default for model in models):
            models.insert(0, ModelInfo(default, default, MODEL_DESCRIPTIONS.get(default, "")))
        marked = tuple(replace(model, is_default=model.id == default) for model in models)
        return marked, bool(entries)

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
