"""Engine input and configuration (contract between the web layer and the engine)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agentic_os.domain import AgentName, TurnMode, TurnOptions


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


@dataclass(frozen=True, slots=True)
class EngineConfig:
    compaction_threshold_tokens: int = 6000
    """Compact the history when its estimated size exceeds this many tokens."""
    keep_recent_messages: int = 6
    """Final messages kept verbatim after a compaction."""
    cache_ttl_seconds: int = 7 * 24 * 3600
    max_output_tokens: int = 8000
    max_question_chars: int = 100_000
