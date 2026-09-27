"""ClaudeApiProvider against a mock httpx2 transport serving SSE (no network)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest

from agentic_os.config import Settings
from agentic_os.domain import Purpose, Usage
from agentic_os.providers.base import (
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
    TextDelta,
)
from agentic_os.providers.claude_api import (
    FALLBACK_BETA,
    ClaudeApiProvider,
    estimate_cost,
)
from agentic_os.providers.prompt_format import to_chat_messages

SYSTEM = "Ets Claude. Respon en català."
Handler = Callable[[httpx2.Request], Awaitable[httpx2.Response]]
Event = tuple[str, dict[str, Any]]


def sse(events: list[Event]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def message_start(model: str, *, cache_read: int = 3000, cache_write: int = 40) -> Event:
    return (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 1,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                },
            },
        },
    )


def thinking_block(index: int) -> list[Event]:
    # display "omitted": no thinking text, only a signature.
    return [
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "signature_delta", "signature": "sig"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def text_block(index: int, parts: list[str]) -> list[Event]:
    events: list[Event] = [
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            },
        )
    ]
    for part in parts:
        delta = {"type": "text_delta", "text": part}
        events.append(
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": index, "delta": delta},
            )
        )
    events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    return events


def message_end(
    stop_reason: str, output_tokens: int, stop_details: dict[str, Any] | None = None
) -> list[Event]:
    usage = {"output_tokens": output_tokens, "output_tokens_details": {"thinking_tokens": 7}}
    delta = {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details}
    return [
        ("ping", {"type": "ping"}),
        ("message_delta", {"type": "message_delta", "delta": delta, "usage": usage}),
        ("message_stop", {"type": "message_stop"}),
    ]


def answer(model: str, parts: list[str]) -> bytes:
    return sse(
        [
            message_start(model),
            *thinking_block(0),
            *text_block(1, parts),
            *message_end("end_turn", 25),
        ]
    )


def ok(body: bytes) -> httpx2.Response:
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream", "request-id": "req_mock"}, content=body
    )


def error(status: int, kind: str, message: str) -> httpx2.Response:
    body = {"type": "error", "error": {"type": kind, "message": message}}
    return httpx2.Response(status, headers={"request-id": "req_mock"}, json=body)


class MockApi:
    """Records requests and answers them with ``handler``."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx2.Request] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return await self.handler(request)

    @property
    def body(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[-1].content)
        return data

    def client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key="sk-ant-api03-test",
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self)),
        )


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path,
        "claude_model": None,
        "claude_fast_model": None,
        "anthropic_api_key": "sk-ant-api03-test",
        "provider_timeout_seconds": 10.0,
        **overrides,
    }
    return Settings(**values)


def replying(body: bytes) -> Handler:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return ok(body)

    return handler


def failing(response: httpx2.Response) -> Handler:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return response

    return handler


async def run(
    api: MockApi, request: GenerationRequest, settings: Settings
) -> tuple[list[str], GenerationResult]:
    provider = ClaudeApiProvider(settings, api.client())
    deltas: list[str] = []
    result: GenerationResult | None = None
    try:
        async for event in provider.stream(request):
            if isinstance(event, TextDelta):
                deltas.append(event.text)
            else:
                result = event
    finally:
        await provider.aclose()
    assert result is not None
    return deltas, result


async def run_error(api: MockApi, request: GenerationRequest, settings: Settings) -> ProviderError:
    with pytest.raises(ProviderError) as info:
        await run(api, request, settings)
    return info.value


def request(purpose: Purpose = "answer", **overrides: Any) -> GenerationRequest:
    return GenerationRequest(system=SYSTEM, prompt="Hola, qui ets?", purpose=purpose, **overrides)


# -- streaming and request shape ------------------------------------------------------------


