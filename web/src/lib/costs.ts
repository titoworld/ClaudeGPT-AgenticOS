// Costs in euros: per-answer and per-turn amounts, savings value and the
// month-to-date budget / subscription lines of the sidebar; and the tokens a call
// processed, the count every total and ratio uses (ADR 0008).

import { formatDay, formatEur, formatInt, formatPercent, usdToEur } from './format';
import { AGENTS, type AgentSpend, type FxRate, type ProviderMode, type Savings, type Usage } from './protocol';
import type { CostBasis, StreamView, TurnView } from './turns.svelte';

export type Level = 'ok' | 'warn' | 'bad';

/** The token counts of a Usage, any of them possibly missing (a stats row, a stored meta). */
export type TokenCounts = Partial<
  Pick<Usage, 'input_tokens' | 'output_tokens' | 'cache_read_tokens' | 'cache_write_tokens' | 'reasoning_tokens'>
>;

/** A token count: negative, missing and non-numeric values count as 0. */
const tokens = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0);

/**
 * Tokens the calls processed (ADR 0008): input, cache reads, cache writes and output.
 * The reasoning is a part of the output, so it is never added again. Turn totals, the
 * dashboard and its savings share all count this, whatever each kind costs.
 */
export function processedTokens(usage: TokenCounts | null | undefined): number {
  if (!usage) return 0;
  return (
    tokens(usage.input_tokens) +
    tokens(usage.cache_read_tokens) +
    tokens(usage.cache_write_tokens) +
    tokens(usage.output_tokens)
  );
}

/**
 * Each kind of processed token, e.g. "3 d'entrada · 10.000 llegits de la memòria cau ·
 * 20.000 escrits a la memòria cau · 100 de sortida (50 de raonament)"; the cache parts only
 * when there are any.
 */
export function tokenBreakdown(usage: TokenCounts): string {
  const parts = [`${formatInt(tokens(usage.input_tokens))} d'entrada`];
  const read = tokens(usage.cache_read_tokens);
  const written = tokens(usage.cache_write_tokens);
  const reasoning = tokens(usage.reasoning_tokens);
  if (read) parts.push(`${formatInt(read)} llegits de la memòria cau`);
  if (written) parts.push(`${formatInt(written)} escrits a la memòria cau`);
  const thinking = reasoning ? ` (${formatInt(reasoning)} de raonament)` : '';
  parts.push(`${formatInt(tokens(usage.output_tokens))} de sortida${thinking}`);
  return parts.join(' · ');
}

/** Budget share from which the bar turns amber (and red at 100 %). */
export const BUDGET_WARN_RATIO = 0.8;

export function budgetLevel(ratio: number | null | undefined): Level {
  if (ratio == null || !Number.isFinite(ratio)) return 'ok';
  if (ratio >= 1) return 'bad';
  if (ratio >= BUDGET_WARN_RATIO) return 'warn';
  return 'ok';
}

