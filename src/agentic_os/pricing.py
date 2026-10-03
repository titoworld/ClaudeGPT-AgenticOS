"""Model prices and cost estimation.

Prices are in USD per million tokens, as the vendors publish them. The owner can
override or add models from the dashboard (new models work as soon as a price is
entered). In cli (subscription) mode the same prices give the *equivalent* API
cost, which is what the dashboard compares with the plan price.

Usage semantics (domain.Usage): ``input_tokens`` is the uncached remainder,
cache reads/writes are separate, ``output_tokens`` already includes reasoning.

Every lookup goes through one effective table (:func:`price_table`): the default
prices with each owner price replacing the default of the same normalized id. The
engine prices calls with it (:func:`find_price`) and the pricing page is meant to list
exactly its rows, so a model is always charged the price the page shows for it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from agentic_os.domain import Usage
from agentic_os.i18n import t

PriceSource = Literal["default", "custom"]


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input: float
    output: float
    cache_read: float
    cache_write: float

    def to_wire(self) -> dict[str, float]:
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> ModelPrice:
        def number(key: str) -> float:
            raw = value.get(key)
            if isinstance(raw, bool) or not isinstance(raw, int | float) or raw < 0:
                raise ValueError(t("storage.settings.bad_price", key=key))
            try:
                return float(raw)
            except OverflowError:  # a JSON integer too large for a float
                raise ValueError(t("storage.settings.bad_price", key=key)) from None

        return cls(
            input=number("input"),
            output=number("output"),
            cache_read=number("cache_read"),
            cache_write=number("cache_write"),
        )


@dataclass(frozen=True, slots=True)
class PriceEntry:
    """One row of the effective price table."""

    model: str
    """The id as written in its table (a ``DEFAULT_PRICES`` key or the owner's key)."""
    price: ModelPrice
    source: PriceSource


def _standard(input_price: float, output_price: float) -> ModelPrice:
    """Anthropic-style pricing: cache reads 0.1x input, 5-minute cache writes 1.25x."""
    return ModelPrice(input_price, output_price, input_price * 0.1, input_price * 1.25)


# Keys are model id prefixes (see normalize_model). Longest prefix wins.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    # Anthropic (claude-api reference, 2026-06)
    "claude-fable-5-1": ModelPrice(10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": _standard(10.0, 50.0),
    "claude-mythos-5-1": ModelPrice(10.0, 50.0, 0.25, 12.5),  # Fable 5.1 pricing
    "claude-mythos-5": _standard(10.0, 50.0),
    "claude-opus-5-5": ModelPrice(4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": _standard(5.0, 25.0),
    "claude-opus-4-8": _standard(5.0, 25.0),
    "claude-opus-4-7": _standard(5.0, 25.0),
    "claude-opus-4-6": _standard(5.0, 25.0),
    "claude-opus-4-5": _standard(5.0, 25.0),
    "claude-opus-4": _standard(15.0, 75.0),  # Opus 4.0 (and the dated id) and 4.1
    "claude-sonnet-5": _standard(2.0, 10.0),
    "claude-sonnet-4": _standard(3.0, 15.0),
    "claude-haiku-4": _standard(1.0, 5.0),
    # OpenAI (developers.openai.com pricing, 2026-09)
    "gpt-6-astra": ModelPrice(10.0, 50.0, 1.0, 12.5),
    "gpt-6-sol": ModelPrice(2.0, 10.0, 0.20, 2.5),
    "gpt-6-luna": ModelPrice(0.10, 0.50, 0.01, 0.125),
}

_DATE_SUFFIX = re.compile(r"(-\d{8}|@\d{8}|-latest)$")
_CONTEXT_SUFFIX = re.compile(r"\[[^\]]*\]$")


def normalize_model(model: str) -> str:
    """Lower-case id without vendor prefixes, context tags or date suffixes."""
    name = model.strip().lower()
    name = name.rsplit("/", 1)[-1]  # e.g. "anthropic/claude-opus-5"
    if name.startswith("anthropic."):
        name = name.removeprefix("anthropic.")
    name = _CONTEXT_SUFFIX.sub("", name)
    return _DATE_SUFFIX.sub("", name)


def price_table(overrides: Mapping[str, ModelPrice] | None = None) -> dict[str, PriceEntry]:
    """The effective prices, keyed by normalized model id.

    Starts from ``DEFAULT_PRICES``; an owner price replaces the default of the same
    normalized id (a later owner key wins over an earlier one with the same id) or
    adds a new model. Keys that normalize to ``""`` (e.g. ``"openai/"``) are ignored:
    as a prefix they would match, and reprice, every model.
    """
    table: dict[str, PriceEntry] = {}
    sources: tuple[tuple[PriceSource, Mapping[str, ModelPrice]], ...] = (
        ("default", DEFAULT_PRICES),
        ("custom", overrides or {}),
    )
    for source, prices in sources:
        for model, price in prices.items():
            key = normalize_model(model)
            if key:
                table[key] = PriceEntry(model=model, price=price, source=source)
    return table


def lookup_price(model: str, table: Mapping[str, PriceEntry]) -> PriceEntry | None:
    """Row of ``table`` (from :func:`price_table`) for ``model``: the exact normalized
    id, otherwise the longest id that prefixes it; None when nothing matches."""
    name = normalize_model(model)
    if not name:
        return None
    exact = table.get(name)
    if exact is not None:
        return exact
    matches = [key for key in table if key and name.startswith(key)]
    return table[max(matches, key=len)] if matches else None


def find_price(model: str, overrides: Mapping[str, ModelPrice] | None = None) -> ModelPrice | None:
    """Price for ``model`` in the effective table (defaults with the owner's prices over
    them, see :func:`price_table`): exact match first, then the longest prefix across
    both, so an owner price on a family never shadows a more specific default."""
    entry = lookup_price(model, price_table(overrides))
    return None if entry is None else entry.price


def estimate_cost_usd(
    model: str, usage: Usage, overrides: Mapping[str, ModelPrice] | None = None
) -> float | None:
    """Estimated cost in USD, or None when the model has no known price."""
    price = find_price(model, overrides)
    if price is None:
        return None
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return total / 1_000_000
