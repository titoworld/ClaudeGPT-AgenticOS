"""Per-turn accounting of token usage and estimated savings."""

from __future__ import annotations

from dataclasses import replace

from agentic_os.domain import Purpose, SavingKind, Usage
from agentic_os.orchestrator.events import Savings
from agentic_os.orchestrator.store import SavingRecord
from agentic_os.orchestrator.tokens import estimate_tokens


class TurnAccounting:
    """Accumulates the usage of a turn and the tokens its savings avoided."""

    def __init__(self) -> None:
        self.usage = Usage()
        """Every model call of the turn, including the compaction summary."""
        self.turn_usage = Usage()
        """Only the calls that produce the turn's messages (what a cache hit replays)."""
        self.cache = 0
        self.unchanged = 0
        self.early_stop = 0
        self.compaction_per_request = 0
        self.context_requests = 0
        """Requests of this turn that carried the (compacted) conversation context."""
        self.rounds_skipped = 0
        self.unchanged_count = 0
        self.cache_cost: float | None = None
        """Value of the turn a cache hit replays (its cost at current prices), if known."""
        self._revision_tokens = 0
        self._revision_calls = 0
        self._priced_cost = 0.0
        self._priced_tokens = 0
        """Cost and tokens of the calls with a known price (the turn's average price)."""

    def add_summary(self, usage: Usage) -> None:
        self.usage += usage

    def add_call(self, usage: Usage, purpose: Purpose) -> None:
        """A successful model call that produced a message of the turn."""
        self.usage += usage
        self.turn_usage += usage
        if usage.cost_usd is not None:
            self._priced_cost += usage.cost_usd
            self._priced_tokens += usage.total_tokens
        if purpose == "revision":
            self._revision_tokens += usage.total_tokens
            self._revision_calls += 1

    def add_context_request(self) -> None:
        self.context_requests += 1

    def add_unchanged(self, kept_answer: str) -> None:
        """An agent kept its answer instead of rewriting it."""
        self.unchanged += estimate_tokens(kept_answer)
        self.unchanged_count += 1

    def add_early_stop(self, rounds_skipped: int) -> None:
        """Rounds skipped by consensus, valued at the average revision pair of this turn."""
        if rounds_skipped <= 0 or self._revision_calls == 0:
            return
        pair_tokens = 2 * self._revision_tokens / self._revision_calls
        self.rounds_skipped += rounds_skipped
        self.early_stop += round(rounds_skipped * pair_tokens)

    @property
    def compaction(self) -> int:
        return self.compaction_per_request * self.context_requests

    def savings(self) -> Savings:
        """Tokens saved so far and their value: a cache hit is worth what the replayed
        turn cost; other savings are valued at this turn's average price per token.
        ``cost_usd`` is None while no call of the turn has a known price."""
        saved = Savings(
            cache=self.cache,
            compaction=self.compaction,
            early_stop=self.early_stop,
            unchanged=self.unchanged,
        )
        if self.cache_cost is None and not self._priced_tokens:
            return saved
        value = self.cache_cost or 0.0
        if self._priced_tokens:
            value += (saved.total - self.cache) * self._priced_cost / self._priced_tokens
        return replace(saved, cost_usd=value)

    def saving_records(self, conversation_id: int, turn_id: int) -> list[SavingRecord]:
        """One record per kind with a positive saving (details in Catalan)."""
        entries: list[tuple[SavingKind, int, str]] = [
            ("cache", self.cache, "Resposta servida des de la memòria cau"),
            (
                "compaction",
                self.compaction,
                f"{self.compaction_per_request} tokens menys en {self.context_requests} crides",
            ),
            ("early_stop", self.early_stop, f"{self.rounds_skipped} rondes omeses per consens"),
            ("unchanged", self.unchanged, f"{self.unchanged_count} respostes sense reescriure"),
        ]
        return [
            SavingRecord(
                conversation_id=conversation_id,
                turn_id=turn_id,
                kind=kind,
                tokens_saved=tokens,
                detail=detail,
            )
            for kind, tokens, detail in entries
            if tokens > 0
        ]
