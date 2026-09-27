"""Model prices and cost estimation.

Prices are in USD per million tokens, as the vendors publish them. The owner can
override or add models from the dashboard (new models work as soon as a price is
entered). In cli (subscription) mode the same prices give the *equivalent* API
cost, which is what the dashboard compares with the plan price.

Usage semantics (domain.Usage): ``input_tokens`` is the uncached remainder,
cache reads/writes are separate, ``output_tokens`` already includes reasoning.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from agentic_os.domain import Usage


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
                raise ValueError(f"Preu invàlid per a «{key}»: ha de ser un nombre ≥ 0.")
            return float(raw)

        return cls(
            input=number("input"),
            output=number("output"),
            cache_read=number("cache_read"),
            cache_write=number("cache_write"),
        )


def _standard(input_price: float, output_price: float) -> ModelPrice:
    """Anthropic-style pricing: cache reads 0.1x input, 5-minute cache writes 1.25x."""
    return ModelPrice(input_price, output_price, input_price * 0.1, input_price * 1.25)


# Keys are model id prefixes (see normalize_model). Longest prefix wins.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    # Anthropic (claude-api reference, 2026-06)
    "claude-fable-5-1": ModelPrice(10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": _standard(10.0, 50.0),
    "claude-mythos-5": _standard(10.0, 50.0),
    "claude-opus-5-5": ModelPrice(4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": _standard(5.0, 25.0),
    "claude-opus-4": _standard(5.0, 25.0),
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


def find_price(model: str, overrides: Mapping[str, ModelPrice] | None = None) -> ModelPrice | None:
    """Price for ``model``: exact or longest-prefix match, owner overrides first."""
    name = normalize_model(model)
    if not name:
        return None
    for table in (overrides or {}, DEFAULT_PRICES):
        normalized = {normalize_model(key): price for key, price in table.items()}
        if name in normalized:
            return normalized[name]
        matches = [key for key in normalized if name.startswith(key)]
        if matches:
            return normalized[max(matches, key=len)]
    return None


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
