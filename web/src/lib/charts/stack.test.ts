import { describe, expect, it } from 'vitest';
import { maxTotal, peakIndex, positive, stackData } from './stack';
import { chartTable } from './table';
import type { Datum, SeriesDef } from './types';

const series: SeriesDef[] = [
  { key: 'a', label: 'A', color: 'var(--a)' },
  { key: 'b', label: 'B', color: 'var(--b)' },
];
const day = (key: string, a: number | null, b: number | null): Datum => ({
  key,
  label: key,
  fullLabel: `dia ${key}`,
  values: { a, b },
});

describe('stackData', () => {
  it('stacks in series order and marks the top segment', () => {
    const [s] = stackData([day('1', 3, 5)], series);
    expect(s!.total).toBe(8);
    expect(s!.segments.map((g) => [g.key, g.y0, g.y1, g.top])).toEqual([
      ['a', 0, 3, false],
      ['b', 3, 8, true],
    ]);
  });

  it('drops empty, null and negative values so the gap logic stays simple', () => {
    const [s] = stackData([day('1', 0, 4)], series);
    expect(s!.segments).toHaveLength(1);
    expect(s!.segments[0]).toMatchObject({ key: 'b', y0: 0, y1: 4, top: true });
    const [n] = stackData([day('2', null, -3)], series);
    expect(n!.segments).toEqual([]);
    expect(n!.total).toBe(0);
  });

  it('finds the maximum and the peak (latest on ties, -1 when empty)', () => {
    const stacks = stackData([day('1', 1, 1), day('2', 5, 0), day('3', 3, 2)], series);
    expect(maxTotal(stacks)).toBe(5);
    expect(peakIndex(stacks)).toBe(2);
    expect(peakIndex(stackData([day('1', 0, 0)], series))).toBe(-1);
    expect(maxTotal([])).toBe(0);
  });

  it('positive() treats junk as zero', () => {
    expect(positive(Number.NaN)).toBe(0);
    expect(positive(undefined)).toBe(0);
    expect(positive(-1)).toBe(0);
    expect(positive(2)).toBe(2);
  });
});

describe('chartTable', () => {
  const fmt = (v: number) => `${v}`;

  it('builds the accessible twin with a category column and totals', () => {
    const t = chartTable([day('1', 1, 2), day('2', null, 4)], series, {
      caption: 'Prova',
      categoryLabel: 'Dia',
      format: fmt,
      total: true,
    });
    expect(t.caption).toBe('Prova');
    expect(t.columns.map((c) => [c.label, c.numeric])).toEqual([
      ['Dia', false],
      ['A', true],
      ['B', true],
      ['Total', true],
    ]);
    expect(t.rows).toEqual([
      { key: '1', cells: ['dia 1', '1', '2', '3'] },
      { key: '2', cells: ['dia 2', '—', '4', '4'] },
    ]);
    expect(t.note).toBeUndefined();
  });

  it('can omit empty rows and says so', () => {
    const t = chartTable([day('1', 0, 0), day('2', 1, 0), day('3', null, null)], series, {
      caption: 'Prova',
      categoryLabel: 'Dia',
      format: fmt,
      skipEmpty: true,
    });
    expect(t.rows.map((r) => r.key)).toEqual(['2']);
    expect(t.note).toBe('Només es mostren els dies amb activitat (1 de 3).');
  });
});
