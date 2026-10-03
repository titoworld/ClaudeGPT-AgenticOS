"""ClaudeCliProvider against a fake ``claude`` executable (no network, no real CLI).

The fake (fixtures/claude/fake_claude.py) replays stream-json recorded from the real
CLI and records what it received in ``<bin>/calls/<pid>.json``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from orchestrator.attachment_fixtures import HOSTILE_TEXT, AttachmentFiles, reserved_tags
from portability import alive, reaped, without_platform_variables

from agentic_os import i18n
from agentic_os.config import Settings
from agentic_os.domain import DebateOptions, Purpose, TurnOptions, Usage
from agentic_os.orchestrator.engine import Engine
from agentic_os.orchestrator.events import StreamFailed, TurnCompleted, TurnFailed
from agentic_os.orchestrator.memory_store import InMemoryStore
from agentic_os.orchestrator.types import TurnRequest
from agentic_os.providers import claude_cli
from agentic_os.providers.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    Attachment,
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
    RefusalError,
    TextDelta,
    UsageLimit,
)
from agentic_os.providers.claude_cli import (
    ENV_ALLOW_LIST,
    ClaudeCliProvider,
    parse_limits,
    redact,
)
from agentic_os.providers.fake import FakeProvider
from agentic_os.providers.prompt_format import file_code, render_transcript

FAKE = Path(__file__).parent / "fixtures" / "claude" / "fake_claude.py"
SYSTEM = "Ets Claude. Respon en català."


class FakeCli:
    """The fake executable as ``<dir>/claude`` plus its scenario and call records."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.path = directory / "claude"
        self.path.symlink_to(FAKE)

    def scenario(self, **values: Any) -> None:
        (self.dir / "scenario.json").write_text(json.dumps(values))

    def calls(self) -> list[dict[str, Any]]:
        calls_dir = self.dir / "calls"
        if not calls_dir.exists():
            return []
        records = [json.loads(p.read_text()) for p in calls_dir.glob("*.json")]
        return sorted(records, key=lambda r: r["pid"])

    def runs(self) -> list[dict[str, Any]]:
        """Records of ``-p`` processes (not ``auth status``)."""
        return [r for r in self.calls() if r["phase"] != "status"]

    async def wait(
        self, predicate: Callable[[list[dict[str, Any]]], bool], timeout: float = 5.0
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while True:
            runs = self.runs()
            if predicate(runs):
                return runs
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting for the fake CLI: {runs}")
            await asyncio.sleep(0.02)

    async def wait_count(self, count: int) -> list[dict[str, Any]]:
        return await self.wait(lambda runs: len(runs) == count)


async def eventually(check: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@pytest.fixture
def fake(tmp_path: Path) -> FakeCli:
    directory = tmp_path / "bin"
    directory.mkdir()
    return FakeCli(directory)


def make_settings(tmp_path: Path, fake: FakeCli, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path / "data",
        "claude_cli_path": str(fake.path),
        "claude_model": None,
        "claude_fast_model": None,
        "provider_timeout_seconds": 10.0,
        **overrides,
    }
    return Settings(**values)


@pytest.fixture
async def provider(tmp_path: Path, fake: FakeCli) -> AsyncIterator[ClaudeCliProvider]:
    instance = ClaudeCliProvider(make_settings(tmp_path, fake))
    yield instance
    await instance.aclose()


def request(
    purpose: Purpose = "answer", prompt: str = "Hola, qui ets?", **overrides: Any
) -> GenerationRequest:
    return GenerationRequest(system=SYSTEM, prompt=prompt, purpose=purpose, **overrides)


async def collect(
    provider: ClaudeCliProvider, req: GenerationRequest
) -> tuple[list[str], GenerationResult]:
    deltas: list[str] = []
    result: GenerationResult | None = None
    async for event in provider.stream(req):
        if isinstance(event, TextDelta):
            deltas.append(event.text)
        else:
            result = event
    assert result is not None
    return deltas, result


async def expect_error(provider: ClaudeCliProvider, req: GenerationRequest) -> ProviderError:
    with pytest.raises(ProviderError) as info:
        await collect(provider, req)
    return info.value


# -- streaming -------------------------------------------------------------------------


async def test_is_a_provider(provider: ClaudeCliProvider) -> None:
    assert isinstance(provider, Provider)
    assert (provider.agent, provider.mode) == ("claude", "cli")


async def test_streams_text_usage_and_limits(
    tmp_path: Path, fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    req = request(
        "summary",
        fast=True,
        history=(ChatTurn("user", "Què és uv?"), ChatTurn("assistant", "Un gestor.", "chatgpt")),
        context_summary="Parlàvem de Python.",
    )
    deltas, result = await collect(provider, req)

    assert deltas == ["Hola", "! Soc", " Claude", "."]  # thinking deltas are ignored
    assert result.text == "Hola! Soc Claude."
    assert result.model == "claude-haiku-4-5-20251001"
    # Per-turn result.usage; the cumulative list-price cost is not reported.
    assert result.usage == Usage(
        input_tokens=926, output_tokens=48, reasoning_tokens=42, cost_usd=None
    )
    assert result.ttft_ms is not None and 0 <= result.ttft_ms <= result.latency_ms

    limits = {limit.window: limit for limit in (await provider.status()).limits}
    assert limits["5h"] == UsageLimit(
        "5h", 1.0, datetime.fromtimestamp(1790549400, tz=UTC), "allowed"
    )
    assert limits["7d"].used_percent == 34.0

    (run,) = await fake.wait(lambda runs: len(runs) == 1 and runs[0]["phase"] == "exited")
    argv = run["argv"]
    assert "--model=haiku" in argv and f"--system-prompt={SYSTEM}" in argv
    assert argv[argv.index("--thinking") + 1] == "disabled"
    assert "--effort" not in argv
    assert run["stdin"]["message"]["content"] == render_transcript(req)
    sandbox = tmp_path / "data" / "sandbox" / "claude"
    assert Path(run["cwd"]) == sandbox.absolute()
    assert run["cwd_entries"] == [] and run["cwd_mode"] == 0o700
    assert run["sid"] == run["pid"]  # own session and process group

    await provider.aclose()
    assert reaped(run["pid"])


@pytest.mark.parametrize(
    ("purpose", "effort"),
    [("answer", "high"), ("revision", "medium"), ("synthesis", "high"), ("summary", "low")],
)
async def test_default_model_and_effort_per_purpose(
    fake: FakeCli, provider: ClaudeCliProvider, purpose: Purpose, effort: str
) -> None:
    await collect(provider, request(purpose))
    (run,) = fake.runs()
    argv = run["argv"]
    assert "--model=opus" in argv
    assert argv[argv.index("--effort") + 1] == effort
    assert "--thinking" not in argv


async def test_model_overrides(tmp_path: Path, fake: FakeCli) -> None:
    settings = make_settings(tmp_path, fake, claude_model="sonnet", claude_fast_model="haiku")
    provider = ClaudeCliProvider(settings)
    try:
        await collect(provider, request())
        await collect(provider, request(model="claude-opus-5-5"))
        await collect(provider, request(fast=True))
        models = [next(a for a in run["argv"] if a.startswith("--model=")) for run in fake.runs()]
        assert models == ["--model=sonnet", "--model=claude-opus-5-5", "--model=haiku"]
        assert (await provider.status()).model == "sonnet"
    finally:
        await provider.aclose()


async def test_list_models_offers_the_aliases_and_what_they_resolved_to(
    provider: ClaudeCliProvider,
) -> None:
    models = await provider.list_models()
    assert provider.models_live and provider.fast_model == "haiku"
    assert [(m.id, m.label, m.is_default) for m in models] == [
        ("opus", "Claude Opus", True),
        ("sonnet", "Claude Sonnet", False),
        ("haiku", "Claude Haiku", False),
        ("fable", "Claude Fable", False),
    ]
    assert all(str(m.description).startswith("Sempre la versió més nova.") for m in models)
    assert not any("Ara:" in str(m.description) for m in models)

    # The recorded system/init resolves "haiku" to a concrete model.
    await collect(provider, request(fast=True))
    haiku = next(m for m in await provider.list_models() if m.id == "haiku")
    assert str(haiku.description).endswith("Ara: claude-haiku-4-5-20251001")


async def test_list_models_with_a_configured_full_id(tmp_path: Path, fake: FakeCli) -> None:
    provider = ClaudeCliProvider(make_settings(tmp_path, fake, claude_model="claude-opus-5-5"))
    models = await provider.list_models()
    await provider.aclose()
    assert [(m.id, m.is_default) for m in models][:2] == [
        ("claude-opus-5-5", True),
        ("opus", False),
    ]
    assert str(models[0].description) == "Raonament profund i tasques llargues."


async def test_environment_is_allow_listed(
    monkeypatch: pytest.MonkeyPatch, fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    secrets = {
        "ANTHROPIC_API_KEY": "sk-ant-api03-leak",
        "ANTHROPIC_AUTH_TOKEN": "leak",
        "ANTHROPIC_BASE_URL": "https://evil.example",
        "AOS_ANTHROPIC_API_KEY": "sk-ant-api03-leak",
        "AOS_SESSION_SECRET": "leak",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_SESSION_ID": "parent-session",
        "OPENAI_API_KEY": "leak",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token-for-the-cli")
    await collect(provider, request())

    (run,) = fake.runs()
    env: dict[str, str] = run["env"]
    assert not set(secrets) & set(env)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token-for-the-cli"
    assert env["DISABLE_AUTOUPDATER"] == "1"
    assert env["PATH"] == os.environ["PATH"]
    # Computed by the provider (the request's budget), never inherited.
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(DEFAULT_MAX_OUTPUT_TOKENS)
    # The system may add a few variables itself: LC_CTYPE (the fake's own Python, PEP
    # 538), and __CF_*/VERSIONER_* on macOS.
    computed = {"DISABLE_AUTOUPDATER", "CLAUDE_CODE_MAX_OUTPUT_TOKENS"}
    assert without_platform_variables(env) <= {*ENV_ALLOW_LIST, *computed}


async def test_result_text_is_used_when_no_delta_arrives(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(stream="stream_auth_error.jsonl", result_override={"is_error": False})
    deltas, result = await collect(provider, request())
    assert deltas == [result.text]
    assert result.text.startswith("Failed to authenticate")


# -- errors ------------------------------------------------------------------------------


async def test_auth_error_even_with_subtype_success(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(stream="stream_auth_error.jsonl")
    error = await expect_error(provider, request())
    assert error.kind == "auth" and not error.retryable
    # The token only works once it is in .env (N25): the hint says so.
    assert "«claude setup-token» a CLAUDE_CODE_OAUTH_TOKEN (.env)" in error.message


async def test_rejected_rate_limit(fake: FakeCli, provider: ClaudeCliProvider) -> None:
    fake.scenario(stream="stream_rate_limited.jsonl")
    error = await expect_error(provider, request())
    assert error.kind == "rate_limit"
    assert "27/09 a les 19:48 UTC" in error.message
    limits = {limit.window: limit for limit in (await provider.status()).limits}
    assert limits["5h"].status == "rejected" and limits["5h"].used_percent == 100.0
    assert limits["7d"].status == "allowed"


@pytest.mark.parametrize(
    ("lang", "message"),
    [
        (
            "en",
            "The Claude subscription has reached its usage limit. It resets on 27/09 at 19:48 UTC.",
        ),
        (
            "es",
            "Se ha alcanzado el límite de uso de la suscripción de Claude. "
            "Se restablece el 27/09 a las 19:48 UTC.",
        ),
        (
            "ca",
            "S'ha arribat al límit d'ús de la subscripció de Claude. "
            "Es restableix el 27/09 a les 19:48 UTC.",
        ),
    ],
)
async def test_the_usage_limit_is_told_in_the_language_of_the_turn(
    lang: i18n.Lang, message: str, fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    """The turn's task carries the language of the connection that started it."""
    fake.scenario(stream="stream_rate_limited.jsonl")
    with i18n.use(lang):
        error = await expect_error(provider, request())
    assert (error.kind, error.message) == ("rate_limit", message)


async def test_overloaded_is_retryable(fake: FakeCli, provider: ClaudeCliProvider) -> None:
    fake.scenario(
        result_override={
            "is_error": True,
            "api_error_status": 529,
            "result": "API Error: 529 Overloaded",
        }
    )
    error = await expect_error(provider, request())
    assert error.kind == "unavailable" and error.retryable


async def test_crash_without_result_reports_redacted_stderr(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(
        action="crash",
        stderr="boom: Authorization: Bearer abc.DEF-123 key=sk-ant-oat01-SECRET_value\n",
        exit_code=3,
    )
    error = await expect_error(provider, request())
    assert error.kind == "unavailable" and error.retryable
    assert "codi 3" in error.message and "boom" in error.message
    assert "SECRET" not in error.message and "abc.DEF" not in error.message
    assert "Bearer ***" in error.message and "sk-ant-***" in error.message


TOKEN_BODY = "Zx9" * 30
"""The body of a token: without its "Bearer "/"sk-ant-" prefix nothing hides it."""


async def test_a_secret_cut_by_the_edge_of_the_stderr_tail_is_hidden(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    stderr = "API Error: 401 Authorization: Bearer sk-ant-oat01-" + TOKEN_BODY + "\n" + "q" * 300
    # The premise: the last 400 characters start inside the token's prefix.
    assert stderr[-400:].startswith("nt-oat01-" + TOKEN_BODY)
    fake.scenario(action="crash", stderr=stderr + "\n", exit_code=3)
    error = await expect_error(provider, request())
    assert "codi 3" in error.message
    assert "Zx9" not in error.message
    assert "Bearer ***" in error.message and error.message.endswith("q" * 300)


async def test_a_secret_cut_by_the_edge_of_the_stderr_buffer_is_hidden(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    # Only the last STDERR_KEEP bytes are kept: here they start inside the token and,
    # after it, there is only blank space.
    stderr = "boom: Bearer sk-ant-oat01-" + TOKEN_BODY + " " * (claude_cli.STDERR_KEEP - 40)
    fake.scenario(action="crash", stderr=stderr + "\n", exit_code=3)
    error = await expect_error(provider, request())
    assert "codi 3" in error.message
    assert "Zx9" not in error.message


@pytest.mark.parametrize(
    ("dropped", "kept"),
    [
        # "Bearer" ends the partial first line that the cut leaves, and its token is on
        # the next line; the last 400 characters start inside the token too.
        ("y" * 50, "y" * 50 + " Bearer\n" + TOKEN_BODY + "\n" + "q" * 350),
        # The cut falls inside "Bearer": only "arer" is left before the token's line.
        ("boom: Be", "arer\n" + TOKEN_BODY),
        # The cut falls right after "Bearer", before a blank line and the token.
        ("boom: Bearer", "\n\n  " + TOKEN_BODY),
    ],
    ids=["bearer-ends-the-cut-line", "cut-inside-bearer", "cut-after-bearer"],
)
async def test_a_token_on_the_line_after_its_bearer_is_hidden_when_stderr_is_cut(
    fake: FakeCli, provider: ClaudeCliProvider, dropped: str, kept: str
) -> None:
    # The "\s+" after "Bearer" spans lines. Only the last STDERR_KEEP bytes of stderr
    # are kept: `kept`, padded with blank space to exactly that many, so the cut falls
    # right between `dropped` and `kept`.
    kept += " " * (claude_cli.STDERR_KEEP - len(kept) - 1) + "\n"
    assert len(kept.encode()) == claude_cli.STDERR_KEEP
    fake.scenario(action="crash", stderr=dropped + kept, exit_code=3)
    error = await expect_error(provider, request())
    assert "codi 3" in error.message
    assert "Zx9" not in error.message and "***" in error.message


async def test_invalid_flags_would_crash(fake: FakeCli, provider: ClaudeCliProvider) -> None:
    # The fake refuses any forbidden or missing flag: guard against the fake itself.
    fake.scenario()
    await collect(provider, request())
    (run,) = fake.runs()
    assert not any(a.startswith(("--append-system-prompt", "--bare")) for a in run["argv"])


async def test_missing_cli(tmp_path: Path, fake: FakeCli) -> None:
    settings = make_settings(tmp_path, fake, claude_cli_path=str(tmp_path / "nope" / "claude"))
    provider = ClaudeCliProvider(settings)
    error = await expect_error(provider, request())
    assert error.kind == "unavailable" and "no trobada" in error.message
    await provider.prewarm(request())  # never raises
    status = await provider.status()
    assert not status.available and str(status.detail) == "CLI de Claude no trobada"
    await provider.aclose()


async def test_system_prompt_too_long(provider: ClaudeCliProvider, fake: FakeCli) -> None:
    error = await expect_error(provider, GenerationRequest(system="x" * 200_000, prompt="Hola"))
    assert error.kind == "invalid"
    assert fake.runs() == []


# -- timeouts and cancellation -------------------------------------------------------------


async def test_timeout_kills_the_process_group(tmp_path: Path, fake: FakeCli) -> None:
    fake.scenario(action="hang", spawn_child=True)
    provider = ClaudeCliProvider(make_settings(tmp_path, fake, provider_timeout_seconds=0.5))
    error = await expect_error(provider, request())
    assert error.kind == "timeout"
    (run,) = fake.runs()
    assert reaped(run["pid"])
    await eventually(lambda: not alive(run["child_pid"]))
    await provider.aclose()


async def test_cancellation_kills_the_process_group(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(action="hang", spawn_child=True)
    task = asyncio.create_task(collect(provider, request()))
    (run,) = await fake.wait(lambda runs: len(runs) == 1 and runs[0]["phase"] == "input")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reaped(run["pid"])
    await eventually(lambda: not alive(run["child_pid"]))


async def test_sigterm_is_escalated_to_sigkill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeCli
) -> None:
    monkeypatch.setattr(claude_cli, "KILL_GRACE_SECONDS", 0.2)
    fake.scenario(action="hang", ignore_sigterm=True)
    provider = ClaudeCliProvider(make_settings(tmp_path, fake, provider_timeout_seconds=0.3))
    error = await expect_error(provider, request())
    assert error.kind == "timeout"
    (run,) = fake.runs()
    assert reaped(run["pid"])
    await provider.aclose()


async def test_consumer_closing_early_kills_the_process(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(delay=0.2)
    stream = provider.stream(request())
    assert isinstance(stream, AsyncGenerator)
    assert await anext(stream) == TextDelta("Hola")
    await stream.aclose()
    (run,) = fake.runs()
    assert reaped(run["pid"])


# -- warm processes -------------------------------------------------------------------------


async def test_prewarm_is_reused(fake: FakeCli, provider: ClaudeCliProvider) -> None:
    await provider.prewarm(request("revision", prompt=""))
    await provider.prewarm(request("revision", prompt=""))  # same key: no second process
    (warm,) = await fake.wait(lambda runs: len(runs) == 1)
    assert warm["phase"] == "started"

    _, result = await collect(provider, request("revision"))
    assert result.text == "Hola! Soc Claude."
    (run,) = fake.runs()
    assert run["pid"] == warm["pid"] and run["phase"] in ("input", "exited")

    await collect(provider, request("answer"))  # other effort: not the same key
    assert len(fake.runs()) == 2


async def test_at_most_two_warm_processes(fake: FakeCli, provider: ClaudeCliProvider) -> None:
    purposes: tuple[Purpose, ...] = ("answer", "revision", "summary")
    for count, purpose in enumerate(purposes, start=1):
        await provider.prewarm(request(purpose))
        runs = await fake.wait_count(count)
    oldest, *others = runs
    await eventually(lambda: reaped(oldest["pid"]))
    assert all(alive(run["pid"]) for run in others)

    await provider.aclose()
    assert not any(alive(run["pid"]) for run in runs)
    await provider.prewarm(request())  # closed: ignored
    with pytest.raises(ProviderError):
        await collect(provider, request())


async def test_warm_process_expires(tmp_path: Path, fake: FakeCli) -> None:
    provider = ClaudeCliProvider(make_settings(tmp_path, fake), warm_ttl_seconds=0.2)
    try:
        await provider.prewarm(request())
        (warm,) = await fake.wait(lambda runs: len(runs) == 1)
        await eventually(lambda: reaped(warm["pid"]))
        await collect(provider, request())
        assert len(fake.runs()) == 2
    finally:
        await provider.aclose()


async def test_dead_warm_process_is_not_reused(fake: FakeCli, provider: ClaudeCliProvider) -> None:
    await provider.prewarm(request())
    (warm,) = await fake.wait_count(1)
    os.kill(warm["pid"], signal.SIGKILL)
    await eventually(lambda: reaped(warm["pid"]))
    _, result = await collect(provider, request())
    assert result.text == "Hola! Soc Claude."
    assert len(fake.runs()) == 2


WARM_STARTS = pytest.mark.parametrize(
    "startup_lines", [False, True], ids=["silent-start", "startup-lines"]
)
"""The two ways a real CLI waits on stdin: logged in to the real API, the 2.1.283 had
printed ``active_goal`` and ``autocompact_state`` within a second; with no session it
had printed nothing (see the fake's "Start-up lines")."""


async def wait_warm(fake: FakeCli, startup_lines: bool) -> dict[str, Any]:
    """The only warm process, once it waits on stdin (its start-up lines printed)."""
    phase = "startup_printed" if startup_lines else "started"
    (warm,) = await fake.wait(lambda runs: [run["phase"] for run in runs] == [phase])
    return warm


@WARM_STARTS
async def test_a_warm_process_that_died_unseen_is_replaced_by_a_fresh_one(
    fake: FakeCli, provider: ClaudeCliProvider, startup_lines: bool
) -> None:
    """N27: asyncio sets a process's return code a moment after it dies, so the pool can
    hand out a warm process that has just died. One that dies before its turn began
    never sent a request: the call goes, once and transparently, to a fresh process. Its
    start-up lines are already in the pipe and do not count as the turn."""
    fake.scenario(startup_lines=startup_lines)
    await provider.prewarm(request())
    warm = await wait_warm(fake, startup_lines)
    # No await between the kill and the call: the pool still sees the process alive.
    os.kill(warm["pid"], signal.SIGKILL)
    deltas, result = await collect(provider, request())

    assert deltas == ["Hola", "! Soc", " Claude", "."]
    assert result.text == "Hola! Soc Claude." and result.model == "claude-haiku-4-5-20251001"
    runs = {run["pid"]: run for run in fake.runs()}
    assert len(runs) == 2 and "stdin" not in runs.pop(warm["pid"])
    [fresh] = runs.values()
    assert fresh["stdin"]["message"]["content"] == render_transcript(request())
    await eventually(lambda: reaped(warm["pid"]))


@WARM_STARTS
async def test_a_warm_process_that_dies_before_answering_is_replaced_only_once(
    fake: FakeCli, provider: ClaudeCliProvider, startup_lines: bool
) -> None:
    fake.scenario(
        startup_lines=startup_lines, action="crash", crash_after=0, stderr="boom\n", exit_code=3
    )
    await provider.prewarm(request())
    await wait_warm(fake, startup_lines)
    error = await expect_error(provider, request())
    # The fresh process died the same way: its error is the call's, with no third try.
    assert error.kind == "unavailable" and error.retryable
    assert "codi 3" in error.message and "boom" in error.message
    runs = fake.runs()
    assert len(runs) == 2 and all("stdin" in run for run in runs)


@WARM_STARTS
async def test_a_process_is_not_replaced_once_its_turn_began_or_when_it_was_fresh(
    fake: FakeCli, provider: ClaudeCliProvider, startup_lines: bool
) -> None:
    # A warm process that printed its turn's system/init may have sent its request.
    # Both scenarios crash right after it: the start-up lines come first in the stream.
    crash_after = 1 if startup_lines else 3
    fake.scenario(startup_lines=startup_lines, action="crash", crash_after=crash_after, exit_code=3)
    await provider.prewarm(request())
    await wait_warm(fake, startup_lines)
    error = await expect_error(provider, request())
    assert "codi 3" in error.message
    assert len(fake.runs()) == 1
    # A fresh process that dies at once is a real crash, not a stale warm process.
    fake.scenario(startup_lines=startup_lines, action="crash", crash_after=0, exit_code=4)
    error = await expect_error(provider, request())
    assert "codi 4" in error.message
    assert len(fake.runs()) == 2


@pytest.mark.parametrize(
    "line",
    ['{"type": "system", "subtype": "init"}', '{"type": "turn_notice"}', "Loading..."],
    ids=["init", "unknown-event", "not-json"],
)
async def test_only_the_known_startup_lines_keep_a_dead_warm_process_replaceable(
    fake: FakeCli, provider: ClaudeCliProvider, line: str
) -> None:
    """Any other line may belong to a turn whose request has left (a newer CLI may print
    something new): the call fails rather than risk a second billed request."""
    fake.scenario(startup_lines=['{"type": "active_goal", "value": null}', line])
    await provider.prewarm(request())
    warm = await wait_warm(fake, startup_lines=True)
    os.kill(warm["pid"], signal.SIGKILL)
    error = await expect_error(provider, request())
    assert error.kind == "unavailable" and "codi -9" in error.message
    assert len(fake.runs()) == 1


async def test_a_warm_process_that_printed_its_startup_lines_serves_the_call(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(startup_lines=True)
    await provider.prewarm(request())
    warm = await wait_warm(fake, startup_lines=True)
    deltas, result = await collect(provider, request())
    assert deltas == ["Hola", "! Soc", " Claude", "."] and result.text == "Hola! Soc Claude."
    (run,) = fake.runs()
    assert run["pid"] == warm["pid"] and run["startup_printed"] == 2
    assert run["stdin"]["message"]["content"] == render_transcript(request())


async def test_unusable_data_dir(tmp_path: Path, fake: FakeCli) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    provider = ClaudeCliProvider(make_settings(tmp_path, fake, data_dir=blocker))
    error = await expect_error(provider, request())
    assert error.kind == "internal" and "directori" in error.message
    await provider.prewarm(request())
    assert not (await provider.status()).available
    await provider.aclose()


# -- status ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "available", "detail"),
    [
        (
            {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"},
            True,
            "Subscripció activa (max)",
        ),
        (
            {"loggedIn": True, "authMethod": "oauth_token"},
            True,
            "Subscripció activa (token OAuth)",
        ),
        (
            {"loggedIn": False, "authMethod": "none"},
            False,
            "Sense sessió: posa el token de «claude setup-token» a CLAUDE_CODE_OAUTH_TOKEN "
            "(.env) i fes «docker compose up -d», o executa «claude auth login» al servidor",
        ),
    ],
)
async def test_status(
    fake: FakeCli,
    provider: ClaudeCliProvider,
    status: dict[str, Any],
    available: bool,
    detail: str,
) -> None:
    fake.scenario(status={"json": status, "exit_code": 0 if status["loggedIn"] else 1})
    result = await provider.status()
    assert (result.agent, result.mode, result.model) == ("claude", "cli", "opus")
    assert (result.available, str(result.detail)) == (available, detail)
    assert result.limits == ()
    await provider.status()  # cached
    assert len([c for c in fake.calls() if c["phase"] == "status"]) == 1


@pytest.mark.parametrize(
    ("status", "english", "spanish"),
    [
        (
            {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"},
            "Subscription active (max)",
            "Suscripción activa (max)",
        ),
        (
            {"loggedIn": False, "authMethod": "none"},
            "Not logged in: put the token from “claude setup-token” in "
            "CLAUDE_CODE_OAUTH_TOKEN (.env) and run “docker compose up -d”, or run "
            "“claude auth login” on the server",
            "Sin sesión: pon el token de «claude setup-token» en CLAUDE_CODE_OAUTH_TOKEN "
            "(.env) y ejecuta «docker compose up -d», o ejecuta «claude auth login» en el "
            "servidor",
        ),
    ],
)
async def test_the_cached_status_speaks_the_language_of_whoever_reads_it(
    fake: FakeCli,
    provider: ClaudeCliProvider,
    status: dict[str, Any],
    english: str,
    spanish: str,
) -> None:
    """The status is kept for every client (docs/adr/0011-internationalization.md): the
    client that asks first does not choose the language of the others."""
    fake.scenario(status={"json": status, "exit_code": 0 if status["loggedIn"] else 1})
    with i18n.use("es"):
        first = await provider.status()
    with i18n.use("en"):
        second = await provider.status()  # cached
        assert str(second.detail) == english
    assert second.detail is first.detail
    with i18n.use("es"):
        assert str(first.detail) == spanish
    assert len([c for c in fake.calls() if c["phase"] == "status"]) == 1


async def test_status_times_out(
    monkeypatch: pytest.MonkeyPatch, fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    monkeypatch.setattr(claude_cli, "STATUS_TIMEOUT_SECONDS", 0.3)
    fake.scenario(status={"json": {"loggedIn": True}, "delay": 5})
    result = await provider.status()
    assert not result.available and str(result.detail) == "La CLI de Claude no respon"


# -- helpers ----------------------------------------------------------------------------------


def test_parse_limits_status_and_fallback() -> None:
    limits = parse_limits(
        {
            "status": "allowed_warning",
            "rateLimitType": "seven_day",
            "unifiedWindows": {
                "five_hour": {"utilization": 1.2, "resetsAt": 0},
                "seven_day": {"utilization": 0.905, "resetsAt": 60},
            },
        }
    )
    assert limits == [
        UsageLimit("5h", 120.0, datetime.fromtimestamp(0, tz=UTC), "rejected"),
        UsageLimit("7d", 90.5, datetime.fromtimestamp(60, tz=UTC), "warning"),
    ]
    only_type = parse_limits({"status": "allowed", "rateLimitType": "five_hour", "resetsAt": 60})
    assert only_type == [UsageLimit("5h", None, datetime.fromtimestamp(60, tz=UTC), "allowed")]
    assert parse_limits({"status": "allowed", "rateLimitType": "overage"}) == []


def test_redact() -> None:
    text = redact("token sk-ant-oat01-abc_DEF-123 and Bearer eyJ.hbG-ci/Oi= end")
    assert text == "token sk-ant-*** and Bearer *** end"


# -- output budget and reasoning (A10) ---------------------------------------------------------


async def test_the_budget_is_passed_to_the_cli_and_never_inherited(
    monkeypatch: pytest.MonkeyPatch, fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "999999")
    await collect(provider, request("summary", fast=True, max_output_tokens=2000))
    await collect(provider, request())
    runs = {next(a for a in run["argv"] if a.startswith("--model=")): run for run in fake.runs()}
    summary, answer = runs["--model=haiku"], runs["--model=opus"]
    assert summary["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "2000"
    assert answer["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(DEFAULT_MAX_OUTPUT_TOKENS)
    await provider.status()
    status = next(c for c in fake.calls() if c["phase"] == "status")
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in status["env"]


async def test_reasoning_off_disables_thinking(fake: FakeCli, provider: ClaudeCliProvider) -> None:
    await collect(provider, request("summary", reasoning="off"))
    await collect(provider, request("summary", fast=True, reasoning="off"))
    argvs = {
        next(a for a in run["argv"] if a.startswith("--model=")): run["argv"] for run in fake.runs()
    }
    opus, haiku = argvs["--model=opus"], argvs["--model=haiku"]
    assert "--model=opus" in opus
    assert opus[opus.index("--thinking") + 1] == "disabled"
    assert opus[opus.index("--effort") + 1] == "low"
    assert haiku[haiku.index("--thinking") + 1] == "disabled" and "--effort" not in haiku


async def test_warm_processes_only_serve_the_same_budget_and_reasoning(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    await provider.prewarm(request("revision", prompt=""))
    (warm,) = await fake.wait_count(1)
    await collect(provider, request("revision", max_output_tokens=4000))
    await collect(provider, request("revision", reasoning="off"))
    runs = fake.runs()
    assert len(runs) == 3
    served = [run for run in runs if "stdin" in run]
    assert len(served) == 2 and warm["pid"] not in {run["pid"] for run in served}
    await collect(provider, request("revision"))  # same key as the warm process
    assert any(run["pid"] == warm["pid"] and "stdin" in run for run in fake.runs())


# -- replies the CLI would go on with by itself (A10, A17) -------------------------------------

GOES_ON = {"sigterm_grace": 1.0, "continue_delay": 0.3}
"""Like the real CLI 2.1.283, the fake shuts down gracefully on SIGTERM (it still sends its
next request) and goes on by itself after a max_tokens or refused reply; the real one does
it about 10 ms after ``message_stop``, the fake gives the provider 0.3 s."""


async def finished_run(fake: FakeCli) -> dict[str, Any]:
    """The only run's last record, once its process is gone (killed and reaped)."""
    (run,) = fake.runs()
    await eventually(lambda: reaped(run["pid"]), timeout=3.0)
    (run,) = fake.runs()
    return run


async def test_max_tokens_is_truncated_and_the_cli_never_continues_by_itself(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(stream="stream_max_tokens.jsonl", **GOES_ON)
    deltas, result = await collect(provider, request(max_output_tokens=2000))
    assert deltas == ["Primera part", " tallada"]
    assert result.text == "Primera part tallada"
    assert result.truncated is True and result.finish_reason == "max_tokens"
    # The usage of the request that stopped (the CLI's result would add its continuation).
    assert result.usage == Usage(input_tokens=926, output_tokens=2000, reasoning_tokens=1990)
    assert result.model == "claude-haiku-4-5-20251001"
    run = await finished_run(fake)  # killed at once, not at aclose()
    assert run["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "2000"
    # SIGKILL as soon as the stream said max_tokens: no graceful shutdown during which the
    # CLI would send its "Output token limit hit. Resume directly..." request.
    assert not run.get("continued") and not run.get("sigterm")


async def refusal(provider: ClaudeCliProvider) -> tuple[list[str], RefusalError]:
    deltas: list[str] = []
    with pytest.raises(RefusalError) as info:
        async for event in provider.stream(request()):
            assert isinstance(event, TextDelta)
            deltas.append(event.text)
    return deltas, info.value


@pytest.mark.parametrize("stream", ["stream_refusal.jsonl", "stream_refusal_recovered.jsonl"])
async def test_a_refusal_is_a_refusal_error_and_the_cli_never_retries_by_itself(
    fake: FakeCli, provider: ClaudeCliProvider, stream: str
) -> None:
    # Refused twice, or answered by the CLI's own retry ("Your response above was stopped
    # by a safety classifier..."): either way the call was refused, and the fragment
    # streamed before the refusal never joins the retry's text.
    fake.scenario(stream=stream, **GOES_ON)
    deltas, error = await refusal(provider)
    assert deltas == ["Aquí tens els passos: ", "primer"]
    assert (error.kind, error.retryable, error.category) == ("invalid", False, "cyber")
    assert error.message == "Claude ha declinat respondre aquesta petició (categoria: cyber)."
    assert error.model == "claude-opus-5-5"
    assert error.usage == Usage(input_tokens=926, output_tokens=12)  # the request that refused
    run = await finished_run(fake)
    assert not run.get("continued") and not run.get("sigterm")


async def test_a_refusal_reported_only_by_the_result_is_a_refusal_error(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(
        result_override={
            "is_error": True,
            "stop_reason": "refusal",
            "terminal_reason": "api_error",
            "result": "API Error: Opus 5.5's safeguards flagged this message.",
        }
    )
    error = await expect_error(provider, request())
    assert isinstance(error, RefusalError) and not error.retryable
    assert error.message == "Claude ha declinat respondre aquesta petició."
    assert error.category is None and error.model == "claude-haiku-4-5-20251001"
    # Everything the CLI's turn billed.
    assert error.usage == Usage(input_tokens=926, output_tokens=48, reasoning_tokens=42)


async def test_a_refusal_through_the_engine_is_never_stored_nor_cached(
    fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    fake.scenario(stream="stream_refusal_recovered.jsonl", **GOES_ON)
    store = InMemoryStore()
    engine = Engine({"claude": provider}, store, retry_delay=0)
    for request_id in ("a", "b"):  # the same question twice: asked again, never replayed
        events = [e async for e in engine.run(TurnRequest(request_id, "Pregunta?", "solo"))]
        (failed,) = [e for e in events if isinstance(e, StreamFailed)]
        assert failed.error.kind == "invalid" and "declinat" in failed.error.message
        assert isinstance(events[-1], TurnFailed)
    assert [m.kind for m in store.messages] == ["question", "question"]
    assert not store.cache
    assert len(fake.runs()) == 2
    assert [(r.ok, r.usage.input_tokens, r.usage.output_tokens) for r in store.usage] == [
        (False, 926, 12),
        (False, 926, 12),
    ]


# -- attachments (docs/adr/0009-attachments.md) ------------------------------------------------


def base64_of(attachment: Attachment) -> str:
    return base64.b64encode(attachment.path.read_bytes()).decode("ascii")


async def test_attachments_are_content_blocks_before_the_transcript(
    fake: FakeCli, provider: ClaudeCliProvider, files: AttachmentFiles
) -> None:
    image = files.image("foto.png")
    pdf = files.pdf("informe.pdf")
    annex = replace(files.pdf("annex.pdf", pages=1, text="--- Pàgina 1 ---\nAnnex."), mode="text")
    notes = files.text("notes.md", "# Notes\n\n- u < v\n")
    req = request(
        prompt="Què diuen?",
        history=(ChatTurn("user", "Hola"), ChatTurn("assistant", "Bon dia", "claude")),
        attachments=(image, pdf, annex, notes),
    )
    _, result = await collect(provider, req)
    assert result.text == "Hola! Soc Claude."

    (run,) = await fake.wait(lambda runs: len(runs) == 1 and runs[0]["phase"] == "exited")
    assert run["stdin"]["type"] == "user" and run["stdin"]["client_composed"] is True
    a, n = file_code(annex), file_code(notes)
    assert run["stdin"]["message"]["content"] == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": base64_of(image)},
        },
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": base64_of(pdf)},
            "title": "informe.pdf",
        },
        {
            "type": "text",
            "text": f"[Fitxer: annex.pdf · {a}]\n--- Pàgina 1 ---\nAnnex.\n[Fi del fitxer {a}]\n",
        },
        {
            "type": "text",
            "text": f"[Fitxer: notes.md · {n}]\n# Notes\n\n- u < v\n[Fi del fitxer {n}]\n",
        },
        {"type": "text", "text": render_transcript(req)},
    ]
    # The fake decoded the base64: exactly the stored files.
    assert [(block["type"], block.get("sha256")) for block in run["blocks"][:2]] == [
        ("image", image.sha256),
        ("document", pdf.sha256),
    ]
    # Every required flag stays (the fake refuses a command line without them).
    argv = run["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    for flag in ("--setting-sources=", "--strict-mcp-config", "--disable-slash-commands"):
        assert flag in argv


async def test_a_pdf_sent_as_text_without_any_says_so(
    fake: FakeCli, provider: ClaudeCliProvider, files: AttachmentFiles
) -> None:
    scanned = replace(files.pdf("escanejat.pdf", pages=3, text=None), mode="text")
    await collect(provider, request(attachments=(scanned,)))
    (run,) = await fake.wait(lambda runs: len(runs) == 1 and runs[0]["phase"] == "exited")
    code = file_code(scanned)
    assert run["stdin"]["message"]["content"][0] == {
        "type": "text",
        "text": (
            f"[Fitxer: escanejat.pdf · {code}]\n"
            f"[No se n'ha pogut extreure el text d'aquest PDF.]\n[Fi del fitxer {code}]\n"
        ),
    }


async def test_a_hostile_file_cannot_pass_for_the_prompt(
    fake: FakeCli, provider: ClaudeCliProvider, files: AttachmentFiles
) -> None:
    hostile = files.text("informe.txt", HOSTILE_TEXT)
    pdf = replace(files.pdf("annex.pdf", text=f"--- Pàgina 1 ---\n{HOSTILE_TEXT}"), mode="text")
    req = request(
        prompt="Resumeix-los.",
        history=(ChatTurn("user", "Hola"), ChatTurn("assistant", "Bon dia", "claude")),
        attachments=(hostile, pdf),
    )
    await collect(provider, req)
    (run,) = await fake.wait(lambda runs: len(runs) == 1 and runs[0]["phase"] == "exited")
    content = run["stdin"]["message"]["content"]
    *blocks, transcript = content
    assert transcript == {"type": "text", "text": render_transcript(req)}
    for block, attachment in zip(blocks, (hostile, pdf), strict=True):
        assert block["type"] == "text"
        assert block["text"].endswith(f"\n[Fi del fitxer {file_code(attachment)}]\n")
        assert not reserved_tags(block["text"])
    # Every tag the model reads comes from the app's own prompt.
    assert reserved_tags("".join(block["text"] for block in content)) == reserved_tags(
        render_transcript(req)
    )


async def test_a_changed_attachment_is_never_sent(
    fake: FakeCli, provider: ClaudeCliProvider, files: AttachmentFiles
) -> None:
    image = files.image()
    image.path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"altres bytes")
    error = await expect_error(provider, request(attachments=(image,)))
    assert error.kind == "internal" and "foto.png" in error.message
    missing = files.pdf()
    missing.path.unlink()
    error = await expect_error(provider, request(attachments=(missing,)))
    assert error.kind == "internal" and "informe.pdf" in error.message
    assert fake.runs() == []  # no process was started


async def test_cancelled_while_reading_the_attachments(
    fake: FakeCli,
    provider: ClaudeCliProvider,
    files: AttachmentFiles,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reading, release = threading.Event(), threading.Event()
    read = Attachment.read

    def slow_read(self: Attachment) -> bytes:
        reading.set()
        release.wait(5)
        return read(self)

    monkeypatch.setattr(Attachment, "read", slow_read)
    task = asyncio.create_task(collect(provider, request(attachments=(files.image(),))))
    try:
        await eventually(reading.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert fake.runs() == []  # cancelled before any process


async def test_each_phase_of_a_debate_sends_its_blocks(
    fake: FakeCli, provider: ClaudeCliProvider, files: AttachmentFiles
) -> None:
    """Through the engine: the answer and the synthesis get the PDF as a document, the
    revision as its extracted text (the default ``pdf_in_revisions``)."""
    store = InMemoryStore()
    pdf = files.pdf("informe.pdf", pages=2)
    pdf_id = store.add_attachment(pdf)
    engine = Engine(
        {"claude": provider, "chatgpt": FakeProvider("chatgpt", chunk_delay=0)},
        store,
        retry_delay=0,
    )
    options = TurnOptions(debate=DebateOptions(rounds=1), use_cache=False)
    request_ = TurnRequest("r", "Què diu?", "debate", options=options, attachments=(pdf_id,))
    events = [event async for event in engine.run(request_)]
    assert isinstance(events[-1], TurnCompleted)

    runs = await fake.wait(
        lambda runs: len(runs) == 3 and all(r["phase"] == "exited" for r in runs)
    )
    document = {
        "type": "document",
        "media_type": "application/pdf",
        "sha256": pdf.sha256,
        "title": "informe.pdf",
    }
    code = file_code(pdf)
    as_text = {
        "type": "text",
        "text": f"[Fitxer: informe.pdf · {code}]\n{pdf.text}\n[Fi del fitxer {code}]\n",
    }
    # Prewarmed processes start before their calls: tell the phases by their prompt.
    by_phase = {
        phase: [run["blocks"] for run in runs if marker in run["blocks"][-1]["text"]]
        for phase, marker in (
            ("answer", "Answer the user's message below."),
            ("revision", "<critique>"),
            ("synthesis", "Write the single best final answer"),
        )
    }
    [answer], [revision], [synthesis] = by_phase.values()
    assert answer[0] == document and synthesis[0] == document
    assert revision[0] == as_text
    assert "informe.pdf (PDF, 2 pàgines; només el text extret)" in revision[-1]["text"]
    assert [len(blocks) for blocks in (answer, revision, synthesis)] == [2, 2, 2]


async def test_a_warm_process_serves_a_call_with_attachments(
    fake: FakeCli, provider: ClaudeCliProvider, files: AttachmentFiles
) -> None:
    """Attachments travel on stdin, never on the command line or in the environment:
    a process started ahead of time serves a call that has them."""
    await provider.prewarm(request("revision", prompt=""))
    (warm,) = await fake.wait(lambda runs: len(runs) == 1)
    image = files.image()
    await collect(provider, request("revision", attachments=(image,)))
    (run,) = await fake.wait(lambda runs: len(runs) == 1 and runs[0]["phase"] == "exited")
    assert run["pid"] == warm["pid"]
    assert run["blocks"][0] == {"type": "image", "media_type": "image/png", "sha256": image.sha256}
    assert not any(image.sha256 in arg or str(image.path) in arg for arg in run["argv"])