async def test_streams_text_usage_and_cost(tmp_path: Path) -> None:
    api = MockApi(replying(answer("claude-opus-5", ["Hola", ", ", "món!"])))
    req = request(
        history=(
            ChatTurn("user", "Què és uv?"),
            ChatTurn("assistant", "Un gestor de paquets.", agent="chatgpt"),
        ),
        context_summary="Parlàvem de Python.",
    )
    deltas, result = await run(api, req, make_settings(tmp_path))

    assert deltas == ["Hola", ", ", "món!"]
    assert result.text == "Hola, món!"
    assert result.model == "claude-opus-5"
    expected = Usage(
        input_tokens=12,
        output_tokens=25,
        cache_read_tokens=3000,
        cache_write_tokens=40,
        reasoning_tokens=7,
    )
    cost = (12 * 5 + 40 * 5 * 1.25 + 3000 * 0.5 + 25 * 25) / 1_000_000
    assert replace(result.usage, cost_usd=None) == expected
    assert result.usage.cost_usd == pytest.approx(cost)
    assert result.ttft_ms is not None and result.ttft_ms <= result.latency_ms

    (sent,) = api.requests
    assert sent.url.path == "/v1/messages" and sent.headers["x-api-key"] == "sk-ant-api03-test"
    assert sent.headers["anthropic-beta"] == FALLBACK_BETA
    body = api.body
    assert body["model"] == "claude-opus-5" and body["stream"] is True
    assert body["max_tokens"] == 16_000
    assert body["system"] == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert body["output_config"] == {"effort": "high"}
    assert body["fallbacks"] == "default"
    expected_messages = [
        {"role": role, "content": content} for role, content in to_chat_messages(req, "claude")
    ]
    assert body["messages"] == expected_messages
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert "[ChatGPT]" in body["messages"][1]["content"]


@pytest.mark.parametrize(
    ("purpose", "effort"),
    [("answer", "high"), ("revision", "medium"), ("synthesis", "high"), ("summary", "low")],
)
async def test_effort_per_purpose(tmp_path: Path, purpose: Purpose, effort: str) -> None:
    api = MockApi(replying(answer("claude-opus-5", ["ok"])))
    await run(api, request(purpose, max_output_tokens=20_000), make_settings(tmp_path))
    assert api.body["output_config"] == {"effort": effort}
    assert api.body["max_tokens"] == 20_000


async def test_haiku_runs_without_thinking_effort_or_fallbacks(tmp_path: Path) -> None:
    api = MockApi(replying(answer("claude-haiku-4-5", ["ok"])))
    _, result = await run(api, request("summary", fast=True), make_settings(tmp_path))
    body = api.body
    assert body["model"] == "claude-haiku-4-5"
    assert body["max_tokens"] == 8000
    assert not {"thinking", "output_config", "fallbacks"} & set(body)
    assert "anthropic-beta" not in api.requests[0].headers
    assert result.usage.cost_usd == pytest.approx(
        (12 * 1 + 40 * 1 * 1.25 + 3000 * 0.1 + 25 * 5) / 1_000_000
    )


async def test_sonnet_thinks_but_has_no_fallbacks(tmp_path: Path) -> None:
    api = MockApi(replying(answer("claude-sonnet-5", ["ok"])))
    await run(api, request(), make_settings(tmp_path, claude_model="claude-sonnet-5"))
    body = api.body
    assert body["model"] == "claude-sonnet-5"
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert "fallbacks" not in body and "anthropic-beta" not in api.requests[0].headers


async def test_explicit_model_and_opus_5_5_fallbacks(tmp_path: Path) -> None:
    api = MockApi(replying(answer("claude-opus-5-5", ["ok"])))
    _, result = await run(api, request(model="claude-opus-5-5"), make_settings(tmp_path))
    assert api.body["model"] == "claude-opus-5-5" and api.body["fallbacks"] == "default"
    assert result.usage.cost_usd == pytest.approx(
        (12 * 4 + 40 * 4 * 1.25 + 3000 * 0.2 + 25 * 20) / 1_000_000
    )


async def test_empty_system_prompt_is_omitted(tmp_path: Path) -> None:
    api = MockApi(replying(answer("claude-opus-5", ["ok"])))
    await run(api, GenerationRequest(system="", prompt="Hola"), make_settings(tmp_path))
    assert "system" not in api.body


# -- refusals and errors ------------------------------------------------------------------------


