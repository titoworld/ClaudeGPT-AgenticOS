// Subscription usage windows (ProviderStatus.limits): one warning threshold and
// one reset wording for the sidebar badges and the dashboard.

import { formatRelative, formatTime } from './format';

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

// Local day, like formatTime's local clock (format.ts's formatDay is a UTC day).
const localDay = new Intl.DateTimeFormat('ca-ES', { day: 'numeric', month: 'short' });

/** "Es restableix d’aquí a 3 dies (30 de set., 01:30)", date and time in local time. */
export function resetText(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === now.toDateString();
  const when = sameDay ? formatTime(iso) : `${localDay.format(d)}, ${formatTime(iso)}`;
  return `Es restableix ${formatRelative(iso, now)} (${when})`;
}

/** "35 min", "2 h", "3 d": time left until the window resets. */
export function shortUntil(iso: string, now: Date = new Date()): string {
  const ms = new Date(iso).getTime() - now.getTime();
  if (ms <= 60_000) return 'ara';
  const hours = ms / 3_600_000;
  if (hours < 1) return `${Math.round(ms / 60_000)} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} d`;
}
