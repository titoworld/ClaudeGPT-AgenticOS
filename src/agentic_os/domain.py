"""Shared domain types used across providers, orchestrator, storage and web layers.

This module is part of the internal contract: keep it dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AgentName = Literal["claude", "chatgpt"]
AGENTS: tuple[AgentName, ...] = ("claude", "chatgpt")

ProviderMode = Literal["cli", "api", "fake"]
"""How an agent is reached: official CLI with the user's subscription (OAuth),
the vendor API with an API key, or a deterministic fake (tests and demo)."""

TurnMode = Literal["solo", "duel", "debate"]
"""solo: one agent answers. duel: both answer in parallel. debate: both answer,
critique each other for up to N rounds (early stop on consensus) and one of them
synthesizes the final answer."""

MessageKind = Literal["question", "answer", "revision", "synthesis"]
"""question: the user's message. answer: first answer of an agent in a turn.
revision: critique + revised answer of an agent in a debate round.
synthesis: final answer of a debate."""

Purpose = Literal["answer", "revision", "synthesis", "summary"]

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


@dataclass(frozen=True, slots=True)
class TurnOptions:
    debate: DebateOptions = field(default_factory=DebateOptions)
    use_cache: bool = True
    """Reuse an identical previous answer (same question, mode, agents and context)."""
