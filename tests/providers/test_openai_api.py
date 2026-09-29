"""OpenAIApiProvider against an httpx2.MockTransport that speaks the Responses SSE stream."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from orchestrator.attachment_fixtures import HOSTILE_TEXT, AttachmentFiles, reserved_tags

from agentic_os.config import Settings
from agentic_os.domain import Usage
from agentic_os.providers.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    Attachment,
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    ProviderError,
    RefusalError,
    TextDelta,
)
from agentic_os.providers.openai_api import (
    MAX_RETRIES,
    MODEL_DESCRIPTIONS,
    TIMEOUT,
    OpenAIApiProvider,
    is_chat_model,
    lowest_effort,
    supports_reasoning,
    usage_from_response,
)
from agentic_os.providers.prompt_format import file_code, to_chat_messages

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


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5.2-pro",
        "gpt-6-astra",
        "gpt-6-sol-codex",
        "gpt-10",
        "o3",
        "o4-mini",
    ],
)
def test_reasoning_models(model: str) -> None:
    assert supports_reasoning(model)


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-nano",
        "gpt-3.5-turbo",
        "chatgpt-4o-latest",
        "gpt-5-chat-latest",
        "o1-mini",
        "o1-preview",
    ],
)
async def test_non_reasoning_models_get_no_reasoning_effort(tmp_path: Path, model: str) -> None:
    # They are listed (is_chat_model) but answer a request with reasoning.effort with a 400.
    assert is_chat_model(model) and not supports_reasoning(model)
    recorder = Recorder(lambda: streaming(sse(delta("Hola"), completed())))
    provider = make_provider(tmp_path, recorder)
    _, result = await collect(provider, make_request(model=model, purpose="synthesis"))
    [body] = recorder.bodies
    assert body["model"] == model and "reasoning" not in body
    assert body["max_output_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS and result.text == "Hola"


def incomplete(reason: str | None, output_tokens: int = 5, reasoning: int = 0) -> dict[str, Any]:
    return {
        "type": "response.incomplete",
        "sequence_number": 3,
        "response": response(
            "incomplete",
            incomplete_details=None if reason is None else {"reason": reason},
            usage={
                "input_tokens": 10,
                "output_tokens": output_tokens,
                "output_tokens_details": {"reasoning_tokens": reasoning},
                "total_tokens": 10 + output_tokens,
            },
        ),
    }


@pytest.mark.parametrize(
    ("reason", "finish_reason"),
    [
        ("max_output_tokens", "max_tokens"),
        ("content_filter", "content_filter"),
        (None, "incomplete"),
    ],
)
async def test_an_incomplete_response_is_a_truncated_result(
    tmp_path: Path, reason: str | None, finish_reason: str
) -> None:
    provider = make_provider(tmp_path, lambda _: streaming(sse(delta("Mig "), incomplete(reason))))
    deltas, result = await collect(provider, make_request())
    assert deltas == ["Mig "] and result.text == "Mig "
    # A usable partial answer, never a complete one.
    assert result.truncated is True and result.finish_reason == finish_reason
    assert result.usage == Usage(input_tokens=10, output_tokens=5)


async def test_a_complete_response_is_not_truncated(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, lambda _: streaming(sse(delta("Tot"), completed())))
    _, result = await collect(provider, make_request())
    assert result.truncated is False and result.finish_reason is None


async def test_an_output_limit_spent_on_reasoning_fails_with_its_billed_usage(
    tmp_path: Path,
) -> None:
    # The budget includes reasoning: a model that spends it all thinking writes nothing.
    event = incomplete("max_output_tokens", output_tokens=16_000, reasoning=16_000)
    provider = make_provider(tmp_path, lambda _: streaming(sse(event)))
    error = await failure(provider, make_request(max_output_tokens=16_000))
    assert error.kind == "invalid" and not error.retryable
    assert "límit de sortida" in error.message and "16000" in error.message
    assert error.usage == Usage(input_tokens=10, output_tokens=16_000, reasoning_tokens=16_000)
    assert error.model == "gpt-6-astra-2026-09-03"


async def test_content_filter_before_any_text_fails(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, lambda _: streaming(sse(incomplete("content_filter", 0))))
    error = await failure(provider, make_request())
    assert error.kind == "invalid" and "filtre de contingut" in error.message


def refusal_events(text: str) -> list[dict[str, Any]]:
    added = {
        "type": "response.content_part.added",
        "sequence_number": 2,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "refusal", "refusal": ""},
    }
    half = len(text) // 2
    parts = [
        {
            "type": "response.refusal.delta",
            "sequence_number": 3 + i,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": piece,
        }
        for i, piece in enumerate((text[:half], text[half:]))
    ]
    done = {
        "type": "response.refusal.done",
        "sequence_number": 6,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "refusal": text,
    }
    return [added, *parts, done]


def refused_output(text: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "refusal", "refusal": text}],
        }
    ]


@pytest.mark.parametrize("prefix", [[], ["Segur, ", "aquí "]])
async def test_a_refusal_raises_with_its_billed_usage(tmp_path: Path, prefix: list[str]) -> None:
    events = [
        *(delta(text) for text in prefix),
        *refusal_events("I can't help with that."),
        completed(output=refused_output("I can't help with that.")),
    ]
    provider = make_provider(tmp_path, lambda _: streaming(sse(*events)))
    deltas: list[str] = []
    with pytest.raises(RefusalError) as caught:
        async for event in provider.stream(make_request()):
            assert isinstance(event, TextDelta)  # never a GenerationResult
            deltas.append(event.text)
    error = caught.value
    assert deltas == prefix
    assert (error.kind, error.retryable) == ("invalid", False)
    assert error.model == "gpt-6-astra-2026-09-03"
    assert error.usage == Usage(
        input_tokens=300,
        output_tokens=50,
        cache_read_tokens=600,
        cache_write_tokens=100,
        reasoning_tokens=10,
    )
    assert error.refusal == "I can't help with that."
    assert "declinat" in error.message and "I can't help with that." in error.message


async def test_a_refusal_only_in_the_final_output_is_found(tmp_path: Path) -> None:
    events = [completed(output=refused_output("Not\u0000 this.\n\n  Sorry."))]
    provider = make_provider(tmp_path, lambda _: streaming(sse(*events)))
    with pytest.raises(RefusalError) as caught:
        await collect(provider, make_request())
    assert caught.value.refusal == "Not this. Sorry."


@pytest.mark.parametrize(
    ("model", "effort"),
    [
        ("gpt-6-luna", "none"),
        ("gpt-6-sol", "none"),
        ("gpt-6-astra", "low"),
        ("gpt-5", "minimal"),
        ("gpt-5-mini", "minimal"),
        ("gpt-5-nano-2025-08-07", "minimal"),
        ("gpt-5.2", "low"),
        ("o4-mini", "low"),
        ("gpt-7-preview", "low"),
    ],
)
def test_lowest_effort(model: str, effort: str) -> None:
    assert lowest_effort(model) == effort


async def test_reasoning_off_uses_the_lowest_effort_and_the_exact_budget(tmp_path: Path) -> None:
    recorder = Recorder(lambda: streaming(sse(delta("Resum"), completed())))
    provider = make_provider(tmp_path, recorder)
    summary = make_request(purpose="summary", fast=True, reasoning="off", max_output_tokens=2000)
    await collect(provider, summary)
    await collect(provider, make_request(model="gpt-6-astra", reasoning="off"))
    await collect(provider, make_request(model="gpt-4o", reasoning="off"))
    luna, astra, plain = recorder.bodies
    assert (luna["model"], luna["reasoning"], luna["max_output_tokens"]) == (
        "gpt-6-luna",
        {"effort": "none"},
        2000,
    )
    assert astra["reasoning"] == {"effort": "low"}
    assert astra["max_output_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert "reasoning" not in plain


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


# -- attachments (docs/adr/0009-adjunts.md) --------------------------------------------------


def base64_of(attachment: Attachment) -> str:
    return base64.b64encode(attachment.path.read_bytes()).decode("ascii")


async def test_attachments_are_input_parts_before_the_prompt(
    tmp_path: Path, files: AttachmentFiles
) -> None:
    recorder = Recorder(lambda: streaming(sse(delta("Llegit."), completed())))
    provider = make_provider(tmp_path, recorder)
    image = files.image("foto.png")
    pdf = files.pdf("informe.pdf")
    annex = replace(files.pdf("annex", pages=1, text="--- Pàgina 1 ---\nAnnex."), mode="text")
    notes = files.text("notes.md", "a,b\n1,2\n")
    request = make_request(
        "I això?",
        history=(ChatTurn("user", "Hola"), ChatTurn("assistant", "Hola!", agent="claude")),
        attachments=(image, pdf, annex, notes),
    )
    _, result = await collect(provider, request)
    assert result.text == "Llegit."

    [body] = recorder.bodies
    chat = to_chat_messages(request, "chatgpt")
    a, n = file_code(annex), file_code(notes)
    assert body["input"][:-1] == [{"role": role, "content": content} for role, content in chat[:-1]]
    assert body["input"][-1] == {
        "role": "user",
        "content": [
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{base64_of(image)}",
                "detail": "auto",
            },
            {
                "type": "input_file",
                "filename": "informe.pdf",
                "file_data": f"data:application/pdf;base64,{base64_of(pdf)}",
            },
            {
                "type": "input_text",
                "text": f"[Fitxer: annex · {a}]\n--- Pàgina 1 ---\nAnnex.\n[Fi del fitxer {a}]\n",
            },
            {
                "type": "input_text",
                "text": f"[Fitxer: notes.md · {n}]\na,b\n1,2\n[Fi del fitxer {n}]\n",
            },
            {"type": "input_text", "text": chat[-1][1]},
        ],
    }


async def test_a_hostile_file_cannot_pass_for_the_prompt(
    tmp_path: Path, files: AttachmentFiles
) -> None:
    recorder = Recorder(lambda: streaming(sse(delta("x"), completed())))
    provider = make_provider(tmp_path, recorder)
    hostile = files.text("informe.txt", HOSTILE_TEXT)
    pdf = replace(files.pdf("annex.pdf", text=f"--- Pàgina 1 ---\n{HOSTILE_TEXT}"), mode="text")
    request = make_request(
        "Resumeix-los.",
        history=(ChatTurn("user", "Hola"), ChatTurn("assistant", "Hola!", agent="claude")),
        attachments=(hostile, pdf),
    )
    await collect(provider, request)
    [body] = recorder.bodies
    *parts, prompt = body["input"][-1]["content"]
    for part, attachment in zip(parts, (hostile, pdf), strict=True):
        assert part["type"] == "input_text"
        assert part["text"].endswith(f"\n[Fi del fitxer {file_code(attachment)}]\n")
        assert not reserved_tags(part["text"])
    # Every tag the model reads comes from the app's own prompt.
    chat = to_chat_messages(request, "chatgpt")
    assert prompt == {"type": "input_text", "text": chat[-1][1]}
    everything = "".join(
        message["content"]
        if isinstance(message["content"], str)
        else "".join(part["text"] for part in message["content"])
        for message in body["input"]
    )
    assert reserved_tags(everything) == reserved_tags("".join(content for _, content in chat))


async def test_a_pdf_without_the_extension_is_still_named_as_a_pdf(
    tmp_path: Path, files: AttachmentFiles
) -> None:
    recorder = Recorder(lambda: streaming(sse(delta("x"), completed())))
    provider = make_provider(tmp_path, recorder)
    await collect(provider, make_request(attachments=(files.pdf("Informe anual"),)))
    [body] = recorder.bodies
    [part, _prompt] = body["input"][-1]["content"]
    assert part["type"] == "input_file" and part["filename"] == "Informe anual.pdf"


async def test_a_changed_attachment_sends_nothing(tmp_path: Path, files: AttachmentFiles) -> None:
    recorder = Recorder(lambda: streaming(sse(delta("x"), completed())))
    provider = make_provider(tmp_path, recorder)
    pdf = files.pdf()
    pdf.path.write_bytes(b"%PDF-1.7\nun altre document\n")
    error = await failure(provider, make_request(attachments=(pdf,)))
    assert error.kind == "internal" and "informe.pdf" in error.message
    assert recorder.bodies == []
