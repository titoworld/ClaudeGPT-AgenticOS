"""ChatGPT through the OpenAI Responses API with an API key (``api`` mode).

Every call is stateless (``store=False``): the system prompt goes in ``instructions``
and the conversation in ``input``, append-only across turns so OpenAI's automatic
prompt cache keeps hitting; ``prompt_cache_key`` is stable per purpose. Reasoning
models (GPT-5 on, o-series) get an effort per purpose, or the lowest one they accept
when the request asks for reasoning "off"; older chat models such as gpt-4o reject
``reasoning`` and run without it. ``max_output_tokens`` is the request's billed output
budget exactly (it includes the reasoning). openai>=3 uses httpx2 (never httpx) for its
client, timeouts and transports.

A reply that stops early (``response.incomplete``) is a truncated result, or an error
with its billed usage when no text came at all; a refusal (``refusal`` events or parts)
raises :class:`RefusalError` with its billed usage, even after some text streamed.

Attachments go at the start of the last user message, in order (:func:`input_parts`):
an image as ``input_image`` and a PDF as ``input_file``, both as base64 data URLs, and a
text file (or a PDF sent as its extracted text) as ``input_text``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Sequence
from typing import Literal

import httpx2
import openai
from openai import omit
from openai.types.responses import ResponseInputContentParam, ResponseInputParam

from agentic_os.config import Settings
from agentic_os.domain import AgentName, ProviderMode, Purpose, Usage
from agentic_os.i18n import Lazy, lazy, number, t
from agentic_os.providers.base import (
    Attachment,
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderEvent,
    ProviderStatus,
    RefusalError,
    TextDelta,
    clean_refusal,
    seconds,
)
from agentic_os.providers.prompt_format import attachment_text, read_files, to_chat_messages

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-6-astra"
DEFAULT_FAST_MODEL = "gpt-6-luna"
TIMEOUT = httpx2.Timeout(connect=5.0, read=120.0, write=30.0, pool=30.0)
"""``read`` is the longest silence allowed between two chunks of the stream."""
MAX_RETRIES = 2
"""SDK retries (408/409/429/5xx and connection errors), only before the stream starts."""
DETAIL_MAX_CHARS = 200

Effort = Literal["none", "minimal", "low", "medium"]
EFFORT_BY_PURPOSE: dict[Purpose, Effort] = {
    "answer": "medium",
    "revision": "low",
    "synthesis": "medium",
    "summary": "low",
    "check": "low",
}
"""Never "none" here: gpt-6-astra rejects it. No temperature/top_p either (unsupported)."""

_NO_EFFORT_MODEL = re.compile(r"^gpt-6-(?:sol|luna)(?:-\d{4}-\d{2}-\d{2})?$")
_MINIMAL_EFFORT_MODEL = re.compile(r"^gpt-5(?:-mini|-nano)?(?:-\d{4}-\d{2}-\d{2})?$")
FINISH_REASONS: dict[str, str] = {"max_output_tokens": "max_tokens"}
"""``incomplete_details.reason`` -> ``GenerationResult.finish_reason`` (others as they are)."""

MODELS_TTL_SECONDS = 600.0
FALLBACK_TTL_SECONDS = 60.0
"""A failed listing is retried sooner than a live one is refreshed."""
LIST_TIMEOUT_SECONDS = 15.0

MODEL_DESCRIPTIONS: dict[str, Lazy] = {
    "gpt-6-astra": lazy("providers.model.gpt_6_astra"),
    "gpt-6-sol": lazy("providers.model.gpt_6_sol"),
    "gpt-6-luna": lazy("providers.model.gpt_6_luna"),
}
"""Descriptions of known models; also the fallback list (in this order)."""

_CHAT_MODEL = re.compile(r"^(gpt-|chatgpt-|o\d)")
_NOT_CHAT = re.compile(
    r"embedding|audio|realtime|tts|transcribe|image|dall-e|whisper|moderation|search|instruct"
)
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_REASONING_MODEL = re.compile(r"^(o\d|gpt-([5-9]|[1-9]\d))")
_NOT_REASONING = re.compile(r"-chat\b|^o1-(mini|preview)")
_SECRET_RE = re.compile(r"(sk-)[A-Za-z0-9_\-*]{8,}|(Bearer\s+)\S+")
_SERVER_CODES = frozenset({"server_error", "vector_store_timeout"})


def lowest_effort(model_id: str) -> Effort:
    """Lowest reasoning effort ``model_id`` accepts (reasoning "off"): "none" on GPT-6 Sol
    and Luna, "minimal" on the first GPT-5 models and "low" on the rest (GPT-6 Astra and
    the o-series reject lower values, and "low" is safe on a model this list does not
    know yet)."""
    if _NO_EFFORT_MODEL.match(model_id):
        return "none"
    if _MINIMAL_EFFORT_MODEL.match(model_id):
        return "minimal"
    return "low"


def input_parts(
    attachments: Sequence[Attachment], files: Sequence[bytes | None]
) -> list[ResponseInputContentParam]:
    """Responses API content parts of the attachments, in order: ``input_image`` or
    ``input_file`` (a PDF, named ``*.pdf``) from the file's bytes (``files``, from
    :func:`~agentic_os.providers.prompt_format.read_files`) as a data URL, else an
    ``input_text`` (a text file, or a PDF in mode "text")."""
    parts: list[ResponseInputContentParam] = []
    for attachment, data in zip(attachments, files, strict=True):
        if data is None:
            parts.append({"type": "input_text", "text": attachment_text(attachment)})
            continue
        encoded = base64.b64encode(data).decode("ascii")
        if attachment.kind == "image":
            parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{attachment.mime};base64,{encoded}",
                    "detail": "auto",
                }
            )
        else:
            name = attachment.name
            parts.append(
                {
                    "type": "input_file",
                    "filename": name if name.lower().endswith(".pdf") else f"{name}.pdf",
                    "file_data": f"data:application/pdf;base64,{encoded}",
                }
            )
    return parts


def _refusal_in_output(output: object) -> str | None:
    """Text of the ``refusal`` content parts of a response's output (None when none)."""
    found: list[str] = []
    for item in output if isinstance(output, list) else []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                text = getattr(part, "refusal", "")
                found.append(text if isinstance(text, str) else "")
    return "\n".join(found) if found else None


