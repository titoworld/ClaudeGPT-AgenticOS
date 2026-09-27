// Formatting helpers shared by the whole UI (Catalan locale).

const LOCALE = 'ca-ES';

const compact = new Intl.NumberFormat(LOCALE, { notation: 'compact', maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const usd = new Intl.NumberFormat(LOCALE, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 });
const percent = new Intl.NumberFormat(LOCALE, { style: 'percent', maximumFractionDigits: 0 });
const time = new Intl.DateTimeFormat(LOCALE, { hour: '2-digit', minute: '2-digit' });
const dateShort = new Intl.DateTimeFormat(LOCALE, { day: 'numeric', month: 'short' });
const relative = new Intl.RelativeTimeFormat(LOCALE, { numeric: 'auto' });

export const formatTokens = (n: number): string => (n >= 10_000 ? compact.format(n) : integer.format(n));
export const formatInt = (n: number): string => integer.format(n);
export const formatUsd = (n: number | null | undefined): string => (n == null ? '—' : usd.format(n));
export const formatPercent = (ratio: number): string => percent.format(ratio);
export const formatTime = (iso: string): string => time.format(new Date(iso));
export const formatDay = (iso: string): string => dateShort.format(new Date(iso));

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
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
