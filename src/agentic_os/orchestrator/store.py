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
from agentic_os.orchestrator.events import TurnOutcome
from agentic_os.pdf_facts import PdfCheck
from agentic_os.providers.base import Attachment

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class AttachmentNotFoundError(LookupError):
    """:meth:`Store.get_attachments` was asked for an attachment that does not exist."""

    def __init__(self, attachment_id: int) -> None:
        super().__init__(f"L'adjunt {attachment_id} no existeix.")
        self.attachment_id = attachment_id


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
    """Free-form metadata: usage, model, latency_ms, agreement, critique, cached... A
    question starts with ``outcome: None`` (see :meth:`Store.set_turn_outcome`)."""
    attachments: tuple[int, ...] = ()
    """Ids of a question's attachments, in order (docs/adr/0009-adjunts.md): linked in
    the same transaction that stores the question, so a question never exists without
    the attachments its ``meta.attachments`` describes. Only a question takes any."""


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
    """Estimated processed tokens avoided (input, cache reads and writes, output: see
    ``Usage.processed_tokens``). Rows stored before docs/adr/0008-recompte-de-tokens.md
    counted only input + output for the cache and early-stop savings."""
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
    """Processed tokens of the original turn's calls that a hit avoids: every message's
    call and the attempts declined before it (reported as saved on a hit)."""


class Store(Protocol):
    async def create_conversation(self, title: str) -> int: ...

    async def discard_conversation(self, conversation_id: int) -> bool:
        """Delete a conversation that has no messages (one a turn created and then could
        not store its question in); ``False``, and nothing changes, if it has any or does
        not exist."""
        ...

    async def conversation_exists(self, conversation_id: int) -> bool: ...

    async def get_history(self, conversation_id: int) -> History: ...

    async def add_message(self, message: NewMessage) -> int:
        """Store a message and return its id. A question with ``attachments`` is stored
        with its links or not at all: :class:`AttachmentNotFoundError` with the first id
        that does not exist, :class:`ValueError` if an id repeats or the message is not a
        question."""
        ...

    async def set_summary(
        self, conversation_id: int, summary: str, upto_message_id: int
    ) -> None: ...

    async def record_usage(self, record: UsageRecord) -> None: ...

    async def record_saving(self, record: SavingRecord) -> None: ...

    async def set_turn_outcome(self, question_message_id: int, outcome: TurnOutcome) -> None:
        """Store how a turn ended as ``meta.outcome`` of its question (``outcome.to_wire()``
        as a JSON object, the other keys untouched). The engine calls it once, when the
        turn ends; an id that is not a question (a deleted conversation) is ignored."""
        ...

    async def cache_get(self, key: str, now: datetime) -> CachedTurn | None: ...

    async def cache_put(self, key: str, value: CachedTurn, expires_at: datetime) -> None: ...

    async def get_attachments(self, ids: Sequence[int]) -> list[Attachment]:
        """The uploaded attachments with these ids, in the given order (``mode``
        "full"); raises :class:`AttachmentNotFoundError` with the first id that does
        not exist."""
        ...

    async def link_attachments(self, message_id: int, ids: Sequence[int]) -> None:
        """Link attachments to a question message already stored, in the given order
        (their position): a linked attachment is kept while its conversation exists. The
        engine stores a question and its links together instead
        (:attr:`NewMessage.attachments`)."""
        ...

    async def get_pdf_check(self, sha256: str) -> PdfCheck | None:
        """Claude's stored check of a PDF's text (docs/adr/0009-adjunts.md), by the PDF's
        content: the one of the current ``pdf_facts.CHECK_VERSION``, or None when there is
        none or it is not valid (the PDF is then checked again)."""
        ...

    async def put_pdf_check(self, sha256: str, check: PdfCheck) -> None:
        """Store Claude's check of a PDF's text, replacing the one of the same content and
        ``check.version``: later turns and other conversations reuse it."""
        ...
