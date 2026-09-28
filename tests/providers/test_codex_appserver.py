"""CodexAppServerProvider against a fake ``codex app-server`` (fixtures/codex)."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import stat
import sys
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import agentic_os.providers.codex_appserver as codex_appserver
from agentic_os.config import Settings
from agentic_os.domain import Usage
from agentic_os.orchestrator.tokens import estimate_tokens
from agentic_os.providers.base import (
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    ProviderError,
    TextDelta,
    UsageLimit,
)
from agentic_os.providers.codex_appserver import (
    BACKOFF_MAX,
    ENV_ALLOWLIST,
    MAX_SUB_AGENT_RUNS,
    SUB_AGENT_LIMIT_MESSAGE,
    CodexAppServerProvider,
    _AppServerConnection,
    codex_environment,
    config_arguments,
    remove_log_databases,
    sub_agent_run,
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

    def entries(self, key: str) -> list[dict[str, Any]]:
        """Log entries the fake wrote itself with this key (sub-agent runs, exits...)."""
        return [entry for entry in self.log() if key in entry]


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
    except (FileNotFoundError, ProcessLookupError, IndexError):
        # ProcessLookupError (ESRCH): it exited between opening and reading the file.
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
    state = (fake.data_dir / "sandbox" / "codex-state").resolve()
    assert start["argv"] == ["app-server", *config_arguments(state), "--listen", "stdio://"]
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


async def test_reasoning_off_uses_the_lowest_effort(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    await collect(provider, make_request("[echo] a", purpose="synthesis", reasoning="off"))
    [turn] = fake.params("turn/start")
    assert turn["effort"] == "low"


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


async def test_a_retry_after_partial_output_fails_instead_of_duplicating(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    deltas: list[str] = []
    with pytest.raises(ProviderError) as caught:
        async for event in provider.stream(make_request("[midstream-retry]")):
            assert isinstance(event, TextDelta)  # never a result with the answer twice
            deltas.append(event.text)
    assert deltas == ["La resposta ", "completa comen"]
    error = caught.value
    assert (error.kind, error.retryable) == ("unavailable", True)
    assert error.message == "La resposta de ChatGPT s'ha interromput."
    await wait_until(lambda: fake.received("thread/unsubscribe"))

    _, result = await collect(provider, make_request("[echo] de nou"))
    assert result.text.strip() == "de nou"


async def test_only_completed_items_make_the_final_text(provider: CodexAppServerProvider) -> None:
    _, result = await collect(provider, make_request("[orphan]"))
    assert result.text == "Resposta final."
    assert not result.truncated


async def test_the_output_budget_is_enforced_locally(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    deltas, result = await collect(provider, make_request("[long]", max_output_tokens=10))
    assert result.truncated is True and result.finish_reason == "max_tokens"
    assert result.text == "".join(deltas)
    # Approximate (about 4 characters per token): it stops at the delta that passes it.
    assert 10 < estimate_tokens(result.text) <= 10 + estimate_tokens("paraula ")
    # The usage is the one Codex reports, never an estimate of the text.
    assert result.usage == Usage(input_tokens=700, output_tokens=90, reasoning_tokens=60)
    assert fake.entries("long_interrupted")
    await wait_until(lambda: fake.received("thread/unsubscribe"))
    assert len(fake.received("turn/interrupt")) == 1  # our stop only, not a second one

    _, complete = await collect(provider, make_request("[echo] encara aquí"))
    assert complete.text.strip() == "encara aquí" and not complete.truncated
    assert len(set(fake.pids())) == 1


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


async def test_sub_agents_are_interrupted_and_not_counted(
    fake: FakeCodex, provider: CodexAppServerProvider, caplog: pytest.LogCaptureFixture
) -> None:
    """A prompt injection can make ChatGPT spawn a Codex sub-agent: a thread no call
    listens to, which would keep spending the owner's plan after the call ends."""
    caplog.set_level(logging.WARNING, logger="agentic_os.providers.codex_appserver")
    await provider.prewarm(make_request())
    first = provider._conn
    assert first is not None

    _, result = await collect(provider, make_request("[spawn] fet"))

    assert result.text.strip() == "fet"
    # Only the call's own thread is counted, never the sub-agent's usage.
    assert result.usage == Usage(
        input_tokens=400, output_tokens=50, cache_read_tokens=600, reasoning_tokens=10
    )
    await wait_until(lambda: fake.entries("sub_agent"))
    [stopped] = fake.entries("sub_agent")
    assert stopped["interrupted_after"] is not None
    assert stopped["interrupted_after"] < 20  # within a second, not after 20 s of work
    [root] = fake.params("turn/start")
    # One interrupt for the sub-agent's turn despite its many notifications; none for
    # the call's own turn, which had finished.
    [interrupt] = fake.params("turn/interrupt")
    assert interrupt["threadId"] == stopped["sub_agent"] != root["threadId"]
    assert "outside any call" in caplog.text
    # The call's thread and the sub-agent's are both released...
    await wait_until(lambda: len(fake.received("thread/unsubscribe")) == 2)
    unsubscribed = [p["threadId"] for p in fake.params("thread/unsubscribe")]
    assert unsubscribed == [root["threadId"], stopped["sub_agent"]]
    # ...and the process that ran it is replaced as soon as it is idle: 0.157.1 never
    # gives the memory of a sub-agent's thread back.
    await wait_until(lambda: provider._conn is None)
    await wait_until(lambda: not provider._background)
    assert process_gone(first.pid)
    assert "ChatGPT started sub-agents" in caplog.text
    assert not provider._stray_turns
    assert not provider._threads

    _, again = await collect(provider, make_request("[echo] de nou"))
    assert again.text.strip() == "de nou"
    assert len(set(fake.pids())) == 2