def refusal_error(refusal: str, usage: Usage, model: str) -> RefusalError:
    """RefusalError for a refusal of ChatGPT, with its billed usage and its explanation
    (cleaned and shortened) in the message."""
    explanation = clean_refusal(refusal)
    message = (
        t("providers.chatgpt.refused_explained", explanation=explanation)
        if explanation
        else t("providers.chatgpt.refused")
    )
    return RefusalError(message, usage=usage, model=model, refusal=explanation)


def incomplete_error(reason: str | None, budget: int, usage: Usage, model: str) -> ProviderError:
    """ProviderError for a reply that stopped before writing any text (billed all the
    same: the usage goes with it)."""
    if reason == "max_output_tokens":
        message = t("providers.chatgpt.output_budget_spent", budget=number(budget))
    elif reason == "content_filter":
        message = t("providers.chatgpt.content_filter")
    else:
        message = t("providers.chatgpt.incomplete")
    return ProviderError(message, kind="invalid", usage=usage, model=model)


def is_chat_model(model_id: str) -> bool:
    """Text chat/reasoning models usable with the Responses API (dated snapshots are
    left out: their alias is listed and any id can still be typed)."""
    return bool(
        _CHAT_MODEL.match(model_id)
        and not _NOT_CHAT.search(model_id)
        and not _SNAPSHOT.search(model_id)
    )


def supports_reasoning(model_id: str) -> bool:
    """Whether the Responses API takes ``reasoning.effort`` for this model: GPT-5 and
    later and the o-series, except their ``-chat`` variants. Other chat models (gpt-4o,
    gpt-4.1, chatgpt-4o-latest...) answer a request that carries it with a 400."""
    return bool(_REASONING_MODEL.match(model_id) and not _NOT_REASONING.search(model_id))


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def usage_from_response(usage: object) -> Usage:
    """Usage from a ``ResponseUsage``, reading every field defensively (the SDK returns
    None for fields the server omits). ``input_tokens`` is the uncached remainder, as
    for Anthropic: OpenAI's input_tokens include cache reads and cache writes. No cost:
    the engine prices every call (owner price overrides included)."""
    if usage is None:
        return Usage()
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached = _int(getattr(input_details, "cached_tokens", None))
    written = _int(getattr(input_details, "cache_write_tokens", None))
    return Usage(
        input_tokens=max(0, _int(getattr(usage, "input_tokens", None)) - cached - written),
        output_tokens=_int(getattr(usage, "output_tokens", None)),
        cache_read_tokens=cached,
        cache_write_tokens=written,
        reasoning_tokens=_int(getattr(output_details, "reasoning_tokens", None)),
    )


