// Shared shapes for the hand-rolled SVG charts.

/** One series: identity colour comes from a CSS custom property (e.g. 'var(--claude)'). */
export interface SeriesDef {
  key: string;
  label: string;
  color: string;
}

/** One category (a day, a percentile, a mode) with a value per series key. */
export interface Datum {
  key: string;
  /** Short label for the axis. */
  label: string;
  /** Full label for tooltips, aria-labels and tables. */
  fullLabel: string;
  values: Record<string, number | null>;
}

export interface TableColumn {
  key: string;
  label: string;
  numeric: boolean;
}

/** Accessible twin of a chart ("Show table"). Cells are preformatted text. */
export interface ChartTable {
  caption: string;
  columns: TableColumn[];
  rows: { key: string; cells: string[] }[];
  /** Optional note under the table (e.g. rows omitted). */
  note?: string;
}

export interface TooltipRow {
  key: string;
  /** Series colour for the line key; null for rows such as 'Total'. */
  color: string | null;
  label: string;
  value: string;
}
