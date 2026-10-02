// Euros on the usage dashboard: costs of the selected range (real API cost vs
// subscription value at API prices), the value of the savings, the exchange-rate
// note, and the "Aquest mes" panel (API budgets and subscription value).

import { AGENT_LABEL, formatEur, formatPercent } from '../format';
import { AGENTS, type Agent, type AgentSpend, type FxRate, type MonthSpend, type ProviderStatus, type Stats } from '../protocol';
import { plural, providerCards, type LimitTone, type ProviderCard } from './usage';

/** Share of the monthly API budget from which it warns, and where it is used up. */
export const BUDGET_WARNING = 0.8;
export const BUDGET_CRITICAL = 1;

const LOCALE = 'ca-ES';
const eurWhole = new Intl.NumberFormat(LOCALE, { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
const eurCents = new Intl.NumberFormat(LOCALE, {
  style: 'currency',
  currency: 'EUR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const eurTick = new Intl.NumberFormat(LOCALE, {
  style: 'currency',
  currency: 'EUR',
  minimumFractionDigits: 0,
  maximumFractionDigits: 4,
});
const eurTickCompact = new Intl.NumberFormat(LOCALE, {
  style: 'currency',
  currency: 'EUR',
  notation: 'compact',
  maximumFractionDigits: 1,
});
const rateFmt = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 4 });

const finite = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
/** Costs are never negative; null, garbage and negatives count as 0. */
const amount = (v: unknown): number => Math.max(0, finite(v) ?? 0);

/** Cents from 0,10 € up; tiny amounts keep formatEur's precision ('0,0123 €'). */
function eurText(eur: number): string {
  return Math.abs(eur) >= 0.1 ? eurCents.format(eur) : formatEur(eur);
}

/** Totals, budgets and plan prices: '0 €', '50 €', '12,30 €', '0,71 €', '0,0123 €'. */
export function formatAmount(eur: number | null | undefined): string {
  if (eur == null || !Number.isFinite(eur)) return '—';
  return Number.isInteger(eur) ? eurWhole.format(eur) : eurText(eur);
}

/** Chart values and table cells: always cents ('1,00 €'), so columns line up; '0 €' when empty. */
export function formatCost(eur: number | null | undefined): string {
  if (eur == null || !Number.isFinite(eur)) return '—';
  return eur === 0 ? eurWhole.format(0) : eurText(eur);
}

/** Y-axis ticks in euros: '0 €', '0,025 €', '1,5 €', '1,2 k €'. */
export function formatEurTick(eur: number): string {
  return Math.abs(eur) >= 1000 ? eurTickCompact.format(eur) : eurTick.format(eur);
}

/** Euros per dollar, or null when the rate is missing or invalid. */
export function rateOf(fx: FxRate | null | undefined): number | null {
  const r = finite(fx?.eur_per_usd);
  return r != null && r > 0 ? r : null;
}

/** '1 USD = 0,8612 € · BCE 25/9' or '1 USD = 0,86 € · tipus manual'. */
export function fxNote(fx: FxRate | null | undefined): string | null {
  const rate = rateOf(fx);
  if (!fx || rate == null) return null;
  const head = `1 USD = ${rateFmt.format(rate)} €`;
  if (fx.source !== 'ecb') return `${head} · tipus manual`;
  const day = /^\d{4}-(\d{2})-(\d{2})$/.exec(fx.as_of ?? '');
  return day ? `${head} · BCE ${Number(day[2])}/${Number(day[1])}` : `${head} · BCE`;
}

/** '3 crides sense preu' (calls whose model has no known price). */
export function unpricedText(calls: number): string {
  return plural(calls, 'crida sense preu', 'crides sense preu', calls.toLocaleString(LOCALE));
}

// ------------------------------------------------------------ range KPIs

export interface EurSplit {
  total: number;
  byAgent: Record<Agent, number>;
}

