import { describe, expect, it } from 'vitest';
import { addDays, axisDayLabel, dayRange, fullDayLabel, isIsoDay, rangeEnd, tickIndices, utcDay } from './dates';

describe('calendar days', () => {
  it('uses the UTC day and adds days across month and year ends', () => {
    expect(utcDay(new Date('2026-09-27T23:30:00Z'))).toBe('2026-09-27');
    expect(addDays('2026-02-28', 1)).toBe('2026-03-01');
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31');
    expect(addDays('2026-03-29', 1)).toBe('2026-03-30'); // DST change in Europe is irrelevant in UTC
  });

  it('validates ISO days', () => {
    expect(isIsoDay('2026-09-27')).toBe(true);
    expect(isIsoDay('2026-9-27')).toBe(false);
    expect(isIsoDay('garbage')).toBe(false);
  });

  it('builds a range of N days ending at the given day, oldest first', () => {
    expect(dayRange(3, '2026-09-27')).toEqual(['2026-09-25', '2026-09-26', '2026-09-27']);
    expect(dayRange(7, '2026-09-27')).toHaveLength(7);
    expect(dayRange(0, '2026-09-27')).toEqual(['2026-09-27']);
    expect(dayRange(1000, '2026-09-27')).toHaveLength(365);
  });

  it('extends the range end when the server is a day ahead', () => {
    expect(rangeEnd(['2026-09-26', '2026-09-28'], '2026-09-27')).toBe('2026-09-28');
    expect(rangeEnd(['2026-09-20'], '2026-09-27')).toBe('2026-09-27');
    expect(rangeEnd(['not-a-date'], '2026-09-27')).toBe('2026-09-27');
  });
});

describe('day labels (Catalan)', () => {
  it('formats short axis labels without time-zone drift', () => {
    expect(axisDayLabel('2026-09-27')).toBe('27 set.');
    expect(axisDayLabel('2026-03-01')).toBe('1 març');
    expect(axisDayLabel('2026-05-10')).toBe('10 maig');
  });

  it('formats full labels for tooltips and tables', () => {
    expect(fullDayLabel('2026-09-27')).toMatch(/27 de setembre/);
    expect(fullDayLabel('2026-09-27')).toMatch(/^dg/);
    expect(fullDayLabel('nope')).toBe('nope');
  });
});

describe('tickIndices', () => {
  it('labels every day when they fit', () => {
    expect(tickIndices(7, 10)).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });

  it('thins labels at a regular interval anchored on the latest day', () => {
    const idx = tickIndices(30, 6);
    expect(idx.length).toBeLessThanOrEqual(6);
    expect(idx.at(-1)).toBe(29);
    const gaps = idx.slice(1).map((v, i) => v - idx[i]!);
    expect(new Set(gaps).size).toBe(1);
    expect(tickIndices(90, 8).length).toBeLessThanOrEqual(8);
  });

  it('handles degenerate inputs', () => {
    expect(tickIndices(0, 5)).toEqual([]);
    expect(tickIndices(5, 0)).toEqual([4]);
  });
});
