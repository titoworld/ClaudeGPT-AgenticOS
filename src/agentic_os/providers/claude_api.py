"""Claude through the Anthropic API (official ``anthropic`` SDK) with an API key.

Prompt caching: the system prompt is a block with its own breakpoint and top-level
automatic caching moves a second breakpoint to the end of the append-only history,
so each call re-reads the prefix the previous one wrote. Thinking models run with
adaptive thinking (text omitted) and an effort per call purpose; Haiku runs without
thinking. Opus 5 / Opus 5.5 / Fable requests opt into server-side refusal fallbacks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Sequence

import anthropic
from anthropic import omit
from anthropic.types.beta import BetaMessageParam, BetaTextBlockParam

from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode, Usage
from agentic_os.providers.base import (
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
)
from agentic_os.providers.claude_cli import (
    EFFORT_BY_PURPOSE,
    family_description,
    is_haiku,
    redact,
)
from agentic_os.providers.prompt_format import to_chat_messages

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_FAST_MODEL = "claude-haiku-4-5"
MIN_THINKING_MAX_TOKENS = 16_000
"""max_tokens caps thinking and visible text together on thinking models."""

FALLBACK_MODELS = frozenset(
    {"claude-opus-5", "claude-opus-5-5", "claude-fable-5", "claude-fable-5-1"}
)
"""Models whose safety classifiers can decline; they get ``fallbacks="default"``."""
FALLBACK_BETA = "server-side-fallback-2026-07-01"

MODELS_TTL_SECONDS = 600.0
FALLBACK_TTL_SECONDS = 60.0
"""A failed listing is retried sooner than a live one is refreshed."""
LIST_TIMEOUT_SECONDS = 15.0
MAX_LISTED_MODELS = 200

STATIC_MODELS: tuple[tuple[str, str, int], ...] = (
    ("claude-opus-5", "Claude Opus 5", 1_000_000),
    ("claude-sonnet-5", "Claude Sonnet 5", 1_000_000),
    ("claude-haiku-4-5", "Claude Haiku 4.5", 200_000),
    ("claude-fable-5-1", "Claude Fable 5.1", 1_000_000),
)
"""(id, label, context window) shown when the Models API cannot be queried."""


def _with_default(models: list[ModelInfo], default: str) -> tuple[ModelInfo, ...]:
    """``models`` with the configured default first if the listing does not include it."""
    if any(model.id == default for model in models):
        return tuple(models)
    extra = ModelInfo(
        id=default,
        label=default,
        description=family_description(default) or "Model configurat al servidor.",
        is_default=True,
    )
    return (extra, *models)


def _error_detail(exc: anthropic.APIStatusError) -> str:
    body = exc.body
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return redact(str(message or exc.message))[:200]


def map_api_error(exc: anthropic.APIError) -> ProviderError:
    """Typed SDK errors -> ProviderError (after the SDK's own retries)."""
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return ProviderError(
            "No s'ha pogut contactar amb l'API d'Anthropic.", kind="unavailable", retryable=True
        )
    if not isinstance(exc, anthropic.APIStatusError):
        return ProviderError("Resposta inesperada de l'API d'Anthropic.", kind="internal")
    status = exc.status_code
    if status < 400:
        # An SSE ``error`` event after the stream opened: the SDK never retries these.
        if exc.type == "rate_limit_error":
            return ProviderError(
                "Límit de peticions de l'API d'Anthropic.", kind="rate_limit", retryable=True
            )
        return ProviderError(
            "La resposta de Claude s'ha interromput.", kind="unavailable", retryable=True
        )
    if status in (401, 403):
        return ProviderError(
            "L'API d'Anthropic ha rebutjat la clau d'API (AOS_ANTHROPIC_API_KEY).", kind="auth"
        )
    if status == 402:
        return ProviderError("Problema de facturació del compte d'Anthropic.", kind="auth")
    if status == 429:
        return ProviderError(
            "Límit de peticions de l'API d'Anthropic.", kind="rate_limit", retryable=True
        )
    if status >= 500:
        return ProviderError(
            "L'API d'Anthropic no està disponible ara mateix.", kind="unavailable", retryable=True
        )
    if status in (400, 404, 413, 422):
        return ProviderError(
            f"L'API d'Anthropic ha rebutjat la petició: {_error_detail(exc)}", kind="invalid"
        )
    return ProviderError(f"Error de l'API d'Anthropic ({status}).", kind="internal")


class ClaudeApiProvider:
    """Provider for agent ``claude`` in mode ``api``.

    ``client`` is optional: by default one ``AsyncAnthropic`` is created on first use
    from ``settings.anthropic_api_key`` and closed by :meth:`aclose`.
    """

    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._models_cache: tuple[float, tuple[ModelInfo, ...], bool] | None = None
        """(expiry on the monotonic clock, models, live)."""
        self._models_lock = asyncio.Lock()

    @property
    def agent(self) -> AgentName:
        return "claude"

    @property
    def mode(self) -> ProviderMode:
        return "api"

    @property
    def default_model(self) -> str:
        return self._settings.claude_model or DEFAULT_MODEL

    @property
    def fast_model(self) -> str:
        """Model of the cheap internal calls (summaries) when none is requested."""
        return self._settings.claude_fast_model or DEFAULT_FAST_MODEL

    def _configured(self) -> bool:
        key = self._settings.anthropic_api_key
        return self._client is not None or bool(key and key.get_secret_value())

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            key = self._settings.anthropic_api_key
            if key is None or not key.get_secret_value():
                raise ProviderError(
                    "Falta la clau d'API d'Anthropic (AOS_ANTHROPIC_API_KEY).", kind="auth"
                )
            self._client = anthropic.AsyncAnthropic(
                api_key=key.get_secret_value(),
                timeout=anthropic.Timeout(self._settings.provider_timeout_seconds, connect=10.0),
                max_retries=2,
            )
        return self._client

    def _model(self, request: GenerationRequest) -> str:
        if request.model:
            return request.model
        if request.fast:
            return self.fast_model
        return self.default_model

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        client = self._get_client()
        model = self._model(request)
        thinking = not is_haiku(model)  # Haiku 4.5 takes neither adaptive thinking nor effort
        fallbacks = model in FALLBACK_MODELS
        system: list[BetaTextBlockParam] = []
        if request.system:
            system.append(
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            )
        messages: list[BetaMessageParam] = [
            {"role": role, "content": content}
            for role, content in to_chat_messages(request, "claude")
        ]
        manager = client.beta.messages.stream(
            model=model,
            max_tokens=(
                max(request.max_output_tokens, MIN_THINKING_MAX_TOKENS)
                if thinking
                else request.max_output_tokens
            ),
            system=system or omit,
            messages=messages,
            cache_control={"type": "ephemeral"},
            thinking={"type": "adaptive", "display": "omitted"} if thinking else omit,
            output_config={"effort": EFFORT_BY_PURPOSE[request.purpose]} if thinking else omit,
            fallbacks="default" if fallbacks else omit,
            betas=[FALLBACK_BETA] if fallbacks else omit,
        )

        started = time.monotonic()
        deadline = asyncio.get_running_loop().time() + self._settings.provider_timeout_seconds
        chunks: list[str] = []
        ttft_ms: int | None = None
        try:
            async with contextlib.AsyncExitStack() as stack:
                stream = await self._with_deadline(stack.enter_async_context(manager), deadline)
                events = aiter(stream)
                while True:
                    try:
                        event = await self._with_deadline(anext(events), deadline)
                    except StopAsyncIteration:
                        break
                    # The helper emits a raw content_block_delta and a "text" event per
                    # delta: consume only the latter.
                    if event.type == "text" and event.text:
                        if ttft_ms is None:
                            ttft_ms = int((time.monotonic() - started) * 1000)
                        chunks.append(event.text)
                        yield TextDelta(event.text)
                final = await self._with_deadline(stream.get_final_message(), deadline)
        except anthropic.APIError as exc:
            raise map_api_error(exc) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        if final.stop_reason == "refusal":
            category = final.stop_details.category if final.stop_details else None
            reason = f" (categoria: {category})" if category else ""
            raise ProviderError(
                f"Claude ha declinat respondre aquesta petició{reason}.", kind="invalid"
            )
        usage = final.usage
        details = usage.output_tokens_details
        # No cost here: the engine prices every call (owner price overrides included).
        result_usage = Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            reasoning_tokens=details.thinking_tokens if details else 0,
        )
        yield GenerationResult(
            text="".join(chunks),
            usage=result_usage,
            model=final.model,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
        )

    async def _with_deadline[T](self, operation: Awaitable[T], deadline: float) -> T:
        try:
            async with asyncio.timeout_at(deadline):
                return await operation
        except TimeoutError:
            seconds = self._settings.provider_timeout_seconds
            raise ProviderError(
                f"Claude no ha respost a temps ({seconds:g} s).", kind="timeout"
            ) from None

    async def prewarm(self, request: GenerationRequest) -> None:
        """Nothing to start ahead of time in api mode."""

    async def status(self) -> ProviderStatus:
        configured = self._configured()
        return ProviderStatus(
            agent="claude",
            mode="api",
            available=configured,
            model=self.default_model,
            detail=(
                "Clau d'API configurada"
                if configured
                else "Falta la clau d'API d'Anthropic (AOS_ANTHROPIC_API_KEY)"
            ),
        )

    @property
    def models_live(self) -> bool:
        """Whether the latest :meth:`list_models` came from the Models API."""
        return self._models_cache is not None and self._models_cache[2]

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        """Models of the Models API (cached ~10 min), or a static list if it fails."""
        async with self._models_lock:
            cached = self._models_cache
            if refresh or cached is None or time.monotonic() >= cached[0]:
                models, live = await self._fetch_models()
                ttl = MODELS_TTL_SECONDS if live else FALLBACK_TTL_SECONDS
                cached = self._models_cache = (time.monotonic() + ttl, models, live)
        return cached[1]

    async def _fetch_models(self) -> tuple[tuple[ModelInfo, ...], bool]:
        default = self.default_model
        try:
            client = self._get_client()
            listed: list[ModelInfo] = []
            async with asyncio.timeout(LIST_TIMEOUT_SECONDS):
                async for model in client.models.list(limit=100):
                    listed.append(
                        ModelInfo(
                            id=model.id,
                            label=model.display_name or model.id,
                            description=family_description(model.id),
                            is_default=model.id == default,
                            context_window=model.max_input_tokens,
                        )
                    )
                    if len(listed) >= MAX_LISTED_MODELS:
                        break
            if not listed:
                raise ProviderError("L'API d'Anthropic no ha retornat cap model.", kind="internal")
        except Exception as exc:
            reason = exc.message if isinstance(exc, ProviderError) else type(exc).__name__
            logger.warning("Could not list the Anthropic models (%s); using the fallback", reason)
            listed = [
                ModelInfo(
                    id=model_id,
                    label=label,
                    description=family_description(model_id),
                    is_default=model_id == default,
                    context_window=window,
                )
                for model_id, label, window in STATIC_MODELS
            ]
            return _with_default(listed, default), False
        return _with_default(listed, default), True

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await client.close()
