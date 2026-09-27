"""CodexAppServerProvider against a fake ``codex app-server`` (fixtures/codex)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentic_os.config import Settings
from agentic_os.domain import Usage
from agentic_os.providers.base import (
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    ProviderError,
    TextDelta,
    UsageLimit,
)
from agentic_os.providers.codex_appserver import (
    ENV_ALLOWLIST,
    CodexAppServerProvider,
    codex_environment,
    config_arguments,
    turn_error,
    usage_from_breakdown,
)
from agentic_os.providers.prompt_format import render_transcript

FAKE_SERVER = Path(__file__).parent / "fixtures" / "codex" / "fake_app_server.py"
SYSTEM = "Ets un assistent de proves. Respon en català."


@dataclass
class FakeCodex:
    home: Path
    data_dir: Path

    def settings(self, **overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "data_dir": self.data_dir,
            "codex_cli_path": str(FAKE_SERVER),
            "provider_timeout_seconds": 10.0,
        }
        values.update(overrides)
        values.setdefault("_env_file", None)  # ignore a developer .env
        return Settings(**values)

    def options(self, **values: Any) -> None:
        (self.home / "fake.json").write_text(json.dumps(values), encoding="utf-8")

    def log(self) -> list[dict[str, Any]]:
        path = self.home / "requests.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def received(self, method: str) -> list[dict[str, Any]]:
        """Messages the client sent with this method (requests and notifications)."""
        return [
            entry["msg"]
            for entry in self.log()
            if "msg" in entry and entry["msg"].get("method") == method
        ]

    def params(self, method: str) -> list[dict[str, Any]]:
        return [message.get("params") or {} for message in self.received(method)]

    def pids(self) -> list[int]:
        return [entry["pid"] for entry in self.log() if "argv" in entry]


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCodex:
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return FakeCodex(home=home, data_dir=tmp_path / "data")


@pytest.fixture
async def provider(fake: FakeCodex) -> AsyncIterator[CodexAppServerProvider]:
    codex = CodexAppServerProvider(fake.settings())
    yield codex
    await codex.aclose()


def make_request(prompt: str = "Say hi", **kwargs: Any) -> GenerationRequest:
    return GenerationRequest(system=SYSTEM, prompt=prompt, **kwargs)


async def collect(
    codex: CodexAppServerProvider, request: GenerationRequest
) -> tuple[list[str], GenerationResult]:
    deltas: list[str] = []
    result: GenerationResult | None = None
    async for event in codex.stream(request):
        if isinstance(event, TextDelta):
            deltas.append(event.text)
        else:
            result = event
    assert result is not None
    return deltas, result


async def wait_until(condition: Callable[[], object], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def process_gone(pid: int) -> bool:
    """True if the process no longer exists or is a zombie waiting to be reaped."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, IndexError):
        return True
    return state in ("Z", "X")


# -- generation ---------------------------------------------------------------------------


