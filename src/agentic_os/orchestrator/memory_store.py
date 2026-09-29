"""In-memory :class:`~agentic_os.orchestrator.store.Store` (tests and the fake demo).

Nothing is persisted; every call completes without awaiting, so the store is
safe to share between concurrent turns of one event loop.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast

from agentic_os.orchestrator.events import TurnOutcome
from agentic_os.orchestrator.store import (
    AttachmentNotFoundError,
    CachedTurn,
    History,
    JsonValue,
    NewMessage,
    SavingRecord,
    StoredMessage,
    UsageRecord,
)
from agentic_os.pdf_facts import CHECK_VERSION, PdfCheck, check_from_json
from agentic_os.providers.base import Attachment


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
        self.attachments: dict[int, Attachment] = {}
        self.attachment_links: dict[int, tuple[int, ...]] = {}
        """Attachment ids of each question message, in order."""
        self.pdf_checks: dict[tuple[str, int], str] = {}
        """Claude's checks of PDFs by (sha256, version), as JSON like the SQLite store."""
        self._next_conversation_id = 1
        self._next_message_id = 1
        self._next_attachment_id = 1

    async def create_conversation(self, title: str) -> int:
        conversation_id = self._next_conversation_id
        self._next_conversation_id += 1
        self.conversations[conversation_id] = _Conversation(title=title, created_at=self._clock())
        return conversation_id

    async def discard_conversation(self, conversation_id: int) -> bool:
        """Delete a conversation without messages (see the protocol)."""
        if conversation_id not in self.conversations or any(
            m.conversation_id == conversation_id for m in self.messages
        ):
            return False
        del self.conversations[conversation_id]
        return True

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
        """Store a message. Same rules as the SQLite store: a question starts its own
        turn (``turn_id`` None) and is stored with its attachments or not at all; any
        other message references a question of the same conversation."""
        if message.conversation_id not in self.conversations:
            raise KeyError(f"unknown conversation {message.conversation_id}")
        if message.kind == "question":
            if message.turn_id is not None:
                raise ValueError("a question starts its own turn: turn_id must be None")
            self._check_links(message.attachments)
        elif message.attachments:
            raise ValueError("only a question takes attachments")
        elif not any(
            m.id == message.turn_id
            and m.kind == "question"
            and m.conversation_id == message.conversation_id
            for m in self.messages
        ):
            raise ValueError(f"a {message.kind} message needs the turn_id of its question")
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
        if message.attachments:
            self.attachment_links[message_id] = tuple(message.attachments)
        return message_id

    async def set_summary(self, conversation_id: int, summary: str, upto_message_id: int) -> None:
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            raise KeyError(f"unknown conversation {conversation_id}")
        conversation.summary = summary
        conversation.summary_upto_id = upto_message_id

    async def set_turn_outcome(self, question_message_id: int, outcome: TurnOutcome) -> None:
        """``meta.outcome`` of the question, as JSON (like the SQLite store, it goes
        through a JSON round trip); ignored if the id is not a question."""
        for index, message in enumerate(self.messages):
            if message.id == question_message_id and message.kind == "question":
                value = cast(JsonValue, json.loads(json.dumps(outcome.to_wire(), allow_nan=False)))
                meta = {**message.meta, "outcome": value}
                self.messages[index] = replace(message, meta=meta)
                return

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

    def add_attachment(self, attachment: Attachment) -> int:
        """Store an uploaded attachment (what the upload route does with the SQLite
        store) and return its id; its ``created_at`` defaults to the store's clock."""
        attachment_id = self._next_attachment_id
        self._next_attachment_id += 1
        if attachment.created_at is None:
            attachment = replace(attachment, created_at=self._clock())
        self.attachments[attachment_id] = replace(attachment, mode="full")
        return attachment_id

    async def get_attachments(self, ids: Sequence[int]) -> list[Attachment]:
        found: list[Attachment] = []
        for attachment_id in ids:
            attachment = self.attachments.get(attachment_id)
            if attachment is None:
                raise AttachmentNotFoundError(attachment_id)
            found.append(attachment)
        return found

    async def link_attachments(self, message_id: int, ids: Sequence[int]) -> None:
        """Same rules as the SQLite store: only a question takes attachments, once, each
        of them once, and only ones that exist (:class:`AttachmentNotFoundError`)."""
        if not any(m.id == message_id and m.kind == "question" for m in self.messages):
            raise ValueError(f"message {message_id} is not a question")
        if message_id in self.attachment_links:
            raise ValueError(f"message {message_id} already has attachments")
        self._check_links(ids)
        self.attachment_links[message_id] = tuple(ids)

    async def get_pdf_check(self, sha256: str) -> PdfCheck | None:
        """The check of the current version, through a JSON round trip (see the
        protocol)."""
        return check_from_json(self.pdf_checks.get((sha256, CHECK_VERSION)))

    async def put_pdf_check(self, sha256: str, check: PdfCheck) -> None:
        self.pdf_checks[(sha256, check.version)] = check.to_json()

    def _check_links(self, ids: Sequence[int]) -> None:
        """Each id once, and every one an attachment that exists."""
        if len(set(ids)) != len(ids):
            raise ValueError("an attachment cannot be linked twice to a message")
        for attachment_id in ids:
            if attachment_id not in self.attachments:
                raise AttachmentNotFoundError(attachment_id)