const eurWhole = new Intl.NumberFormat('ca-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
const eurCents = new Intl.NumberFormat('ca-ES', {
  style: 'currency',
  currency: 'EUR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Totals and limits: "50 €", "12,30 €"; tiny amounts keep formatEur's precision. */
export function formatMoney(eur: number): string {
  if (Number.isInteger(eur)) return eurWhole.format(eur);
  return Math.abs(eur) >= 1 ? eurCents.format(eur) : formatEur(eur);
}

/** "≈ 0,0123 €" for a USD cost, or null when the cost is unknown. */
export function approxEur(usd: number | null | undefined, eurPerUsd: number): string | null {
  const eur = usdToEur(usd, eurPerUsd);
  return eur == null || !Number.isFinite(eur) ? null : `≈ ${formatEur(eur)}`;
}

export const COST_BASIS_TITLE: Record<CostBasis, string> = {
  api: "Cost real de l'API",
  equivalent: "Valor equivalent a preus d'API — inclòs a la subscripció",
};

/**
 * Cost basis of a live answer (stream.completed does not carry it), with the
 * server's rule: API mode is a real cost, subscription (or a priced demo) a value.
 */
export function inferCostBasis(mode: ProviderMode | undefined, usage: Usage): CostBasis | null {
  if (mode === 'api') return 'api';
  if (mode === 'cli' || (mode && usage.cost_usd != null)) return 'equivalent';
  return null;
}

/** Tooltip for one answer's cost. */
export function costTitle(basis: CostBasis | null, usd: number): string {
  const what = basis ? COST_BASIS_TITLE[basis] : "Cost estimat a preus d'API";
  return `${what} (${usd.toLocaleString('ca-ES', { maximumFractionDigits: 6 })} $)`;
}

/** Visible cost text of a finished answer, or null to hide it. */
export function streamCost(stream: StreamView, eurPerUsd: number): { text: string; title: string } | null {
  const usd = stream.usage?.cost_usd;
  if (usd == null || (stream.cached && usd === 0)) return null;
  const text = approxEur(usd, eurPerUsd);
  return text ? { text, title: costTitle(stream.costBasis, usd) } : null;
}

export interface TurnCost {
  /** Server total when known (includes internal calls), else the sum of the answers. */
  totalUsd: number | null;
  /** Real API cost. */
  apiUsd: number;
  /** Subscription calls valued at API prices. */
  equivalentUsd: number;
  /**
   * The rest of the total when the turn does not tell its kind: calls no answer shows (a
   * history summary, a failed call, attempts declined before a fallback) that may have been
   * an API agent's or a subscription agent's. The three parts add up to the total.
   */
  otherUsd: number;
}

/** Less than this between a total and its parts is rounding, never a call (USD). */
const ROUNDING_USD = 1e-9;

/**
 * The cost basis of every call of a turn, when its answers tell it: every agent that could
 * have made a call answered with that same basis (an agent's mode does not change while the
 * server runs). Those agents are both in a duel or a debate, and the one that answered in a
 * solo turn, unless it compacted the history: either agent may have written the summary
 * (Claude first, even in a turn for ChatGPT). Null when the turn does not tell.
 */
function turnBasis(turn: TurnView): CostBasis | null {
  const callers = turn.mode === 'solo' && !turn.compacted ? new Set(turn.streams.map((s) => s.agent)) : AGENTS;
  const bases = new Set<CostBasis>();
  for (const agent of callers) {
    const known = turn.streams.filter((s) => s.agent === agent && s.costBasis);
    if (!known.length) return null;
    for (const s of known) bases.add(s.costBasis!);
  }
  return bases.size === 1 ? [...bases][0]! : null;
}

/**
 * A turn's total and its split: the answers' costs by basis, and what the total has that no
 * answer shows (a history summary, failed calls, attempts declined before a fallback, a
 * billed call that was retried) under the turn's basis when it has one, else apart.
 */
export function turnCost(turn: TurnView): TurnCost {
  let apiUsd = 0;
  let equivalentUsd = 0;
  let sum: number | null = null;
  for (const s of turn.streams) {
    const usd = s.usage?.cost_usd;
    if (usd == null) continue;
    sum = (sum ?? 0) + usd;
    if (s.costBasis === 'api') apiUsd += usd;
    else if (s.costBasis === 'equivalent') equivalentUsd += usd;
  }
  const totalUsd = turn.usage?.cost_usd ?? sum;
  const rest = totalUsd == null ? 0 : totalUsd - apiUsd - equivalentUsd;
  if (!(rest > ROUNDING_USD)) return { totalUsd, apiUsd, equivalentUsd, otherUsd: 0 };
  const basis = turnBasis(turn);
  if (basis === 'api') return { totalUsd, apiUsd: apiUsd + rest, equivalentUsd, otherUsd: 0 };
  if (basis === 'equivalent') return { totalUsd, apiUsd, equivalentUsd: equivalentUsd + rest, otherUsd: 0 };
  return { totalUsd, apiUsd, equivalentUsd, otherUsd: rest };
}

const TURN_COST_TITLE = "Cost del torn a preus d'API";

/**
 * Tooltip of the turn total: real API cost vs value included in subscriptions, and the
 * calls whose kind the turn does not tell. Without either kind it only names the total.
 */
export function turnCostTitle(cost: TurnCost, eurPerUsd: number): string {
  if (!(cost.apiUsd > 0) && !(cost.equivalentUsd > 0)) return TURN_COST_TITLE;
  const part = (label: string, usd: number) => (usd > 0 ? [`${label}: ${formatEur(usd * eurPerUsd)}`] : []);
  return [
    TURN_COST_TITLE,
    ...part("cost real d'API", cost.apiUsd),
    ...part('valor inclòs a la subscripció', cost.equivalentUsd),
    ...part('altres crides', cost.otherUsd),
  ].join(' · ');
}

/** Euros worth of the tokens a turn saved, or null when unknown / nothing saved. */
export function savingsEur(savings: Savings | null | undefined, eurPerUsd: number): number | null {
  if (!savings || !savings.cost_usd || savings.cost_usd <= 0) return null;
  return usdToEur(savings.cost_usd, eurPerUsd);
}

// ------------------------------------------------------------ month spend

export interface SpendLine {
  /** Caption prefix: "Gastat" (API) or "Valor aprofitat" (subscription). */
  label: string;
  /** e.g. "12,30 € de 50 €" or "34,20 € · pla 100 €". */
  amount: string;
  /** e.g. "25 %", null when there is no budget / plan to compare with. */
  percent: string | null;
  /** Bar fill ratio (0..1+), null for a plain amount without bar. */
  ratio: number | null;
  /** Colour of the bar: budgets warn and turn critical, subscription value never does. */
  level: Level;
  kind: 'budget' | 'plan';
  /** Full sentence for tooltips and screen readers. */
  title: string;
}

const MONTHS = new Intl.DateTimeFormat('ca-ES', { month: 'long', year: 'numeric', timeZone: 'UTC' });

/** "setembre del 2026" for "2026-09". */
export function monthName(month: string): string {
  const d = new Date(`${month}-01T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? month : MONTHS.format(d);
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function unpricedNote(spend: AgentSpend): string {
  const n = spend.unpriced_calls;
  if (!n) return '';
  return n === 1
    ? ' 1 crida amb un model sense preu conegut no hi compta.'
    : ` ${n} crides amb models sense preu conegut no hi compten.`;
}

/**
 * Month-to-date line of a provider badge: spend against the monthly budget in
 * API mode, value obtained against the plan price in subscription mode.
 */
export function spendLine(mode: ProviderMode, spend: AgentSpend, fx: FxRate, month: string): SpendLine | null {
  const rate = fx.eur_per_usd;
  const when = capitalize(monthName(month));
  if (mode === 'api') {
    const eur = spend.api_usd * rate;
    const budget = spend.budget_eur;
    if (budget != null && budget > 0) {
      const ratio = spend.budget_used ?? eur / budget;
      const percent = formatPercent(ratio);
      return {
        label: 'Gastat',
        amount: `${formatMoney(eur)} de ${formatMoney(budget)}`,
        percent,
        ratio,
        level: budgetLevel(ratio),
        kind: 'budget',
        title: `${when}: despesa d'API de ${formatMoney(eur)} sobre un pressupost de ${formatMoney(budget)} (${percent}).${unpricedNote(spend)}`,
      };
    }
    return {
      label: 'Gastat',
      amount: formatMoney(eur),
      percent: null,
      ratio: null,
      level: 'ok',
      kind: 'budget',
      title: `${when}: despesa d'API de ${formatMoney(eur)}, sense pressupost mensual definit.${unpricedNote(spend)}`,
    };
  }
  if (mode === 'cli') {
    const eur = spend.equivalent_usd * rate;
    const plan = spend.plan_eur;
    if (plan != null && plan > 0) {
      const ratio = spend.plan_value ?? eur / plan;
      const percent = formatPercent(ratio);
      return {
        label: 'Valor aprofitat',
        amount: `${formatMoney(eur)} · pla ${formatMoney(plan)}`,
        percent,
        ratio,
        level: 'ok',
        kind: 'plan',
        title: `${when}: valor aprofitat de ${formatMoney(eur)} a preus d'API, el ${percent} dels ${formatMoney(plan)} que costa el pla.${unpricedNote(spend)}`,
      };
    }
    return {
      label: 'Valor aprofitat',
      amount: formatMoney(eur),
      percent: null,
      ratio: null,
      level: 'ok',
      kind: 'plan',
      title: `${when}: valor aprofitat de ${formatMoney(eur)} a preus d'API, inclòs a la subscripció.${unpricedNote(spend)}`,
    };
  }
  return null;
}

/** "1 $ = 0,8612 € · BCE, 26 de set." (the ECB publishes on working days). */
export function fxText(fx: FxRate): string {
  const rate = fx.eur_per_usd.toLocaleString('ca-ES', { maximumFractionDigits: 4 });
  const source = fx.source === 'ecb' ? 'BCE' : 'manual';
  const when = fx.as_of && !Number.isNaN(Date.parse(fx.as_of)) ? `, ${formatDay(fx.as_of)}` : '';
  return `1 $ = ${rate} € · ${source}${when}`;
}