export interface CostKpis {
  fx: FxRate;
  /** Real cost of API-mode calls, in euros. */
  api: EurSplit;
  /** Subscription (and demo) calls valued at API prices, in euros. */
  equivalent: EurSplit;
  /** Value of the saved tokens in euros; null when no turn reported one. */
  saved: number | null;
  unpriced: { total: number; byAgent: Record<Agent, number> };
}

function split(values: (a: Agent) => number): EurSplit {
  const byAgent = Object.fromEntries(AGENTS.map((a) => [a, values(a)])) as Record<Agent, number>;
  return { total: AGENTS.reduce((acc, a) => acc + byAgent[a], 0), byAgent };
}

/** Euro KPIs of the selected range, or null when the server sent no costs. */
export function costKpis(stats: Stats): CostKpis | null {
  const costs = stats.costs;
  const fx = costs?.fx ?? stats.month?.fx;
  const rate = rateOf(fx);
  if (!costs || !fx || rate == null) return null;
  const by = costs.by_agent;
  const saved = finite(stats.savings?.cost_usd);
  const unpriced = split((a) => Math.round(amount(by?.[a]?.unpriced_calls)));
  return {
    fx,
    api: split((a) => amount(by?.[a]?.api_usd) * rate),
    equivalent: split((a) => amount(by?.[a]?.equivalent_usd) * rate),
    saved: saved == null ? null : Math.max(0, saved) * rate,
    unpriced,
  };
}

// ------------------------------------------------------------ month panel

export type StatusIcon = LimitTone | 'good';

/** One money line of an agent card: API budget or subscription value. */
export interface MoneyRow {
  kind: 'budget' | 'plan';
  label: string;
  amountEur: number;
  /** Monthly budget or plan price; null when the owner has not set one. */
  limitEur: number | null;
  /** amount / limit, unclamped (may pass 1); null without a limit. */
  ratio: number | null;
  /** Head of the row: '84 %', or the amount when there is no limit. */
  headText: string;
  /** '42,10 € de 50 €' (empty without a limit: the head already says it). */
  amountText: string;
  /** Meter fill colour. */
  tone: LimitTone;
  /** Status with icon + label (never colour alone); null for a plain caption. */
  status: { icon: StatusIcon; label: string } | null;
  /** Caption when there is no status. */
  caption: string;
  /** Text of the 'open settings' link when a budget / plan price is missing. */
  action: string | null;
  /** Accessible name and value of the meter. */
  meterLabel: string;
  valueText: string;
}

export interface MonthCard {
  agent: Agent;
  name: string;
  /** Live provider status (mode, availability, windows); null if unknown. */
  provider: ProviderCard | null;
  budget: MoneyRow | null;
  plan: MoneyRow | null;
  /** Calls this month whose model has no price (not counted above). */
  unpriced: number;
}

export function budgetStatus(ratio: number): { tone: LimitTone; label: string } {
  if (ratio >= BUDGET_CRITICAL) return { tone: 'critical', label: 'Pressupost exhaurit' };
  if (ratio >= BUDGET_WARNING) return { tone: 'warning', label: 'A prop del pressupost' };
  return { tone: 'ok', label: 'Dins del pressupost' };
}

const positiveLimit = (v: unknown): number | null => {
  const n = finite(v);
  return n != null && n > 0 ? n : null;
};

function budgetRow(name: string, spend: AgentSpend, rate: number): MoneyRow {
  const eur = amount(spend.api_usd) * rate;
  const limit = positiveLimit(spend.budget_eur);
  if (limit == null) {
    return {
      kind: 'budget',
      label: 'Despesa d’API',
      amountEur: eur,
      limitEur: null,
      ratio: null,
      headText: formatAmount(eur),
      amountText: '',
      tone: 'unknown',
      status: null,
      caption: 'Sense pressupost mensual.',
      action: 'Defineix-lo a Configuració',
      meterLabel: `${name}, despesa d’API del mes`,
      valueText: formatAmount(eur),
    };
  }
  const ratio = Math.max(0, finite(spend.budget_used) ?? eur / limit);
  const { tone, label } = budgetStatus(ratio);
  const amountText = `${formatAmount(eur)} de ${formatAmount(limit)}`;
  return {
    kind: 'budget',
    label: 'Pressupost d’API',
    amountEur: eur,
    limitEur: limit,
    ratio,
    headText: formatPercent(ratio),
    amountText,
    tone,
    status: { icon: tone, label },
    caption: '',
    action: null,
    meterLabel: `${name}, pressupost d’API del mes`,
    valueText: `${amountText} (${formatPercent(ratio)}): ${label.toLowerCase()}`,
  };
}

