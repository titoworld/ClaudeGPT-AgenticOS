"""ChatGPT through the OpenAI Responses API with an API key (``api`` mode).

Every call is stateless (``store=False``): the system prompt goes in ``instructions``
and the conversation in ``input``, append-only across turns so OpenAI's automatic
prompt cache keeps hitting; ``prompt_cache_key`` is stable per purpose. openai>=3
uses httpx2 (never httpx) for its client, timeouts and transports.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable
from dataclasses import replace
from typing import Literal

import httpx2
import openai
from openai import omit
from openai.types.responses import ResponseInputParam

from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
)
from agentic_os.providers.prompt_format import to_chat_messages

DEFAULT_MODEL = "gpt-6-astra"
DEFAULT_FAST_MODEL = "gpt-6-luna"
TIMEOUT = httpx2.Timeout(connect=5.0, read=120.0, write=30.0, pool=30.0)
"""``read`` is the longest silence allowed between two chunks of the stream."""
MAX_RETRIES = 2
"""SDK retries (408/409/429/5xx and connection errors), only before the stream starts."""
DETAIL_MAX_CHARS = 200

Effort = Literal["low", "medium"]
EFFORT_BY_PURPOSE: dict[Purpose, Effort] = {
    "answer": "medium",
    "revision": "low",
    "synthesis": "medium",
    "summary": "low",
}
"""Never "none": gpt-6-astra rejects it. No temperature/top_p either (unsupported)."""

PRICES_PER_MTOK: dict[str, tuple[float, float, float, float]] = {
    # model: (input, cached input, cache write, output) in USD per million tokens
    "gpt-6-astra": (10.0, 1.0, 12.5, 50.0),
    "gpt-6-sol": (2.0, 0.2, 2.5, 10.0),
    "gpt-6-luna": (0.1, 0.01, 0.125, 0.5),
}

_SNAPSHOT = r"(-\d{4}-\d{2}-\d{2})?"
_SECRET_RE = re.compile(r"(sk-)[A-Za-z0-9_\-*]{8,}|(Bearer\s+)\S+")
_SERVER_CODES = frozenset({"server_error", "vector_store_timeout"})


def estimate_cost(model: str, usage: Usage) -> float | None:
    """Cost in USD of ``usage`` (uncached input semantics), None for an unknown model.
    Dated snapshots (``gpt-6-sol-2026-09-22``) use the price of their model."""
    for name, (price_in, price_cached, price_write, price_out) in PRICES_PER_MTOK.items():
        if re.fullmatch(re.escape(name) + _SNAPSHOT, model):
            total = (
                usage.input_tokens * price_in
                + usage.cache_read_tokens * price_cached
                + usage.cache_write_tokens * price_write
                + usage.output_tokens * price_out
            )
            return round(total / 1_000_000, 6)
    return None


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def usage_from_response(usage: object, model: str) -> Usage:
    """Usage from a ``ResponseUsage``, reading every field defensively (the SDK returns
    None for fields the server omits). ``input_tokens`` is the uncached remainder, as
    for Anthropic: OpenAI's input_tokens include cache reads and cache writes."""
    if usage is None:
        return Usage()
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached = _int(getattr(input_details, "cached_tokens", None))
    written = _int(getattr(input_details, "cache_write_tokens", None))
    result = Usage(
        input_tokens=max(0, _int(getattr(usage, "input_tokens", None)) - cached - written),
        output_tokens=_int(getattr(usage, "output_tokens", None)),
        cache_read_tokens=cached,
        cache_write_tokens=written,
        reasoning_tokens=_int(getattr(output_details, "reasoning_tokens", None)),
    )
    return replace(result, cost_usd=estimate_cost(model, result))


def _detail(message: object) -> str:
    text = _SECRET_RE.sub(lambda m: f"{m.group(1) or m.group(2)}***", str(message or "").strip())
    return text if len(text) <= DETAIL_MAX_CHARS else text[: DETAIL_MAX_CHARS - 1] + "…"


def response_error(code: str | None, message: object) -> ProviderError:
    """ProviderError for an error reported inside the stream (``response.failed``, an
    ``error`` event or an SSE error payload)."""
    if code == "rate_limit_exceeded":
        return ProviderError(
            "Límit de peticions de l'API d'OpenAI.", kind="rate_limit", retryable=True
        )
    if code == "insufficient_quota":
        return ProviderError("El compte d'OpenAI no té crèdit disponible.", kind="rate_limit")
    if code is None or code in _SERVER_CODES:
        return ProviderError(
            "La resposta de ChatGPT s'ha interromput.", kind="unavailable", retryable=True
        )
    return ProviderError(
        f"L'API d'OpenAI ha rebutjat la petició ({code}): {_detail(message)}", kind="invalid"
    )


