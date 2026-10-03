<script lang="ts">
  /**
   * Chart container: title, legend (for >= 2 series), the "Show table" toggle
   * with the accessible table twin, and the empty state.
   */
  import type { Snippet } from 'svelte';
  import { i18n } from '../i18n/index.svelte';
  import type { ChartTable, SeriesDef } from './types';

  interface Props {
    title: string;
    subtitle?: string;
    legend?: SeriesDef[];
    table: ChartTable;
    empty?: boolean;
    /** What the empty state says (by default, that there is no data in the period). */
    emptyText?: string;
    children: Snippet;
  }

  let { title, subtitle, legend = [], table, empty = false, emptyText, children }: Props = $props();
  const t = $derived(i18n.m.dashboard.chart);

  let showTable = $state(false);
  const id = $props.id();
</script>

<figure class="card" aria-labelledby="{id}-title">
  <figcaption class="head">
    <div class="titles">
      <h3 id="{id}-title">{title}</h3>
      {#if subtitle}<p class="subtitle">{subtitle}</p>{/if}
    </div>
    {#if !empty}
      <button type="button" class="toggle" aria-controls="{id}-body" onclick={() => (showTable = !showTable)}>
        {#if showTable}
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 13.5h12M4 11V7m4 4V4m4 7V8" /></svg>
          {t.showChart}
        {:else}
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h11v9h-11zM2.5 6.5h11M2.5 9.5h11M6.5 3.5v9" /></svg>
          {t.showTable}
        {/if}
      </button>
    {/if}
  </figcaption>

  {#if legend.length >= 2 && !empty && !showTable}
    <ul class="legend">
      {#each legend as s (s.key)}
        <li><span class="swatch" style:background={s.color}></span>{s.label}</li>
      {/each}
    </ul>
  {/if}

  <div class="body" id="{id}-body">
    {#if empty}
      <div class="empty">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19.5h16M7 16v-3m5 3V9m5 7v-5" /></svg>
        <p>{emptyText ?? t.empty}</p>
      </div>
    {:else if showTable}
      <div class="table-wrap">
        <table>
          <caption class="sr-only">{table.caption}</caption>
          <thead>
            <tr>
              {#each table.columns as c (c.key)}
                <th scope="col" class:num={c.numeric}>{c.label}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each table.rows as r (r.key)}
              <tr>
                {#each r.cells as cell, i (i)}
                  {#if i === 0}
                    <th scope="row">{cell}</th>
                  {:else}
                    <td class="num">{cell}</td>
                  {/if}
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if table.note}<p class="note">{table.note}</p>{/if}
    {:else}
      {@render children()}
    {/if}
  </div>
</figure>

<style>
  .card {
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-width: 0;
    margin: 0;
    padding: 18px 18px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface-1);
  }

  .head {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    justify-content: space-between;
  }

  .titles {
    min-width: 0;
  }

  h3 {
    margin: 0;
    color: var(--text-primary);
    font-size: var(--text-md);
    font-weight: 600;
  }

  .subtitle {
    margin: 2px 0 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
  }

  .toggle {
    display: inline-flex;
    flex: none;
    gap: 6px;
    align-items: center;
    padding: 5px 10px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: transparent;
    color: var(--text-secondary);
    font: inherit;
    font-size: var(--text-xs);
    cursor: pointer;
    transition:
      background var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }

  .toggle:hover {
    background: var(--surface-2);
    color: var(--text-primary);
  }

  .toggle:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .toggle svg {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.3;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    margin: 0;
    padding: 0;
    list-style: none;
    color: var(--text-secondary);
    font-size: var(--text-xs);
  }

  .legend li {
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }

  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 3px;
  }

  .body {
    position: relative;
    min-width: 0;
  }

  .empty {
    display: grid;
    gap: 8px;
    place-items: center;
    align-content: center;
    min-height: 180px;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    color: var(--text-muted);
    font-size: var(--text-sm);
    text-align: center;
  }

  .empty p {
    max-width: 36ch;
    margin: 0;
  }

  .empty svg {
    width: 28px;
    height: 28px;
    fill: none;
    stroke: var(--text-muted);
    stroke-width: 1.4;
    stroke-linecap: round;
  }

  .table-wrap {
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
  }

  th,
  td {
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
    text-align: left;
    white-space: nowrap;
  }

  thead th {
    color: var(--text-muted);
    font-size: var(--text-xs);
    font-weight: 500;
  }

  tbody th {
    color: var(--text-secondary);
    font-weight: 400;
  }

  td {
    color: var(--text-primary);
  }

  .num {
    font-variant-numeric: tabular-nums;
    text-align: right;
  }

  .note {
    margin: 8px 0 0;
    color: var(--text-muted);
    font-size: var(--text-xs);
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
