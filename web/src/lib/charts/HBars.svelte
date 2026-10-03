<script lang="ts">
  /**
   * Horizontal bars, grouped by category (one bar per series). Every value is
   * labelled at the bar tip, so no value axis is drawn: only the baseline.
   * Each category row is the hover/focus target; arrow keys move between rows.
   */
  import { i18n } from '../i18n/index.svelte';
  import { navIndex } from './nav';
  import { linear, roundedBar } from './scale';
  import { positive } from './stack';
  import Tooltip from './Tooltip.svelte';
  import type { Datum, SeriesDef, TooltipRow } from './types';

  interface Props {
    data: Datum[];
    series: SeriesDef[];
    label: string;
    format: (v: number) => string;
  }

  let { data, series, label, format }: Props = $props();

  const GAP = 2;
  const PAD = 10;

  let width = $state(0);
  let hover = $state(-1);
  let focused = $state(-1);
  let svg: SVGSVGElement | undefined = $state();

  const k = $derived(Math.max(1, series.length));
  const thick = $derived(k === 1 ? 16 : 13);
  const rowH = $derived(k * thick + (k - 1) * GAP + PAD * 2);
  const labelW = $derived(Math.min(150, Math.max(...data.map((d) => d.label.length), 3) * 7.2 + 18));
  const valueW = $derived(
    Math.max(
      44,
      ...data.flatMap((d) => series.map((s) => (d.values[s.key] == null ? 1 : format(Number(d.values[s.key])).length))).map((n) => n * 6.8 + 12),
    ),
  );
  const max = $derived(Math.max(0, ...data.flatMap((d) => series.map((s) => positive(d.values[s.key])))));
  const x = $derived(linear(max, labelW, Math.max(labelW + 1, width - valueW)));
  const totalH = $derived(data.length * rowH);
  const active = $derived(focused >= 0 ? focused : hover);
  const tabStop = $derived(focused >= 0 ? focused : 0);

  function barsOf(i: number) {
    const d = data[i];
    if (!d) return [];
    return series.map((s, j) => {
      const raw = d.values[s.key];
      const v = raw == null || !Number.isFinite(raw) ? null : Math.max(0, raw);
      const y = i * rowH + PAD + j * (thick + GAP);
      const w = v == null ? 0 : x(v) - labelW;
      return {
        key: s.key,
        color: s.color,
        d: v != null && w >= 0.75 ? roundedBar(labelW, y, w, thick, 4, 'right') : '',
        tipX: labelW + Math.max(0, w) + 6,
        cy: y + thick / 2,
        text: v == null ? '—' : format(v),
        missing: v == null,
      };
    });
  }

  function rowsOf(i: number): TooltipRow[] {
    const d = data[i];
    if (!d) return [];
    return series.map((s) => {
      const v = d.values[s.key];
      return { key: s.key, color: s.color, label: s.label, value: v == null ? '—' : format(v) };
    });
  }

  function ariaOf(i: number): string {
    const d = data[i];
    if (!d) return '';
    const parts = series.map((s) => {
      const v = d.values[s.key];
      const value = v == null ? i18n.m.dashboard.chart.noValue : format(v);
      return series.length > 1 ? `${s.label} ${value}` : value;
    });
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
</script>

<div class="chart" bind:clientWidth={width}>
  {#if width > 0}
    <svg
      bind:this={svg}
      width={width}
      height={totalH}
      role="group"
      aria-label={label}
      onpointerleave={(e) => {
        if (e.pointerType !== 'touch') hover = -1;
      }}
    >
      {#if active >= 0}
        <rect class="highlight" x="0" y={active * rowH + 2} width={width} height={rowH - 4} rx="6" aria-hidden="true" />
      {/if}

      <g aria-hidden="true">
        {#each data as d, i (d.key)}
          <text class="cat" x={labelW - 12} y={i * rowH + rowH / 2} dy="0.32em" text-anchor="end">{d.label}</text>
          <g class:lift={i === active}>
            {#each barsOf(i) as b (b.key)}
              {#if b.d}<path d={b.d} style:fill={b.color} />{/if}
              <text class="value" class:missing={b.missing} x={b.tipX} y={b.cy} dy="0.32em">{b.text}</text>
            {/each}
          </g>
        {/each}
        <line class="base" x1={labelW + 0.5} x2={labelW + 0.5} y1="4" y2={totalH - 4} />
      </g>

      {#each data as d, i (d.key)}
        <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
        <rect
          class="hit"
          data-i={i}
          x="0"
          y={i * rowH + 2}
          width={width}
          height={rowH - 4}
          rx="6"
          role="img"
          aria-label={ariaOf(i)}
          tabindex={i === tabStop ? 0 : -1}
          onpointerenter={(e) => {
            if (e.pointerType !== 'touch') hover = i;
          }}
          onpointerdown={(e) => {
            e.preventDefault();
            if (e.pointerType === 'touch') hover = hover === i ? -1 : i;
          }}
          onfocus={() => (focused = i)}
          onblur={() => (focused = -1)}
          onkeydown={(e) => onkeydown(e, i)}
        />
      {/each}
    </svg>

    {#if active >= 0 && data[active] && series.length > 1}
      <Tooltip
        title={data[active].fullLabel}
        rows={rowsOf(active)}
        x={labelW - 10}
        y={(active + 1) * rowH}
        containerWidth={width}
      />
    {/if}
  {/if}
</div>

<style>
  .chart {
    position: relative;
    width: 100%;
  }

  svg {
    display: block;
    overflow: visible;
    font-size: 12px;
  }

  .highlight {
    fill: var(--surface-2);
  }

  .cat {
    fill: var(--text-secondary);
  }

  .value {
    fill: var(--text-secondary);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }

  .value.missing {
    fill: var(--text-muted);
  }

  .base {
    stroke: var(--border-strong);
    stroke-width: 1;
  }

  g.lift path {
    filter: brightness(1.22);
  }

  .hit {
    fill: transparent;
    outline: none;
  }

  .hit:focus-visible {
    stroke: var(--accent);
    stroke-width: 2;
  }
</style>
