"""Claude through the Anthropic API (official ``anthropic`` SDK) with an API key.

Prompt caching: the system prompt is a block with its own breakpoint and top-level
automatic caching moves a second breakpoint to the end of the append-only history,
so each call re-reads the prefix the previous one wrote. Attachments go at the start of
the last user message, as the CLI sends them (:func:`attachment_blocks`), and the last
one carries a third breakpoint: a later phase of the turn that sends them after the
same prefix (the synthesis after the answers, a revision round after the previous one)
reads them from the cache instead of paying for them whole again.

Models of the 4.6 generation on run with adaptive thinking (text omitted) and an effort
per call purpose; older Claude 4 / 3.7 models get a thinking budget instead, and Haiku
or unknown ids run without thinking (what each model accepts comes from the Models API
when it says so). A request with reasoning "off" disables thinking. ``max_tokens`` is the
request's billed output budget exactly (thinking included), never raised. Opus 5 /
Opus 5.5 / Fable requests opt into server-side refusal fallbacks.

Usage is what Anthropic bills, per attempt: a refusal still bills its input and any
streamed output (and, before any output, the categories in ``BILLED_BEFORE_OUTPUT``), so
a refusal raises :class:`RefusalError` carrying that usage. After a server-side fallback
the result's ``usage`` is the served attempt's only, and the billed attempts of the
models that declined before it are ``declined``, each with its own model: every attempt
is billed at the rates of the model that ran it, and tokens of different models are
never summed.

A reply cut at ``max_tokens`` is a truncated result (an error with its billed usage when
no text came at all). A stream that drops (anthropic does not wrap the httpx2 errors it
meets while reading the body) or ends without its final events is a retryable
"interrupted" error, never a complete answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Sequence
from dataclasses import dataclass
from typing import Literal, cast

import anthropic
import httpx2
from anthropic import Omit, omit
from anthropic.types import ModelCapabilities
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaMessage,
    BetaMessageParam,
    BetaTextBlockParam,
    BetaThinkingConfigParam,
)

from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode, Usage
from agentic_os.i18n import lazy, number, t
from agentic_os.providers.base import (
    DeclinedAttempt,
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    TextDelta,
    seconds,
)
from agentic_os.providers.claude_cli import (
    EFFORT_BY_PURPOSE,
    Effort,
    attachment_blocks,
    family_description,
    is_haiku,
    redact,
    refusal_error,
)
from agentic_os.providers.prompt_format import read_files, to_chat_messages

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_FAST_MODEL = "claude-haiku-4-5"
THINKING_BUDGET_BY_EFFORT: dict[Effort, int] = {"low": 2_048, "medium": 4_096, "high": 8_192}
"""budget_tokens for models without adaptive thinking (at most half of max_tokens, which
caps thinking and visible text together)."""
MIN_THINKING_BUDGET = 1_024
"""The smallest budget_tokens Anthropic accepts: a smaller output budget runs without
thinking rather than being raised."""
COMPLETE_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})
"""Stop reasons of a complete reply; any other one (but a refusal) is a truncated one."""

FALLBACK_MODELS = frozenset(
    {"claude-opus-5", "claude-opus-5-5", "claude-fable-5", "claude-fable-5-1"}
)
"""Models whose safety classifiers can decline; they get ``fallbacks="default"``."""
FALLBACK_BETA = "server-side-fallback-2026-07-01"

BILLED_BEFORE_OUTPUT = frozenset({"bio", "frontier_llm", "reasoning_extraction"})
"""Refusal categories Anthropic bills even when the decline comes before any output
("How refusals are billed", September 2026). A decline before any output in another
category, or with no category, is not billed; a decline after output always is."""

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


ThinkingMode = Literal["adaptive", "budget", "none"]


@dataclass(frozen=True, slots=True)
class ModelSupport:
    """Request features a model accepts."""

    thinking: ThinkingMode
    """``adaptive`` thinking, a thinking ``budget`` (budget_tokens) or ``none``."""
    effort: bool
    """Whether it takes ``output_config.effort`` (low, medium and high)."""


NO_THINKING = ModelSupport("none", effort=False)
_ADAPTIVE_MODEL = re.compile(r"claude-(?:(?:opus|sonnet)-(?:4-[6-9]|[5-9])|fable|mythos)")
_BUDGET_MODEL = re.compile(r"claude-(?:(?:opus|sonnet)-4|3-7-sonnet)")


def model_support(model: str) -> ModelSupport:
    """What ``model`` accepts, judged by its id (for ids the Models API did not describe).

    Adaptive thinking and effort exist from the 4.6 generation on (Opus / Sonnet 4.6+,
    5.x, Fable, Mythos); older Claude 4 and 3.7 models think with a budget and reject
    both. Haiku runs without thinking (cheap internal calls), and so does any unknown
    id: a plain request is valid on every model.
    """
    lowered = model.lower()
    if is_haiku(lowered):
        return NO_THINKING
    if _ADAPTIVE_MODEL.search(lowered):
        return ModelSupport("adaptive", effort=True)
    if _BUDGET_MODEL.search(lowered):
        return ModelSupport("budget", effort=False)
    return NO_THINKING


def support_from_capabilities(
    model: str, capabilities: ModelCapabilities | None
) -> ModelSupport | None:
    """What ``model`` accepts according to the Models API (None if it does not say).
    Haiku keeps running without thinking whatever it supports."""
    if capabilities is None or is_haiku(model):
        return None
    try:
        thinking = capabilities.thinking
        effort = capabilities.effort
        mode: ThinkingMode = "none"
        if thinking.supported and thinking.types.adaptive.supported:
            mode = "adaptive"
        elif thinking.supported and thinking.types.enabled.supported:
            mode = "budget"
        levels = (effort.low, effort.medium, effort.high)
        takes_effort = effort.supported and all(level.supported for level in levels)
    except AttributeError:  # a partial capability tree: judge by the id instead
        return None
    return ModelSupport(mode, effort=takes_effort)


def _attempt_billed(output_tokens: int, category: str | None) -> bool:
    return output_tokens > 0 or category in BILLED_BEFORE_OUTPUT


def _tokens(entry: object, name: str) -> int:
    """A token count of a usage object, 0 when missing: the SDK builds streamed objects
    without validation, so a field the API left out can be absent or None."""
    value = getattr(entry, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _category(reason: object) -> str | None:
    """``category`` of a refusal's ``stop_details`` or of a fallback block's ``trigger``
    (either can be missing or None)."""
    category = getattr(reason, "category", None)
    return category if isinstance(category, str) else None


def _model_name(value: object) -> str:
    """A model id of the SDK's objects ("" when missing, None or not a string)."""
    return value if isinstance(value, str) else ""


