// Stacking for stacked columns: series order is the stacking order (bottom up).

import type { Datum, SeriesDef } from './types';

export interface Segment {
  key: string;
  color: string;
  value: number;
  /** Lower and upper cumulative values. */
  y0: number;
  y1: number;
  /** True for the upper-most non-empty segment (gets the rounded data end). */
  top: boolean;
}

export interface Stack {
  datum: Datum;
  total: number;
  segments: Segment[];
}

/** Negative, null and non-finite values count as 0 (tokens are never negative). */
export function positive(v: number | null | undefined): number {
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0;
}

export function stackData(data: Datum[], series: SeriesDef[]): Stack[] {
  return data.map((datum) => {
    let acc = 0;
    const segments: Segment[] = [];
    for (const s of series) {
      const value = positive(datum.values[s.key]);
      if (value === 0) continue;
      segments.push({ key: s.key, color: s.color, value, y0: acc, y1: acc + value, top: false });
      acc += value;
    }
    const last = segments.at(-1);
    if (last) last.top = true;
    return { datum, total: acc, segments };
  });
}

/** Largest stack total (0 when empty). */
export function maxTotal(stacks: Stack[]): number {
  return stacks.reduce((m, s) => Math.max(m, s.total), 0);
}

/** Index of the largest total, or -1 when every total is 0 (ties: the latest). */
export function peakIndex(stacks: Stack[]): number {
  let best = -1;
  let bestValue = 0;
  stacks.forEach((s, i) => {
    if (s.total > 0 && s.total >= bestValue) {
      best = i;
      bestValue = s.total;
    }
  });
  return best;
}
