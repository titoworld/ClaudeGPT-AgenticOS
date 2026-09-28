"""Turn a GenerationRequest into provider-ready input.

- ``render_transcript``: a single text block, for cli providers whose process
  receives one user message per call.
- ``to_chat_messages``: alternating user/assistant messages for api providers,
  append-only across turns so the vendor prompt cache keeps hitting.

Text embedded inside the XML-like sections (history, summaries, the other agent's
answer...) goes through ``neutralize_tags`` first, so it cannot close a section or
forge an owner's message such as ``<message from="User">``, not even with the tag name
split by invisible characters (a U+200B zero-width space inside ``claude_answer``) or
written in full-width letters.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from agentic_os.domain import AgentName
from agentic_os.providers.base import ChatTurn, GenerationRequest

AGENT_LABELS: dict[AgentName, str] = {"claude": "Claude", "chatgpt": "ChatGPT"}

ChatRole = Literal["user", "assistant"]

RESERVED_TAGS: frozenset[str] = frozenset(
    {
        # render_transcript and to_chat_messages
        "conversation_summary",
        "conversation_history",
        "message",
        "current_message",
        # orchestrator/prompts.py templates (a transcript may embed them)
        "user_message",
        "question",
        "your_previous_answer",
        "claude_answer",
        "chatgpt_answer",
        "critique",
        "answer",
        "agreement",
    }
)
"""Every tag name the app's prompts use as a delimiter."""

IGNORABLE_CHARS = (
    r"\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f"
    r"\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8"
    r"\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff"
)
"""Regex character-class body of Unicode's Default_Ignorable_Code_Point characters:
invisible format characters (zero-width spaces and joiners, soft hyphen, bidi controls,
word joiner, BOM, variation selectors, tag characters...) that nobody sees on screen."""

_IGNORABLE_RE = re.compile(f"[{IGNORABLE_CHARS}]")
_NAME_CHARS = r"\w\-\uff0d\uff3f"  # full-width "-" and "_" are not \w
_TAG_OPENING_RE = re.compile(
    # "<" (or its full-width and small forms), an optional "/", then the tag name;
    # spaces and invisible characters may come anywhere, even inside the name.
    rf"[<\uff1c\ufe64](?=[\s{IGNORABLE_CHARS}]*(?:[/\uff0f][\s{IGNORABLE_CHARS}]*)?"
    rf"([{_NAME_CHARS}][{_NAME_CHARS}{IGNORABLE_CHARS}]*))"
)


def _escape_reserved(match: re.Match[str]) -> str:
    name = unicodedata.normalize("NFKC", _IGNORABLE_RE.sub("", match.group(1))).casefold()
    return "&lt;" if name in RESERVED_TAGS else match.group(0)


def neutralize_tags(text: str) -> str:
    """Escape the ``<`` of reserved tags in untrusted text (``</answer>`` becomes
    ``&lt;/answer>``): still readable, but it can no longer close or open a section.

    Tag names are compared ignoring case, invisible characters (``IGNORABLE_CHARS``)
    and compatibility forms (NFKC): a zero-width space inside ``claude_answer``, a
    full-width ``<`` (U+FF1C) or full-width letters do not get past it. The text itself
    is kept as is, other markup (``<div>``, ``a < b``) is left alone, and the result is
    deterministic, so re-rendered history stays byte-identical for the prompt caches."""
    return _TAG_OPENING_RE.sub(_escape_reserved, text)


def _speaker(turn: ChatTurn) -> str:
    if turn.role == "user":
        return "User"
    return AGENT_LABELS[turn.agent] if turn.agent else "Assistant"


def _summary_section(summary: str) -> str:
    return f"<conversation_summary>\n{neutralize_tags(summary)}\n</conversation_summary>"


def render_transcript(request: GenerationRequest) -> str:
    """Summary, history and the current message as one text. ``request.prompt`` is
    embedded as is: the orchestrator composes it (its templates neutralize what they
    embed), and in a solo turn it is the owner's own message."""
    parts: list[str] = []
    if request.context_summary:
        parts.append(_summary_section(request.context_summary))
    if request.history:
        messages = "\n".join(
            f'<message from="{_speaker(turn)}">\n{neutralize_tags(turn.content)}\n</message>'
            for turn in request.history
        )
        parts.append(f"<conversation_history>\n{messages}\n</conversation_history>")
    if not parts:
        return request.prompt
    parts.append(f"<current_message>\n{request.prompt}\n</current_message>")
    return "\n\n".join(parts)


def to_chat_messages(request: GenerationRequest, agent: AgentName) -> list[tuple[ChatRole, str]]:
    """Messages as seen by ``agent``: the other agent's answers are labelled."""
    raw: list[tuple[ChatRole, str]] = []
    if request.context_summary:
        raw.append(("user", _summary_section(request.context_summary)))
    for turn in request.history:
        if turn.role == "user":
            raw.append(("user", turn.content))
        elif turn.agent is None or turn.agent == agent:
            raw.append(("assistant", turn.content))
        else:
            raw.append(("assistant", f"[{AGENT_LABELS[turn.agent]}]\n{turn.content}"))
    raw.append(("user", request.prompt))

    merged: list[tuple[ChatRole, str]] = []
    for role, content in raw:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, f"{merged[-1][1]}\n\n{content}")
        else:
            merged.append((role, content))
    if merged[0][0] == "assistant":
        merged.insert(0, ("user", "(continuació de la conversa)"))
    return merged