def billed_usage(
    message: BetaMessage, requested_model: str = ""
) -> tuple[Usage, tuple[DeclinedAttempt, ...]]:
    """What Anthropic bills for ``message``, per attempt and without cost (the engine
    prices every attempt at the rates of the model that ran it).

    The first value is top-level ``usage``, which covers only the attempt that produced
    the message: the one that served it, or the one whose refusal ended the call (a
    refusal is billed as "How refusals are billed" says). With server-side fallbacks
    every earlier attempt is a ``message`` entry of ``usage.iterations`` (the served one
    is ``fallback_message``), paired in order with the ``fallback`` content blocks that
    give each decline's category and the model that declined (no tools are sent, so
    every ``message`` entry is a declined hop, never a tool-loop step). The billed ones
    are the second value, each with its own model: the entry's ``model``, else its
    block's ``from``, else the model the previous hop fell back to, else (first hop)
    ``requested_model``. Tokens of different models are never summed ("Billing and rate
    limits" of Anthropic's refusals and fallback guide).

    Entries and blocks are picked by their ``type``, never by class: the SDK builds an
    entry of a type it does not know as the first variant of the union (the ``message``
    entry class). A fallback block without ``trigger`` (the documented example has none)
    gives no category, so its hop is billed only if it streamed output.
    """
    usage = message.usage
    details = usage.output_tokens_details
    billed = Usage(
        input_tokens=_tokens(usage, "input_tokens"),
        output_tokens=_tokens(usage, "output_tokens"),
        cache_read_tokens=_tokens(usage, "cache_read_input_tokens"),
        cache_write_tokens=_tokens(usage, "cache_creation_input_tokens"),
        reasoning_tokens=_tokens(details, "thinking_tokens"),
    )
    if message.stop_reason == "refusal":
        category = _category(message.stop_details)
        if not _attempt_billed(billed.output_tokens, category):
            billed = Usage()
    iterations = usage.iterations or []
    if not any(getattr(entry, "type", None) == "fallback_message" for entry in iterations):
        return billed, ()  # no fallback ran: top-level usage is the whole call
    blocks = [block for block in message.content if getattr(block, "type", None) == "fallback"]
    hops = [entry for entry in iterations if getattr(entry, "type", None) == "message"]
    declined: list[DeclinedAttempt] = []
    for index, entry in enumerate(hops):
        block = blocks[index] if index < len(blocks) else None
        category = _category(getattr(block, "trigger", None))
        output_tokens = _tokens(entry, "output_tokens")
        if not _attempt_billed(output_tokens, category):
            continue
        previous = blocks[index - 1] if 0 < index <= len(blocks) else None
        model = (
            _model_name(getattr(entry, "model", None))
            or _model_name(getattr(getattr(block, "from_", None), "model", None))
            or _model_name(getattr(getattr(previous, "to", None), "model", None))
            or requested_model
        )
        declined.append(
            DeclinedAttempt(
                model=model,
                usage=Usage(
                    input_tokens=_tokens(entry, "input_tokens"),
                    output_tokens=output_tokens,
                    cache_read_tokens=_tokens(entry, "cache_read_input_tokens"),
                    cache_write_tokens=_tokens(entry, "cache_creation_input_tokens"),
                ),
            )
        )
    return billed, tuple(declined)


