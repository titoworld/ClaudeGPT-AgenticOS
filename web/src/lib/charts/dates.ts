// Calendar-day helpers. Dates travel as 'YYYY-MM-DD' (UTC days, as the API sends
// them) and are always formatted in UTC so a browser west of Greenwich never
// shows the previous day. Labels are in the language in force (lib/i18n).

import { i18n } from '../i18n/index.svelte';
import { perLocale } from './intl';

const DAY_MS = 86_400_000;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
/** A month's short name on its own ('Sept', 'sept', 'set.'), without the "de" Catalan puts after a day. */
const monthShort = perLocale((tag) => new Intl.DateTimeFormat(tag, { month: 'short', timeZone: 'UTC' }));
const longDay = perLocale(
  (tag) => new Intl.DateTimeFormat(tag, { weekday: 'short', day: 'numeric', month: 'long', timeZone: 'UTC' }),
);

export function isIsoDay(value: string): boolean {
  return ISO_DAY.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

/** 'YYYY-MM-DD' of `now` in UTC. */
export function utcDay(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

export function addDays(day: string, delta: number): string {
  return utcDay(new Date(Date.parse(`${day}T00:00:00Z`) + delta * DAY_MS));
}

/**
 * The `days` consecutive days ending at `end` (inclusive), oldest first.
 * `days` is clamped to 1..365 like the API.
 */
export function dayRange(days: number, end: string): string[] {
  const n = Math.min(365, Math.max(1, Math.floor(Number.isFinite(days) ? days : 1)));
  const out: string[] = [];
  for (let i = n - 1; i >= 0; i--) out.push(addDays(end, -i));
  return out;
}

/** Range end: today (UTC) or the latest day in the data if the server is ahead. */
export function rangeEnd(dataDays: string[], today: string = utcDay()): string {
  let end = today;
  for (const d of dataDays) if (isIsoDay(d) && d > end) end = d;
  return end;
}

/** Axis label: '27 Sept', '27 sept', '27 set.' */
export function axisDayLabel(day: string): string {
  const [, m, d] = day.split('-').map(Number);
  if (!m || !d) return day;
  const month = m <= 12 ? monthShort().format(Date.UTC(2000, m - 1, 1)) : '';
  return i18n.m.dashboard.chart.axisDay(d, month).trim();
}

/** Tooltip/table label: 'Sun 27 September', 'dom, 27 de septiembre', 'dg., 27 de setembre' */
export function fullDayLabel(day: string): string {
  const t = Date.parse(`${day}T00:00:00Z`);
  return Number.isNaN(t) ? day : longDay().format(t);
}

/**
 * Which of `count` evenly spaced labels to show so at most `maxLabels` appear,
 * always including the last one (the most recent day) and spacing the rest
 * backwards from it at a regular interval.
 */
export function tickIndices(count: number, maxLabels: number): number[] {
  if (count <= 0) return [];
  const max = Math.max(1, Math.floor(maxLabels));
  if (count <= max) return Array.from({ length: count }, (_, i) => i);
  if (max === 1) return [count - 1];
  const every = Math.ceil((count - 1) / (max - 1));
  const out: number[] = [];
  for (let i = count - 1; i >= 0; i -= every) out.unshift(i);
  return out;
}
