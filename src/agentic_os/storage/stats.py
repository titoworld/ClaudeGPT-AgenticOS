"""Usage statistics for the dashboard (``Stats`` in docs/PROTOCOL.md).

The window covers ``days`` UTC calendar days ending today (the day of ``now``).
Daily series are dense: one entry per day and agent (or saving kind), zeros
included, oldest first.

Definitions:

- ``totals``: every recorded model call; ``errors`` counts calls with ``ok`` false
  (a failed call, and each attempt another model declined before a fallback served).
  An agent's ``cost_usd`` is ``None`` when none of its calls reported a cost.
- ``daily``: every kind of processed token (input, cache reads, cache writes and
  output; reasoning is part of the output), so the dashboard can add them up as
  ``Usage.processed_tokens`` does (docs/adr/0008-token-accounting.md).
- ``latency``: nearest-rank percentiles over successful calls.
- ``costs``: per agent, ``api_usd`` sums the cost of api-mode calls (real spend) and
  ``equivalent_usd`` the cost of the other calls (subscription usage valued at API
  prices); ``unpriced_calls`` counts successful non-demo calls without a price.
- ``month``: the same costs for the current UTC calendar month (whatever ``days``),
  converted to euros and compared with the owner's monthly budgets and plan prices.
- ``savings.cost_usd``: value of the saved tokens, the sum of the ``cost_usd`` of the
  saving rows in the window (kept, like their tokens, when a conversation is
  deleted). Turns stored before savings had a value fall back to
  ``meta.savings.cost_usd`` of their last final message (while it exists). ``None``
  when nothing in the window has a value.
- ``turns``: question messages created in the window, by their ``meta.mode`` (solo,
  duel, debate and refine).
- ``consensus``: completed debates (a debate question with a synthesis message). A
  refine turn also ends with a synthesis (its last version), but it is no debate.
  When the synthesis has ``meta.consensus`` (``{"reached", "round", ...}``, as the
  engine writes it) that is used. Otherwise it is derived: the debate reached
  consensus when, in its last revision round, both agents reported
  ``agreement`` >= the question's ``meta.options.debate.consensus_threshold``
  (default 85), and its round is that last revision round (0 without revisions).
  ``avg_rounds`` is the mean round over the counted debates.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Final, TypedDict

from agentic_os.domain import AGENTS, AgentName, DebateOptions, SavingKind
from agentic_os.fx import FxRate
from agentic_os.i18n import t
from agentic_os.storage.db import Tx
from agentic_os.storage.models import TURN_MODES, as_utc, format_ts

SAVING_KINDS: Final[tuple[SavingKind, ...]] = ("cache", "compaction", "early_stop", "unchanged")
MAX_DAYS: Final = 365


class AgentUsage(TypedDict):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    cost_usd: float | None
    calls: int


class Totals(TypedDict):
    calls: int
    errors: int
    cost_usd: float
    by_agent: dict[AgentName, AgentUsage]


class SavingsTotals(TypedDict):
    cache: int
    compaction: int
    early_stop: int
    unchanged: int
    total: int
    cost_usd: float | None


class DailyUsage(TypedDict):
    date: str
    agent: AgentName
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


class FxWire(TypedDict):
    eur_per_usd: float
    as_of: str | None
    source: str


class AgentCosts(TypedDict):
    api_usd: float
    equivalent_usd: float
    unpriced_calls: int


class Costs(TypedDict):
    fx: FxWire
    by_agent: dict[AgentName, AgentCosts]


class AgentSpend(TypedDict):
    api_usd: float
    equivalent_usd: float
    unpriced_calls: int
    budget_eur: float | None
    budget_used: float | None
    plan_eur: float | None
    plan_value: float | None


class MonthSpend(TypedDict):
    """``MonthSpend`` of PROTOCOL.md."""

    month: str
    fx: FxWire
    by_agent: dict[AgentName, AgentSpend]


class DailySaving(TypedDict):
    date: str
    kind: SavingKind
    tokens: int


class Latency(TypedDict):
    p50_ms: int | None
    p95_ms: int | None
    ttft_p50_ms: int | None


class TurnCounts(TypedDict):
    solo: int
    duel: int
    debate: int
    refine: int


class ConsensusStats(TypedDict):
    debates: int
    reached: int
    avg_rounds: float | None


class Stats(TypedDict):
    """JSON-ready ``Stats`` object of PROTOCOL.md."""

    days: int
    totals: Totals
    savings: SavingsTotals
    daily: list[DailyUsage]
    savings_daily: list[DailySaving]
    latency: dict[AgentName, Latency]
    turns: TurnCounts
    consensus: ConsensusStats
    costs: Costs
    month: MonthSpend


def fx_wire(rate: FxRate) -> FxWire:
    return FxWire(
        eur_per_usd=rate.eur_per_usd,
        as_of=rate.as_of.isoformat() if rate.as_of else None,
        source=rate.source,
    )


def percentile(values: Sequence[int], pct: float) -> int | None:
    """Nearest-rank percentile of ``values`` (``None`` when empty)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def window(days: int, now: datetime) -> tuple[list[str], str, str]:
    """Dates (``YYYY-MM-DD``) of the window plus its ``[start, end)`` timestamps."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(t("storage.stats_days", max=MAX_DAYS))
    today = as_utc(now).date()
    first = today - timedelta(days=days - 1)
    dates = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    return dates, _day_start(first), _day_start(today + timedelta(days=1))


def _day_start(day: date) -> str:
    return format_ts(datetime.combine(day, time.min))


def month_bounds(now: datetime) -> tuple[str, str, str]:
    """``YYYY-MM`` of ``now``'s UTC month plus its ``[start, end)`` timestamps."""
    today = as_utc(now).date()
    first = today.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return first.strftime("%Y-%m"), _day_start(first), _day_start(following)