@pytest.mark.parametrize(
    ("marker", "new_threads"), [("[spawn-loop]", True), ("[followup-loop]", False)]
)
async def test_a_sub_agent_loop_stops_the_call(
    fake: FakeCodex, provider: CodexAppServerProvider, marker: str, new_threads: bool
) -> None:
    """Interrupting a sub-agent frees its slot (agents.max_threads=1), so an injected
    loop could start dozens of sub-agent runs in one call: after MAX_SUB_AGENT_RUNS the
    call is stopped and the process replaced."""
    with pytest.raises(ProviderError) as caught:
        await collect(provider, make_request(marker))

    assert caught.value.message == SUB_AGENT_LIMIT_MESSAGE
    assert caught.value.kind == "invalid"
    assert not caught.value.retryable
    # The root turn is interrupted and the process replaced once idle: the loop stops
    # right after the limit, not after the twelve runs the fake would otherwise start.
    await wait_until(lambda: provider._conn is None)
    await wait_until(lambda: not provider._background)
    assert all(process_gone(pid) for pid in fake.pids())
    [root] = fake.params("turn/start")
    root_interrupts = [
        p for p in fake.params("turn/interrupt") if p["threadId"] == root["threadId"]
    ]
    assert len(root_interrupts) == 1
    runs = fake.entries("sub_agent_run")
    assert MAX_SUB_AGENT_RUNS < len(runs) <= MAX_SUB_AGENT_RUNS + 3  # not all twelve
    # The call's thread and every sub-agent thread it saw are unsubscribed.
    threads = list(dict.fromkeys(run["thread"] for run in runs[: MAX_SUB_AGENT_RUNS + 1]))
    assert len(threads) == (MAX_SUB_AGENT_RUNS + 1 if new_threads else 1)
    unsubscribed = [p["threadId"] for p in fake.params("thread/unsubscribe")]
    assert unsubscribed[0] == root["threadId"]
    assert set(threads) <= set(unsubscribed[1:])

    _, result = await collect(provider, make_request("[echo] tot bé"))
    assert result.text.strip() == "tot bé"
    assert len(set(fake.pids())) == 2