async def test_streams_a_recorded_turn_with_usage_and_limits(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    status = await provider.status()
    assert [(limit.window, limit.used_percent) for limit in status.limits] == [
        ("5h", 21.0),
        ("7d", 3.0),
    ]
    request = make_request(
        history=(ChatTurn("user", "Hola"), ChatTurn("assistant", "Bon dia", agent="claude"))
    )

    deltas, result = await collect(provider, request)

    assert deltas == ["Alpha ", "beta ", "gamma ", "delta ", "epsilon."]
    assert result.text == "Alpha beta gamma delta epsilon."
    # inputTokens 1000 include 600 cached: the uncached remainder is reported.
    assert result.usage == Usage(
        input_tokens=400,
        output_tokens=50,
        cache_read_tokens=600,
        cache_write_tokens=0,
        reasoning_tokens=10,
        cost_usd=None,
    )
    assert result.model == "gpt-6-astra"
    assert result.ttft_ms is not None
    assert result.latency_ms >= result.ttft_ms

    # Sparse account/rateLimits/updated notifications refresh the cached status.
    limits = (await provider.status()).limits
    assert limits == [
        UsageLimit("5h", 43.0, datetime.fromtimestamp(1790536746, tz=UTC), "allowed"),
        UsageLimit("7d", 7.0, datetime.fromtimestamp(1790635746, tz=UTC), "allowed"),
    ]
    assert len(fake.received("account/read")) == 1

    sandbox = (fake.data_dir / "sandbox" / "codex").resolve()
    assert stat.S_IMODE(sandbox.stat().st_mode) == 0o700
    [thread] = fake.params("thread/start")
    assert thread == {
        "cwd": str(sandbox),
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "baseInstructions": SYSTEM,
        "ephemeral": True,
    }
    [turn] = fake.params("turn/start")
    assert turn["input"] == [
        {"type": "text", "text": render_transcript(request), "text_elements": []}
    ]
    assert turn["effort"] == "medium"
    assert turn["summary"] == "none"

    [initialize] = fake.params("initialize")
    assert initialize["clientInfo"]["name"] == "agentic-os"
    assert fake.received("initialized")
    [start] = [entry for entry in fake.log() if "argv" in entry]
    assert start["argv"] == ["app-server", *config_arguments(), "--listen", "stdio://"]
    assert start["cwd"] == str(sandbox)
    assert "features.shell_tool=false" in start["argv"]
    assert 'web_search="disabled"' in start["argv"]

    await wait_until(lambda: fake.received("thread/unsubscribe"))
    assert not fake.received("turn/interrupt")


async def test_models_and_effort_follow_the_request(fake: FakeCodex) -> None:
    codex = CodexAppServerProvider(
        fake.settings(chatgpt_model="gpt-6-sol", chatgpt_fast_model="gpt-6-luna-mini")
    )
    try:
        _, answer = await collect(codex, make_request("[echo] a", purpose="synthesis"))
        _, summary = await collect(codex, make_request("[echo] b", purpose="summary", fast=True))
        _, explicit = await collect(
            codex, make_request("[echo] c", purpose="revision", model="gpt-6-astra")
        )
    finally:
        await codex.aclose()
    assert (answer.model, summary.model, explicit.model) == (
        "gpt-6-sol",
        "gpt-6-luna-mini",
        "gpt-6-astra",
    )
    assert [p["model"] for p in fake.params("thread/start")] == [
        "gpt-6-sol",
        "gpt-6-luna-mini",
        "gpt-6-astra",
    ]
    assert [p["effort"] for p in fake.params("turn/start")] == ["medium", "low", "low"]


async def test_fast_requests_default_to_luna(provider: CodexAppServerProvider) -> None:
    _, result = await collect(provider, make_request("[echo] x", fast=True))
    assert result.model == "gpt-6-luna"


async def test_every_server_request_is_declined(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    _, result = await collect(provider, make_request("[approval]"))

    assert result.text == "Totes declinades."
    # Two model requests in the turn: the thread total is the turn's usage.
    assert result.usage == Usage(
        input_tokens=240,
        output_tokens=30,
        cache_read_tokens=50,
        cache_write_tokens=10,
        reasoning_tokens=7,
    )
    [answers] = [entry["answers"] for entry in fake.log() if "answers" in entry]
    results = {method: reply.get("result") for method, reply in answers.items()}
    assert results["item/commandExecution/requestApproval"] == {"decision": "decline"}
    assert results["item/fileChange/requestApproval"] == {"decision": "decline"}
    assert results["item/permissions/requestApproval"] == {"permissions": {}, "scope": "turn"}
    assert results["item/tool/requestUserInput"] == {"answers": {}}
    assert results["mcpServer/elicitation/request"]["action"] == "decline"
    assert results["item/tool/call"] == {"contentItems": [], "success": False}
    assert "denied" in results["applyPatchApproval"]["decision"]
    assert "denied" in results["execCommandApproval"]["decision"]
    refresh = answers["account/chatgptAuthTokens/refresh"]
    assert refresh["error"]["code"] == -32601


async def test_usage_limit_is_a_rate_limit_error(provider: CodexAppServerProvider) -> None:
    await provider.status()  # cached: the next status shows the limits pushed by the turn

    with pytest.raises(ProviderError) as caught:
        await collect(provider, make_request("[usage-limit]"))

    assert caught.value.kind == "rate_limit"
    assert not caught.value.retryable
    assert caught.value.message.startswith("Has arribat al límit d'ús")
    assert "usage limit" in caught.value.message
    limits = (await provider.status()).limits
    assert [(limit.window, limit.status) for limit in limits] == [
        ("5h", "rejected"),
        ("7d", "allowed"),
    ]


async def test_non_final_errors_and_commentary_are_ignored(
    provider: CodexAppServerProvider,
) -> None:
    _, retried = await collect(provider, make_request("[retry-error] [echo] hola"))
    assert retried.text.strip().endswith("hola")

    deltas, result = await collect(provider, make_request("[commentary]"))
    assert deltas == ["Primer.", "\n\n", "Segon."]
    assert result.text == "Primer.\n\nSegon."


async def test_an_interrupted_turn_is_a_cancellation(provider: CodexAppServerProvider) -> None:
    with pytest.raises(asyncio.CancelledError):
        await collect(provider, make_request("[interrupted]"))


async def test_crash_mid_turn_then_recovery(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    deltas: list[str] = []
    with pytest.raises(ProviderError) as caught:
        async for event in provider.stream(make_request("[crash]")):
            assert isinstance(event, TextDelta)
            deltas.append(event.text)
    assert deltas == ["Parcial"]
    assert caught.value.kind == "unavailable"
    assert caught.value.retryable

    _, result = await collect(provider, make_request("[echo] de nou"))
    assert result.text.strip() == "de nou"
    assert len(set(fake.pids())) == 2


async def test_cancellation_interrupts_the_turn_and_releases_everything(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    first_delta = asyncio.Event()

    async def consume() -> None:
        async for _ in provider.stream(make_request("[slow]")):
            first_delta.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(first_delta.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await wait_until(lambda: fake.received("thread/unsubscribe"))
    [interrupt] = fake.params("turn/interrupt")
    [unsubscribe] = fake.params("thread/unsubscribe")
    assert interrupt["threadId"] == unsubscribe["threadId"]
    await wait_until(lambda: not provider._background)
    conn = provider._conn
    assert conn is not None
    assert not conn._pending
    assert not conn._listeners

    _, result = await collect(provider, make_request("[echo] encara viu"))
    assert result.text.strip() == "encara viu"
    assert len(set(fake.pids())) == 1


async def test_wall_clock_timeout_interrupts_the_turn(fake: FakeCodex) -> None:
    codex = CodexAppServerProvider(fake.settings(provider_timeout_seconds=0.5))
    try:
        with pytest.raises(ProviderError) as caught:
            await collect(codex, make_request("[slow]"))
        assert caught.value.kind == "timeout"
        await wait_until(lambda: fake.received("thread/unsubscribe"))
        assert len(fake.received("turn/interrupt")) == 1
    finally:
        await codex.aclose()


async def test_concurrent_calls_share_one_process(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    first, second = await asyncio.gather(
        collect(provider, make_request("[echo] u dos tres quatre")),
        collect(provider, make_request("[echo] cinc sis set vuit")),
    )
    assert first[1].text.strip() == "u dos tres quatre"
    assert second[1].text.strip() == "cinc sis set vuit"
    assert len(fake.pids()) == 1
    assert len(fake.received("initialize")) == 1


# -- process isolation and lifecycle -------------------------------------------------------


async def test_the_process_gets_only_the_allow_listed_environment(
    fake: FakeCodex, provider: CodexAppServerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "AOS_OPENAI_API_KEY")
    for name in secrets:
        monkeypatch.setenv(name, "sk-secret")
    monkeypatch.setenv("LANG", "ca_ES.UTF-8")

    await provider.prewarm(make_request())

    env = json.loads((fake.home / "env.json").read_text(encoding="utf-8"))
    assert not set(secrets) & set(env)
    # Python may add LC_CTYPE itself (PEP 538 locale coercion).
    assert set(env) - {"LC_CTYPE"} <= set(ENV_ALLOWLIST)
    assert env["CODEX_HOME"] == str(fake.home)
    assert env["LANG"] == "ca_ES.UTF-8"
    assert "PATH" in env


def test_codex_environment_filters_the_source() -> None:
    source = {"PATH": "/bin", "HOME": "/home/x", "OPENAI_API_KEY": "sk", "AOS_SECRET": "s"}
    assert codex_environment(source) == {"PATH": "/bin", "HOME": "/home/x"}


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
async def test_aclose_kills_the_whole_process_group(fake: FakeCodex) -> None:
    fake.options(spawn_child=True)
    codex = CodexAppServerProvider(fake.settings())
    await codex.prewarm(make_request())
    conn = codex._conn
    assert conn is not None
    server_pid = conn.pid
    await wait_until(lambda: (fake.home / "child.pid").exists())
    child_pid = int((fake.home / "child.pid").read_text())

    await codex.aclose()

    assert process_gone(server_pid)
    await wait_until(lambda: process_gone(child_pid))
    with pytest.raises(ProviderError):
        await collect(codex, make_request())


# -- status ----------------------------------------------------------------------------------


async def test_status_reports_the_plan_and_default_model(fake: FakeCodex) -> None:
    fake.options(config_model="gpt-6-sol")
    codex = CodexAppServerProvider(fake.settings())
    try:
        status = await codex.status()
        again = await codex.status()
    finally:
        await codex.aclose()
    assert status.agent == "chatgpt"
    assert status.mode == "cli"
    assert status.available
    assert status.detail == "Subscripció ChatGPT activa (Plus)"
    assert status.model == "gpt-6-sol"
    assert again == status
    assert len(fake.received("account/read")) == 1


async def test_status_without_login_recycles_the_process(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    fake.options(account=None)
    status = await provider.status()
    assert not status.available
    assert status.detail == "Sense sessió: executa «codex login --device-auth» al servidor"
    await wait_until(lambda: provider._conn is None)

    # The owner runs `codex login`: a fresh process sees the new credentials.
    fake.options(account={"type": "chatgpt", "email": None, "planType": "pro"})
    provider._status_cache = None
    status = await provider.status()
    assert status.available
    assert status.detail == "Subscripció ChatGPT activa (Pro)"
    assert len(set(fake.pids())) == 2


async def test_status_with_an_api_key_account(fake: FakeCodex) -> None:
    fake.options(account={"type": "apiKey"})
    codex = CodexAppServerProvider(fake.settings(chatgpt_model="gpt-6-sol"))
    try:
        status = await codex.status()
    finally:
        await codex.aclose()
    assert status.available
    assert status.model == "gpt-6-sol"
    assert status.detail == "Codex amb clau d'API (es factura per ús)"


async def test_missing_cli(fake: FakeCodex, tmp_path: Path) -> None:
    missing = str(tmp_path / "no-such-codex")
    codex = CodexAppServerProvider(fake.settings(codex_cli_path=missing))
    try:
        status = await codex.status()
        assert not status.available
        assert status.detail == f"CLI de Codex no trobada: {missing}"
        with pytest.raises(ProviderError) as caught:
            await collect(codex, make_request())
        assert caught.value.kind == "unavailable"
        await codex.prewarm(make_request())  # never raises
    finally:
        await codex.aclose()


async def test_failed_initialize(fake: FakeCodex) -> None:
    fake.options(init_error=True)
    codex = CodexAppServerProvider(fake.settings())
    try:
        status = await codex.status()
        assert not status.available
        assert status.detail.startswith("No s'ha pogut iniciar Codex.")
        assert codex._conn is None
    finally:
        await codex.aclose()
    for pid in fake.pids():
        assert process_gone(pid)


# -- mapping helpers --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "kind", "retryable"),
    [
        ("usageLimitExceeded", "rate_limit", False),
        ("rateLimitExceeded", "rate_limit", True),
        ("unauthorized", "auth", False),
        ({"httpConnectionFailed": {"httpStatusCode": 401}}, "auth", False),
        ({"responseStreamConnectionFailed": {"httpStatusCode": 429}}, "rate_limit", True),
        ({"responseStreamDisconnected": {"httpStatusCode": None}}, "unavailable", True),
        ("serverOverloaded", "unavailable", True),
        ("contextWindowExceeded", "invalid", False),
        ("cyberPolicy", "invalid", False),
        ("sandboxError", "internal", False),
        (None, "internal", False),
    ],
)
def test_turn_errors_map_to_provider_errors(info: object, kind: str, retryable: bool) -> None:
    error = turn_error({"message": "upstream text", "codexErrorInfo": info})
    assert error.kind == kind
    assert error.retryable is retryable


def test_usage_from_breakdown_is_none_safe() -> None:
    assert usage_from_breakdown(None) == Usage()
    assert usage_from_breakdown({"inputTokens": 10, "cachedInputTokens": None}) == Usage(
        input_tokens=10
    )


def test_config_arguments_disable_tools_and_retries() -> None:
    arguments = config_arguments()
    pairs = [arguments[i + 1] for i in range(0, len(arguments), 2)]
    assert all(flag == "-c" for flag in arguments[::2])
    for expected in (
        "features.shell_tool=false",
        "features.unified_exec=false",
        "features.multi_agent=false",
        "features.unbounded_connection_retries=false",
        "skills.bundled.enabled=false",
        "analytics.enabled=false",
        "include_environment_context=false",
    ):
        assert expected in pairs
    assert not any(pair.startswith("model_catalog_json") for pair in pairs)


def test_fake_server_is_executable() -> None:
    assert os.access(FAKE_SERVER, os.X_OK)
