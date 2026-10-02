import { describe, expect, it } from 'vitest';
import { navIndex } from './nav';
import { band, barThickness, clampBox, linear, niceTicks, roundedBar } from './scale';

describe('niceTicks', () => {
  it('produces clean round steps that cover the maximum', () => {
    expect(niceTicks(1830, 4)).toEqual([0, 500, 1000, 1500, 2000]);
    expect(niceTicks(35659, 4)).toEqual([0, 10000, 20000, 30000, 40000]);
    expect(niceTicks(100, 4)).toEqual([0, 25, 50, 75, 100]);
    expect(niceTicks(7, 4)).toEqual([0, 2, 4, 6, 8]);
  });

  it('never yields fractional ticks for integer data', () => {
    expect(niceTicks(3, 4)).toEqual([0, 1, 2, 3]);
    expect(niceTicks(1, 4)).toEqual([0, 1]);
  });

  it('handles empty and invalid maxima', () => {
    expect(niceTicks(0)).toEqual([0, 1]);
    expect(niceTicks(Number.NaN)).toEqual([0, 1]);
    expect(niceTicks(-5)).toEqual([0, 1]);
  });

  it('allows fractional ticks when asked', () => {
    expect(niceTicks(0.9, 4, false)).toEqual([0, 0.25, 0.5, 0.75, 1]);
  });
});

describe('linear and band', () => {
  it('maps the domain onto the range (inverted for y)', () => {
    const y = linear(100, 200, 0);
    expect(y(0)).toBe(200);
    expect(y(50)).toBe(100);
    expect(y(100)).toBe(0);
    expect(linear(0, 0, 10)(0)).toBe(0); // zero domain does not divide by zero
  });

  it('centres categories evenly', () => {
    const b = band(4, 0, 400);
    expect(b.step).toBe(100);
    expect(b.center(0)).toBe(50);
    expect(b.center(3)).toBe(350);
  });

  it('caps bar thickness at 24px and leaves 2px between grouped bars', () => {
    expect(barThickness(200)).toBe(24);
    expect(barThickness(10)).toBeCloseTo(7);
    expect(barThickness(40, 2, 2, 24, 1)).toBe(19); // (40 - 2) / 2
    expect(barThickness(0)).toBe(1);
  });
});

describe('roundedBar', () => {
  it('rounds only the data end', () => {
    const up = roundedBar(10, 20, 12, 50);
    expect(up.startsWith('M10,70V24')).toBe(true); // square baseline at y=70
    expect(up).toContain('Q10,20 14,20');
    const right = roundedBar(0, 0, 80, 12, 4, 'right');
    expect(right.startsWith('M0,0H76')).toBe(true);
    expect(right.endsWith('H0Z')).toBe(true);
  });

  it('clamps the radius on tiny bars and skips empty ones', () => {
    expect(roundedBar(0, 0, 4, 1)).toContain('Q0,0 1,0');
    expect(roundedBar(0, 0, 0, 10)).toBe('');
    expect(roundedBar(0, 0, 10, 0)).toBe('');
  });
});

describe('clampBox', () => {
  it('keeps a box inside its container', () => {
    expect(clampBox(100, 50, 400)).toBe(75);
    expect(clampBox(10, 50, 400)).toBe(4);
    expect(clampBox(395, 50, 400)).toBe(346);
  });
});

describe('navIndex', () => {
  it('moves between marks and clamps at the ends', () => {
    expect(navIndex('ArrowRight', 0, 5)).toBe(1);
    expect(navIndex('ArrowDown', 4, 5)).toBe(4);
    expect(navIndex('ArrowLeft', 0, 5)).toBe(0);
    expect(navIndex('ArrowUp', 3, 5)).toBe(2);
    expect(navIndex('Home', 3, 5)).toBe(0);
    expect(navIndex('End', 0, 5)).toBe(4);
    expect(navIndex('PageDown', 0, 30)).toBe(7);
    expect(navIndex('PageUp', 3, 30)).toBe(0);
  });

  it('ignores other keys and empty charts', () => {
    expect(navIndex('Enter', 1, 5)).toBeNull();
    expect(navIndex('ArrowRight', 0, 0)).toBeNull();
  });
});
