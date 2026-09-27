// Transforms from the API's Stats / ProviderStatus into what the usage
// dashboard renders (KPIs, chart data, subscription limits).

import { AGENT_LABEL } from '../format';
import { AGENTS, type Agent, type ProviderMode, type ProviderStatus, type SavingKind, type Stats, type TurnMode } from '../protocol';
import { axisDayLabel, dayRange, fullDayLabel, rangeEnd, utcDay } from './dates';
import { positive } from './stack';
import type { Datum, SeriesDef } from './types';

export const SAVING_KINDS: readonly SavingKind[] = ['cache', 'compaction', 'early_stop', 'unchanged'];
export const TURN_MODES: readonly TurnMode[] = ['solo', 'duel', 'debate'];

export const SAVING_LABEL: Record<SavingKind, string> = {
  cache: 'Memòria cau',
  compaction: 'Compactació',
  early_stop: 'Parada per consens',
  unchanged: 'Sense canvis',
};

export const MODE_LABEL: Record<TurnMode, string> = { solo: 'Solo', duel: 'Duel', debate: 'Debat' };

/** Fixed categorical order (validated palette in tokens.css). */
export const AGENT_SERIES: SeriesDef[] = AGENTS.map((a) => ({ key: a, label: AGENT_LABEL[a], color: `var(--${a})` }));
export const SAVING_SERIES: SeriesDef[] = SAVING_KINDS.map((k) => ({
  key: k,
  label: SAVING_LABEL[k],
  color: `var(--saving-${k.replace('_', '-')})`,
}));
/** Single, identity-free series (turn counts): neutral ink, never a categorical hue. */
export const COUNT_SERIES: SeriesDef[] = [{ key: 'count', label: 'Torns', color: 'var(--text-muted)' }];

const compactFmt = new Intl.NumberFormat('ca-ES', { notation: 'compact', maximumFractionDigits: 1 });

/** Axis ticks: always compact ('0', '500', '5 k', '10 k', '1,2 M'). */
export function formatCompact(n: number): string {
  return compactFmt.format(n);
}

