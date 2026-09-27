<script lang="ts">
  /**
   * Stacked columns over time (one column per day). Thin columns (<= 24px),
   * 2px surface gaps between segments, 4px rounded data end, recessive grid,
   * a single y-axis. Each day's full-height slot is the hover/focus target; one
   * tooltip lists every series. Arrow keys move between days.
   */
  import { tickIndices } from './dates';
  import { navIndex } from './nav';
  import { band, barThickness, linear, niceTicks, roundedBar } from './scale';
  import { maxTotal, peakIndex, stackData } from './stack';
  import Tooltip from './Tooltip.svelte';
  import type { Datum, SeriesDef, TooltipRow } from './types';

  interface Props {
    data: Datum[];
    series: SeriesDef[];
    /** Accessible name of the chart. */
    label: string;
    /** Value formatter (tooltips, direct labels). */
    format: (v: number) => string;
    /** Y-axis tick formatter (defaults to `format`). */
    tickFormat?: (v: number) => string;
    /** Plot height in px (the x-axis band is added on top of it). */
    height?: number;
  }

  let { data, series, label, format, tickFormat, height = 188 }: Props = $props();

  const GAP = 2;
  const TOP = 22;
  const AXIS = 26;
  const RIGHT = 4;

  let width = $state(0);
  let hover = $state(-1);
  let focused = $state(-1);
  let svg: SVGSVGElement | undefined = $state();

  const fmtTick = $derived(tickFormat ?? format);
  const stacks = $derived(stackData(data, series));
  const ticks = $derived(niceTicks(maxTotal(stacks), 4));
  const yMax = $derived(ticks.at(-1) ?? 1);
  const left = $derived(Math.max(30, Math.max(...ticks.map((t) => fmtTick(t).length)) * 6.8 + 12));
  const plotW = $derived(Math.max(0, width - left - RIGHT));
  const x = $derived(band(data.length, left, left + plotW));
  const y = $derived(linear(yMax, TOP + height, TOP));
  const barW = $derived(barThickness(x.step, 1, GAP, 24, 0.7));
  const labelled = $derived(tickIndices(data.length, Math.max(2, Math.floor(plotW / 62))));
  const peak = $derived(peakIndex(stacks));
  const active = $derived(focused >= 0 ? focused : hover);
  const tabStop = $derived(focused >= 0 ? focused : data.length - 1);

  function segmentsOf(i: number) {
    const s = stacks[i];
    if (!s) return [];
    const x0 = x.center(i) - barW / 2;
    return s.segments.flatMap((seg) => {
      const yTop = y(seg.y1);
      // Every segment above the first leaves a 2px surface gap below itself.
      const yBottom = y(seg.y0) - (seg.y0 > 0 ? GAP : 0);
      const h = yBottom - yTop;
      if (h < 0.75) return [];
      const d = seg.top ? roundedBar(x0, yTop, barW, h) : `M${x0},${yTop}h${barW}v${h}h${-barW}Z`;
      return [{ key: seg.key, color: seg.color, d }];
    });
  }

  function rowsOf(i: number): TooltipRow[] {
    const d = data[i];
    if (!d) return [];
    const rows: TooltipRow[] = series
      .slice()
      .reverse() // same order as the stack reads, top to bottom
      .map((s) => ({ key: s.key, color: s.color, label: s.label, value: format(Number(d.values[s.key] ?? 0)) }));
    if (series.length > 1) rows.push({ key: '__total', color: null, label: 'Total', value: format(stacks[i]?.total ?? 0) });
    return rows;
  }

  function ariaOf(i: number): string {
    const d = data[i];
    if (!d) return '';
    const parts = series.map((s) => `${s.label} ${format(Number(d.values[s.key] ?? 0))}`);
    if (series.length > 1) parts.push(`total ${format(stacks[i]?.total ?? 0)}`);
    return `${d.fullLabel}: ${parts.join(', ')}`;
  }

  function onkeydown(e: KeyboardEvent, i: number) {
    const next = navIndex(e.key, i, data.length);
    if (next === null) {
      if (e.key === 'Escape') (e.currentTarget as SVGElement).blur();
      return;
    }
    e.preventDefault();
    svg?.querySelector<SVGElement>(`[data-i="${next}"]`)?.focus();
  }

  function labelAnchor(i: number): 'start' | 'middle' | 'end' {
    const cx = x.center(i);
    if (cx - 22 < left - 6) return 'start';
    if (cx + 22 > width) return 'end';
    return 'middle';
  }
