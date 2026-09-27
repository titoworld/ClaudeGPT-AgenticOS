"""Records returned by :class:`agentic_os.storage.SqliteStore` and the runtime settings.

Timestamps are timezone-aware UTC datetimes in Python and UTC ISO 8601 strings with
millisecond precision and a ``Z`` suffix in the database and on the wire (fixed
width, so they sort lexicographically).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from agentic_os.domain import AGENTS, AgentName, DebateOptions, TurnMode, TurnOptions
from agentic_os.orchestrator.store import JsonValue, StoredMessage

TURN_MODES: Final[tuple[TurnMode, ...]] = ("solo", "duel", "debate")

ROUNDS_RANGE: Final = (0, 4)
CONSENSUS_THRESHOLD_RANGE: Final = (50, 100)
COMPACTION_THRESHOLD_RANGE: Final = (1_000, 100_000)

Wire = dict[str, JsonValue]


def utc_now() -> datetime:
    """Current time as an aware UTC datetime (the default clock)."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Convert to aware UTC; naive datetimes are taken to be UTC already."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_ts(value: datetime) -> str:
    """Format as ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (UTC; naive input is taken as UTC)."""
    return as_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    """Parse a timestamp written by :func:`format_ts` into an aware UTC datetime."""
    return as_utc(datetime.fromisoformat(value))


# --------------------------------------------------------------------------
# Runtime settings (changed by the owner from the dashboard)
# --------------------------------------------------------------------------


def _int_in_range(value: object, name: str, bounds: tuple[int, int]) -> int:
    low, high = bounds
    # bool is a subclass of int: reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"«{name}» ha de ser un enter entre {low} i {high}.")
    return value


def _mode(value: object, name: str) -> TurnMode:
    for mode in TURN_MODES:
        if value == mode:
            return mode
    raise ValueError(f"«{name}» ha de ser «solo», «duel» o «debate».")


def _agent(value: object, name: str) -> AgentName:
    for agent in AGENTS:
        if value == agent:
            return agent
    raise ValueError(f"«{name}» ha de ser «claude» o «chatgpt».")


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"«{name}» ha de ser un booleà (true o false).")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Owner preferences stored in the database (``RuntimeSettings`` in PROTOCOL.md).

    Construction validates every field and raises :class:`ValueError` with a
    Catalan message that can be shown to the owner as is.
    """

    default_mode: TurnMode = "debate"
    default_target: AgentName = "claude"
    """Agent that answers in solo mode."""
    debate: DebateOptions = field(default_factory=DebateOptions)
    use_cache: bool = True
    compaction_threshold_tokens: int = 6000

    def __post_init__(self) -> None:
        _mode(self.default_mode, "default_mode")
        _agent(self.default_target, "default_target")
        if not isinstance(self.debate, DebateOptions):
            raise ValueError("«debate» ha de ser un objecte.")
        _int_in_range(self.debate.rounds, "debate.rounds", ROUNDS_RANGE)
        _int_in_range(
            self.debate.consensus_threshold,
            "debate.consensus_threshold",
            CONSENSUS_THRESHOLD_RANGE,
        )
        _agent(self.debate.synthesizer, "debate.synthesizer")
        _bool(self.use_cache, "use_cache")
        _int_in_range(
            self.compaction_threshold_tokens,
            "compaction_threshold_tokens",
            COMPACTION_THRESHOLD_RANGE,
        )

    @classmethod
    def from_wire(cls, data: object) -> RuntimeSettings:
        """Build from decoded JSON. Missing keys take their defaults; unknown keys are
        ignored. Raises :class:`ValueError` (Catalan message) on invalid values."""
        if not isinstance(data, Mapping):
            raise ValueError("La configuració ha de ser un objecte JSON.")
        defaults = cls()
        debate_raw = data.get("debate", {})
        if not isinstance(debate_raw, Mapping):
            raise ValueError("«debate» ha de ser un objecte.")
        debate = DebateOptions(
            rounds=_int_in_range(
                debate_raw.get("rounds", defaults.debate.rounds), "debate.rounds", ROUNDS_RANGE
            ),
            consensus_threshold=_int_in_range(
                debate_raw.get("consensus_threshold", defaults.debate.consensus_threshold),
                "debate.consensus_threshold",
                CONSENSUS_THRESHOLD_RANGE,
            ),
            synthesizer=_agent(
                debate_raw.get("synthesizer", defaults.debate.synthesizer), "debate.synthesizer"
            ),
        )
        return cls(
            default_mode=_mode(data.get("default_mode", defaults.default_mode), "default_mode"),
            default_target=_agent(
                data.get("default_target", defaults.default_target), "default_target"
            ),
            debate=debate,
            use_cache=_bool(data.get("use_cache", defaults.use_cache), "use_cache"),
            compaction_threshold_tokens=_int_in_range(
                data.get("compaction_threshold_tokens", defaults.compaction_threshold_tokens),
                "compaction_threshold_tokens",
                COMPACTION_THRESHOLD_RANGE,
            ),
        )

    def to_wire(self) -> Wire:
        return {
            "default_mode": self.default_mode,
            "default_target": self.default_target,
            "debate": {
                "rounds": self.debate.rounds,
                "consensus_threshold": self.debate.consensus_threshold,
                "synthesizer": self.debate.synthesizer,
            },
            "use_cache": self.use_cache,
            "compaction_threshold_tokens": self.compaction_threshold_tokens,
        }

    def to_turn_options(self) -> TurnOptions:
        """Default options for a turn that does not specify its own."""
        return TurnOptions(debate=self.debate, use_cache=self.use_cache)


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------


def message_to_wire(message: StoredMessage) -> Wire:
    """``Message`` of PROTOCOL.md."""
    return {
        "id": message.id,
        "turn_id": message.turn_id,
        "kind": message.kind,
        "content": message.content,
        "agent": message.agent,
        "round": message.round,
        "final": message.final,
        "meta": dict(message.meta),
        "created_at": format_ts(message.created_at),
    }


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """``ConversationSummary`` of PROTOCOL.md (one row of the conversation list)."""

    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    """Time of the last message added (renaming does not change it)."""
    last_mode: TurnMode | None
    message_count: int
    """All stored messages of the conversation (every kind)."""

    def to_wire(self) -> Wire:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": format_ts(self.created_at),
            "updated_at": format_ts(self.updated_at),
            "last_mode": self.last_mode,
            "message_count": self.message_count,
        }


@dataclass(frozen=True, slots=True)
class ConversationDetail:
    """``ConversationDetail`` of PROTOCOL.md: the summary fields plus the compaction
    summary and every message, oldest first."""

    conversation: ConversationSummary
    summary: str | None
    messages: tuple[StoredMessage, ...]

    def to_wire(self) -> Wire:
        wire = self.conversation.to_wire()
        wire["summary"] = self.summary
        wire["messages"] = [message_to_wire(m) for m in self.messages]
        return wire


# --------------------------------------------------------------------------
# Owner, sessions and login throttling
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnerRecord:
    """The single owner account. Secrets are excluded from ``repr`` so they never
    end up in logs by accident."""

    password_hash: str = field(repr=False)
    totp_secret: str = field(repr=False)
    totp_last_step: int
    """Last accepted TOTP time step (replay protection)."""
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """A server-side login session. Only the SHA-256 of the cookie token is stored."""

    token_hash: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    """Absolute expiry (the idle timeout is applied on top of ``last_seen_at``)."""
    ip: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class ThrottleState:
    """Failed login attempts for one throttle key ("global" or "ip:<addr>")."""

    key: str
    failures: int
    locked_until: datetime | None
    last_failure_at: datetime
