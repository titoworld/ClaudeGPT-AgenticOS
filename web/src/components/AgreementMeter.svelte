<script lang="ts">
  import { AGENT_LABEL } from '../lib/format';
  import type { Agent } from '../lib/protocol';

  interface Props {
    agent: Agent;
    value: number | null;
    threshold: number;
  }

  let { agent, value, threshold }: Props = $props();
  const pct = $derived(value == null ? 0 : Math.max(0, Math.min(100, value)));
  const reached = $derived(value != null && value >= threshold);
</script>

<div
  class="meter {agent}"
  class:reached
  role="meter"
  aria-label="Acord de {AGENT_LABEL[agent]}"
  aria-valuemin={0}
  aria-valuemax={100}
  aria-valuenow={value ?? undefined}
  aria-valuetext={value == null ? 'Pendent' : `${value} de 100 (llindar ${threshold})`}>
  <span class="label">Acord</span>
  <span class="track">
    <span class="fill" style:width="{pct}%"></span>
    <span class="mark" style:left="{threshold}%" title="Llindar de consens: {threshold}"></span>
  </span>
  <span class="value">{value ?? '—'}</span>
</div>

<style>
  .meter {
    --c: var(--claude);
    display: grid;
    grid-template-columns: auto 1fr 2.2em;
    align-items: center;
    gap: 0.5rem;
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .chatgpt {
    --c: var(--chatgpt);
  }

  .track {
    position: relative;
    height: 6px;
    border-radius: 999px;
    background: rgb(255 255 255 / 0.08);
  }

  .fill {
    position: absolute;
    inset: 0 auto 0 0;
    border-radius: inherit;
    background: linear-gradient(90deg, color-mix(in srgb, var(--c) 55%, transparent), var(--c));
    transition: width 700ms var(--ease-out);
  }

  .mark {
    position: absolute;
    top: -3px;
    bottom: -3px;
    width: 2px;
    margin-left: -1px;
    border-radius: 1px;
    background: rgb(255 255 255 / 0.55);
  }

  .value {
    text-align: right;
    font-weight: 700;
    color: var(--text-secondary);
  }

  .reached .value {
    color: #9ff0c9;
  }
</style>
