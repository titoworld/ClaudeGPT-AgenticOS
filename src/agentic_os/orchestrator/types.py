"""Engine input and configuration (contract between the web layer and the engine)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agentic_os.attachments import MAX_ATTACHMENTS, MAX_TURN_BYTES
from agentic_os.domain import AgentName, TurnMode, TurnOptions
from agentic_os.providers.base import DEFAULT_MAX_OUTPUT_TOKENS, AttachmentMode


@dataclass(frozen=True, slots=True)
class TurnRequest:
    request_id: str
    """Client-generated id (UUID), echoed in every event of the turn."""
    text: str
    mode: TurnMode
    target: AgentName = "claude"
    """Agent that answers in solo mode (ignored otherwise)."""
    conversation_id: int | None = None
    """None starts a new conversation."""
    options: TurnOptions = field(default_factory=TurnOptions)
    models: Mapping[AgentName, str] = field(default_factory=dict)
    """Model per agent for answers, revisions and synthesis; a missing agent uses the
    provider's configured default."""
    fast_models: Mapping[AgentName, str] = field(default_factory=dict)
    """Model per agent for cheap internal calls (compaction summaries)."""
    attachments: tuple[int, ...] = ()
    """Ids of the uploaded files attached to the question, in order
    (docs/adr/0009-adjunts.md)."""
    pdf_in_revisions: AttachmentMode = "text"
    """How the debate revisions get the attached PDFs (the owner's runtime setting):
    ``"text"`` their extracted text instead of the document, ``"full"`` the document.
    Answers and the synthesis always get every attachment whole."""


@dataclass(frozen=True, slots=True)
class EngineConfig:
    compaction_threshold_tokens: int = 6000
    """Compact the history when its estimated size exceeds this many tokens."""
    keep_recent_messages: int = 6
    """Final messages kept verbatim after a compaction."""
    cache_ttl_seconds: int = 7 * 24 * 3600
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    """Billed output budget of answers, revisions and syntheses, reasoning included
    (they run with the providers' default reasoning)."""
    summary_max_output_tokens: int = 2000
    """Billed output budget of the compaction summaries (they run with reasoning "off")."""
    max_question_chars: int = 100_000
    max_attachments: int = MAX_ATTACHMENTS
    """Attachments one question may carry (the server checks it too)."""
    max_attachment_bytes: int = MAX_TURN_BYTES
    """Raw bytes all the attachments of one question may add up to."""
