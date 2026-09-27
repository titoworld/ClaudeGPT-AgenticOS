"""Persistence contract used by the orchestrator (implemented by storage.SqliteStore).

The orchestrator depends only on this protocol, so it can be tested with an
in-memory implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from agentic_os.domain import (
    AgentName,
    MessageKind,
    ProviderMode,
    Purpose,
    SavingKind,
    TurnMode,
    Usage,
)

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class NewMessage:
    conversation_id: int
    kind: MessageKind
    content: str
    turn_id: int | None = None
    """Id of the question message of this turn (None only for the question itself)."""
    agent: AgentName | None = None
    """Author agent; None for the user's question."""
    round: int = 0
    final: bool = False
    """True for messages that are part of the canonical history used as context
    (the question, solo/duel answers and the debate synthesis)."""
    meta: Mapping[str, JsonValue] = field(default_factory=dict)
    """Free-form metadata: usage, model, latency_ms, agreement, critique, cached..."""


@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: int
    conversation_id: int
    turn_id: int
    kind: MessageKind
    content: str
    agent: AgentName | None
    round: int
    final: bool
    meta: Mapping[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class History:
    """Context of a conversation for the next turn."""

    summary: str | None
    """Compacted summary of messages with id <= summary_upto_id."""
    summary_upto_id: int
    messages: Sequence[StoredMessage]
    """Final messages with id > summary_upto_id, oldest first."""


@dataclass(frozen=True, slots=True)
class UsageRecord:
    conversation_id: int | None
    turn_id: int | None
    agent: AgentName
    provider_mode: ProviderMode
    model: str
    purpose: Purpose
    usage: Usage
    latency_ms: int
    ttft_ms: int | None
    ok: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SavingRecord:
    conversation_id: int | None
    turn_id: int | None
    kind: SavingKind
    tokens_saved: int
    """Estimated tokens avoided (input + output)."""
    detail: str = ""
    cost_usd: float | None = None
    """Estimated value of the avoided tokens (None when the turn had no priced call)."""


@dataclass(frozen=True, slots=True)
class CachedTurn:
    """Everything needed to replay a finished turn without calling any model."""

    mode: TurnMode
    messages: Sequence[NewMessage]
    """Assistant messages of the original turn (conversation/turn ids are rewritten on replay)."""
    tokens: int
    """Total tokens the original turn consumed (reported as saved on a hit)."""


class Store(Protocol):
    async def create_conversation(self, title: str) -> int: ...

    async def conversation_exists(self, conversation_id: int) -> bool: ...

    async def get_history(self, conversation_id: int) -> History: ...

    async def add_message(self, message: NewMessage) -> int: ...

    async def set_summary(
        self, conversation_id: int, summary: str, upto_message_id: int
    ) -> None: ...

    async def record_usage(self, record: UsageRecord) -> None: ...

    async def record_saving(self, record: SavingRecord) -> None: ...

    async def cache_get(self, key: str, now: datetime) -> CachedTurn | None: ...

    async def cache_put(self, key: str, value: CachedTurn, expires_at: datetime) -> None: ...
