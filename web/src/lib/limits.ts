// Subscription usage windows (ProviderStatus.limits): one warning threshold and
// one reset wording for the sidebar badges and the dashboard.

import { formatRelative, formatTime } from './format';
import { i18n } from './i18n/index.svelte';

/** Used share of a window (percent) from which it shows as nearly full. */
export const LIMIT_WARN_PERCENT = 80;

export type LimitLevel = 'ok' | 'warn' | 'bad';

/**
 * Level of a window from its status and used share: providers may keep reporting
 * "allowed" for a nearly full window that is not the one being enforced.
 */
export function limitLevel(status: string, usedPercent: number | null | undefined): LimitLevel {
  const used = usedPercent ?? 0;
  if (status === 'rejected' || used >= 100) return 'bad';
  if (status === 'warning' || used >= LIMIT_WARN_PERCENT) return 'warn';
  return 'ok';
}

// Local day, like formatTime's local clock (format.ts's formatDay is a UTC day): one
// format per language, made when first needed.
const localDays = new Map<string, Intl.DateTimeFormat>();
function localDay(): Intl.DateTimeFormat {
  let format = localDays.get(i18n.tag);
  if (!format) {
    format = new Intl.DateTimeFormat(i18n.tag, { day: 'numeric', month: 'short' });
    localDays.set(i18n.tag, format);
  }
  return format;
}

/** "Resets in 3 days (30 Sept, 01:30)", date and time in local time. */
export function resetText(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === now.toDateString();
  const when = sameDay ? formatTime(iso) : `${localDay().format(d)}, ${formatTime(iso)}`;
  return i18n.m.app.limits.resets(formatRelative(iso, now), when);
}

/** "35 min", "2 h", "3 d": time left until the window resets. */
export function shortUntil(iso: string, now: Date = new Date()): string {
  const ms = new Date(iso).getTime() - now.getTime();
  if (ms <= 60_000) return i18n.m.app.limits.now;
  const hours = ms / 3_600_000;
  if (hours < 1) return `${Math.round(ms / 60_000)} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} d`;
}