def _detail(message: object) -> str:
    text = _SECRET_RE.sub(lambda m: f"{m.group(1) or m.group(2)}***", str(message or "").strip())
    return text if len(text) <= DETAIL_MAX_CHARS else text[: DETAIL_MAX_CHARS - 1] + "…"


def response_error(code: str | None, message: object) -> ProviderError:
    """ProviderError for an error reported inside the stream (``response.failed``, an
    ``error`` event or an SSE error payload)."""
    if code == "rate_limit_exceeded":
        return ProviderError(lazy("providers.openai.rate_limit"), kind="rate_limit", retryable=True)
    if code == "insufficient_quota":
        return ProviderError(lazy("providers.openai.no_credit"), kind="rate_limit")
    if code is None or code in _SERVER_CODES:
        return _interrupted()
    return ProviderError(
        lazy("providers.openai.rejected_code", code=code, detail=_detail(message)), kind="invalid"
    )


def _interrupted() -> ProviderError:
    return ProviderError(lazy("providers.chatgpt.interrupted"), kind="unavailable", retryable=True)


def map_api_error(exc: openai.APIError) -> ProviderError:
    """Typed SDK errors -> ProviderError (after the SDK's own retries)."""
    if isinstance(exc, openai.APITimeoutError):
        return ProviderError(lazy("providers.openai.timeout"), kind="timeout", retryable=True)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(
            lazy("providers.openai.unreachable"), kind="unavailable", retryable=True
        )
    if not isinstance(exc, openai.APIStatusError):
        # An SSE error payload after the stream opened: never retried by the SDK.
        return response_error(exc.code, exc.message)
    status = exc.status_code
    body = exc.body if isinstance(exc.body, dict) else {}
    message = body.get("message") or exc.message
    if status in (401, 403):
        return ProviderError(lazy("providers.openai.key_rejected"), kind="auth")
    if status == 429:
        if exc.code == "insufficient_quota":
            return ProviderError(lazy("providers.openai.no_credit"), kind="rate_limit")
        return ProviderError(lazy("providers.openai.rate_limit"), kind="rate_limit", retryable=True)
    if status >= 500 or status in (408, 409):
        return ProviderError(
            lazy("providers.openai.unavailable"), kind="unavailable", retryable=True
        )
    if status in (400, 404, 413, 422):
        return ProviderError(
            lazy("providers.openai.rejected", detail=_detail(message)), kind="invalid"
        )
    return ProviderError(lazy("providers.openai.error", status=status), kind="internal")