def _agent(value: object) -> AgentName | None:
    for agent in AGENTS:
        if value == agent:
            return agent
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _derive_consensus(
    by_round: Mapping[int, Mapping[AgentName, float | None]], threshold: float
) -> tuple[bool, int]:
    """(reached, last round) of a debate from its revisions' agreements."""
    if not by_round:
        return False, 0
    last_round = max(by_round)
    scores = by_round[last_round]
    reached = all(
        (score := scores.get(agent)) is not None and score >= threshold for agent in AGENTS
    )
    return reached, last_round


async def agent_costs(tx: Tx, start: str, end: str) -> dict[AgentName, AgentCosts]:
    """Real (api mode) and equivalent (subscription, demo) costs of ``[start, end)``."""
    rows = await tx.fetchall(
        """
        SELECT agent,
               SUM(CASE WHEN provider_mode = 'api' THEN cost_usd END) AS api_usd,
               SUM(CASE WHEN provider_mode != 'api' THEN cost_usd END) AS equivalent_usd,
               SUM(ok = 1 AND cost_usd IS NULL AND provider_mode != 'fake') AS unpriced_calls
        FROM usage WHERE ts >= ? AND ts < ?
        GROUP BY agent
        """,
        (start, end),
    )
    costs = {
        agent: AgentCosts(api_usd=0.0, equivalent_usd=0.0, unpriced_calls=0) for agent in AGENTS
    }
    for row in rows:
        agent = _agent(row["agent"])
        if agent is not None:
            costs[agent] = AgentCosts(
                api_usd=round(float(row["api_usd"] or 0.0), 6),
                equivalent_usd=round(float(row["equivalent_usd"] or 0.0), 6),
                unpriced_calls=int(row["unpriced_calls"] or 0),
            )
    return costs


def _ratio(amount_eur: float, limit_eur: float | None) -> float | None:
    if not limit_eur:
        return None
    return round(amount_eur / limit_eur, 6)


async def compute_month_spend(
    tx: Tx,
    now: datetime,
    fx: FxRate,
    budgets_eur: Mapping[AgentName, float | None],
    plans_eur: Mapping[AgentName, float | None],
) -> MonthSpend:
    """Spend of ``now``'s UTC calendar month against the owner's monthly budget (api
    mode) and plan price (subscription), both in euros at ``fx``."""
    month, start, end = month_bounds(now)
    by_agent: dict[AgentName, AgentSpend] = {}
    for agent, costs in (await agent_costs(tx, start, end)).items():
        budget, plan = budgets_eur.get(agent), plans_eur.get(agent)
        by_agent[agent] = AgentSpend(
            api_usd=costs["api_usd"],
            equivalent_usd=costs["equivalent_usd"],
            unpriced_calls=costs["unpriced_calls"],
            budget_eur=budget,
            budget_used=_ratio(costs["api_usd"] * fx.eur_per_usd, budget),
            plan_eur=plan,
            plan_value=_ratio(costs["equivalent_usd"] * fx.eur_per_usd, plan),
        )
    return MonthSpend(month=month, fx=fx_wire(fx), by_agent=by_agent)


