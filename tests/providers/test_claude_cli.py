"""ClaudeCliProvider against a fake ``claude`` executable (no network, no real CLI).

The fake (fixtures/claude/fake_claude.py) replays stream-json recorded from the real
CLI and records what it received in ``<bin>/calls/<pid>.json``.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentic_os.config import Settings
from agentic_os.domain import Purpose, Usage
from agentic_os.providers import claude_cli
from agentic_os.providers.base import (
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
    TextDelta,
    UsageLimit,
)
from agentic_os.providers.claude_cli import (
    ENV_ALLOW_LIST,
    ClaudeCliProvider,
    parse_limits,
    redact,
)
from agentic_os.providers.prompt_format import render_transcript

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


def alive(pid: int) -> bool:
    """True while ``pid`` runs (zombies count as dead)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] not in ("Z", "X")


def reaped(pid: int) -> bool:
    """True when a direct child is gone and was waited for (no zombie left)."""
    return not Path(f"/proc/{pid}").exists()


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
    assert all(m.description.startswith("Sempre la versió més nova.") for m in models)
    assert not any("Ara:" in m.description for m in models)

    # The recorded system/init resolves "haiku" to a concrete model.
    await collect(provider, request(fast=True))
    haiku = next(m for m in await provider.list_models() if m.id == "haiku")
    assert haiku.description.endswith("Ara: claude-haiku-4-5-20251001")


async def test_list_models_with_a_configured_full_id(tmp_path: Path, fake: FakeCli) -> None:
    provider = ClaudeCliProvider(make_settings(tmp_path, fake, claude_model="claude-opus-5-5"))
    models = await provider.list_models()
    await provider.aclose()
    assert [(m.id, m.is_default) for m in models][:2] == [
        ("claude-opus-5-5", True),
        ("opus", False),
    ]
    assert models[0].description == "Raonament profund i tasques llargues."


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
    # LC_CTYPE may be added by the fake's own Python interpreter (PEP 538).
    assert set(env) - {"LC_CTYPE"} <= {*ENV_ALLOW_LIST, "DISABLE_AUTOUPDATER"}


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
    assert "claude setup-token" in error.message


async def test_rejected_rate_limit(fake: FakeCli, provider: ClaudeCliProvider) -> None:
    fake.scenario(stream="stream_rate_limited.jsonl")
    error = await expect_error(provider, request())
    assert error.kind == "rate_limit"
    assert "27/09 a les 19:48 UTC" in error.message
    limits = {limit.window: limit for limit in (await provider.status()).limits}
    assert limits["5h"].status == "rejected" and limits["5h"].used_percent == 100.0
    assert limits["7d"].status == "allowed"


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
    assert not status.available and status.detail == "CLI de Claude no trobada"
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
            "Sense sessió: executa «claude setup-token» o «claude auth login» al servidor",
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
    assert (result.available, result.detail) == (available, detail)
    assert result.limits == ()
    await provider.status()  # cached
    assert len([c for c in fake.calls() if c["phase"] == "status"]) == 1


async def test_status_times_out(
    monkeypatch: pytest.MonkeyPatch, fake: FakeCli, provider: ClaudeCliProvider
) -> None:
    monkeypatch.setattr(claude_cli, "STATUS_TIMEOUT_SECONDS", 0.3)
    fake.scenario(status={"json": {"loggedIn": True}, "delay": 5})
    result = await provider.status()
    assert not result.available and result.detail == "La CLI de Claude no respon"


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