def map_api_error(exc: openai.APIError) -> ProviderError:
    """Typed SDK errors -> ProviderError (after the SDK's own retries)."""
    if isinstance(exc, openai.APITimeoutError):
        return ProviderError(
            "L'API d'OpenAI ha trigat massa a respondre.", kind="timeout", retryable=True
        )
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(
            "No s'ha pogut contactar amb l'API d'OpenAI.", kind="unavailable", retryable=True
        )
    if not isinstance(exc, openai.APIStatusError):
        # An SSE error payload after the stream opened: never retried by the SDK.
        return response_error(exc.code, exc.message)
    status = exc.status_code
    body = exc.body if isinstance(exc.body, dict) else {}
    message = body.get("message") or exc.message
    if status in (401, 403):
        return ProviderError(
            "L'API d'OpenAI ha rebutjat la clau d'API (AOS_OPENAI_API_KEY).", kind="auth"
        )
    if status == 429:
        if exc.code == "insufficient_quota":
            return ProviderError("El compte d'OpenAI no té crèdit disponible.", kind="rate_limit")
        return ProviderError(
            "Límit de peticions de l'API d'OpenAI.", kind="rate_limit", retryable=True
        )
    if status >= 500 or status in (408, 409):
        return ProviderError(
            "L'API d'OpenAI no està disponible ara mateix.", kind="unavailable", retryable=True
        )
    if status in (400, 404, 413, 422):
        return ProviderError(
            f"L'API d'OpenAI ha rebutjat la petició: {_detail(message)}", kind="invalid"
        )
    return ProviderError(f"Error de l'API d'OpenAI ({status}).", kind="internal")


class OpenAIApiProvider:
    """Provider for agent ``chatgpt`` in mode ``api``.

    ``client`` is optional: by default one ``AsyncOpenAI`` is created on first use from
    ``settings.openai_api_key`` and closed by :meth:`aclose`.
    """

    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    @property
    def agent(self) -> AgentName:
        return "chatgpt"

    @property
    def mode(self) -> ProviderMode:
        return "api"

    @property
    def default_model(self) -> str:
        return self._settings.chatgpt_model or DEFAULT_MODEL

    def _configured(self) -> bool:
        key = self._settings.openai_api_key
        return self._client is not None or bool(key and key.get_secret_value())

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            key = self._settings.openai_api_key
            if key is None or not key.get_secret_value():
                raise ProviderError(
                    "Falta la clau d'API d'OpenAI (AOS_OPENAI_API_KEY).", kind="auth"
                )
            self._client = openai.AsyncOpenAI(
                api_key=key.get_secret_value(), timeout=TIMEOUT, max_retries=MAX_RETRIES
            )
        return self._client

    def _model(self, request: GenerationRequest) -> str:
        if request.model:
            return request.model
        if request.fast:
            return self._settings.chatgpt_fast_model or DEFAULT_FAST_MODEL
        return self.default_model

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        client = self._get_client()
        model = self._model(request)
        messages: ResponseInputParam = [
            {"role": role, "content": content}
            for role, content in to_chat_messages(request, "chatgpt")
        ]
        started = time.monotonic()
        deadline = started + self._settings.provider_timeout_seconds
        parts: list[str] = []
        ttft_ms: int | None = None
        try:
            stream = await self._until(
                deadline,
                client.responses.create(
                    model=model,
                    instructions=request.system or omit,
                    input=messages,
                    stream=True,
                    store=False,
                    reasoning={"effort": EFFORT_BY_PURPOSE[request.purpose]},
                    max_output_tokens=request.max_output_tokens,
                    prompt_cache_key=f"agentic-os-chatgpt-{request.purpose}",
                ),
            )
            async with stream:
                events = aiter(stream)
                while True:
                    try:
                        event = await self._until(deadline, anext(events))
                    except StopAsyncIteration:
                        break
                    if event.type == "response.output_text.delta":
                        if not event.delta:
                            continue
                        if ttft_ms is None:
                            ttft_ms = int((time.monotonic() - started) * 1000)
                        parts.append(event.delta)
                        yield TextDelta(event.delta)
                    elif event.type in ("response.completed", "response.incomplete"):
                        # incomplete (e.g. max_output_tokens): keep the partial answer.
                        response = event.response
                        final_model = response.model or model
                        yield GenerationResult(
                            text="".join(parts),
                            usage=usage_from_response(response.usage, final_model),
                            model=final_model,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            ttft_ms=ttft_ms,
                        )
                        return
                    elif event.type == "response.failed":
                        error = event.response.error
                        raise response_error(
                            getattr(error, "code", None), getattr(error, "message", "")
                        )
                    elif event.type == "error":
                        raise response_error(event.code, event.message)
        except openai.APIError as exc:
            raise map_api_error(exc) from exc
        raise ProviderError(
            "La resposta de ChatGPT s'ha interromput.", kind="unavailable", retryable=True
        )

    async def _until[T](self, deadline: float, operation: Awaitable[T]) -> T:
        """Await with the time left of the call's wall-clock budget."""
        try:
            return await asyncio.wait_for(operation, max(0.0, deadline - time.monotonic()))
        except TimeoutError:
            seconds = self._settings.provider_timeout_seconds
            raise ProviderError(
                f"ChatGPT no ha respost a temps ({seconds:g} s).", kind="timeout"
            ) from None

    async def prewarm(self, request: GenerationRequest) -> None:
        """Nothing to start ahead of time in api mode."""

    async def status(self) -> ProviderStatus:
        """Configuration only: no network call."""
        configured = self._configured()
        return ProviderStatus(
            agent="chatgpt",
            mode="api",
            available=configured,
            model=self.default_model,
            detail=(
                "Clau d'API configurada"
                if configured
                else "Falta la clau d'API d'OpenAI (AOS_OPENAI_API_KEY)"
            ),
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await client.close()
