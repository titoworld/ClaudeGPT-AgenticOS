"""OpenAIApiProvider against an httpx2.MockTransport that speaks the Responses SSE stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest

from agentic_os.config import Settings
from agentic_os.domain import Usage
from agentic_os.providers.base import (
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    ProviderError,
    TextDelta,
)
from agentic_os.providers.openai_api import (
    MAX_RETRIES,
    MODEL_DESCRIPTIONS,
    TIMEOUT,
    OpenAIApiProvider,
    is_chat_model,
    usage_from_response,
)
from agentic_os.providers.prompt_format import to_chat_messages

SYSTEM = "Ets ChatGPT en un consell de dues IA."
Handler = Callable[[httpx2.Request], httpx2.Response]

USAGE = {
    "input_tokens": 1000,
    "input_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100},
    "output_tokens": 50,
    "output_tokens_details": {"reasoning_tokens": 10},
    "total_tokens": 1050,
}


def response(status: str, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-6-astra-2026-09-03",
        "status": status,
        "output": [],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
        "temperature": None,
        "top_p": None,
    }
    body.update(fields)
    return body


def delta(text: str, sequence: int = 1) -> dict[str, Any]:
    return {
        "type": "response.output_text.delta",
        "sequence_number": sequence,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": text,
        "logprobs": [],
    }


def completed(**fields: Any) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "sequence_number": 9,
        "response": response("completed", usage=USAGE, **fields),
    }


def sse(*events: dict[str, Any]) -> bytes:
    return b"".join(
        f"event: {event.get('type', 'error')}\ndata: {json.dumps(event)}\n\n".encode()
        for event in events
    )


def streaming(body: bytes | httpx2.AsyncByteStream) -> httpx2.Response:
    headers = {"content-type": "text/event-stream"}
    if isinstance(body, bytes):
        return httpx2.Response(200, headers=headers, content=body)
    return httpx2.Response(200, headers=headers, stream=body)


class Recorder:
    """MockTransport handler that records the JSON bodies it receives."""

    def __init__(self, reply: Callable[[], httpx2.Response]) -> None:
        self.reply = reply
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return self.reply()


class StalledStream(httpx2.AsyncByteStream):
    """Sends ``first`` and then waits (or raises ``error``); records whether it was closed."""

    def __init__(self, first: bytes, error: Exception | None = None) -> None:
        self.first = first
        self.error = error
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        if self.error is not None:
            raise self.error
        await asyncio.sleep(30)

    async def aclose(self) -> None:
        self.closed = True


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path,
        "chatgpt_model": None,
        "chatgpt_fast_model": None,
        "openai_api_key": "sk-test-key",
        "provider_timeout_seconds": 10.0,
        **overrides,
    }
    return Settings(**values)


def make_provider(tmp_path: Path, handler: Handler, **overrides: Any) -> OpenAIApiProvider:
    client = openai.AsyncOpenAI(
        api_key="sk-test-key",
        base_url="http://mock.local/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    return OpenAIApiProvider(make_settings(tmp_path, **overrides), client=client)


def make_request(prompt: str = "Hola", **kwargs: Any) -> GenerationRequest:
    return GenerationRequest(system=SYSTEM, prompt=prompt, **kwargs)


async def collect(
    provider: OpenAIApiProvider, request: GenerationRequest
) -> tuple[list[str], GenerationResult]:
    deltas: list[str] = []
    result: GenerationResult | None = None
    async for event in provider.stream(request):
        if isinstance(event, TextDelta):
            deltas.append(event.text)
        else:
            result = event
    assert result is not None
    return deltas, result


async def failure(provider: OpenAIApiProvider, request: GenerationRequest) -> ProviderError:
    with pytest.raises(ProviderError) as caught:
        await collect(provider, request)
    return caught.value


# -- streaming -------------------------------------------------------------------------------


async def test_streams_text_and_usage_without_cost(tmp_path: Path) -> None:
    recorder = Recorder(lambda: streaming(sse(delta("Bon "), delta("dia."), completed())))
    provider = make_provider(tmp_path, recorder)
    request = make_request(
        "I ara?",
        history=(ChatTurn("user", "Hola"), ChatTurn("assistant", "Hola!", agent="claude")),
        max_output_tokens=1234,
    )

    deltas, result = await collect(provider, request)

    assert deltas == ["Bon ", "dia."]
    assert result.text == "Bon dia."
    assert result.model == "gpt-6-astra-2026-09-03"
    # 1000 input tokens include 600 cache reads and 100 cache writes. No cost: the
    # engine prices every call centrally (owner overrides included).
    assert result.usage == Usage(
        input_tokens=300,
        output_tokens=50,
        cache_read_tokens=600,
        cache_write_tokens=100,
        reasoning_tokens=10,
    )
    assert result.ttft_ms is not None
    assert result.latency_ms >= result.ttft_ms

    [body] = recorder.bodies
    assert body["model"] == "gpt-6-astra"
    assert body["instructions"] == SYSTEM
    assert body["input"] == [
        {"role": role, "content": content} for role, content in to_chat_messages(request, "chatgpt")
    ]
    assert body["stream"] is True
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "medium"}
    assert body["max_output_tokens"] == 1234
    assert body["prompt_cache_key"] == "agentic-os-chatgpt-answer"
    assert not {"temperature", "top_p", "previous_response_id"} & set(body)


@pytest.mark.parametrize(
    ("overrides", "request_fields", "model", "effort"),
    [
        ({}, {"purpose": "revision"}, "gpt-6-astra", "low"),
        ({}, {"purpose": "summary", "fast": True}, "gpt-6-luna", "low"),
        ({"chatgpt_model": "gpt-6-sol"}, {"purpose": "synthesis"}, "gpt-6-sol", "medium"),
        ({"chatgpt_fast_model": "gpt-6-sol"}, {"fast": True}, "gpt-6-sol", "medium"),
        ({"chatgpt_model": "gpt-6-sol"}, {"model": "gpt-6-luna"}, "gpt-6-luna", "medium"),
    ],
)
async def test_model_and_effort(
    tmp_path: Path,
    overrides: dict[str, Any],
    request_fields: dict[str, Any],
    model: str,
    effort: str,
) -> None:
    recorder = Recorder(lambda: streaming(sse(delta("x"), completed())))
    provider = make_provider(tmp_path, recorder, **overrides)
    await collect(provider, make_request(**request_fields))
    [body] = recorder.bodies
    assert body["model"] == model
    assert body["reasoning"] == {"effort": effort}
    purpose = request_fields.get("purpose", "answer")
    assert body["prompt_cache_key"] == f"agentic-os-chatgpt-{purpose}"


async def test_incomplete_response_keeps_the_partial_text(tmp_path: Path) -> None:
    incomplete = {
        "type": "response.incomplete",
        "sequence_number": 3,
        "response": response(
            "incomplete",
            incomplete_details={"reason": "max_output_tokens"},
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        ),
    }
    provider = make_provider(tmp_path, lambda _: streaming(sse(delta("Mig "), incomplete)))
    _, result = await collect(provider, make_request())
    assert result.text == "Mig "
    # Missing details (None at runtime despite the int typing) count as 0.
    assert result.usage == Usage(input_tokens=10, output_tokens=5)


async def test_missing_usage_has_no_cost(tmp_path: Path) -> None:
    event = {
        "type": "response.completed",
        "sequence_number": 2,
        "response": response("completed", usage=None),
    }
    provider = make_provider(tmp_path, lambda _: streaming(sse(delta("Hola"), event)))
    _, result = await collect(provider, make_request())
    assert result.usage == Usage()


async def test_a_stream_without_a_final_event_fails(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, lambda _: streaming(sse(delta("Hola"))))
    error = await failure(provider, make_request())
    assert error.kind == "unavailable"
    assert error.retryable


# -- errors ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "kind", "retryable"),
    [
        (
            {
                "type": "response.failed",
                "sequence_number": 2,
                "response": response("failed", error={"code": "server_error", "message": "boom"}),
            },
            "unavailable",
            True,
        ),
        (
            {
                "type": "response.failed",
                "sequence_number": 2,
                "response": response(
                    "failed", error={"code": "invalid_prompt", "message": "bad prompt"}
                ),
            },
            "invalid",
            False,
        ),
        (
            {
                "type": "error",
                "sequence_number": 1,
                "code": "rate_limit_exceeded",
                "message": "slow down",
                "param": None,
            },
            "rate_limit",
            True,
        ),
        (
            {"error": {"message": "nested", "type": "server_error", "code": "server_error"}},
            "unavailable",
            True,
        ),
    ],
)
async def test_errors_inside_the_stream(
    tmp_path: Path, event: dict[str, Any], kind: str, retryable: bool
) -> None:
    provider = make_provider(tmp_path, lambda _: streaming(sse(delta("a"), event)))
    error = await failure(provider, make_request())
    assert (error.kind, error.retryable) == (kind, retryable)


@pytest.mark.parametrize(
    ("status", "code", "kind", "retryable"),
    [
        (401, "invalid_api_key", "auth", False),
        (403, None, "auth", False),
        (429, "rate_limit_exceeded", "rate_limit", True),
        (429, "insufficient_quota", "rate_limit", False),
        (400, "unsupported_value", "invalid", False),
        (500, None, "unavailable", True),
        (418, None, "internal", False),
    ],
)
async def test_http_errors(
    tmp_path: Path, status: int, code: str | None, kind: str, retryable: bool
) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        error = {"message": "Incorrect API key provided: sk-proj-abcdefghijkl", "code": code}
        return httpx2.Response(status, json={"error": error})

    error = await failure(make_provider(tmp_path, handler), make_request())
    assert (error.kind, error.retryable) == (kind, retryable)
    assert "abcdefghijkl" not in error.message


async def test_bad_request_explains_why(tmp_path: Path) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        error = {"message": "Unsupported value: 'none'", "code": "unsupported_value"}
        return httpx2.Response(400, json={"error": error})

    error = await failure(make_provider(tmp_path, handler), make_request())
    assert error.message == "L'API d'OpenAI ha rebutjat la petició: Unsupported value: 'none'"


async def test_mid_stream_read_timeout(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return streaming(
            StalledStream(sse(delta("a")), httpx2.ReadTimeout("stall", request=request))
        )

    error = await failure(make_provider(tmp_path, handler), make_request())
    assert error.kind == "timeout"


async def test_connection_error(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    error = await failure(make_provider(tmp_path, handler), make_request())
    assert (error.kind, error.retryable) == ("unavailable", True)


async def test_wall_clock_timeout_closes_the_stream(tmp_path: Path) -> None:
    body = StalledStream(sse(delta("a")))
    provider = make_provider(tmp_path, lambda _: streaming(body), provider_timeout_seconds=0.3)
    error = await failure(provider, make_request())
    assert error.kind == "timeout"
    assert body.closed


async def test_cancellation_closes_the_stream(tmp_path: Path) -> None:
    body = StalledStream(sse(delta("a")))
    provider = make_provider(tmp_path, lambda _: streaming(body))
    first = asyncio.Event()

    async def consume() -> None:
        async for _ in provider.stream(make_request()):
            first.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(first.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.closed


# -- configuration, status and lifecycle ------------------------------------------------------


async def test_missing_key(tmp_path: Path) -> None:
    provider = OpenAIApiProvider(make_settings(tmp_path, openai_api_key=None))
    status = await provider.status()
    assert not status.available
    assert status.detail == "Falta la clau d'API d'OpenAI (AOS_OPENAI_API_KEY)"
    error = await failure(provider, make_request())
    assert error.kind == "auth"


async def test_status_needs_no_network(tmp_path: Path) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise AssertionError("status() must not call the API")

    status = await make_provider(tmp_path, handler, chatgpt_model="gpt-6-sol").status()
    assert (status.agent, status.mode, status.available) == ("chatgpt", "api", True)
    assert status.model == "gpt-6-sol"
    assert status.detail == "Clau d'API configurada"
    assert not status.limits


async def test_own_client_uses_the_configured_timeouts_and_is_closed(tmp_path: Path) -> None:
    provider = OpenAIApiProvider(make_settings(tmp_path))
    assert (await provider.status()).available
    await provider.prewarm(make_request())
    client = provider._get_client()
    assert client.timeout == TIMEOUT
    assert client.max_retries == MAX_RETRIES
    assert client.api_key == "sk-test-key"
    await provider.aclose()
    assert client.is_closed()


async def test_an_injected_client_is_not_closed(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, lambda _: streaming(sse(completed())))
    client = provider._get_client()
    await provider.aclose()
    assert not client.is_closed()
    await client.close()


def test_usage_from_response_without_details() -> None:
    assert usage_from_response(None) == Usage()


# -- model list ------------------------------------------------------------------------------


def listing(*ids: str) -> Callable[[httpx2.Request], httpx2.Response]:
    data = [
        {"id": model_id, "object": "model", "created": 1_700_000_000 + i, "owned_by": "openai"}
        for i, model_id in enumerate(ids)
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET" and request.url.path == "/v1/models"
        return httpx2.Response(200, json={"object": "list", "data": data})

    return handler


def test_is_chat_model() -> None:
    chat = ["gpt-6-sol", "gpt-7", "o4-mini", "o9", "chatgpt-6o-latest", "gpt-6-sol-codex"]
    other = [
        "text-embedding-3-large",
        "gpt-6-sol-2026-09-22",
        "gpt-realtime",
        "gpt-4o-mini-tts",
        "gpt-4o-transcribe",
        "gpt-image-2",
        "gpt-4o-audio-preview",
        "gpt-4o-search-preview",
        "gpt-3.5-turbo-instruct",
        "dall-e-3",
        "whisper-1",
        "omni-moderation-latest",
        "davinci-002",
    ]
    assert all(is_chat_model(model) for model in chat)
    assert not any(is_chat_model(model) for model in other)


async def test_list_models_live_newest_first_and_cached(tmp_path: Path) -> None:
    calls: list[int] = []
    inner = listing("gpt-6-luna", "whisper-1", "gpt-6-astra", "o4-mini", "gpt-7-preview")

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return inner(request)

    provider = make_provider(tmp_path, handler)
    models = await provider.list_models()
    assert provider.models_live and provider.fast_model == "gpt-6-luna"
    assert [(m.id, m.is_default) for m in models] == [
        ("gpt-7-preview", False),
        ("o4-mini", False),
        ("gpt-6-astra", True),
        ("gpt-6-luna", False),
    ]
    assert models[2].description == MODEL_DESCRIPTIONS["gpt-6-astra"]
    assert models[0].label == "gpt-7-preview" and models[0].description == ""
    assert await provider.list_models() == models and len(calls) == 1
    await provider.list_models(refresh=True)
    assert len(calls) == 2


async def test_list_models_adds_the_configured_model(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, listing("gpt-6-sol"), chatgpt_model="gpt-6-sol-mini")
    models = await provider.list_models()
    assert [(m.id, m.is_default) for m in models] == [
        ("gpt-6-sol-mini", True),
        ("gpt-6-sol", False),
    ]


async def test_list_models_falls_back_without_raising(tmp_path: Path) -> None:
    def broken(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, json={"error": {"message": "boom"}})

    for handler in (broken, listing("text-embedding-3-small")):
        provider = make_provider(tmp_path, handler)
        models = await provider.list_models()
        assert not provider.models_live
        assert [(m.id, m.is_default) for m in models] == [
            ("gpt-6-astra", True),
            ("gpt-6-sol", False),
            ("gpt-6-luna", False),
        ]
        assert all(m.description for m in models)
    no_key = OpenAIApiProvider(make_settings(tmp_path, openai_api_key=None))
    assert len(await no_key.list_models()) == 3 and not no_key.models_live
