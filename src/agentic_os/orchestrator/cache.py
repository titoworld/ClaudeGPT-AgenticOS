"""Turn cache: an identical question (same mode, agents, options and context) is
answered by replaying the stored messages instead of calling any model."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace

from agentic_os.domain import AgentName, TurnMode, TurnOptions, Usage
from agentic_os.orchestrator.memory import TurnContext
from agentic_os.orchestrator.store import JsonValue, NewMessage
from agentic_os.pricing import ModelPrice, estimate_cost_usd

CACHE_KEY_VERSION = 3
"""Bump when prompts, the replay format or the key itself change, to invalidate old
entries (2: the question keeps its inner whitespace; 3: earlier entries may hold replies
that were cut off, duplicated by a Codex retry or mangled by the revision parser, which
are no longer stored as complete or cached)."""


def _digest(payload: object) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def normalize_question(text: str) -> str:
    """Only outer whitespace and line endings (CRLF/CR become LF) are normalized: inner
    newlines and indentation carry meaning (code, lists, tables), so two questions that
    differ there must not share an answer."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def context_fingerprint(context: TurnContext) -> str:
    """Hash of the compacted summary and the ids and contents of the context messages."""
    return _digest(
        {
            "summary": context.summary,
            "messages": [[m.id, m.content] for m in context.messages],
        }
    )


def turn_cache_key(
    *,
    mode: TurnMode,
    target: AgentName,
    options: TurnOptions,
    question: str,
    context_fingerprint: str,
    identities: Mapping[AgentName, str],
) -> str:
    """Cache key of a turn.

    ``identities`` maps each agent taking part to its provider identity
    (``"<mode>:<model>"``, with the model requested for this turn). The solo target
    and the debate options only count in the modes that use them, so irrelevant
    differences do not cause misses.
    """
    debate = options.debate
    return _digest(
        {
            "v": CACHE_KEY_VERSION,
            "mode": mode,
            "target": target if mode == "solo" else None,
            "debate": (
                {
                    "rounds": debate.rounds,
                    "consensus_threshold": debate.consensus_threshold,
                    "synthesizer": debate.synthesizer,
                }
                if mode == "debate"
                else None
            ),
            "question": normalize_question(question),
            "context": context_fingerprint,
            "providers": {agent: identities[agent] for agent in sorted(identities)},
        }
    )


def replayed_message(message: NewMessage, *, conversation_id: int, turn_id: int) -> NewMessage:
    """A cached message moved to a new turn and flagged as ``cached``: a replay spends
    nothing, so its usage is zero (no cost basis) and the original turn's savings and
    unstored usage (its failed calls) go."""
    meta: dict[str, JsonValue] = dict(message.meta)
    meta["cached"] = True
    meta["usage"] = dict(Usage().to_dict())
    meta.pop("cost_basis", None)
    meta.pop("savings", None)
    meta.pop("unstored_usage", None)
    return replace(message, conversation_id=conversation_id, turn_id=turn_id, meta=meta)


def _int(value: JsonValue) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def replay_cost_usd(
    messages: Sequence[NewMessage], price_overrides: Mapping[str, ModelPrice] | None = None
) -> float | None:
    """What the cached turn's calls cost, at current prices (None if none is priced)."""
    total: float | None = None
    for message in messages:
        model, usage = message.meta.get("model"), message.meta.get("usage")
        if not isinstance(model, str) or not isinstance(usage, dict):
            continue
        tokens = Usage(
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
            cache_read_tokens=_int(usage.get("cache_read_tokens")),
            cache_write_tokens=_int(usage.get("cache_write_tokens")),
        )
        cost = estimate_cost_usd(model, tokens, price_overrides)
        if cost is not None:
            total = (total or 0.0) + cost
    return total