async def _savings_cost(tx: Tx, start: str, end: str) -> float | None:
    """Value of the savings of ``[start, end)``: the ``cost_usd`` of the saving rows,
    plus, for turns none of whose saving rows has a value (stored before the column
    existed), ``meta.savings.cost_usd`` of the turn's last final message."""
    recorded = await tx.fetchone(
        "SELECT SUM(cost_usd) AS total, COUNT(cost_usd) AS priced FROM savings "
        "WHERE ts >= ? AND ts < ?",
        (start, end),
    )
    legacy_rows = await tx.fetchall(
        """
        SELECT m.turn_id, json_extract(m.meta, '$.savings.cost_usd') AS cost_usd
        FROM messages AS m
        WHERE m.final = 1 AND m.kind != 'question' AND m.created_at >= ? AND m.created_at < ?
          AND json_type(m.meta, '$.savings') = 'object'
          AND NOT EXISTS (
              SELECT 1 FROM savings AS s WHERE s.turn_id = m.turn_id AND s.cost_usd IS NOT NULL
          )
        ORDER BY m.id
        """,
        (start, end),
    )
    by_turn = {int(row["turn_id"]): _number(row["cost_usd"]) for row in legacy_rows}
    values = [value for value in by_turn.values() if value is not None]
    if recorded is not None and int(recorded["priced"] or 0) > 0:
        values.append(float(recorded["total"]))
    return round(sum(values), 6) if values else None


