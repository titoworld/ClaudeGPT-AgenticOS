"""Usage statistics for the dashboard (``Stats`` in docs/PROTOCOL.md).

The window covers ``days`` UTC calendar days ending today (the day of ``now``).
Daily series are dense: one entry per day and agent (or saving kind), zeros
included, oldest first.

Definitions:

- ``totals``: every recorded model call; ``errors`` counts calls with ``ok`` false.
  An agent's ``cost_usd`` is ``None`` when none of its calls reported a cost.
- ``latency``: nearest-rank percentiles over successful calls.
- ``turns``: question messages created in the window, by their ``meta.mode``.
- ``consensus``: completed debates (a debate question with a synthesis message).
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


class DailyUsage(TypedDict):
    date: str
    agent: AgentName
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


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
        raise ValueError(f"«days» ha de ser un enter entre 1 i {MAX_DAYS}.")
    today = as_utc(now).date()
    first = today - timedelta(days=days - 1)
    dates = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    return dates, _day_start(first), _day_start(today + timedelta(days=1))


def _day_start(day: date) -> str:
    return format_ts(datetime.combine(day, time.min))


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


async def compute_stats(tx: Tx, days: int, now: datetime) -> Stats:
    """Aggregate the stats of the ``days``-day window ending on ``now``'s UTC date."""
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
    latency_rows = await tx.fetchall(
        "SELECT agent, latency_ms, ttft_ms FROM usage WHERE ts >= ? AND ts < ? AND ok = 1",
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
    daily_map: dict[tuple[str, AgentName], tuple[int, int, int]] = {}
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
        )
    for totals in by_agent.values():
        if totals["cost_usd"] is not None:
            totals["cost_usd"] = round(totals["cost_usd"], 6)
    daily: list[DailyUsage] = []
    for day in dates:
        for agent in AGENTS:
            tokens_in, tokens_out, cache_read = daily_map.get((day, agent), (0, 0, 0))
            daily.append(
                DailyUsage(
                    date=day,
                    agent=agent,
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                    cache_read_tokens=cache_read,
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
            solo=mode_counts["solo"], duel=mode_counts["duel"], debate=mode_counts["debate"]
        ),
        consensus=ConsensusStats(
            debates=len(debates),
            reached=reached,
            avg_rounds=round(rounds_total / len(debates), 2) if debates else None,
        ),
    )
