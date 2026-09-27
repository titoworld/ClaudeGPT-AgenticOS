// Scales and geometry for the SVG charts (no dependencies).

/** Rounds a step up to 1, 2, 2.5 or 5 × 10^n. */
function niceStep(raw: number): number {
  if (!(raw > 0) || !Number.isFinite(raw)) return 1;
  const exp = Math.floor(Math.log10(raw));
  const base = 10 ** exp;
  const f = raw / base;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * base;
}

/**
 * Clean ticks from 0 to a rounded maximum that covers `max`
 * (e.g. max 1830, count 4 -> [0, 500, 1000, 1500, 2000]).
 * Integer data never gets fractional ticks.
 */
export function niceTicks(max: number, count = 4, integer = true): number[] {
  if (!(max > 0) || !Number.isFinite(max)) return [0, 1];
  let step = niceStep(max / Math.max(1, count));
  if (integer && step < 1) step = 1;
  const top = Math.ceil(max / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= top + step * 1e-9; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

/** Linear map from [0, domainMax] to [rangeStart, rangeEnd]. */
export function linear(domainMax: number, rangeStart: number, rangeEnd: number): (v: number) => number {
  const d = domainMax > 0 ? domainMax : 1;
  return (v) => rangeStart + ((rangeEnd - rangeStart) * v) / d;
}

export interface Band {
  /** Distance between category centres. */
  step: number;
  /** Centre of category i. */
  center: (i: number) => number;
}

/** Evenly spaced categories across [start, end]. */
export function band(count: number, start: number, end: number): Band {
  const n = Math.max(1, count);
  const step = (end - start) / n;
  return { step, center: (i) => start + step * (i + 0.5) };
}

/**
 * Width of each bar in a group of `k` bars inside a category slot, capped at
 * 24px (thin marks) and leaving `gap` px of surface between touching bars.
 */
export function barThickness(step: number, k = 1, gap = 2, max = 24, fill = 0.7): number {
  const avail = step * fill - gap * (k - 1);
  return Math.max(1, Math.min(max, avail / Math.max(1, k)));
}

/**
 * SVG path for a bar with a rounded data end (4px) and a square baseline.
 * Vertical bars round the top; horizontal bars round the right end.
 */
export function roundedBar(x: number, y: number, w: number, h: number, r = 4, orientation: 'up' | 'right' = 'up'): string {
  if (w <= 0 || h <= 0) return '';
  const f = (n: number) => Math.round(n * 100) / 100;
  if (orientation === 'up') {
    const rr = Math.min(r, w / 2, h);
    return `M${f(x)},${f(y + h)}V${f(y + rr)}Q${f(x)},${f(y)} ${f(x + rr)},${f(y)}H${f(x + w - rr)}Q${f(x + w)},${f(y)} ${f(x + w)},${f(y + rr)}V${f(y + h)}Z`;
  }
  const rr = Math.min(r, h / 2, w);
  return `M${f(x)},${f(y)}H${f(x + w - rr)}Q${f(x + w)},${f(y)} ${f(x + w)},${f(y + rr)}V${f(y + h - rr)}Q${f(x + w)},${f(y + h)} ${f(x + w - rr)},${f(y + h)}H${f(x)}Z`;
}

/** Clamps a tooltip's left edge so a box of `width` stays inside [0, container]. */
export function clampBox(center: number, width: number, container: number, pad = 4): number {
  const left = center - width / 2;
  return Math.max(pad, Math.min(left, container - width - pad));
}
