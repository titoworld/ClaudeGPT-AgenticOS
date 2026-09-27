"""Per-turn accounting of token usage and estimated savings."""

from __future__ import annotations

from dataclasses import replace

from agentic_os.domain import Purpose, SavingKind, Usage
from agentic_os.orchestrator.events import Savings
from agentic_os.orchestrator.store import SavingRecord
from agentic_os.orchestrator.tokens import estimate_tokens


def _billed_tokens(usage: Usage) -> int:
    """Every token a call is billed for: input, output, cache reads and cache writes
    (``usage.cost_usd`` prices all four)."""
    return usage.total_tokens + usage.cache_read_tokens + usage.cache_write_tokens


class TurnAccounting:
    """Accumulates the usage of a turn and the tokens its savings avoided."""

    def __init__(self) -> None:
        self.usage = Usage()
        """Every billed model call of the turn: the messages' calls, the compaction
        summary and calls whose reply was unusable (empty)."""
        self.turn_usage = Usage()
        """Only the calls that produce the turn's messages (what a cache hit replays)."""
        self.cache = 0
        self.unchanged = 0
        self.early_stop = 0
        self.compaction_per_request = 0
        self.context_requests = 0
        """Model calls of this turn that carried the (compacted) conversation context."""
        self.rounds_skipped = 0
        self.unchanged_count = 0
        self.cache_cost: float | None = None
        """Value of the turn a cache hit replays (its cost at current prices), if known."""
        self._revision_tokens = 0
        self._revision_calls = 0
        self._revision_cost = 0.0
        self._revision_priced_calls = 0
        """Cost and number of the revision calls with a known price."""
        self._early_stop_cost: float | None = None
        """Value of the skipped rounds (their revision pairs at this turn's average cost)."""
        self._priced_cost = 0.0
        self._priced_tokens = 0
        """Cost and billed tokens of the calls with a known price (the turn's average
        price per billed token)."""

    def add_spent(self, usage: Usage) -> None:
        """A billed call that produced no message of the turn (the compaction summary,
        or a reply that came back empty): it counts in the turn's usage only."""
        self.usage += usage

    def add_call(self, usage: Usage, purpose: Purpose) -> None:
        """A successful model call that produced a message of the turn."""
        self.usage += usage
        self.turn_usage += usage
        if usage.cost_usd is not None:
            self._priced_cost += usage.cost_usd
            self._priced_tokens += _billed_tokens(usage)
        if purpose == "revision":
            self._revision_tokens += usage.total_tokens
            self._revision_calls += 1
            if usage.cost_usd is not None:
                self._revision_cost += usage.cost_usd
                self._revision_priced_calls += 1

    def add_context_request(self) -> None:
        """A model call that carried the compacted context (and returned a result)."""
        self.context_requests += 1

    def add_unchanged(self, kept_answer: str) -> None:
        """An agent kept its answer instead of rewriting it."""
        self.unchanged += estimate_tokens(kept_answer)
        self.unchanged_count += 1

    def add_early_stop(self, rounds_skipped: int) -> None:
        """Rounds skipped by consensus, counted as the average revision pair of this turn
        and valued at the average cost of its priced revision calls."""
        if rounds_skipped <= 0 or self._revision_calls == 0:
            return
        pair_tokens = 2 * self._revision_tokens / self._revision_calls
        self.rounds_skipped += rounds_skipped
        self.early_stop += round(rounds_skipped * pair_tokens)
        if self._revision_priced_calls:
            pair_cost = 2 * self._revision_cost / self._revision_priced_calls
            self._early_stop_cost = (self._early_stop_cost or 0.0) + rounds_skipped * pair_cost

    @property
    def compaction(self) -> int:
        return self.compaction_per_request * self.context_requests

    def _values(self) -> dict[SavingKind, float | None]:
        """Value in USD of each kind of saving (None when it cannot be priced).

        A cache hit is worth what the replayed turn cost and skipped rounds what their
        revision calls cost; compacted context and kept answers are valued at this
        turn's average price per billed token (cache reads and writes included, as in
        the cost it divides)."""
        rate = self._priced_cost / self._priced_tokens if self._priced_tokens else None
        early_stop = self._early_stop_cost
        if early_stop is None and rate is not None:
            early_stop = self.early_stop * rate  # no priced revision: the average price
        return {
            "cache": self.cache_cost,
            "compaction": None if rate is None else self.compaction * rate,
            "early_stop": early_stop,
            "unchanged": None if rate is None else self.unchanged * rate,
        }

    def savings(self) -> Savings:
        """Tokens saved so far and their value (the sum of :meth:`_values`).
        ``cost_usd`` is None while no call of the turn has a known price."""
        saved = Savings(
            cache=self.cache,
            compaction=self.compaction,
            early_stop=self.early_stop,
            unchanged=self.unchanged,
        )
        known = [value for value in self._values().values() if value is not None]
        if not known:
            return saved
        return replace(saved, cost_usd=sum(known))

    def saving_records(self, conversation_id: int, turn_id: int) -> list[SavingRecord]:
        """One record per kind with a positive saving (details in Catalan), each with
        its own value, so the records add up to ``savings().cost_usd``."""
        values = self._values()
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
                cost_usd=values[kind],
            )
            for kind, tokens, detail in entries
            if tokens > 0
        ]
