// Formatting helpers shared by the whole UI (Catalan locale).

const LOCALE = 'ca-ES';

const compact = new Intl.NumberFormat(LOCALE, { notation: 'compact', maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const usd = new Intl.NumberFormat(LOCALE, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 });
const percent = new Intl.NumberFormat(LOCALE, { style: 'percent', maximumFractionDigits: 0 });
const time = new Intl.DateTimeFormat(LOCALE, { hour: '2-digit', minute: '2-digit' });
const dateShort = new Intl.DateTimeFormat(LOCALE, { day: 'numeric', month: 'short', timeZone: 'UTC' });
const seconds1 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 });
const eur = (digits: number) =>
  new Intl.NumberFormat(LOCALE, { style: 'currency', currency: 'EUR', maximumFractionDigits: digits, minimumFractionDigits: Math.min(2, digits) });
const eurCache = new Map<number, Intl.NumberFormat>();
const relative = new Intl.RelativeTimeFormat(LOCALE, { numeric: 'auto' });

export const formatTokens = (n: number): string => (n >= 10_000 ? compact.format(n) : integer.format(n));
export const formatInt = (n: number): string => integer.format(n);
export const formatUsd = (n: number | null | undefined): string => (n == null ? '—' : usd.format(n));

/** Euros with precision adapted to the amount (tiny per-call costs keep 4 decimals). */
export function formatEur(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const digits = Math.abs(n) >= 10 ? 2 : Math.abs(n) >= 0.1 ? 3 : 4;
  let formatter = eurCache.get(digits);
  if (!formatter) {
    formatter = eur(digits);
    eurCache.set(digits, formatter);
  }
  return formatter.format(n);
}

/** USD amount converted with ``eurPerUsd`` and formatted in euros. */
export const usdToEur = (usd: number | null | undefined, eurPerUsd: number): number | null =>
  usd == null ? null : usd * eurPerUsd;
export const formatPercent = (ratio: number): string => percent.format(ratio);
export const formatTime = (iso: string): string => time.format(new Date(iso));
/** Date-only strings (YYYY-MM-DD) are UTC calendar days. */
export const formatDay = (iso: string): string => dateShort.format(new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso));

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${ms < 10_000 ? seconds1.format(ms / 1000) : integer.format(ms / 1000)} s`;
}

export function formatRelative(iso: string, now: Date = new Date()): string {
  const seconds = Math.round((new Date(iso).getTime() - now.getTime()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 60) return relative.format(seconds, 'second');
  if (abs < 3600) return relative.format(Math.round(seconds / 60), 'minute');
  if (abs < 86_400) return relative.format(Math.round(seconds / 3600), 'hour');
  return relative.format(Math.round(seconds / 86_400), 'day');
}

export const AGENT_LABEL = { claude: 'Claude', chatgpt: 'ChatGPT' } as const;
