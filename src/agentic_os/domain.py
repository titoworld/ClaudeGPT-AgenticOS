"""Shared domain types used across providers, orchestrator, storage and web layers.

This module is part of the internal contract: keep it dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

AgentName = Literal["claude", "chatgpt"]
AGENTS: tuple[AgentName, ...] = ("claude", "chatgpt")

ProviderMode = Literal["cli", "api", "fake"]
"""How an agent is reached: official CLI with the user's subscription (OAuth),
the vendor API with an API key, or a deterministic fake (tests and demo)."""

TurnMode = Literal["solo", "duel", "debate", "refine"]
"""solo: one agent answers. duel: both answer in parallel. debate: both answer,
critique each other for up to N rounds (early stop on consensus) and one of them
synthesizes the final answer. refine («Perfecciona»): both answer, one of them merges
the answers into a document, and round after round both review it and the editor writes
its next version, until the owner stops it, nobody finds anything left to change or a
limit is reached (docs/adr/0010-mode-perfecciona.md)."""

MessageKind = Literal["question", "answer", "revision", "synthesis"]
"""question: the user's message. answer: first answer of an agent in a turn.
revision: critique + revised answer of an agent in a debate round.
synthesis: final answer of a debate."""

Purpose = Literal["answer", "revision", "synthesis", "summary", "check"]
"""Why a model is called: a message of the turn (answer, revision, synthesis), the
compaction summary, or Claude's check of a PDF's text for ChatGPT (docs/adr/0009-adjunts.md)."""

SavingKind = Literal["cache", "compaction", "early_stop", "unchanged"]


def other_agent(agent: AgentName) -> AgentName:
    return "chatgpt" if agent == "claude" else "claude"


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage of one model call. Missing values are 0 (or None for cost).

    ``input_tokens`` is the uncached remainder of the input: cache reads and writes are
    counted apart, and ``output_tokens`` already includes ``reasoning_tokens`` (so do
    Anthropic, OpenAI and Codex)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None

    @property
    def processed_tokens(self) -> int:
        """Every token the call processed and was billed for: input, cache reads, cache
        writes and output. Reasoning is part of the output, so it is never added again
        (docs/adr/0008-recompte-de-tokens.md)."""
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.output_tokens
        )

    def __add__(self, other: Usage) -> Usage:
        cost: float | None
        if self.cost_usd is None and other.cost_usd is None:
            cost = None
        else:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cost_usd=cost,
        )

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True, slots=True)
class DebateOptions:
    rounds: int = 2
    """Maximum critique rounds after the initial answers (0-4)."""
    consensus_threshold: int = 85
    """Stop early when both agents report agreement >= this value (0-100)."""
    synthesizer: AgentName = "claude"


RefineStopReason = Literal["owner", "converged", "unchanged", "max_rounds", "budget", "failed"]
"""Why a refine turn ended with its last version: the owner stopped it, both agents
scored it above the threshold without a defect in consecutive rounds, neither found
anything left to change, the rounds or the budget ran out, or both agents failed."""

REFINE_MAX_CHANGES: Final = 5
"""Changes a review may propose and an edit may apply in one round."""
REFINE_CHANGELOG_TAIL: Final = 30
"""Lines of the turn's changelog (the latest) that every refine prompt gets, so that the
agents do not undo earlier changes without saying why."""
REFINE_MIN_BUDGET_WORDS: Final = 300
REFINE_BUDGET_FACTOR: Final = 1.2
"""Without the owner's word limit, a version may have this many times the words of the
first version (and at least :data:`REFINE_MIN_BUDGET_WORDS`)."""
REFINE_CONVERGENCE_ROUNDS: Final = 2
"""Consecutive rounds that must agree before a refine turn stops by itself."""


@dataclass(frozen=True, slots=True)
class RefineOptions:
    """Options of a refine turn (the ranges are validated with the runtime settings)."""

    max_rounds: int = 12
    """Rounds that write a version, the first (the merge) included: 2-50."""
    budget_eur: float = 3.0
    """What the turn may spend, in euros (in subscription mode, the value at API prices):
    0.1-100. The server converts it to USD for the engine (``refine_budget_usd``)."""
    max_words: int | None = None
    """Word limit of every version: None (``REFINE_BUDGET_FACTOR`` times the first version's)
    or 100-20000."""
    stop_on_convergence: bool = True
    """Stop by itself when both agents score the version at least
    ``convergence_threshold`` and propose no defect, ``REFINE_CONVERGENCE_ROUNDS`` rounds
    in a row (the turn always stops when neither finds anything left to change)."""
    convergence_threshold: int = 90
    """50-100."""
    editor: AgentName = "claude"
    """The agent that merges the answers and writes every version."""


def words(text: str) -> int:
    """The words of a text, as the refine limits count them (runs of non-space)."""
    return len(text.split())


@dataclass(frozen=True, slots=True)
class TurnOptions:
    debate: DebateOptions = field(default_factory=DebateOptions)
    refine: RefineOptions = field(default_factory=RefineOptions)
    use_cache: bool = True
    """Reuse an identical previous answer (same question, mode, agents and context)."""