</script>

<div class="chart" bind:clientWidth={width}>
  {#if width > 0}
    <svg
      bind:this={svg}
      width={width}
      height={TOP + height + AXIS}
      role="group"
      aria-label={label}
      onpointerleave={(e) => {
        if (e.pointerType !== 'touch') hover = -1;
      }}
    >
      <!-- Grid and y-axis ticks (recessive hairlines). -->
      <g class="grid" aria-hidden="true">
        {#each ticks as t (t)}
          <line x1={left} x2={left + plotW} y1={Math.round(y(t)) + 0.5} y2={Math.round(y(t)) + 0.5} class:base={t === 0} />
          <text x={left - 8} y={y(t)} dy="0.32em" text-anchor="end">{fmtTick(t)}</text>
        {/each}
      </g>

      {#if active >= 0}
        <rect class="highlight" x={x.center(active) - x.step / 2} y={TOP - 6} width={x.step} height={height + 6} rx="4" aria-hidden="true" />
      {/if}

      <g class="marks" aria-hidden="true">
        {#each stacks as _, i (data[i]?.key ?? i)}
          <g class:lift={i === active}>
            {#each segmentsOf(i) as seg (seg.key)}
              <path d={seg.d} style:fill={seg.color} />
            {/each}
          </g>
        {/each}
      </g>

      <!-- Direct label on the peak day only. -->
      {#if peak >= 0 && stacks[peak]}
        <text class="peak" x={x.center(peak)} y={y(stacks[peak].total) - 7} text-anchor="middle" aria-hidden="true">
          {format(stacks[peak].total)}
        </text>
      {/if}

      <g class="x-axis" aria-hidden="true">
        {#each labelled as i (i)}
          <text x={x.center(i)} y={TOP + height + 17} text-anchor={labelAnchor(i)}>{data[i]?.label}</text>
        {/each}
      </g>

      <!-- Hit targets: the whole day slot, bigger than the thin column. -->
      {#each data as d, i (d.key)}
        <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
        <rect
          class="hit"
          data-i={i}
          x={x.center(i) - x.step / 2}
          y={TOP - 6}
          width={x.step}
          height={height + 6}
          rx="4"
          role="img"
          aria-label={ariaOf(i)}
          tabindex={i === tabStop ? 0 : -1}
          onpointerenter={(e) => {
            if (e.pointerType !== 'touch') hover = i;
          }}
          onpointerdown={(e) => {
            // Keep keyboard focus for the keyboard; a tap toggles the tooltip.
            e.preventDefault();
            if (e.pointerType === 'touch') hover = hover === i ? -1 : i;
          }}
          onfocus={() => (focused = i)}
          onblur={() => (focused = -1)}
          onkeydown={(e) => onkeydown(e, i)}
        />
      {/each}
    </svg>

    {#if active >= 0 && data[active]}
      <Tooltip
        title={data[active].fullLabel}
        rows={rowsOf(active)}
        x={x.center(active)}
        y={TOP}
        clearance={Math.max(barW / 2, 6)}
        containerWidth={width}
      />
    {/if}
  {/if}
</div>

<style>
  .chart {
    position: relative;
    width: 100%;
    min-height: 236px;
  }

  svg {
    display: block;
    overflow: visible;
    font-size: 11px;
  }

  .grid line {
    stroke: var(--border);
    stroke-width: 1;
    shape-rendering: crispEdges;
  }

  .grid line.base {
    stroke: var(--border-strong);
  }

  .grid text,
  .x-axis text {
    fill: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .highlight {
    fill: var(--surface-2);
  }

  .marks g {
    transition: filter var(--dur-fast) var(--ease-out);
  }

  .marks g.lift {
    filter: brightness(1.22);
  }

  .peak {
    fill: var(--text-secondary);
    font-size: 11px;
    font-weight: 600;
  }

  .hit {
    fill: transparent;
    outline: none;
    cursor: default;
  }

  .hit:focus-visible {
    stroke: var(--accent);
    stroke-width: 2;
  }
</style>