async def test_a_process_with_sub_agents_waits_for_the_running_calls(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    """The replacement never cuts a call short: it waits until no call uses the process."""
    first_delta = asyncio.Event()

    async def slow() -> None:
        async for _ in provider.stream(make_request("[slow]")):
            first_delta.set()

    running = asyncio.create_task(slow())
    await asyncio.wait_for(first_delta.wait(), 5)
    conn = provider._conn
    assert conn is not None

    _, result = await collect(provider, make_request("[spawn] fet"))
    assert result.text.strip() == "fet"
    await wait_until(lambda: len(fake.received("thread/unsubscribe")) == 2)
    await asyncio.sleep(0.1)
    assert provider._conn is conn and conn.alive and not running.done()

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await wait_until(lambda: provider._conn is None)
    await wait_until(lambda: not conn.alive)
    assert len(fake.received("thread/unsubscribe")) == 3


def test_sub_agent_run_items() -> None:
    def activity(kind: str) -> dict[str, Any]:
        return {"type": "subAgentActivity", "id": "call_1", "kind": kind, "agentThreadId": "t1"}

    assert sub_agent_run(activity("started")) == ("call_1", ["t1"])
    assert sub_agent_run(activity("interacted")) == ("call_1", ["t1"])
    assert sub_agent_run(activity("interrupted")) is None
    assert sub_agent_run(activity("completed")) is None
    spawn = {
        "type": "collabAgentToolCall",
        "id": "c2",
        "tool": "spawnAgent",
        "receiverThreadIds": ["t2", None],
    }
    assert sub_agent_run(spawn) == ("c2", ["t2"])
    assert sub_agent_run({**spawn, "tool": "wait"}) is None
    assert sub_agent_run({"type": "agentMessage", "id": "m1"}) is None


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


async def test_codex_state_and_logs_stay_out_of_codex_home(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    """Codex logs every prompt in logs_2.sqlite: it must not land in CODEX_HOME (the
    volume with the CLI login, which is backed up) but in a private state directory."""
    await collect(provider, make_request("[echo] SECRET-7731"))

    state = (fake.data_dir / "sandbox" / "codex-state").resolve()
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert "SECRET-7731" in (state / "logs_2.sqlite").read_text(encoding="utf-8")
    assert not (fake.home / "logs_2.sqlite").exists()
    [start] = [entry for entry in fake.log() if "argv" in entry]
    assert f"sqlite_home={json.dumps(str(state))}" in start["argv"]
    assert start["cwd"] != str(state)  # the working directory stays empty


async def test_codex_state_dir_setting(fake: FakeCodex, tmp_path: Path) -> None:
    state = tmp_path / "run" / "codex state"
    state.mkdir(parents=True, mode=0o755)
    codex = CodexAppServerProvider(fake.settings(codex_state_dir=state))
    try:
        await collect(codex, make_request("[echo] SECRET-7732"))
    finally:
        await codex.aclose()
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert "SECRET-7732" in (state / "logs_2.sqlite").read_text(encoding="utf-8")
    assert not (fake.home / "logs_2.sqlite").exists()
    assert not (fake.data_dir / "sandbox" / "codex-state").exists()


async def test_log_databases_are_deleted_before_every_start(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    """The log databases hold every prompt: they must not outlive the process, and on a
    full state tmpfs a new app-server cannot even start. The state databases stay."""
    state = fake.data_dir / "sandbox" / "codex-state"
    state.mkdir(parents=True)
    kept = {
        "state_5.sqlite": "state",
        "state_5.sqlite-wal": "state wal",
        "goals_1.sqlite": "goals",
        "logs_2.sqlite.bak": "not a log database",
    }
    stale = {
        "logs_2.sqlite": "OLD-PROMPT-1",
        "logs_2.sqlite-wal": "OLD-PROMPT-2",
        "logs_2.sqlite-shm": "shm",
        "logs_1.sqlite": "OLD-PROMPT-3",
    }
    for name, text in (kept | stale).items():
        (state / name).write_text(text, encoding="utf-8")
    (state / "logs_9.sqlite").mkdir()  # never a directory

    await collect(provider, make_request("[echo] SECRET-A"))

    log = (state / "logs_2.sqlite").read_text(encoding="utf-8")
    assert "SECRET-A" in log and "OLD-PROMPT" not in log
    for name in stale:
        if name != "logs_2.sqlite":
            assert not (state / name).exists(), name
    for name, text in kept.items():
        assert (state / name).read_text(encoding="utf-8") == text
    assert (state / "logs_9.sqlite").is_dir()

    # A restart (here after a crash) starts with empty logs again.
    with pytest.raises(ProviderError):
        await collect(provider, make_request("[crash]"))
    await collect(provider, make_request("[echo] SECRET-B"))
    log = (state / "logs_2.sqlite").read_text(encoding="utf-8")
    assert "SECRET-B" in log and "SECRET-A" not in log
    assert len(set(fake.pids())) == 2


async def test_a_new_process_waits_for_the_old_one_to_stop(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    """Two app-servers must not share the state directory: the one being replaced may
    still write (or unlink) the log databases the new start deletes."""
    fake.options(term_delay=1.0)
    await collect(provider, make_request("[spawn] fet"))
    await wait_until(lambda: provider._conn is None)  # replaced: now shutting down slowly

    await collect(provider, make_request("[echo] nou"))

    await wait_until(lambda: fake.entries("exited"))
    [old, new] = fake.entries("started")
    [exited] = fake.entries("exited")
    assert exited["pid"] == old["pid"] != new["pid"]
    assert new["started"] >= exited["exited"]


def test_remove_log_databases_returns_what_it_deleted(tmp_path: Path) -> None:
    for name in ("logs_2.sqlite", "logs_2.sqlite-shm", "state_5.sqlite", "xlogs_2.sqlite"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert remove_log_databases(tmp_path) == ["logs_2.sqlite", "logs_2.sqlite-shm"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state_5.sqlite", "xlogs_2.sqlite"]
    assert remove_log_databases(tmp_path) == []


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


# -- a process that stops reading its input ---------------------------------------------------

CONTROL = 1.0
"""CONTROL_TIMEOUT in these tests (30 s in production)."""
CLEANUP = 1.0
"""CLEANUP_TIMEOUT in these tests (10 s in production)."""
PIPE_AND_BUFFER = 131_072
"""What a process that no longer reads its stdin still takes before ``drain()`` blocks:
the pipe (64 KiB) plus asyncio's write buffer (64 KiB high-water mark), on Linux."""

CHILD_READS_LATER = """
import pathlib, sys, time
go, received = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
while not go.exists():
    time.sleep(0.01)
with received.open("wb") as out:
    for line in sys.stdin.buffer:
        out.write(line)
        out.flush()
"""
"""A process that reads nothing until ``go`` exists, then copies every line it gets."""

CHILD_ASKS_LATER = """
import pathlib, sys, time
go = pathlib.Path(sys.argv[1])
while not go.exists():
    time.sleep(0.01)
sys.stdout.write('{"id": "srv-1", "method": "item/tool/requestUserInput", "params": {}}\\n')
sys.stdout.flush()
time.sleep(60)
"""
"""A process that never reads its stdin and asks something once ``go`` exists."""


@pytest.fixture
def short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_appserver, "CONTROL_TIMEOUT", CONTROL)
    monkeypatch.setattr(codex_appserver, "CLEANUP_TIMEOUT", CLEANUP)


def request_tasks() -> list[asyncio.Task[Any]]:
    """Unfinished runs of ``_AppServerConnection.request`` (the calls' shielded RPCs)."""
    return [
        task
        for task in asyncio.all_tasks()
        if not task.done()
        and getattr(task.get_coro(), "__qualname__", "").endswith("Connection.request")
    ]


async def bare_connection(*arguments: str) -> _AppServerConnection:
    """A JSON-RPC connection, without a provider, to ``python -c <arguments>``."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        *arguments,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    return _AppServerConnection(process, on_notification=lambda *_: None, on_exit=lambda *_: None)


async def test_codex_stderr_is_redacted_before_it_is_kept_or_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every stderr line is redacted as it is read: the tail that reaches error messages
    and the server log never holds a key or a token, not even one split after "Bearer"."""
    caplog.set_level(logging.DEBUG, logger="agentic_os.providers.codex_appserver")
    lines = [
        "auth header Bearer abc.def-123456",
        "key sk-ABCDEFGH12345678 rejected",
        "retrying with Bearer",
        "xyz-split-token-999 after the break",
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    ]
    text = "\n".join(lines) + "\n"
    conn = await bare_connection(
        f"import sys, time; sys.stderr.write({text!r}); sys.stderr.flush(); time.sleep(30)"
    )
    try:
        await wait_until(lambda: len(conn.stderr_tail) == len(lines))
    finally:
        await conn.close()
    kept = "\n".join(conn.stderr_tail)
    for secret in ("abc.def-123456", "ABCDEFGH12345678", "xyz-split-token-999", "c2lnbmF0dXJl"):
        assert secret not in kept
        assert secret not in caplog.text
    assert "after the break" in kept  # only the token itself is hidden


async def test_a_process_that_stops_reading_a_long_input_is_replaced(
    fake: FakeCodex, short_timeouts: None
) -> None:
    """Audit item 3: the app-server stops reading its stdin when a call writes a
    turn/start line longer than the pipe and the write buffer take. The request's timeout
    covers the write, so the call fails at the answer timeout instead of the deadline; the
    release gives up on the process and replaces it, and nothing is left waiting."""
    fake.options(wedge_after="thread/start")
    codex = CodexAppServerProvider(fake.settings(provider_timeout_seconds=6.0))
    request = make_request("[echo] " + "L'àvia explicà què passà a l'estació. " * 12_000)
    params = {"input": [{"type": "text", "text": render_transcript(request)}]}
    line = json.dumps({"id": 3, "method": "turn/start", "params": params}, ensure_ascii=False)
    assert len(line.encode()) > PIPE_AND_BUFFER
    try:
        started = time.monotonic()
        with pytest.raises(ProviderError) as caught:
            await collect(codex, request)
        assert caught.value.kind == "timeout"
        assert time.monotonic() - started < CONTROL + 1.5  # not the 6 s deadline
        [wedged] = fake.pids()
        await wait_until(lambda: not codex._background and codex._releasing == 0, CLEANUP + 5)
        assert codex._conn is None and not codex._threads
        assert process_gone(wedged)
        assert not request_tasks()
        assert fake.entries("wedged")
        assert not fake.received("turn/start")  # it never read a byte of it

        _, result = await collect(codex, make_request("[echo] de nou"))
        assert result.text.strip() == "de nou"
        assert len(set(fake.pids())) == 2
    finally:
        await codex.aclose()


async def test_a_process_that_stops_answering_before_a_call_is_replaced(
    fake: FakeCodex, short_timeouts: None
) -> None:
    """No thread/start answer: there is no thread to release, and so no request of the
    release that would find the process stuck. A cheap request does, and it is replaced."""
    fake.options(wedge_after="initialize")
    codex = CodexAppServerProvider(fake.settings())
    try:
        with pytest.raises(ProviderError) as caught:
            await collect(codex, make_request("[echo] hola"))
        assert caught.value.kind == "timeout"
        [wedged] = fake.pids()
        await wait_until(lambda: not codex._background and codex._releasing == 0)
        assert codex._conn is None
        assert process_gone(wedged)
        assert not fake.received("thread/start")

        _, result = await collect(codex, make_request("[echo] de nou"))
        assert result.text.strip() == "de nou"
        assert len(set(fake.pids())) == 2
    finally:
        await codex.aclose()


async def test_a_busy_process_is_not_replaced_under_a_running_call(
    fake: FakeCodex, short_timeouts: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thread/start answered too late fails only its own call. A cheap request shows
    the process still answers, so another call streaming on it goes on; the process is
    replaced once no call uses it (a late thread could never be released)."""
    send = _AppServerConnection._send

    async def slow_thread_start(self: _AppServerConnection, message: Any) -> None:
        params = message.get("params") or {}
        if message.get("method") == "thread/start" and params.get("baseInstructions") == "SLOW":
            await asyncio.sleep(CONTROL + 0.3)  # a busy server: no answer within CONTROL
        await send(self, message)

    monkeypatch.setattr(_AppServerConnection, "_send", slow_thread_start)
    codex = CodexAppServerProvider(fake.settings())
    try:
        long_call = asyncio.create_task(collect(codex, make_request("[long] parla")))
        await wait_until(lambda: fake.received("turn/start"))
        with pytest.raises(ProviderError) as caught:
            await collect(codex, GenerationRequest(system="SLOW", prompt="[echo] x"))
        assert caught.value.kind == "timeout"
        deltas, _ = await long_call  # not failed with «El procés de Codex s'ha aturat»
        assert len(deltas) > 100
        [busy] = fake.pids()
        await wait_until(lambda: not codex._background and codex._releasing == 0, CLEANUP + 5)
        assert codex._conn is None  # replaced once idle
        assert process_gone(busy)

        _, result = await collect(codex, make_request("[echo] de nou"))
        assert result.text.strip() == "de nou"
        assert len(set(fake.pids())) == 2
    finally:
        await codex.aclose()


@pytest.mark.parametrize("settled", [False, True])
async def test_closing_during_a_pending_release_leaves_nothing_behind(
    fake: FakeCodex, short_timeouts: None, caplog: pytest.LogCaptureFixture, settled: bool
) -> None:
    """aclose() while a call's release is pending, or not even started: the release count
    goes back to 0 and every request's exception is retrieved (asyncio logs none)."""
    caplog.set_level(logging.ERROR, logger="asyncio")
    fake.options(wedge_after="thread/start")
    codex = CodexAppServerProvider(fake.settings(provider_timeout_seconds=0.4))
    with pytest.raises(ProviderError):
        await collect(codex, make_request("[echo] " + "L'àvia explicà què passà. " * 12_000))
    assert codex._releasing == 1
    if settled:
        await asyncio.sleep(0.05)  # the release now waits for the call's requests
    await codex.aclose()
    await asyncio.sleep(0.3)
    gc.collect()
    await asyncio.sleep(0.05)
    assert codex._releasing == 0
    assert not [record for record in caplog.records if "never retrieved" in record.getMessage()]


async def test_the_release_of_a_call_is_bounded(
    fake: FakeCodex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a request that never ends (its own timeout makes that impossible) does not
    hold the release: after CONTROL_TIMEOUT + CLEANUP_TIMEOUT the process is replaced."""
    monkeypatch.setattr(codex_appserver, "CONTROL_TIMEOUT", 0.5)
    monkeypatch.setattr(codex_appserver, "CLEANUP_TIMEOUT", 0.5)
    resume = asyncio.Event()
    request = _AppServerConnection.request

    async def stuck_turn_start(
        self: _AppServerConnection, method: str, params: Any, timeout: float
    ) -> Any:
        if method == "turn/start":
            await resume.wait()  # neither an answer nor a timeout
        return await request(self, method, params, timeout)

    monkeypatch.setattr(_AppServerConnection, "request", stuck_turn_start)
    codex = CodexAppServerProvider(fake.settings(provider_timeout_seconds=0.5))
    try:
        with pytest.raises(ProviderError) as caught:
            await collect(codex, make_request("[echo] hola"))
        assert caught.value.kind == "timeout"
        [pid] = fake.pids()
        started = time.monotonic()
        await wait_until(lambda: codex._releasing == 0)
        assert time.monotonic() - started < 0.5 + 0.5 + 0.5
        assert codex._conn is None and not codex._threads
        await wait_until(lambda: not codex._background)
        assert process_gone(pid)
    finally:
        resume.set()
        await codex.aclose()


async def test_a_write_that_times_out_never_splits_a_frame(tmp_path: Path) -> None:
    """The timeout of a request, and the one of a notification, cover the write: a
    process that reads nothing cannot hold the caller or the write lock. Giving up on
    ``drain()`` leaves the whole line in the transport's buffer, ahead of the next one."""
    go, received = tmp_path / "go", tmp_path / "received.jsonl"
    conn = await bare_connection(CHILD_READS_LATER, str(go), str(received))
    try:
        text = "L'àvia explicà què passà. " * 12_000
        assert len(text.encode()) > PIPE_AND_BUFFER
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(conn.request("turn/start", {"text": text}, 0.3), 3)
        assert time.monotonic() - started < 0.3 + 1.0  # its own timeout, not the 3 s guard
        assert not conn._write_lock.locked() and not conn._pending

        started = time.monotonic()
        with pytest.raises(TimeoutError):  # the pipe is still full
            await asyncio.wait_for(conn.notify("initialized", timeout=0.3), 3)
        assert time.monotonic() - started < 0.3 + 1.0
        assert not conn._write_lock.locked()

        go.touch()  # it starts reading: both lines arrive whole and in order
        await wait_until(lambda: received.exists() and received.read_bytes().count(b"\n") == 2)
        first, second = (json.loads(line) for line in received.read_bytes().splitlines())
        assert first == {"id": 1, "method": "turn/start", "params": {"text": text}}
        assert second == {"method": "initialized"}
    finally:
        await conn.close()


async def test_an_answer_to_a_server_request_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The answer to a server -> client request cannot be written to a process that
    reads nothing either: it gives up after CLEANUP_TIMEOUT and frees the write lock."""
    monkeypatch.setattr(codex_appserver, "CLEANUP_TIMEOUT", 0.3)
    caplog.set_level(logging.INFO, logger="agentic_os.providers.codex_appserver")
    go = tmp_path / "go"
    conn = await bare_connection(CHILD_ASKS_LATER, str(go))
    try:
        with pytest.raises(TimeoutError):  # fills the pipe and the write buffer
            await asyncio.wait_for(conn.request("turn/start", {"text": "x" * 400_000}, 0.2), 3)
        go.touch()
        await wait_until(lambda: "request item/tool/requestUserInput" in caplog.text)
        await wait_until(lambda: not conn._tasks and not conn._write_lock.locked(), 0.3 + 1.5)
        assert "did not take the answer to item/tool/requestUserInput" in caplog.text
    finally:
        await conn.close()


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


@pytest.mark.parametrize(
    "options",
    [
        {"config_error": True, "config_model": "gpt-6-sol"},  # config.toml unreadable
        {"model_list_error": True},  # no model in config.toml, and no catalog
    ],
)
async def test_status_never_names_a_guessed_model(
    fake: FakeCodex, options: dict[str, object]
) -> None:
    """When Codex cannot say which model a thread gets, the status names none (N16): the
    engine keys its turn cache on it, and a guess would replay another model's answers."""
    fake.options(**options)
    codex = CodexAppServerProvider(fake.settings())
    try:
        status = await codex.status()
    finally:
        await codex.aclose()
    assert status.available
    assert status.detail == "Subscripció ChatGPT activa (Plus)"
    assert status.model == ""


# -- model list ------------------------------------------------------------------------------


async def test_list_models_reads_the_visible_catalog(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    fake.options(page_size=1)  # three pages: the client follows nextCursor
    models = await provider.list_models()
    assert provider.models_live and provider.fast_model == "gpt-6-luna"
    assert [(m.id, m.label, m.is_default) for m in models] == [
        ("gpt-6-astra", "GPT-6-Astra", True),
        ("gpt-6-sol", "GPT-6-Sol", False),
        ("gpt-7-nova", "GPT-7-Nova", False),
    ]  # the hidden entry is left out
    assert models[0].description == "El més capaç, per a la feina més exigent."
    assert models[2].description == "GPT-7-Nova (vendor description)."
    assert all(p.get("includeHidden") is False for p in fake.params("model/list")[-4:])
    requests = len(fake.params("model/list"))
    assert await provider.list_models() == models  # cached
    assert len(fake.params("model/list")) == requests
    await provider.list_models(refresh=True)
    assert len(fake.params("model/list")) > requests


@pytest.mark.parametrize(
    ("setting", "config_model", "default", "first"),
    [
        ("gpt-6-sol", None, "gpt-6-sol", "gpt-6-astra"),
        (None, "gpt-6-sol", "gpt-6-sol", "gpt-6-astra"),
        (None, "gpt-9-lab", "gpt-9-lab", "gpt-9-lab"),
    ],
)
async def test_list_models_marks_the_configured_default(
    fake: FakeCodex, setting: str | None, config_model: str | None, default: str, first: str
) -> None:
    fake.options(config_model=config_model)
    codex = CodexAppServerProvider(fake.settings(chatgpt_model=setting))
    try:
        models = await codex.list_models()
    finally:
        await codex.aclose()
    assert [m.id for m in models if m.is_default] == [default]
    assert models[0].id == first


async def test_list_models_falls_back_to_the_default(fake: FakeCodex, tmp_path: Path) -> None:
    fake.options(model_list_error=True)
    codex = CodexAppServerProvider(fake.settings(chatgpt_model="gpt-6-sol"))
    try:
        models = await codex.list_models()
    finally:
        await codex.aclose()
    assert not codex.models_live
    assert [(m.id, m.is_default) for m in models] == [("gpt-6-sol", True)]

    missing = CodexAppServerProvider(fake.settings(codex_cli_path=str(tmp_path / "nope")))
    try:
        models = await missing.list_models()
    finally:
        await missing.aclose()
    assert [(m.id, m.is_default) for m in models] == [("gpt-6-astra", True)]
    assert not missing.models_live


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


async def test_status_is_cached_before_the_slow_usage_read(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    fake.options(rate_limits_delay=1.0)
    # The engine gives status() a short budget: giving up must still leave a status.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(provider.status(), 0.5)
    status = await asyncio.wait_for(provider.status(), 0.5)
    assert status.available
    assert status.detail == "Subscripció ChatGPT activa (Plus)"
    assert len(fake.received("account/read")) == 1


async def test_respawn_backoff(fake: FakeCodex, provider: CodexAppServerProvider) -> None:
    provider._failures = 2  # two crashes in a row: wait 0.5 s before the next spawn
    provider._last_failure = time.monotonic()

    status = await provider.status()
    assert not status.available
    assert status.detail == "Codex s'ha aturat; es reiniciarà d'aquí a 1 s."
    assert not fake.pids()

    started = time.monotonic()
    _, result = await collect(provider, make_request("[echo] ja"))
    assert result.text.strip() == "ja"
    assert time.monotonic() - started >= 0.4


@pytest.mark.parametrize(
    ("failures", "delay"),
    [
        (0, 0.0),
        (1, 0.0),
        (2, 0.5),
        (3, 1.0),
        (7, 16.0),
        (8, BACKOFF_MAX),
        (1025, BACKOFF_MAX),
        (1026, BACKOFF_MAX),  # 2.0 ** 1024 overflows
        (10**6, BACKOFF_MAX),
    ],
)
async def test_respawn_backoff_delays(fake: FakeCodex, failures: int, delay: float) -> None:
    codex = CodexAppServerProvider(fake.settings())
    codex._failures = failures
    codex._last_failure = time.monotonic()
    assert codex._backoff_delay() == pytest.approx(delay, abs=0.05)


async def test_the_backoff_never_overflows(
    fake: FakeCodex, provider: CodexAppServerProvider
) -> None:
    """Audit item 26: after more than a thousand failed starts in a row the status still
    answers (and so would a call) instead of raising OverflowError until a restart."""
    provider._failures = 10**6
    provider._last_failure = time.monotonic()
    status = await provider.status()
    assert not status.available
    assert status.detail == "Codex s'ha aturat; es reiniciarà d'aquí a 30 s."
    assert not fake.pids()


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


def test_config_arguments_disable_tools_and_retries(tmp_path: Path) -> None:
    arguments = config_arguments(tmp_path / 'dir "x"')
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
        "agents.max_threads=1",
        "thread_unload_delay_secs=0",
    ):
        assert expected in pairs
    # A TOML string: codex parses the value after "=" as TOML.
    assert f'sqlite_home="{tmp_path}/dir \\"x\\""' in pairs
    assert not any(pair.startswith("model_catalog_json") for pair in pairs)


def test_fake_server_is_executable() -> None:
    assert os.access(FAKE_SERVER, os.X_OK)