def _interrupted() -> ProviderError:
    return ProviderError(t("providers.claude.interrupted"), kind="unavailable", retryable=True)


def _with_default(models: list[ModelInfo], default: str) -> tuple[ModelInfo, ...]:
    """``models`` with the configured default first if the listing does not include it."""
    if any(model.id == default for model in models):
        return tuple(models)
    extra = ModelInfo(
        id=default,
        label=default,
        description=family_description(default) or lazy("providers.model_configured"),
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
            t("providers.anthropic.unreachable"), kind="unavailable", retryable=True
        )
    if not isinstance(exc, anthropic.APIStatusError):
        return ProviderError(t("providers.anthropic.unexpected"), kind="internal")
    status = exc.status_code
    if status < 400:
        # An SSE ``error`` event after the stream opened: the SDK never retries these.
        if exc.type == "rate_limit_error":
            return ProviderError(
                t("providers.anthropic.rate_limit"), kind="rate_limit", retryable=True
            )
        return _interrupted()
    if status in (401, 403):
        return ProviderError(t("providers.anthropic.key_rejected"), kind="auth")
    if status == 402:
        return ProviderError(t("providers.anthropic.billing"), kind="auth")
    if status == 429:
        return ProviderError(t("providers.anthropic.rate_limit"), kind="rate_limit", retryable=True)
    if status >= 500:
        return ProviderError(
            t("providers.anthropic.unavailable"), kind="unavailable", retryable=True
        )
    if status in (400, 404, 413, 422):
        return ProviderError(
            t("providers.anthropic.rejected", detail=_error_detail(exc)), kind="invalid"
        )
    return ProviderError(t("providers.anthropic.error", status=status), kind="internal")


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
        self._listed_support: dict[str, ModelSupport] = {}
        """What each model of the latest live listing accepts, from its capabilities."""

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
                raise ProviderError(t("providers.anthropic.missing_key"), kind="auth")
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

    def support(self, model: str) -> ModelSupport:
        """What ``model`` accepts: its listed capabilities, else judged by its id."""
        return self._listed_support.get(model) or model_support(model)

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ProviderEvent]:
        client = self._get_client()
        model = self._model(request)
        support = self.support(model)
        effort = EFFORT_BY_PURPOSE[request.purpose]
        # The billed output budget, thinking included: sent as it is, never raised.
        max_tokens = request.max_output_tokens
        thinking: BetaThinkingConfigParam | Omit = omit
        if support.thinking != "none" and request.reasoning == "off":
            thinking = {"type": "disabled"}
        elif support.thinking == "adaptive":
            thinking = {"type": "adaptive", "display": "omitted"}
        elif support.thinking == "budget":
            budget = min(THINKING_BUDGET_BY_EFFORT[effort], max_tokens // 2)
            if budget >= MIN_THINKING_BUDGET:
                thinking = {"type": "enabled", "budget_tokens": budget}
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
        if request.attachments:
            messages[-1] = await self._with_attachments(request, messages[-1])
        manager = client.beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system or omit,
            messages=messages,
            cache_control={"type": "ephemeral"},
            thinking=thinking,
            output_config={"effort": effort} if support.effort else omit,
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
                try:
                    final = await self._with_deadline(stream.get_final_message(), deadline)
                except AssertionError:  # the body ended before message_start
                    raise _interrupted() from None
        except anthropic.APIError as exc:
            raise map_api_error(exc) from exc
        except httpx2.TimeoutException:
            raise self._timeout() from None
        except httpx2.RequestError as exc:
            # anthropic does not wrap what httpx2 raises while reading the body
            # (RemoteProtocolError, ReadError...): the connection dropped mid-reply.
            raise _interrupted() from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        stop_reason = final.stop_reason
        if stop_reason is None:
            # The body ended cleanly but without message_delta/message_stop.
            raise _interrupted()
        # No cost here: the engine prices every attempt (owner price overrides included).
        usage, declined = billed_usage(final, model)
        if stop_reason == "refusal":
            raise refusal_error(usage, final.model, _category(final.stop_details), declined)
        text = "".join(chunks)
        truncated = stop_reason not in COMPLETE_STOP_REASONS
        if truncated and not text.strip():
            raise ProviderError(
                t("providers.claude.output_budget_spent", budget=number(max_tokens))
                if stop_reason == "max_tokens"
                else t("providers.claude.stopped_before_text", reason=stop_reason),
                kind="invalid",
                usage=usage,
                model=final.model,
                declined=declined,
            )
        yield GenerationResult(
            text=text,
            usage=usage,
            model=final.model,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            truncated=truncated,
            finish_reason=stop_reason if truncated else None,
            declined=declined,
        )

    async def _with_deadline[T](self, operation: Awaitable[T], deadline: float) -> T:
        try:
            async with asyncio.timeout_at(deadline):
                return await operation
        except TimeoutError:
            raise self._timeout() from None

    def _timeout(self) -> ProviderError:
        limit = seconds(self._settings.provider_timeout_seconds)
        return ProviderError(t("providers.claude.timeout", seconds=limit), kind="timeout")

    @staticmethod
    async def _with_attachments(
        request: GenerationRequest, last: BetaMessageParam
    ) -> BetaMessageParam:
        """The last user message (it ends with the prompt) with the attachment blocks
        before its text; the last attachment block is a cache breakpoint."""
        blocks = attachment_blocks(request.attachments, await read_files(request.attachments))
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
        text = last["content"]
        if not isinstance(text, str):  # pragma: no cover - to_chat_messages gives text
            raise ProviderError(t("providers.claude.unexpected_message"), kind="internal")
        content = [*blocks, {"type": "text", "text": text}]
        return {"role": "user", "content": cast(list[BetaContentBlockParam], content)}

    async def prewarm(self, request: GenerationRequest) -> None:
        """Nothing to start ahead of time in api mode."""

    async def status(self) -> ProviderStatus:
        configured = self._configured()
        return ProviderStatus(
            agent="claude",
            mode="api",
            available=configured,
            model=self.default_model,
            detail=lazy(
                "providers.api_key_configured"
                if configured
                else "providers.anthropic.status.missing_key"
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
            supports: dict[str, ModelSupport] = {}
            async with asyncio.timeout(LIST_TIMEOUT_SECONDS):
                async for model in client.models.list(limit=100):
                    if support := support_from_capabilities(model.id, model.capabilities):
                        supports[model.id] = support
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
                # Only logged (below): in English, as the logs are.
                raise ProviderError("the Models API listed no model", kind="internal")
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
        self._listed_support = supports
        return _with_default(listed, default), True

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await client.close()