async def compute_stats(
    tx: Tx,
    days: int,
    now: datetime,
    *,
    fx: FxRate,
    budgets_eur: Mapping[AgentName, float | None],
    plans_eur: Mapping[AgentName, float | None],
) -> Stats:
    """Aggregate the stats of the ``days``-day window ending on ``now``'s UTC date;
    costs are converted to euros at ``fx`` (budgets and plan prices are monthly)."""
    dates, start, end = window(days, now)
    usage_rows = await tx.fetchall(
        """
        SELECT substr(ts, 1, 10) AS day, agent, COUNT(*) AS calls, SUM(1 - ok) AS errors,
               SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens,
               SUM(cache_write_tokens) AS cache_write_tokens,
               SUM(reasoning_tokens) AS reasoning_tokens, SUM(cost_usd) AS cost_usd
        FROM usage WHERE ts >= ? AND ts < ?
        GROUP BY day, agent
        """,
        (start, end),
    )
    # An agent's response time is that of the calls that write a message: a history
    # summary (short) and Claude's check of a PDF for ChatGPT (a long transcription) would
    # skew it, so they count only in the tokens and costs.
    latency_rows = await tx.fetchall(
        """
        SELECT agent, latency_ms, ttft_ms FROM usage
        WHERE ts >= ? AND ts < ? AND ok = 1 AND purpose IN ('answer', 'revision', 'synthesis')
        """,
        (start, end),
    )
    saving_rows = await tx.fetchall(
        """
        SELECT substr(ts, 1, 10) AS day, kind, SUM(tokens) AS tokens
        FROM savings WHERE ts >= ? AND ts < ?
        GROUP BY day, kind
        """,
        (start, end),
    )
    question_rows = await tx.fetchall(
        """
        SELECT id, json_extract(meta, '$.mode') AS mode,
               json_extract(meta, '$.options.debate.consensus_threshold') AS threshold
        FROM messages
        WHERE kind = 'question' AND created_at >= ? AND created_at < ?
        """,
        (start, end),
    )
    debate_rows = await tx.fetchall(
        """
        SELECT m.turn_id, m.kind, m.agent, m.round,
               json_extract(m.meta, '$.agreement') AS agreement,
               json_extract(m.meta, '$.consensus.reached') AS reached,
               json_extract(m.meta, '$.consensus.round') AS consensus_round
        FROM messages AS q JOIN messages AS m ON m.turn_id = q.id
        WHERE q.kind = 'question' AND q.created_at >= ? AND q.created_at < ?
          AND json_extract(q.meta, '$.mode') = 'debate'
          AND m.kind IN ('revision', 'synthesis')
        ORDER BY m.id
        """,
        (start, end),
    )

    # --- totals and daily usage ------------------------------------------
    by_agent: dict[AgentName, AgentUsage] = {
        agent: AgentUsage(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            reasoning_tokens=0,
            cost_usd=None,
            calls=0,
        )
        for agent in AGENTS
    }
    daily_map: dict[tuple[str, AgentName], tuple[int, int, int, int, float]] = {}
    errors = 0
    for row in usage_rows:
        agent = _agent(row["agent"])
        if agent is None:
            continue
        totals = by_agent[agent]
        totals["calls"] += int(row["calls"])
        totals["input_tokens"] += int(row["input_tokens"])
        totals["output_tokens"] += int(row["output_tokens"])
        totals["cache_read_tokens"] += int(row["cache_read_tokens"])
        totals["cache_write_tokens"] += int(row["cache_write_tokens"])
        totals["reasoning_tokens"] += int(row["reasoning_tokens"])
        if row["cost_usd"] is not None:
            totals["cost_usd"] = (totals["cost_usd"] or 0.0) + float(row["cost_usd"])
        errors += int(row["errors"])
        daily_map[(str(row["day"]), agent)] = (
            int(row["input_tokens"]),
            int(row["output_tokens"]),
            int(row["cache_read_tokens"]),
            int(row["cache_write_tokens"]),
            round(float(row["cost_usd"] or 0.0), 6),
        )
    for totals in by_agent.values():
        if totals["cost_usd"] is not None:
            totals["cost_usd"] = round(totals["cost_usd"], 6)
    daily: list[DailyUsage] = []
    for day in dates:
        for agent in AGENTS:
            tokens_in, tokens_out, cache_read, cache_write, cost = daily_map.get(
                (day, agent), (0, 0, 0, 0, 0.0)
            )
            daily.append(
                DailyUsage(
                    date=day,
                    agent=agent,
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write,
                    cost_usd=cost,
                )
            )

    # --- latency ------------------------------------------------------------
    latencies: dict[AgentName, list[int]] = defaultdict(list)
    ttfts: dict[AgentName, list[int]] = defaultdict(list)
    for row in latency_rows:
        agent = _agent(row["agent"])
        if agent is None:
            continue
        latencies[agent].append(int(row["latency_ms"]))
        if row["ttft_ms"] is not None:
            ttfts[agent].append(int(row["ttft_ms"]))
    latency: dict[AgentName, Latency] = {
        agent: Latency(
            p50_ms=percentile(latencies[agent], 50),
            p95_ms=percentile(latencies[agent], 95),
            ttft_p50_ms=percentile(ttfts[agent], 50),
        )
        for agent in AGENTS
    }

    # --- savings ------------------------------------------------------------
    saving_map: dict[tuple[str, str], int] = {
        (str(row["day"]), str(row["kind"])): int(row["tokens"]) for row in saving_rows
    }
    kind_totals = {
        kind: sum(saving_map.get((day, kind), 0) for day in dates) for kind in SAVING_KINDS
    }
    savings = SavingsTotals(
        cache=kind_totals["cache"],
        compaction=kind_totals["compaction"],
        early_stop=kind_totals["early_stop"],
        unchanged=kind_totals["unchanged"],
        total=sum(kind_totals.values()),
        cost_usd=await _savings_cost(tx, start, end),
    )
    savings_daily = [
        DailySaving(date=day, kind=kind, tokens=saving_map.get((day, kind), 0))
        for day in dates
        for kind in SAVING_KINDS
    ]

    # --- turns and consensus -------------------------------------------------
    mode_counts = dict.fromkeys(TURN_MODES, 0)
    thresholds: dict[int, float] = {}
    for row in question_rows:
        mode = row["mode"]
        if mode in mode_counts:
            mode_counts[mode] += 1
        if mode == "debate":
            threshold = _number(row["threshold"])
            thresholds[int(row["id"])] = (
                threshold if threshold is not None else DebateOptions().consensus_threshold
            )
    # turn -> (reached, round) from the synthesis meta; None when it has no consensus.
    synthesized: dict[int, tuple[bool, int] | None] = {}
    revisions: dict[int, dict[int, dict[AgentName, float | None]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in debate_rows:
        turn_id = int(row["turn_id"])
        if row["kind"] == "synthesis":
            stored_round = row["consensus_round"]
            synthesized[turn_id] = (
                (bool(row["reached"]), int(stored_round))
                if row["reached"] is not None and isinstance(stored_round, int)
                else None
            )
            continue
        agent = _agent(row["agent"])
        if agent is not None:
            revisions[turn_id][int(row["round"])][agent] = _number(row["agreement"])
    reached = 0
    rounds_total = 0
    debates = [turn_id for turn_id in thresholds if turn_id in synthesized]
    for turn_id in debates:
        outcome = synthesized[turn_id]
        if outcome is None:
            outcome = _derive_consensus(revisions.get(turn_id, {}), thresholds[turn_id])
        reached += outcome[0]
        rounds_total += outcome[1]

    return Stats(
        days=days,
        totals=Totals(
            calls=sum(t["calls"] for t in by_agent.values()),
            errors=errors,
            cost_usd=round(sum(t["cost_usd"] or 0.0 for t in by_agent.values()), 6),
            by_agent=by_agent,
        ),
        savings=savings,
        daily=daily,
        savings_daily=savings_daily,
        latency=latency,
        turns=TurnCounts(
            solo=mode_counts["solo"],
            duel=mode_counts["duel"],
            debate=mode_counts["debate"],
            refine=mode_counts["refine"],
        ),
        consensus=ConsensusStats(
            debates=len(debates),
            reached=reached,
            avg_rounds=round(rounds_total / len(debates), 2) if debates else None,
        ),
        costs=Costs(fx=fx_wire(fx), by_agent=await agent_costs(tx, start, end)),
        month=await compute_month_spend(tx, now, fx, budgets_eur, plans_eur),
    )
