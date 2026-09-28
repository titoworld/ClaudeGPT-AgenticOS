"""Conversation context for a turn: canonical history and compaction.

The canonical history only contains final messages (the question and the solo/duel
answers or the debate synthesis); intermediate debate messages never reach the
context. When the context grows beyond a threshold, everything except the most
recent messages is replaced by a summary written by a fast model.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass, replace

from agentic_os.domain import AgentName, Usage
from agentic_os.orchestrator.accounting import failed_call_usage, is_billed
from agentic_os.orchestrator.prompts import SUMMARY_PROMPT, system_prompt
from agentic_os.orchestrator.store import History, Store, StoredMessage, UsageRecord
from agentic_os.orchestrator.tokens import estimate_context_tokens
from agentic_os.pricing import ModelPrice, estimate_cost_usd
from agentic_os.providers.base import (
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
)

logger = logging.getLogger(__name__)

SUMMARY_MAX_OUTPUT_TOKENS = 2000
"""Default billed output budget of a summary call (``EngineConfig`` passes its own)."""
SUMMARY_PREFERENCE: tuple[AgentName, ...] = ("claude", "chatgpt")
EMPTY_SUMMARY_ERROR = "invalid: El resum és buit."
"""Usage-record error of a summary call that returned no text (billed all the same)."""
TRUNCATED_SUMMARY_ERROR = "invalid: El resum ha quedat tallat."
"""Usage-record error of a summary that was cut off: it would replace the older messages
for good, so it is never used (billed all the same)."""


@dataclass(frozen=True, slots=True)
class TurnContext:
    """The conversation context sent with answer and synthesis calls."""

    summary: str | None
    messages: tuple[StoredMessage, ...]
    """Canonical messages after the summary, oldest first."""
    history: tuple[ChatTurn, ...]
    """``messages`` as provider chat turns (same order and length)."""
    tokens: int
    """Estimated tokens of summary + history."""


@dataclass(frozen=True, slots=True)
class CompactionResult:
    context: TurnContext | None
    """The compacted context; None when no provider could write the summary (the
    context is then left untouched)."""
    tokens_removed: int
    """Estimated tokens removed from every request that carries the context."""
    usage: Usage
    """Priced usage of every summary call that returned a result, including one whose
    summary came back empty (it was billed all the same)."""
    agent: AgentName | None
    """Agent that wrote the summary (None when none did)."""
    billed: bool
    """Whether any summary call returned a result, i.e. ``usage`` was spent."""


def canonical_messages(messages: Sequence[StoredMessage]) -> tuple[StoredMessage, ...]:
    """Final messages, without questions whose turn produced no final answer (failed turns)."""
    answered = {m.turn_id for m in messages if m.final and m.kind != "question"}
    return tuple(m for m in messages if m.final and (m.kind != "question" or m.id in answered))


def to_chat_turn(message: StoredMessage) -> ChatTurn:
    if message.kind == "question":
        return ChatTurn(role="user", content=message.content)
    return ChatTurn(role="assistant", content=message.content, agent=message.agent)


def build_context(summary: str | None, messages: Sequence[StoredMessage]) -> TurnContext:
    canonical = canonical_messages(messages)
    history = tuple(to_chat_turn(m) for m in canonical)
    return TurnContext(
        summary=summary,
        messages=canonical,
        history=history,
        tokens=estimate_context_tokens(summary, history),
    )


def context_from_history(history: History) -> TurnContext:
    return build_context(history.summary, history.messages)


def compaction_cut(context: TurnContext, *, threshold: int, keep_recent: int) -> int | None:
    """Index of the first message to keep verbatim, or None when no compaction is needed.

    The cut is moved to a turn boundary (a question) so a question is never separated
    from its answers; if that is impossible, everything is summarized.
    """
    if context.tokens <= threshold or not context.messages:
        return None
    messages = context.messages
    count = len(messages)
    cut = max(0, count - max(0, keep_recent))
    index = cut
    while 0 < index < count and messages[index].kind != "question":
        index -= 1
    if index == 0:
        index = cut
        while index < count and messages[index].kind != "question":
            index += 1
    return index if index > 0 else None


async def _collect(provider: Provider, request: GenerationRequest) -> GenerationResult:
    stream = provider.stream(request)
    result: GenerationResult | None = None
    try:
        async for event in stream:
            if isinstance(event, GenerationResult):
                result = event
    finally:
        if isinstance(stream, AsyncGenerator):
            await stream.aclose()
    if result is None:
        raise ProviderError("El proveïdor no ha retornat cap resultat.", kind="internal")
    return result


async def compact(
    context: TurnContext,
    cut: int,
    *,
    conversation_id: int,
    providers: Mapping[AgentName, Provider],
    store: Store,
    max_output_tokens: int = SUMMARY_MAX_OUTPUT_TOKENS,
    models: Mapping[AgentName, str] | None = None,
    price_overrides: Mapping[str, ModelPrice] | None = None,
) -> CompactionResult:
    """Summarize ``context.messages[:cut]`` (plus the previous summary) with a fast model.

    Claude is preferred, ChatGPT is the fallback. ``models`` picks the fast model per
    agent (the provider's own when missing). Each call gets exactly ``max_output_tokens``
    of billed output and reasoning "off". Every attempt is recorded as usage, priced
    with ``price_overrides`` over the default prices: an empty or cut-off summary keeps
    its real model and billed tokens, and the next provider is tried. The result's
    ``context`` is None when no provider could write the summary; its ``usage`` adds up
    every attempt that reached a model.
    """
    upto_message_id = context.messages[cut - 1].id
    spent = Usage()
    billed = False
    for agent in (a for a in SUMMARY_PREFERENCE if a in providers):
        provider = providers[agent]
        request = GenerationRequest(
            system=system_prompt(agent),
            prompt=SUMMARY_PROMPT,
            history=context.history[:cut],
            context_summary=context.summary,
            purpose="summary",
            fast=True,
            model=(models or {}).get(agent),
            max_output_tokens=max_output_tokens,
            reasoning="off",
        )
        started = time.monotonic()
        try:
            result = await _collect(provider, request)
        except Exception as exc:
            if isinstance(exc, ProviderError):
                error = f"{exc.kind}: {exc.message}"
                logger.warning("Compaction summary by %s failed: %s", agent, error)
            else:
                error = f"internal: {type(exc).__name__}"
                logger.exception("Compaction summary by %s failed unexpectedly", agent)
            # A failure can still be billed (a refusal): it is part of the summary cost.
            model, failed = failed_call_usage(exc, request.model or "", price_overrides)
            if is_billed(failed):
                spent += failed
                billed = True
            await store.record_usage(
                UsageRecord(
                    conversation_id=conversation_id,
                    turn_id=None,
                    agent=agent,
                    provider_mode=provider.mode,
                    model=model,
                    purpose="summary",
                    usage=failed,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    ttft_ms=None,
                    ok=False,
                    error=error,
                )
            )
            continue
        usage = replace(
            result.usage, cost_usd=estimate_cost_usd(result.model, result.usage, price_overrides)
        )
        spent += usage
        billed = True
        summary = result.text.strip()
        failure: str | None = None
        if not summary:
            failure = EMPTY_SUMMARY_ERROR
            logger.warning("Compaction summary by %s came back empty", agent)
        elif result.truncated:
            failure = TRUNCATED_SUMMARY_ERROR
            logger.warning("Compaction summary by %s was cut off (%s)", agent, result.finish_reason)
        await store.record_usage(
            UsageRecord(
                conversation_id=conversation_id,
                turn_id=None,
                agent=agent,
                provider_mode=provider.mode,
                model=result.model,
                purpose="summary",
                usage=usage,
                latency_ms=result.latency_ms,
                ttft_ms=result.ttft_ms,
                ok=failure is None,
                error=failure,
            )
        )
        if failure is not None:
            continue
        await store.set_summary(conversation_id, summary, upto_message_id)
        compacted = build_context(summary, context.messages[cut:])
        return CompactionResult(
            context=compacted,
            tokens_removed=max(0, context.tokens - compacted.tokens),
            usage=spent,
            agent=agent,
            billed=True,
        )
    return CompactionResult(context=None, tokens_removed=0, usage=spent, agent=None, billed=billed)
