"""In-memory :class:`~agentic_os.orchestrator.store.Store` (tests and the fake demo).

Nothing is persisted; every call completes without awaiting, so the store is
safe to share between concurrent turns of one event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_os.orchestrator.store import (
    CachedTurn,
    History,
    NewMessage,
    SavingRecord,
    StoredMessage,
    UsageRecord,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _Conversation:
    title: str
    created_at: datetime
    summary: str | None = None
    summary_upto_id: int = 0


class InMemoryStore:
    """Implements the orchestrator's Store protocol with plain dicts and lists."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self.conversations: dict[int, _Conversation] = {}
        self.messages: list[StoredMessage] = []
        self.usage: list[UsageRecord] = []
        self.savings: list[SavingRecord] = []
        self.cache: dict[str, tuple[CachedTurn, datetime]] = {}
        self._next_conversation_id = 1
        self._next_message_id = 1

    async def create_conversation(self, title: str) -> int:
        conversation_id = self._next_conversation_id
        self._next_conversation_id += 1
        self.conversations[conversation_id] = _Conversation(title=title, created_at=self._clock())
        return conversation_id

    async def conversation_exists(self, conversation_id: int) -> bool:
        return conversation_id in self.conversations

    async def get_history(self, conversation_id: int) -> History:
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            return History(summary=None, summary_upto_id=0, messages=())
        messages = tuple(
            m
            for m in self.messages
            if m.conversation_id == conversation_id
            and m.final
            and m.id > conversation.summary_upto_id
        )
        return History(
            summary=conversation.summary,
            summary_upto_id=conversation.summary_upto_id,
            messages=messages,
        )

    async def add_message(self, message: NewMessage) -> int:
        if message.conversation_id not in self.conversations:
            raise KeyError(f"unknown conversation {message.conversation_id}")
        message_id = self._next_message_id
        self._next_message_id += 1
        self.messages.append(
            StoredMessage(
                id=message_id,
                conversation_id=message.conversation_id,
                turn_id=message.turn_id if message.turn_id is not None else message_id,
                kind=message.kind,
                content=message.content,
                agent=message.agent,
                round=message.round,
                final=message.final,
                meta=dict(message.meta),
                created_at=self._clock(),
            )
        )
        return message_id

    async def set_summary(self, conversation_id: int, summary: str, upto_message_id: int) -> None:
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            raise KeyError(f"unknown conversation {conversation_id}")
        conversation.summary = summary
        conversation.summary_upto_id = upto_message_id

    async def record_usage(self, record: UsageRecord) -> None:
        self.usage.append(record)

    async def record_saving(self, record: SavingRecord) -> None:
        self.savings.append(record)

    async def cache_get(self, key: str, now: datetime) -> CachedTurn | None:
        entry = self.cache.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= now:
            del self.cache[key]
            return None
        return value

    async def cache_put(self, key: str, value: CachedTurn, expires_at: datetime) -> None:
        self.cache[key] = (value, expires_at)