/** '1 crida' / '3 crides' (the number formatted by the caller). */
export function plural(n: number, one: string, many: string, formatted: string = String(n)): string {
  return `${formatted} ${n === 1 ? one : many}`;
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** Tokens a call really spent: input + output (cache reads are reported apart). */
export function consumed(u: { input_tokens?: number; output_tokens?: number } | null | undefined): number {
  return positive(u?.input_tokens) + positive(u?.output_tokens);
}

// ------------------------------------------------------------------ daily series

function daysOf(stats: Stats, today: string, dates: string[]): string[] {
  return dayRange(stats.days, rangeEnd(dates, today));
}

function emptyDay(day: string, keys: readonly string[]): Datum {
  return {
    key: day,
    label: axisDayLabel(day),
    fullLabel: fullDayLabel(day),
    values: Object.fromEntries(keys.map((k) => [k, 0])),
  };
}

/** Consumed tokens per day and agent, with every day of the range present. */
export function dailyTokens(stats: Stats, today: string = utcDay()): Datum[] {
  const rows = Array.isArray(stats.daily) ? stats.daily : [];
  const days = daysOf(
    stats,
    today,
    rows.map((r) => r.date),
  );
  const byDay = new Map(days.map((d) => [d, emptyDay(d, AGENTS)]));
  for (const r of rows) {
    const d = byDay.get(r.date);
    if (!d || !(AGENTS as readonly string[]).includes(r.agent)) continue;
    d.values[r.agent] = (d.values[r.agent] ?? 0) + consumed(r);
  }
  return days.map((d) => byDay.get(d)!);
}

/** Saved tokens per day and technique, with every day of the range present. */
export function dailySavings(stats: Stats, today: string = utcDay()): Datum[] {
  const rows = Array.isArray(stats.savings_daily) ? stats.savings_daily : [];
  const days = daysOf(
    stats,
    today,
    rows.map((r) => r.date),
  );
  const byDay = new Map(days.map((d) => [d, emptyDay(d, SAVING_KINDS)]));
  for (const r of rows) {
    const d = byDay.get(r.date);
    if (!d || !(SAVING_KINDS as readonly string[]).includes(r.kind)) continue;
    d.values[r.kind] = (d.values[r.kind] ?? 0) + positive(r.tokens);
  }
  return days.map((d) => byDay.get(d)!);
}

/**
 * Cost in euros per day and agent (real API cost or subscription value at API
 * prices, whichever each call was), with every day of the range present.
 */
export function dailyCost(stats: Stats, eurPerUsd: number, today: string = utcDay()): Datum[] {
  const rows = Array.isArray(stats.daily) ? stats.daily : [];
  const rate = Number.isFinite(eurPerUsd) && eurPerUsd > 0 ? eurPerUsd : 0;
  const days = daysOf(
    stats,
    today,
    rows.map((r) => r.date),
  );
  const byDay = new Map(days.map((d) => [d, emptyDay(d, AGENTS)]));
  for (const r of rows) {
    const d = byDay.get(r.date);
    if (!d || !(AGENTS as readonly string[]).includes(r.agent)) continue;
    d.values[r.agent] = (d.values[r.agent] ?? 0) + positive(r.cost_usd) * rate;
  }
  return days.map((d) => byDay.get(d)!);
}

// ------------------------------------------------------------------ categorical series

/** Latency percentiles (rows) per agent (series); null when unknown. */
export function latencyData(stats: Stats): Datum[] {
  const lat = (a: Agent) => stats.latency?.[a];
  const pick = (v: number | null | undefined) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  return [
    { key: 'p50', label: 'p50', fullLabel: 'Mediana (p50)', values: {} },
    { key: 'p95', label: 'p95', fullLabel: 'Percentil 95 (p95)', values: {} },
  ].map((row) => ({
    ...row,
    values: Object.fromEntries(AGENTS.map((a) => [a, pick(row.key === 'p50' ? lat(a)?.p50_ms : lat(a)?.p95_ms)])),
  }));
}

export function turnsData(stats: Stats): Datum[] {
  return TURN_MODES.map((m) => ({
    key: m,
    label: MODE_LABEL[m],
    fullLabel: MODE_LABEL[m],
    values: { count: num(stats.turns?.[m]) },
  }));
}

/** True when a chart would draw nothing (all values empty or zero). */
export function isEmpty(data: Datum[]): boolean {
  return data.every((d) => Object.values(d.values).every((v) => !(typeof v === 'number' && v > 0)));
}

// ------------------------------------------------------------------ KPIs

export interface Kpis {
  consumed: { total: number; byAgent: Record<Agent, number>; calls: number; errors: number; cacheRead: number };
  saved: { total: number; byKind: Record<SavingKind, number>; ratio: number | null };
  turns: { total: number; byMode: Record<TurnMode, number> };
  consensus: { debates: number; reached: number; rate: number | null; avgRounds: number | null };
  latency: Record<Agent, { p50: number | null; p95: number | null; ttft: number | null }>;
}

const finiteOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export function kpis(stats: Stats): Kpis {
  const byAgentUsage = stats.totals?.by_agent;
  const byAgent = Object.fromEntries(AGENTS.map((a) => [a, consumed(byAgentUsage?.[a])])) as Record<Agent, number>;
  const total = AGENTS.reduce((acc, a) => acc + byAgent[a], 0);
  const cacheRead = AGENTS.reduce((acc, a) => acc + positive(byAgentUsage?.[a]?.cache_read_tokens), 0);

  const byKind = Object.fromEntries(SAVING_KINDS.map((k) => [k, positive(stats.savings?.[k])])) as Record<SavingKind, number>;
  const kindsSum = SAVING_KINDS.reduce((acc, k) => acc + byKind[k], 0);
  const savedTotal = positive(stats.savings?.total) || kindsSum;
  const wouldHaveSpent = total + savedTotal;

  const byMode = Object.fromEntries(TURN_MODES.map((m) => [m, num(stats.turns?.[m])])) as Record<TurnMode, number>;
  const debates = num(stats.consensus?.debates);
  const reached = num(stats.consensus?.reached);

  return {
    consumed: { total, byAgent, calls: num(stats.totals?.calls), errors: num(stats.totals?.errors), cacheRead },
    saved: { total: savedTotal, byKind, ratio: wouldHaveSpent > 0 ? savedTotal / wouldHaveSpent : null },
    turns: { total: TURN_MODES.reduce((acc, m) => acc + byMode[m], 0), byMode },
    consensus: {
      debates,
      reached,
      rate: debates > 0 ? Math.min(1, reached / debates) : null,
      avgRounds: finiteOrNull(stats.consensus?.avg_rounds),
    },
    latency: Object.fromEntries(
      AGENTS.map((a) => {
        const l = stats.latency?.[a];
        return [a, { p50: finiteOrNull(l?.p50_ms), p95: finiteOrNull(l?.p95_ms), ttft: finiteOrNull(l?.ttft_p50_ms) }];
      }),
    ) as Kpis['latency'],
  };
}

// ------------------------------------------------------------------ subscription limits

export type LimitTone = 'ok' | 'warning' | 'critical' | 'unknown';

export interface LimitRow {
  key: string;
  window: string;
  /** 0..100, or null when the provider does not report usage. */
  usedPercent: number | null;
  tone: LimitTone;
  statusLabel: string;
  resetsAt: string | null;
}

export interface ProviderCard {
  agent: Agent;
  name: string;
  mode: ProviderMode;
  modeLabel: string;
  model: string;
  available: boolean;
  detail: string;
  limits: LimitRow[];
}

export const PROVIDER_MODE_LABEL: Record<ProviderMode, string> = {
  cli: 'Subscripció (CLI)',
  api: 'Clau d’API',
  fake: 'Simulat',
};

/** '5h' -> 'Finestra de 5 hores', '7d' -> 'Finestra de 7 dies'. */
export function windowLabel(window: string): string {
  const m = /^(\d+)\s*([mhdw])$/i.exec(window.trim());
  if (!m) return window ? `Finestra ${window}` : 'Finestra';
  const n = Number(m[1]);
  const unit = m[2]!.toLowerCase();
  const words: Record<string, [string, string]> = {
    m: ['minut', 'minuts'],
    h: ['hora', 'hores'],
    d: ['dia', 'dies'],
    w: ['setmana', 'setmanes'],
  };
  const [one, many] = words[unit]!;
  return `Finestra de ${n} ${n === 1 ? one : many}`;
}

export function limitTone(status: string, usedPercent: number | null): { tone: LimitTone; label: string } {
  switch (status) {
    case 'allowed':
      return { tone: 'ok', label: 'Dins del límit' };
    case 'warning':
      return { tone: 'warning', label: 'A prop del límit' };
    case 'rejected':
      return { tone: 'critical', label: 'Límit exhaurit' };
    default:
      if (usedPercent != null && usedPercent >= 100) return { tone: 'critical', label: 'Límit exhaurit' };
      return { tone: 'unknown', label: 'Estat desconegut' };
  }
}

export function providerCards(providers: ProviderStatus[] | null | undefined): ProviderCard[] {
  const list = Array.isArray(providers) ? providers : [];
  return AGENTS.map((agent) => list.find((p) => p?.agent === agent))
    .filter((p): p is ProviderStatus => !!p)
    .map((p) => ({
      agent: p.agent,
      name: AGENT_LABEL[p.agent],
      mode: p.mode,
      modeLabel: PROVIDER_MODE_LABEL[p.mode] ?? p.mode,
      model: typeof p.model === 'string' ? p.model : '',
      available: p.available === true,
      detail: typeof p.detail === 'string' ? p.detail : '',
      limits: (Array.isArray(p.limits) ? p.limits : []).map((l, i) => {
        const used = finiteOrNull(l?.used_percent);
        const usedPercent = used == null ? null : Math.max(0, Math.min(100, used));
        const { tone, label } = limitTone(String(l?.status ?? ''), usedPercent);
        return {
          key: `${l?.window ?? 'w'}-${i}`,
          window: windowLabel(String(l?.window ?? '')),
          usedPercent,
          tone,
          statusLabel: label,
          resetsAt: typeof l?.resets_at === 'string' && !Number.isNaN(Date.parse(l.resets_at)) ? l.resets_at : null,
        };
      }),
    }));
}

const resetClock = new Intl.DateTimeFormat('ca-ES', { hour: '2-digit', minute: '2-digit' });
const resetDay = new Intl.DateTimeFormat('ca-ES', { weekday: 'short', day: 'numeric' });
const relative = new Intl.RelativeTimeFormat('ca-ES', { numeric: 'auto' });

/** 'Es restableix d’aquí a 2 hores (19:30)' or '… d’aquí a 3 dies (dl. 6 a les 09:00)'. */
export function resetLabel(iso: string | null, now: Date = new Date()): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const diff = t - now.getTime();
  if (diff <= 0) return 'Es restableix en breu';
  const minutes = Math.round(diff / 60_000);
  const rel =
    minutes < 60
      ? relative.format(Math.max(1, minutes), 'minute')
      : minutes < 60 * 36
        ? relative.format(Math.round(minutes / 60), 'hour')
        : relative.format(Math.round(minutes / 1440), 'day');
  const clock = diff < 20 * 3_600_000 ? resetClock.format(t) : `${resetDay.format(t)} a les ${resetClock.format(t)}`;
  return `Es restableix ${rel} (${clock})`;
}