async def test_refusal_is_invalid(tmp_path: Path) -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "declined"}
    body = sse(
        [
            message_start("claude-opus-5"),
            *text_block(0, ["Part"]),
            *message_end("refusal", 3, details),
        ]
    )
    error = await run_error(MockApi(replying(body)), request(), make_settings(tmp_path))
    assert error.kind == "invalid" and not error.retryable
    assert "cyber" in error.message


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (400, "invalid", False),
        (401, "auth", False),
        (403, "auth", False),
        (404, "invalid", False),
        (429, "rate_limit", True),
        (500, "unavailable", True),
        (529, "unavailable", True),
    ],
)
async def test_http_errors(tmp_path: Path, status: int, kind: str, retryable: bool) -> None:
    api = MockApi(failing(error(status, "some_error", "messages: roles must alternate")))
    exc = await run_error(api, request(), make_settings(tmp_path))
    assert (exc.kind, exc.retryable) == (kind, retryable)
    if kind == "invalid":
        assert "roles must alternate" in exc.message


async def test_mid_stream_error_is_retryable(tmp_path: Path) -> None:
    body = sse(
        [
            message_start("claude-opus-5"),
            *text_block(0, ["parcial"])[:2],
            ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}),
        ]
    )
    api = MockApi(replying(body))
    provider = ClaudeApiProvider(make_settings(tmp_path), api.client())
    deltas: list[str] = []
    with pytest.raises(ProviderError) as info:
        async for event in provider.stream(request()):
            assert isinstance(event, TextDelta)
            deltas.append(event.text)
    assert deltas == ["parcial"]
    assert (info.value.kind, info.value.retryable) == ("unavailable", True)
    assert len(api.requests) == 1


async def test_connection_error(tmp_path: Path) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    exc = await run_error(MockApi(handler), request(), make_settings(tmp_path))
    assert (exc.kind, exc.retryable) == ("unavailable", True)


async def test_overall_timeout(tmp_path: Path) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(5)
        return ok(answer("claude-opus-5", ["tard"]))

    settings = make_settings(tmp_path, provider_timeout_seconds=0.2)
    exc = await run_error(MockApi(handler), request(), settings)
    assert exc.kind == "timeout"


async def test_missing_api_key(tmp_path: Path) -> None:
    provider = ClaudeApiProvider(make_settings(tmp_path, anthropic_api_key=None))
    status = await provider.status()
    assert not status.available
    assert status.detail == "Falta la clau d'API d'Anthropic (AOS_ANTHROPIC_API_KEY)"
    with pytest.raises(ProviderError) as info:
        async for _ in provider.stream(request()):
            pass  # pragma: no cover
    assert info.value.kind == "auth"
    await provider.aclose()


# -- lifecycle -----------------------------------------------------------------------------------


async def test_status_prewarm_and_protocol(tmp_path: Path) -> None:
    provider = ClaudeApiProvider(make_settings(tmp_path))
    assert isinstance(provider, Provider)
    assert (provider.agent, provider.mode) == ("claude", "api")
    await provider.prewarm(request())
    status = await provider.status()
    assert (status.available, status.model, status.detail) == (
        True,
        "claude-opus-5",
        "Clau d'API configurada",
    )
    assert status.limits == ()
    await provider.aclose()


async def test_aclose_closes_only_its_own_client(tmp_path: Path) -> None:
    owned = ClaudeApiProvider(make_settings(tmp_path))
    client = owned._get_client()
    await owned.aclose()
    assert client.is_closed()

    injected = MockApi(replying(b"")).client()
    provider = ClaudeApiProvider(make_settings(tmp_path), injected)
    await provider.aclose()
    assert not injected.is_closed()
    await injected.close()


def test_estimate_cost() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost("claude-opus-5", usage) == 30.0
    assert estimate_cost("claude-opus-5-5", usage) == 24.0
    assert estimate_cost("claude-opus-5-20260101", usage) == 30.0
    assert estimate_cost("claude-sonnet-5", usage) == 12.0
    assert estimate_cost("claude-haiku-4-5", Usage(cache_read_tokens=1_000_000)) == 0.1
    assert estimate_cost("claude-haiku-4-5", Usage(cache_write_tokens=1_000_000)) == 1.25
    assert estimate_cost("claude-unknown-9", usage) is None
