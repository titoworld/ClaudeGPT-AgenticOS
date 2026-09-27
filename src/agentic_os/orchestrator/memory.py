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
from dataclasses import dataclass

from agentic_os.domain import AgentName, Usage
from agentic_os.orchestrator.prompts import SUMMARY_PROMPT, system_prompt
from agentic_os.orchestrator.store import History, Store, StoredMessage, UsageRecord
from agentic_os.orchestrator.tokens import estimate_context_tokens
from agentic_os.providers.base import (
    ChatTurn,
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
)

logger = logging.getLogger(__name__)

SUMMARY_MAX_OUTPUT_TOKENS = 2000
SUMMARY_PREFERENCE: tuple[AgentName, ...] = ("claude", "chatgpt")


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
    context: TurnContext
    """The compacted context."""
    tokens_removed: int
    """Estimated tokens removed from every request that carries the context."""
    usage: Usage
    """Usage of the summary call."""
    agent: AgentName
    """Agent that wrote the summary."""


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
) -> CompactionResult | None:
    """Summarize ``context.messages[:cut]`` (plus the previous summary) with a fast model.

    Claude is preferred, ChatGPT is the fallback. Every attempt is recorded as usage.
    Returns None (context left untouched) when no provider could write the summary.
    """
    upto_message_id = context.messages[cut - 1].id
    for agent in (a for a in SUMMARY_PREFERENCE if a in providers):
        provider = providers[agent]
        request = GenerationRequest(
            system=system_prompt(agent),
            prompt=SUMMARY_PROMPT,
            history=context.history[:cut],
            context_summary=context.summary,
            purpose="summary",
            fast=True,
            max_output_tokens=SUMMARY_MAX_OUTPUT_TOKENS,
        )
        started = time.monotonic()
        try:
            result = await _collect(provider, request)
            summary = result.text.strip()
            if not summary:
                raise ProviderError("El resum és buit.", kind="invalid")
        except Exception as exc:
            if isinstance(exc, ProviderError):
                error = f"{exc.kind}: {exc.message}"
                logger.warning("Compaction summary by %s failed: %s", agent, error)
            else:
                error = f"internal: {type(exc).__name__}"
                logger.exception("Compaction summary by %s failed unexpectedly", agent)
            await store.record_usage(
                UsageRecord(
                    conversation_id=conversation_id,
                    turn_id=None,
                    agent=agent,
                    provider_mode=provider.mode,
                    model="",
                    purpose="summary",
                    usage=Usage(),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    ttft_ms=None,
                    ok=False,
                    error=error,
                )
            )
            continue
        await store.record_usage(
            UsageRecord(
                conversation_id=conversation_id,
                turn_id=None,
                agent=agent,
                provider_mode=provider.mode,
                model=result.model,
                purpose="summary",
                usage=result.usage,
                latency_ms=result.latency_ms,
                ttft_ms=result.ttft_ms,
                ok=True,
            )
        )
        await store.set_summary(conversation_id, summary, upto_message_id)
        compacted = build_context(summary, context.messages[cut:])
        return CompactionResult(
            context=compacted,
            tokens_removed=max(0, context.tokens - compacted.tokens),
            usage=result.usage,
            agent=agent,
        )
    return None
