"""Turn a GenerationRequest into provider-ready input.

- ``render_transcript``: a single text block, for cli providers whose process
  receives one user message per call.
- ``to_chat_messages``: alternating user/assistant messages for api providers,
  append-only across turns so the vendor prompt cache keeps hitting.
"""

from __future__ import annotations

from typing import Literal

from agentic_os.domain import AgentName
from agentic_os.providers.base import ChatTurn, GenerationRequest

AGENT_LABELS: dict[AgentName, str] = {"claude": "Claude", "chatgpt": "ChatGPT"}

ChatRole = Literal["user", "assistant"]


def _speaker(turn: ChatTurn) -> str:
    if turn.role == "user":
        return "User"
    return AGENT_LABELS[turn.agent] if turn.agent else "Assistant"


def render_transcript(request: GenerationRequest) -> str:
    parts: list[str] = []
    if request.context_summary:
        parts.append(f"<conversation_summary>\n{request.context_summary}\n</conversation_summary>")
    if request.history:
        messages = "\n".join(
            f'<message from="{_speaker(turn)}">\n{turn.content}\n</message>'
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
        raw.append(
            (
                "user",
                f"<conversation_summary>\n{request.context_summary}\n</conversation_summary>",
            )
        )
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
