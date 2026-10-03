"""Per-turn accounting of token usage and estimated savings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from agentic_os.domain import Purpose, SavingKind, Usage
from agentic_os.i18n import number, t
from agentic_os.orchestrator.events import Savings
from agentic_os.orchestrator.store import SavingRecord
from agentic_os.orchestrator.tokens import estimate_tokens
from agentic_os.pricing import ModelPrice, estimate_cost_usd


def is_billed(usage: Usage) -> bool:
    """Whether a call reports any billed token (input, output, cache reads or writes)."""
    return usage.processed_tokens > 0


def failed_call_usage(
    error: BaseException | None,
    fallback_model: str,
    price_overrides: Mapping[str, ModelPrice] | None = None,
) -> tuple[str, Usage]:
    """Model and priced usage of a failed call.

    A provider error may say what the call billed (``ProviderError.usage`` and
    ``model``: a refusal, see ``RefusalError``, or an output budget spent before any
    text): those tokens are priced with the turn's prices so the failed call keeps its
    cost. Otherwise nothing was billed and the usage is empty (``fallback_model`` is the
    model the call asked for). The attributes are read defensively (any exception)."""
    model = getattr(error, "model", None)
    if not isinstance(model, str) or not model:
        model = fallback_model
    usage = getattr(error, "usage", None)
    if not isinstance(usage, Usage) or not is_billed(usage):
        return model, Usage()
    return model, replace(usage, cost_usd=estimate_cost_usd(model, usage, price_overrides))


def declined_attempts(
    source: object, price_overrides: Mapping[str, ModelPrice] | None = None
) -> list[tuple[str, Usage]]:
    """Model and priced usage of each billed attempt that another model declined before
    ``source`` (a ``GenerationResult`` or a provider error) was served or refused: a
    server-side fallback. Each is priced at its own model's rates, never at the model
    that answered. ``declined`` is read defensively, like :func:`failed_call_usage`."""
    attempts = getattr(source, "declined", ())
    if not isinstance(attempts, tuple | list):
        return []
    priced: list[tuple[str, Usage]] = []
    for attempt in attempts:
        model = getattr(attempt, "model", None)
        usage = getattr(attempt, "usage", None)
        if not isinstance(model, str) or not isinstance(usage, Usage) or not is_billed(usage):
            continue
        priced.append(
            (model, replace(usage, cost_usd=estimate_cost_usd(model, usage, price_overrides)))
        )
    return priced


class TurnAccounting:
    """Accumulates the usage of a turn and the tokens its savings avoided.

    Each kind of saving is valued at the price of what it avoided (see :meth:`_values`):
    a kept answer at the output price of the model that kept it, the compacted context
    at the uncached input price of the models whose calls carried it, skipped rounds at
    the average cost of the turn's priced revision calls and a cache hit at what the
    replayed turn cost. A kind is None when no price is known for it.
    """

    def __init__(self) -> None:
        self.usage = Usage()
        """Every billed model call of the turn: the messages' calls, the compaction
        summary and calls that stored no message (failed, refused or empty, and attempts
        another model declined)."""
        self.unstored = Usage()
        """Billed calls of the turn that stored no message: failed, refused or empty
        replies and declined attempts (not the compaction summary, which travels with
        the question)."""
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
        self._priced = False
        """Whether any call of the turn has a known price."""
        self._revision_tokens = 0
        self._revision_calls = 0
        self._revision_cost = 0.0
        self._revision_priced_calls = 0
        """Cost and number of the revision calls with a known price."""
        self._early_stop_cost: float | None = None
        """Value of the skipped rounds (their revision pairs at the average cost of the
        priced revision calls)."""
        self._unchanged_cost = 0.0
        self._unchanged_priced_tokens = 0
        """Value and tokens of the kept answers whose model has a known price."""
        self._context_input_price = 0.0
        self._context_priced_calls = 0
        """Sum of the uncached input prices (USD/MTok) of the priced calls that carried
        the context, and how many they are."""

    def _see(self, usage: Usage) -> None:
        if usage.cost_usd is not None:
            self._priced = True

    def add_spent(self, usage: Usage) -> None:
        """The compaction summary: billed, stored on no message of its own (its usage
        travels with the question), so it counts in the turn's usage only."""
        self.usage += usage
        self._see(usage)

    def add_unstored(self, usage: Usage) -> None:
        """A billed call that stored no message (failed, refused, empty or declined by
        its model): it counts in the turn's usage and in :attr:`unstored`, never in the
        savings."""
        self.usage += usage
        self.unstored += usage
        self._see(usage)

    def add_call(self, usage: Usage, purpose: Purpose) -> None:
        """A successful model call that produced a message of the turn."""
        self.usage += usage
        self._see(usage)
        if purpose == "revision":
            self._revision_tokens += usage.processed_tokens
            self._revision_calls += 1
            if usage.cost_usd is not None:
                self._revision_cost += usage.cost_usd
                self._revision_priced_calls += 1

    def add_context_request(self, price: ModelPrice | None = None) -> None:
        """A billed model call that carried the compacted context, with the price of the
        model that read it (None when unknown)."""
        self.context_requests += 1
        if price is not None:
            self._priced = True
            self._context_input_price += price.input
            self._context_priced_calls += 1

    def add_unchanged(self, kept_answer: str, price: ModelPrice | None = None) -> None:
        """An agent kept its answer instead of rewriting it; ``price`` is the price of
        the model that kept it (None when unknown)."""
        tokens = estimate_tokens(kept_answer)
        self.unchanged += tokens
        self.unchanged_count += 1
        if price is not None:
            self._priced = True
            self._unchanged_cost += tokens * price.output / 1_000_000
            self._unchanged_priced_tokens += tokens

    def add_early_stop(self, rounds_skipped: int) -> None:
        """Rounds skipped by consensus, counted as the average revision pair of this turn
        (in processed tokens) and valued at the average cost of its priced revision
        calls."""
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

        - ``unchanged``: the kept tokens at the output price of the model that kept each
          answer (the output it did not write);
        - ``compaction``: the removed tokens at the uncached input price of the models
          whose calls carried the compacted context, weighted by calls;
        - ``early_stop``: the skipped revision pairs at the average cost of the priced
          revision calls;
        - ``cache``: what the replayed turn cost, at current prices.

        Tokens of a kind whose calls are only partly priced are valued at the average
        rate of the priced ones. A kind with nothing saved is worth 0 once any call of
        the turn has a price (None before)."""
        unchanged = (
            self._unchanged_cost * self.unchanged / self._unchanged_priced_tokens
            if self._unchanged_priced_tokens
            else None
        )
        compaction = (
            self.compaction * self._context_input_price / self._context_priced_calls / 1_000_000
            if self._context_priced_calls
            else None
        )
        values: dict[SavingKind, tuple[int, float | None]] = {
            "cache": (self.cache, self.cache_cost),
            "compaction": (self.compaction, compaction),
            "early_stop": (self.early_stop, self._early_stop_cost),
            "unchanged": (self.unchanged, unchanged),
        }
        nothing = 0.0 if self._priced else None
        return {kind: value if tokens > 0 else nothing for kind, (tokens, value) in values.items()}

    def savings(self) -> Savings:
        """Tokens saved so far and their value (the sum of :meth:`_values` that are
        known). With nothing saved it is 0 once the turn has a price; otherwise it is None
        while no kind with saved tokens has a known value."""
        saved = Savings(
            cache=self.cache,
            compaction=self.compaction,
            early_stop=self.early_stop,
            unchanged=self.unchanged,
        )
        tokens: dict[SavingKind, int] = {
            "cache": self.cache,
            "compaction": self.compaction,
            "early_stop": self.early_stop,
            "unchanged": self.unchanged,
        }
        values = self._values()
        saved_kinds = [kind for kind, count in tokens.items() if count > 0]
        if not saved_kinds:  # nothing saved: worth 0 once the turn has a price
            return replace(saved, cost_usd=0.0) if self._priced else saved
        known = [value for kind in saved_kinds if (value := values[kind]) is not None]
        if not known:  # no saved kind can be priced: unknown, not 0 €
            return saved
        return replace(saved, cost_usd=sum(known))

    def saving_records(self, conversation_id: int, turn_id: int) -> list[SavingRecord]:
        """One record per kind with a positive saving (details for people, in the turn's
        language), each with its own value, so the records add up to ``savings().cost_usd``."""
        values = self._values()
        entries: list[tuple[SavingKind, int, str]] = [
            ("cache", self.cache, t("engine.saving.cache")),
            (
                "compaction",
                self.compaction,
                t(
                    "engine.saving.compaction",
                    tokens=number(self.compaction_per_request),
                    calls=number(self.context_requests),
                ),
            ),
            (
                "early_stop",
                self.early_stop,
                t("engine.saving.early_stop", rounds=number(self.rounds_skipped)),
            ),
            (
                "unchanged",
                self.unchanged,
                t("engine.saving.unchanged", count=number(self.unchanged_count)),
            ),
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
