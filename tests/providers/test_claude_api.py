"""ClaudeApiProvider against a mock httpx2 transport serving SSE (no network)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
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
    STATIC_MODELS,
    ClaudeApiProvider,
    ModelSupport,
    RefusalError,
    model_support,
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


def fallback_block(index: int, source: str, target: str, category: str | None) -> list[Event]:
    block = {
        "type": "fallback",
        "from": {"model": source},
        "to": {"model": target},
        "trigger": {"type": "refusal", "category": category},
    }
    return [
        (
            "content_block_start",
            {"type": "content_block_start", "index": index, "content_block": block},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def iteration(
    kind: str, model: str, input_tokens: int, output_tokens: int, cache_read: int = 0
) -> dict[str, Any]:
    return {
        "type": kind,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": 0,
    }


def final_delta(
    stop_reason: str,
    usage: dict[str, Any],
    stop_details: dict[str, Any] | None = None,
) -> list[Event]:
    delta = {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details}
    return [
        ("message_delta", {"type": "message_delta", "delta": delta, "usage": usage}),
        ("message_stop", {"type": "message_stop"}),
    ]


def refusal_details(category: str | None) -> dict[str, Any]:
    return {"type": "refusal", "category": category, "explanation": None}


# -- streaming and request shape ------------------------------------------------------------


async def test_streams_text_and_usage_without_cost(tmp_path: Path) -> None:
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
    # No cost: the engine prices every call centrally (owner overrides included).
    assert result.usage == Usage(
        input_tokens=12,
        output_tokens=25,
        cache_read_tokens=3000,
        cache_write_tokens=40,
        reasoning_tokens=7,
    )
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
    await run(api, request("summary", fast=True), make_settings(tmp_path))
    body = api.body
    assert body["model"] == "claude-haiku-4-5"
    assert body["max_tokens"] == 8000
    assert not {"thinking", "output_config", "fallbacks"} & set(body)
    assert "anthropic-beta" not in api.requests[0].headers


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
    assert result.model == "claude-opus-5-5" and result.usage.cost_usd is None


async def test_empty_system_prompt_is_omitted(tmp_path: Path) -> None:
    api = MockApi(replying(answer("claude-opus-5", ["ok"])))
    await run(api, GenerationRequest(system="", prompt="Hola"), make_settings(tmp_path))
    assert "system" not in api.body


ADAPTIVE = ModelSupport("adaptive", effort=True)
BUDGET = ModelSupport("budget", effort=False)
NONE = ModelSupport("none", effort=False)


@pytest.mark.parametrize(
    ("model", "support"),
    [
        ("claude-opus-5", ADAPTIVE),
        ("claude-opus-5-5", ADAPTIVE),
        ("claude-sonnet-5", ADAPTIVE),
        ("claude-fable-5-1", ADAPTIVE),
        ("claude-mythos-5-1", ADAPTIVE),
        ("claude-opus-4-8", ADAPTIVE),
        ("claude-opus-4-6", ADAPTIVE),
        ("claude-sonnet-4-6", ADAPTIVE),
        ("claude-opus-6-preview", ADAPTIVE),
        ("claude-sonnet-4-5", BUDGET),
        ("claude-sonnet-4-5-20250929", BUDGET),
        ("claude-opus-4-5", BUDGET),
        ("claude-opus-4-1-20250805", BUDGET),
        ("claude-opus-4-20250514", BUDGET),
        ("claude-sonnet-4-0", BUDGET),
        ("claude-3-7-sonnet-20250219", BUDGET),
        ("claude-haiku-4-5", NONE),
        ("claude-3-haiku-20240307", NONE),
        ("claude-3-5-sonnet-20241022", NONE),
        ("claude-experimental", NONE),
    ],
)
def test_model_support_by_id(model: str, support: ModelSupport) -> None:
    assert model_support(model) == support


@pytest.mark.parametrize(
    ("model", "purpose", "thinking", "max_tokens"),
    [
        ("claude-sonnet-4-5", "answer", {"type": "enabled", "budget_tokens": 8000}, 16_000),
        ("claude-opus-4-1", "revision", {"type": "enabled", "budget_tokens": 4096}, 16_000),
        ("claude-opus-4-5", "summary", {"type": "enabled", "budget_tokens": 2048}, 16_000),
        ("claude-3-5-sonnet-20241022", "answer", None, 8000),
    ],
)
async def test_models_without_adaptive_thinking_get_neither_it_nor_effort(
    tmp_path: Path, model: str, purpose: Purpose, thinking: dict[str, Any] | None, max_tokens: int
) -> None:
    # Adaptive thinking and effort start with the 4.6 models: older ones answer both
    # with a 400, so they think with a budget (or not at all).
    api = MockApi(replying(answer(model, ["ok"])))
    await run(api, request(purpose, model=model), make_settings(tmp_path))
    body = api.body
    assert body.get("thinking") == thinking
    assert "output_config" not in body and "fallbacks" not in body
    assert body["max_tokens"] == max_tokens


# -- model list ------------------------------------------------------------------------------


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
    ("category", "output_tokens", "billed"),
    [
        ("cyber", 300, True),  # mid-stream: input and streamed output are billed
        (None, 300, True),
        ("cyber", 0, False),  # before any output: billed only in some categories
        ("general_harms", 0, False),
        (None, 0, False),
        ("bio", 0, True),
        ("frontier_llm", 0, True),
        ("reasoning_extraction", 0, True),
    ],
)
async def test_refusal_error_carries_the_billed_usage(
    tmp_path: Path, category: str | None, output_tokens: int, billed: bool
) -> None:
    start = message_start("claude-opus-5", cache_read=1000, cache_write=200)
    start[1]["message"]["usage"]["input_tokens"] = 50_000
    parts = ["Resposta que es talla"] if output_tokens else []
    body = sse(
        [
            start,
            *(text_block(0, parts) if parts else []),
            *message_end("refusal", output_tokens, refusal_details(category)),
        ]
    )
    error = await run_error(MockApi(replying(body)), request(), make_settings(tmp_path))
    assert isinstance(error, RefusalError)
    assert (error.kind, error.retryable, error.model) == ("invalid", False, "claude-opus-5")
    # The engine reads it this way to record the cost of the failed call.
    assert getattr(error, "usage", None) == error.usage
    expected = Usage(
        input_tokens=50_000,
        output_tokens=output_tokens,
        cache_read_tokens=1000,
        cache_write_tokens=200,
        reasoning_tokens=7,
    )
    assert error.usage == (expected if billed else Usage())
    assert error.usage.cost_usd is None  # the engine prices it


async def test_mid_stream_fallback_counts_the_declined_attempt(tmp_path: Path) -> None:
    # Fable 5.1 streams part of the answer, declines, and Opus 4.8 continues. Top-level
    # usage covers only the serving attempt; usage.iterations holds both.
    iterations = [
        iteration("message", "claude-fable-5-1", 20_000, 800, cache_read=500),
        iteration("fallback_message", "claude-opus-4-8", 20_100, 1200),
    ]
    start = message_start("claude-fable-5-1", cache_read=500, cache_write=0)
    start[1]["message"]["usage"]["input_tokens"] = 20_000
    usage = {
        "input_tokens": 20_100,
        "output_tokens": 1200,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens_details": {"thinking_tokens": 90},
        "iterations": iterations,
    }
    body = sse(
        [
            start,
            *text_block(0, ["Part ", "one "]),
            *fallback_block(1, "claude-fable-5-1", "claude-opus-4-8", "cyber"),
            *text_block(2, ["part two."]),
            *final_delta("end_turn", usage),
        ]
    )
    req = request(model="claude-fable-5-1")
    deltas, result = await run(MockApi(replying(body)), req, make_settings(tmp_path))
    assert "".join(deltas) == result.text == "Part one part two."
    assert result.model == "claude-opus-4-8"
    assert result.usage == Usage(
        input_tokens=40_100,
        output_tokens=2000,
        cache_read_tokens=500,
        cache_write_tokens=0,
        reasoning_tokens=90,
    )


@pytest.mark.parametrize(("category", "billed"), [("cyber", False), ("bio", True)])
async def test_pre_output_fallback_counts_only_a_billed_decline(
    tmp_path: Path, category: str, billed: bool
) -> None:
    # A decline before any output: message_start already names the fallback model and
    # the fallback block comes first. The declined attempt is billed by its category.
    iterations = [
        iteration("message", "claude-opus-5", 535, 0),
        iteration("fallback_message", "claude-opus-4-8", 412, 264),
    ]
    start = message_start("claude-opus-4-8", cache_read=0, cache_write=0)
    start[1]["message"]["usage"]["input_tokens"] = 412
    usage = {"input_tokens": 412, "output_tokens": 264, "iterations": iterations}
    body = sse(
        [
            start,
            *fallback_block(0, "claude-opus-5", "claude-opus-4-8", category),
            *text_block(1, ["Hola!"]),
            *final_delta("end_turn", usage),
        ]
    )
    _, result = await run(MockApi(replying(body)), request(), make_settings(tmp_path))
    assert result.model == "claude-opus-4-8"
    served = Usage(input_tokens=412, output_tokens=264)
    assert result.usage == (served + Usage(input_tokens=535) if billed else served)


async def test_refusal_after_a_fallback_bills_every_billed_attempt(tmp_path: Path) -> None:
    # Fable 5.1 declines mid-stream, Opus 4.8 also declines before any output (cyber,
    # not billed): the refusal carries the Fable attempt only.
    iterations = [
        iteration("message", "claude-fable-5-1", 9000, 400),
        iteration("fallback_message", "claude-opus-4-8", 9100, 0),
    ]
    start = message_start("claude-fable-5-1", cache_read=0, cache_write=0)
    start[1]["message"]["usage"]["input_tokens"] = 9000
    usage = {"input_tokens": 9100, "output_tokens": 0, "iterations": iterations}
    body = sse(
        [
            start,
            *text_block(0, ["Comença"]),
            *fallback_block(1, "claude-fable-5-1", "claude-opus-4-8", "cyber"),
            *final_delta("refusal", usage, refusal_details("cyber")),
        ]
    )
    req = request(model="claude-fable-5-1")
    error = await run_error(MockApi(replying(body)), req, make_settings(tmp_path))
    assert isinstance(error, RefusalError) and error.model == "claude-opus-4-8"
    assert error.usage == Usage(input_tokens=9000, output_tokens=400)


@pytest.mark.parametrize(
    "iterations",
    [
        # Sticky routing: the fallback model served directly, nothing declined.
        [iteration("fallback_message", "claude-opus-4-8", 12, 25)],
        # A plain call that reports its only iteration: never counted twice.
        [iteration("message", "claude-opus-5", 12, 25)],
    ],
)
async def test_iterations_without_a_declined_attempt_add_nothing(
    tmp_path: Path, iterations: list[dict[str, Any]]
) -> None:
    usage = {
        "input_tokens": 12,
        "output_tokens": 25,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "iterations": iterations,
    }
    body = sse(
        [
            message_start(iterations[0]["model"], cache_read=0, cache_write=0),
            *text_block(0, ["ok"]),
            *final_delta("end_turn", usage),
        ]
    )
    _, result = await run(MockApi(replying(body)), request(), make_settings(tmp_path))
    assert result.usage == Usage(input_tokens=12, output_tokens=25)


def fallback_without_category(index: int, source: str, target: str, trigger: Any) -> list[Event]:
    """A fallback block whose ``trigger`` is missing (``...``), null or has no category."""
    block: dict[str, Any] = {"type": "fallback", "from": {"model": source}, "to": {"model": target}}
    if trigger is not ...:
        block["trigger"] = trigger
    return [
        (
            "content_block_start",
            {"type": "content_block_start", "index": index, "content_block": block},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


@pytest.mark.parametrize("trigger", [..., None, {"type": "refusal"}])
@pytest.mark.parametrize(("declined_output", "billed"), [(0, False), (300, True)])
async def test_a_fallback_block_without_a_category_still_returns_the_answer(
    tmp_path: Path, trigger: Any, declined_output: int, billed: bool
) -> None:
    # The documented pre-output example has no ``trigger``: before, reading its category
    # raised AttributeError after the whole answer had streamed.
    iterations = [
        iteration("message", "claude-fable-5", 535, declined_output),
        iteration("fallback_message", "claude-opus-4-8", 412, 264),
    ]
    start = message_start("claude-opus-4-8", cache_read=0, cache_write=0)
    usage = {"input_tokens": 412, "output_tokens": 264, "iterations": iterations}
    body = sse(
        [
            start,
            *fallback_without_category(0, "claude-fable-5", "claude-opus-4-8", trigger),
            *text_block(1, ["Hola!"]),
            *final_delta("end_turn", usage),
        ]
    )
    deltas, result = await run(MockApi(replying(body)), request(), make_settings(tmp_path))
    assert "".join(deltas) == result.text == "Hola!"
    served = Usage(input_tokens=412, output_tokens=264)
    declined = Usage(input_tokens=535, output_tokens=declined_output)
    # Without a category only a decline after some output is billed.
    assert result.usage == (served + declined if billed else served)


async def test_iterations_of_an_unknown_type_are_not_declined_hops(tmp_path: Path) -> None:
    # The SDK builds an entry of a type it does not know as a ``message`` entry class:
    # before, it was counted as a declined hop and took the first fallback's category.
    iterations = [
        iteration("advisor_future", "claude-haiku-4-5", 300, 0),
        iteration("message", "claude-opus-5", 535, 0),
        iteration("fallback_message", "claude-opus-4-8", 412, 264),
    ]
    start = message_start("claude-opus-4-8", cache_read=0, cache_write=0)
    usage = {"input_tokens": 412, "output_tokens": 264, "iterations": iterations}
    body = sse(
        [
            start,
            *fallback_block(0, "claude-opus-5", "claude-opus-4-8", "bio"),
            *text_block(1, ["Hola!"]),
            *final_delta("end_turn", usage),
        ]
    )
    _, result = await run(MockApi(replying(body)), request(), make_settings(tmp_path))
    # The bio decline (billed before any output) is counted, the unknown entry is not.
    assert result.usage == Usage(input_tokens=412 + 535, output_tokens=264)


async def test_a_refusal_without_stop_details_carries_its_streamed_usage(
    tmp_path: Path,
) -> None:
    start = message_start("claude-opus-5", cache_read=0, cache_write=0)
    start[1]["message"]["usage"]["input_tokens"] = 7000
    body = sse(
        [
            start,
            *text_block(0, ["Comença"]),
            *final_delta("refusal", {"output_tokens": 120}),
        ]
    )
    error = await run_error(MockApi(replying(body)), request(), make_settings(tmp_path))
    assert isinstance(error, RefusalError) and "categoria" not in error.message
    assert error.usage == Usage(input_tokens=7000, output_tokens=120)


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


def model_entry(
    model_id: str,
    name: str,
    context: int = 1_000_000,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "model",
        "id": model_id,
        "display_name": name,
        "created_at": "2026-09-01T00:00:00Z",
        "max_input_tokens": context,
        "max_tokens": 128_000,
        "capabilities": capabilities,
    }


def capabilities(
    *, adaptive: bool, enabled: bool, effort: bool, low: bool = True
) -> dict[str, Any]:
    def yes(flag: bool) -> dict[str, bool]:
        return {"supported": flag}

    return {
        "batch": yes(True),
        "citations": yes(True),
        "code_execution": yes(True),
        "context_management": {"supported": True},
        "effort": {
            "supported": effort,
            "low": yes(effort and low),
            "medium": yes(effort),
            "high": yes(effort),
            "max": yes(effort),
        },
        "image_input": yes(True),
        "pdf_input": yes(True),
        "structured_outputs": yes(True),
        "thinking": {
            "supported": adaptive or enabled,
            "types": {"adaptive": yes(adaptive), "enabled": yes(enabled)},
        },
    }


def models_page(*entries: dict[str, Any]) -> httpx2.Response:
    body = {
        "data": list(entries),
        "has_more": False,
        "first_id": entries[0]["id"] if entries else None,
        "last_id": entries[-1]["id"] if entries else None,
    }
    return httpx2.Response(200, headers={"request-id": "req_mock"}, json=body)


async def test_list_models_live_and_cached(tmp_path: Path) -> None:
    page = models_page(
        model_entry("claude-fable-5-1", "Claude Fable 5.1"),
        model_entry("claude-opus-5", "Claude Opus 5"),
        model_entry("claude-haiku-4-5-20251001", "Claude Haiku 4.5", 200_000),
    )
    api = MockApi(failing(page))
    provider = ClaudeApiProvider(make_settings(tmp_path), api.client())
    try:
        models = await provider.list_models()
        assert provider.models_live and provider.fast_model == "claude-haiku-4-5"
        assert [(m.id, m.label, m.is_default, m.context_window) for m in models] == [
            ("claude-fable-5-1", "Claude Fable 5.1", False, 1_000_000),
            ("claude-opus-5", "Claude Opus 5", True, 1_000_000),
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5", False, 200_000),
        ]
        assert models[2].description.startswith("El més ràpid")
        assert api.requests[0].url.path == "/v1/models"
        assert await provider.list_models() == models  # cached: no second request
        assert len(api.requests) == 1
        await provider.list_models(refresh=True)
        assert len(api.requests) == 2
    finally:
        await provider.aclose()


async def test_list_models_adds_a_configured_model_missing_from_the_list(tmp_path: Path) -> None:
    api = MockApi(failing(models_page(model_entry("claude-opus-5", "Claude Opus 5"))))
    settings = make_settings(tmp_path, claude_model="claude-opus-6-preview")
    provider = ClaudeApiProvider(settings, api.client())
    models = await provider.list_models()
    await provider.aclose()
    assert [(m.id, m.is_default) for m in models] == [
        ("claude-opus-6-preview", True),
        ("claude-opus-5", False),
    ]


@pytest.mark.parametrize(
    "response", [error(500, "api_error", "boom"), models_page(), error(401, "auth", "no")]
)
async def test_list_models_falls_back_without_raising(
    tmp_path: Path, response: httpx2.Response
) -> None:
    provider = ClaudeApiProvider(make_settings(tmp_path), MockApi(failing(response)).client())
    models = await provider.list_models()
    await provider.aclose()
    assert not provider.models_live
    assert [m.id for m in models] == [model_id for model_id, _, _ in STATIC_MODELS]
    assert [m.id for m in models if m.is_default] == ["claude-opus-5"]
    assert all(m.description for m in models)


async def test_listed_capabilities_decide_thinking_and_effort(tmp_path: Path) -> None:
    page = models_page(
        # Budget thinking plus effort (as Opus 4.5 reports), unlike the id-based guess.
        model_entry(
            "claude-opus-4-5-20251101",
            "Claude Opus 4.5",
            capabilities=capabilities(adaptive=False, enabled=True, effort=True),
        ),
        # An id the heuristic does not know, described as adaptive.
        model_entry(
            "claude-nova-1",
            "Claude Nova 1",
            capabilities=capabilities(adaptive=True, enabled=False, effort=True),
        ),
        # Effort without the low level: never sent.
        model_entry(
            "claude-sonnet-5",
            "Claude Sonnet 5",
            capabilities=capabilities(adaptive=True, enabled=False, effort=True, low=False),
        ),
        # Haiku keeps running without thinking whatever it supports.
        model_entry(
            "claude-haiku-4-5",
            "Claude Haiku 4.5",
            200_000,
            capabilities=capabilities(adaptive=False, enabled=True, effort=True),
        ),
    )

    async def handler(sent: httpx2.Request) -> httpx2.Response:
        if sent.url.path == "/v1/models":
            return page
        return ok(answer(json.loads(sent.content)["model"], ["ok"]))

    api = MockApi(handler)
    provider = ClaudeApiProvider(make_settings(tmp_path), api.client())
    try:
        await provider.list_models()
        assert provider.models_live

        async def sent_for(model: str) -> dict[str, Any]:
            async for _ in provider.stream(request(model=model)):
                pass
            return api.body

        body = await sent_for("claude-opus-4-5-20251101")
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 8000}
        assert body["output_config"] == {"effort": "high"}
        body = await sent_for("claude-nova-1")
        assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
        assert body["output_config"] == {"effort": "high"}
        body = await sent_for("claude-sonnet-5")
        assert body["thinking"]["type"] == "adaptive" and "output_config" not in body
        body = await sent_for("claude-haiku-4-5")
        assert not {"thinking", "output_config"} & set(body)
        # Not in the listing: judged by its id.
        body = await sent_for("claude-sonnet-4-5")
        assert body["thinking"]["type"] == "enabled" and "output_config" not in body
    finally:
        await provider.aclose()


async def test_list_models_without_api_key(tmp_path: Path) -> None:
    provider = ClaudeApiProvider(make_settings(tmp_path, anthropic_api_key=None))
    models = await provider.list_models()
    assert not provider.models_live and len(models) == len(STATIC_MODELS)
    await provider.aclose()
