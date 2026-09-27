"""Turn cache: an identical question (same mode, agents, options and context) is
answered by replaying the stored messages instead of calling any model."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace

from agentic_os.domain import AgentName, TurnMode, TurnOptions
from agentic_os.orchestrator.memory import TurnContext
from agentic_os.orchestrator.store import JsonValue, NewMessage

CACHE_KEY_VERSION = 1
"""Bump when prompts or the replay format change, to invalidate old entries."""


def _digest(payload: object) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def normalize_question(text: str) -> str:
    """Strip and collapse whitespace."""
    return " ".join(text.split())


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
    (``"<mode>:<model>"``). The solo target and the debate options only count in
    the modes that use them, so irrelevant differences do not cause misses.
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
    """A cached message moved to a new turn and flagged as ``cached``."""
    meta: dict[str, JsonValue] = dict(message.meta)
    meta["cached"] = True
    return replace(message, conversation_id=conversation_id, turn_id=turn_id, meta=meta)