class OpenAIApiProvider:
    """Provider for agent ``chatgpt`` in mode ``api``.

    ``client`` is optional: by default one ``AsyncOpenAI`` is created on first use from
    ``settings.openai_api_key`` and closed by :meth:`aclose`.
    """

    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._models_cache: tuple[float, tuple[ModelInfo, ...], bool] | None = None
        """(expiry on the monotonic clock, models, live)."""
        self._models_lock = asyncio.Lock()

    @property
    def agent(self) -> AgentName:
        return "chatgpt"

    @property
    def mode(self) -> ProviderMode:
        return "api"

    @property
    def default_model(self) -> str:
        return self._settings.chatgpt_model or DEFAULT_MODEL

    @property
    def fast_model(self) -> str:
        """Model of the cheap internal calls (summaries) when none is requested."""
        return self._settings.chatgpt_fast_model or DEFAULT_FAST_MODEL

    def _configured(self) -> bool:
        key = self._settings.openai_api_key
        return self._client is not None or bool(key and key.get_secret_value())

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            key = self._settings.openai_api_key
            if key is None or not key.get_secret_value():
                raise ProviderError(lazy("providers.openai.missing_key"), kind="auth")
            self._client = openai.AsyncOpenAI(
                api_key=key.get_secret_value(), timeout=TIMEOUT, max_retries=MAX_RETRIES
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
        chat = to_chat_messages(request, "chatgpt")
        messages: ResponseInputParam = [
            {"role": role, "content": content} for role, content in chat
        ]
        if request.attachments:
            # The last message is the user's, and it ends with the prompt.
            content = input_parts(request.attachments, await read_files(request.attachments))
            content.append({"type": "input_text", "text": chat[-1][1]})
            messages[-1] = {"role": "user", "content": content}
        effort: Effort = (
            lowest_effort(model)
            if request.reasoning == "off"
            else EFFORT_BY_PURPOSE[request.purpose]
        )
        started = time.monotonic()
        deadline = started + self._settings.provider_timeout_seconds
        parts: list[str] = []
        refusal: list[str] = []
        refused = False
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
                    reasoning={"effort": effort} if supports_reasoning(model) else omit,
                    # The billed output budget, reasoning included: never raised here.
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
                    elif event.type == "response.refusal.delta":
                        refused = True
                        refusal.append(event.delta)
                    elif event.type == "response.refusal.done":
                        refused, refusal = True, [event.refusal]
                    elif event.type in ("response.completed", "response.incomplete"):
                        response = event.response
                        final_model = response.model or model
                        usage = usage_from_response(response.usage)
                        declined = _refusal_in_output(response.output)
                        if refused or declined is not None:
                            # Text streamed before a refusal is never an answer.
                            explanation = "".join(refusal) or declined or ""
                            raise refusal_error(explanation, usage, final_model)
                        text = "".join(parts)
                        truncated = event.type == "response.incomplete"
                        finish_reason: str | None = None
                        if truncated:
                            details = response.incomplete_details
                            reason = details.reason if details is not None else None
                            if not text.strip():
                                raise incomplete_error(
                                    reason, request.max_output_tokens, usage, final_model
                                )
                            finish_reason = (
                                FINISH_REASONS.get(reason, reason) if reason else "incomplete"
                            )
                        yield GenerationResult(
                            text=text,
                            usage=usage,
                            model=final_model,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            ttft_ms=ttft_ms,
                            truncated=truncated,
                            finish_reason=finish_reason,
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
        raise _interrupted()

    async def _until[T](self, deadline: float, operation: Awaitable[T]) -> T:
        """Await with the time left of the call's wall-clock budget."""
        try:
            return await asyncio.wait_for(operation, max(0.0, deadline - time.monotonic()))
        except TimeoutError:
            limit = seconds(self._settings.provider_timeout_seconds)
            raise ProviderError(
                lazy("providers.chatgpt.timeout", seconds=limit), kind="timeout"
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
            detail=lazy(
                "providers.api_key_configured"
                if configured
                else "providers.openai.status.missing_key"
            ),
        )

    @property
    def models_live(self) -> bool:
        """Whether the latest :meth:`list_models` came from the Models API."""
        return self._models_cache is not None and self._models_cache[2]

    async def list_models(self, *, refresh: bool = False) -> Sequence[ModelInfo]:
        """Chat models of the account (cached ~10 min), or a static list if it fails."""
        async with self._models_lock:
            cached = self._models_cache
            if refresh or cached is None or time.monotonic() >= cached[0]:
                models, live = await self._fetch_models()
                ttl = MODELS_TTL_SECONDS if live else FALLBACK_TTL_SECONDS
                cached = self._models_cache = (time.monotonic() + ttl, models, live)
        return cached[1]

    async def _fetch_models(self) -> tuple[tuple[ModelInfo, ...], bool]:
        live = True
        try:
            client = self._get_client()
            async with asyncio.timeout(LIST_TIMEOUT_SECONDS):
                listed = [model async for model in client.models.list()]
            # Newest first, so new models show up at the top.
            ids = [m.id for m in sorted(listed, key=lambda m: -m.created) if is_chat_model(m.id)]
            if not ids:
                # Only logged (below): in English, as the logs are.
                raise ProviderError("no chat model in the listing", kind="internal")
        except Exception as exc:
            reason = exc.log_text if isinstance(exc, ProviderError) else type(exc).__name__
            logger.warning("Could not list the OpenAI models (%s); using the fallback", reason)
            ids, live = list(MODEL_DESCRIPTIONS), False
        default = self.default_model
        if default not in ids:
            ids.insert(0, default)
        models = tuple(
            ModelInfo(
                id=model_id,
                label=model_id,
                description=MODEL_DESCRIPTIONS.get(model_id, ""),
                is_default=model_id == default,
            )
            for model_id in ids
        )
        return models, live

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await client.close()