function planRow(name: string, spend: AgentSpend, rate: number): MoneyRow {
  const eur = amount(spend.equivalent_usd) * rate;
  const limit = positiveLimit(spend.plan_eur);
  if (limit == null) {
    return {
      kind: 'plan',
      label: 'Valor de la subscripció',
      amountEur: eur,
      limitEur: null,
      ratio: null,
      headText: formatAmount(eur),
      amountText: '',
      tone: 'unknown',
      status: null,
      caption: 'A preus d’API.',
      action: 'Indica el preu del pla',
      meterLabel: `${name}, valor de la subscripció del mes`,
      valueText: formatAmount(eur),
    };
  }
  const ratio = Math.max(0, finite(spend.plan_value) ?? eur / limit);
  const amountText = `${formatAmount(eur)} de ${formatAmount(limit)}`;
  const paidOff = ratio >= 1;
  return {
    kind: 'plan',
    label: 'Valor de la subscripció',
    amountEur: eur,
    limitEur: limit,
    ratio,
    headText: formatPercent(ratio),
    amountText,
    // Value is never a warning: more is better, so the fill stays neutral.
    tone: 'ok',
    status: paidOff ? { icon: 'good', label: 'Ja surt a compte' } : null,
    caption: paidOff ? '' : 'Equivalent a preus d’API',
    action: null,
    meterLabel: `${name}, valor de la subscripció del mes`,
    valueText: `${amountText} del pla a preus d’API (${formatPercent(ratio)})${paidOff ? ': ja surt a compte' : ''}`,
  };
}

/**
 * One card per agent for the month panel: live subscription windows (from the
 * providers) plus the month's API budget and subscription value. Rows appear
 * for the agent's current mode, or whenever the owner set a budget / plan
 * price or there was spend of that kind this month.
 */
export function monthCards(
  providers: ProviderStatus[] | null | undefined,
  month: MonthSpend | null | undefined,
): MonthCard[] {
  const cards = providerCards(providers);
  const rate = rateOf(month?.fx);
  return AGENTS.flatMap((agent): MonthCard[] => {
    const provider = cards.find((c) => c.agent === agent) ?? null;
    const spend = rate == null ? undefined : month?.by_agent?.[agent];
    if (!provider && !spend) return [];
    const name = AGENT_LABEL[agent];
    const mode = provider?.mode;
    let budget: MoneyRow | null = null;
    let plan: MoneyRow | null = null;
    if (spend && rate != null) {
      if (mode === 'api' || positiveLimit(spend.budget_eur) != null || amount(spend.api_usd) > 0) {
        budget = budgetRow(name, spend, rate);
      }
      if (mode === 'cli' || positiveLimit(spend.plan_eur) != null || (!mode && amount(spend.equivalent_usd) > 0)) {
        plan = planRow(name, spend, rate);
      }
    }
    return [{ agent, name, provider, budget, plan, unpriced: Math.round(amount(spend?.unpriced_calls)) }];
  });
}

const MONTH = new Intl.DateTimeFormat(LOCALE, { month: 'long', year: 'numeric', timeZone: 'UTC' });

/** 'setembre del 2026' for '2026-09' (as-is when unparsable). */
export function monthLabel(month: string | null | undefined): string {
  if (!month || !/^\d{4}-\d{2}$/.test(month)) return month ?? '';
  return MONTH.format(new Date(`${month}-01T00:00:00Z`));
}
