// Builds the accessible table twin of a chart.

import { positive } from './stack';
import type { ChartTable, Datum, SeriesDef } from './types';

export interface TableOptions {
  caption: string;
  /** Header of the category column (e.g. 'Dia'). */
  categoryLabel: string;
  format: (v: number) => string;
  /** Add a 'Total' column (stacked charts). */
  total?: boolean;
  /** Drop rows where every value is empty/zero (long daily series). */
  skipEmpty?: boolean;
}

export const MISSING = '—';

export function chartTable(data: Datum[], series: SeriesDef[], opts: TableOptions): ChartTable {
  const columns = [
    { key: 'category', label: opts.categoryLabel, numeric: false },
    ...series.map((s) => ({ key: s.key, label: s.label, numeric: true })),
    ...(opts.total ? [{ key: 'total', label: 'Total', numeric: true }] : []),
  ];
  let omitted = 0;
  const rows: ChartTable['rows'] = [];
  for (const d of data) {
    const values = series.map((s) => d.values[s.key]);
    const total = values.reduce<number>((acc, v) => acc + positive(v), 0);
    if (opts.skipEmpty && total === 0) {
      omitted++;
      continue;
    }
    const cells = [
      d.fullLabel,
      ...values.map((v) => (v == null || !Number.isFinite(v) ? MISSING : opts.format(v))),
      ...(opts.total ? [opts.format(total)] : []),
    ];
    rows.push({ key: d.key, cells });
  }
  const note =
    omitted > 0 && rows.length > 0
      ? `Només es mostren els dies amb activitat (${rows.length} de ${data.length}).`
      : undefined;
  return { caption: opts.caption, columns, rows, ...(note ? { note } : {}) };
}
