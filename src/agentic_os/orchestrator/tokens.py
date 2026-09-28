"""Fast token estimates (no tokenizer): used for compaction decisions and savings.

The estimates only need to be consistent and roughly right (about 4 characters
per token for Latin-script text), never exact.
"""

from __future__ import annotations

from collections.abc import Iterable

from agentic_os.providers.base import ChatTurn

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
"""Role markers and separators added around every message."""


def estimate_tokens(text: str) -> int:
    """Approximate token count of ``text``: ceil(chars / 4), at least 1 if not empty."""
    return tokens_for_chars(len(text))


def tokens_for_chars(chars: int) -> int:
    """:func:`estimate_tokens` of a text of ``chars`` characters (for running counts)."""
    if chars <= 0:
        return 0
    return max(1, -(-chars // CHARS_PER_TOKEN))


def estimate_turns_tokens(turns: Iterable[ChatTurn]) -> int:
    """Approximate token count of a list of chat messages, including per-message overhead."""
    return sum(estimate_tokens(turn.content) + MESSAGE_OVERHEAD_TOKENS for turn in turns)


def estimate_context_tokens(summary: str | None, turns: Iterable[ChatTurn]) -> int:
    """Approximate token count of a conversation context (compacted summary + history)."""
    summary_tokens = estimate_tokens(summary) + MESSAGE_OVERHEAD_TOKENS if summary else 0
    return summary_tokens + estimate_turns_tokens(turns)
