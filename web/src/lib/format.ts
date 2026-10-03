// Formatting helpers shared by the whole UI, in the language in force (lib/i18n).

import { i18n } from './i18n/index.svelte';

/** An `Intl` formatter for the language in force, made once per language. */
function perLocale<T>(make: (tag: string) => T): () => T {
  const made = new Map<string, T>();
  return () => {
    const tag = i18n.tag;
    let formatter = made.get(tag);
    if (!formatter) {
      formatter = make(tag);
      made.set(tag, formatter);
    }
    return formatter;
  };
}

const compact = perLocale((tag) => new Intl.NumberFormat(tag, { notation: 'compact', maximumFractionDigits: 1 }));
const integer = perLocale((tag) => new Intl.NumberFormat(tag, { maximumFractionDigits: 0 }));
const usd = perLocale((tag) => new Intl.NumberFormat(tag, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }));
const percent = perLocale((tag) => new Intl.NumberFormat(tag, { style: 'percent', maximumFractionDigits: 0 }));
const time = perLocale((tag) => new Intl.DateTimeFormat(tag, { hour: '2-digit', minute: '2-digit' }));
const dateShort = perLocale((tag) => new Intl.DateTimeFormat(tag, { day: 'numeric', month: 'short', timeZone: 'UTC' }));
const seconds1 = perLocale((tag) => new Intl.NumberFormat(tag, { maximumFractionDigits: 1 }));
const eur = [0, 1, 2, 3, 4].map((digits) =>
  perLocale(
    (tag) =>
      new Intl.NumberFormat(tag, {
        style: 'currency',
        currency: 'EUR',
        maximumFractionDigits: digits,
        minimumFractionDigits: Math.min(2, digits),
      }),
  ),
);
const relative = perLocale((tag) => new Intl.RelativeTimeFormat(tag, { numeric: 'auto' }));

export const formatTokens = (n: number): string => (n >= 10_000 ? compact().format(n) : integer().format(n));
export const formatInt = (n: number): string => integer().format(n);
export const formatUsd = (n: number | null | undefined): string => (n == null ? '—' : usd().format(n));

/** Euros with precision adapted to the amount (tiny per-call costs keep 4 decimals). */
export function formatEur(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const digits = Math.abs(n) >= 10 ? 2 : Math.abs(n) >= 0.1 ? 3 : 4;
  return eur[digits]!().format(n);
}

/** USD amount converted with ``eurPerUsd`` and formatted in euros. */
export const usdToEur = (usd: number | null | undefined, eurPerUsd: number): number | null =>
  usd == null ? null : usd * eurPerUsd;
export const formatPercent = (ratio: number): string => percent().format(ratio);
export const formatTime = (iso: string): string => time().format(new Date(iso));
/** Date-only strings (YYYY-MM-DD) are UTC calendar days. */
export const formatDay = (iso: string): string => dateShort().format(new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso));

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${ms < 10_000 ? seconds1().format(ms / 1000) : integer().format(ms / 1000)} s`;
}

export function formatRelative(iso: string, now: Date = new Date()): string {
  const seconds = Math.round((new Date(iso).getTime() - now.getTime()) / 1000);
  const abs = Math.abs(seconds);
  const format = relative();
  if (abs < 60) return format.format(seconds, 'second');
  if (abs < 3600) return format.format(Math.round(seconds / 60), 'minute');
  if (abs < 86_400) return format.format(Math.round(seconds / 3600), 'hour');
  return format.format(Math.round(seconds / 86_400), 'day');
}

export const AGENT_LABEL = { claude: 'Claude', chatgpt: 'ChatGPT' } as const;
