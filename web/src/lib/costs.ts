// Costs in euros: per-answer and per-turn amounts, savings value and the
// month-to-date budget / subscription lines of the sidebar.

import { formatDay, formatEur, formatPercent, usdToEur } from './format';
import type { AgentSpend, FxRate, ProviderMode, Savings, Usage } from './protocol';
import type { CostBasis, StreamView, TurnView } from './turns.svelte';

export type Level = 'ok' | 'warn' | 'bad';

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
  apiUsd: number;
  equivalentUsd: number;
}

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
  return { totalUsd: turn.usage?.cost_usd ?? sum, apiUsd, equivalentUsd };
}

/** Tooltip of the turn total: real API cost vs value included in subscriptions. */
export function turnCostTitle(cost: TurnCost, eurPerUsd: number): string {
  const parts = ["Cost del torn a preus d'API"];
  if (cost.apiUsd > 0) parts.push(`cost real d'API: ${formatEur(cost.apiUsd * eurPerUsd)}`);
  if (cost.equivalentUsd > 0) {
    parts.push(`valor inclòs a la subscripció: ${formatEur(cost.equivalentUsd * eurPerUsd)}`);
  }
  return parts.join(' · ');
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
