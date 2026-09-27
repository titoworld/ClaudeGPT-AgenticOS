<script lang="ts">
  /**
   * Chart tooltip. Values lead, series names follow, keyed by a short line in
   * the series colour. Purely visual (aria-hidden): the focused mark carries the
   * same text in its aria-label and every value is also in the table view.
   */
  import { clampBox } from './scale';
  import type { TooltipRow } from './types';

  interface Props {
    title: string;
    rows: TooltipRow[];
    /** Anchor in container pixels: the tooltip sits beside it, never on top. */
    x: number;
    y: number;
    /** Half-width of the hovered mark, so the box clears it. */
    clearance?: number;
    containerWidth: number;
  }

  let { title, rows, x, y, clearance = 0, containerWidth }: Props = $props();
  let w = $state(0);

  const left = $derived.by(() => {
    const gap = 10;
    const right = x + clearance + gap;
    if (right + w <= containerWidth - 4) return right;
    const leftSide = x - clearance - gap - w;
    if (leftSide >= 4) return leftSide;
    return clampBox(x, w, containerWidth);
  });
</script>

<div class="tip" style:left="{left}px" style:top="{y}px" bind:offsetWidth={w} aria-hidden="true">
  <p class="title">{title}</p>
  <ul>
    {#each rows as row (row.key)}
      <li>
        {#if row.color}<span class="key" style:background={row.color}></span>{:else}<span class="key blank"></span>{/if}
        <strong>{row.value}</strong>
        <span class="label">{row.label}</span>
      </li>
    {/each}
  </ul>
</div>

<style>
  .tip {
    position: absolute;
    z-index: 2;
    min-width: 150px;
    max-width: 260px;
    padding: 8px 10px;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    background: var(--glass-strong);
    backdrop-filter: blur(10px);
    box-shadow: var(--shadow);
    pointer-events: none;
    font-size: var(--text-xs);
    line-height: 1.35;
  }

  .title {
    margin: 0 0 6px;
    color: var(--text-secondary);
  }

  ul {
    display: grid;
    gap: 3px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  li {
    display: grid;
    grid-template-columns: 12px auto 1fr;
    gap: 8px;
    align-items: center;
    white-space: nowrap;
  }

  .key {
    width: 12px;
    height: 2px;
    border-radius: 1px;
  }

  .key.blank {
    background: var(--border-strong);
  }

  strong {
    color: var(--text-primary);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }

  .label {
    color: var(--text-secondary);
  }
</style>
