<script lang="ts">
  import { savingsEur } from '../lib/costs';
  import { formatEur, formatInt } from '../lib/format';
  import { i18n } from '../lib/i18n/index.svelte';
  import type { Savings, SavingKind } from '../lib/protocol';
  import { formatK } from '../lib/text';
  import Icon from './Icon.svelte';

  interface Props {
    savings: Savings;
    /** Euros per dollar, to show the value of the saved tokens. */
    eurPerUsd: number;
  }

  let { savings, eurPerUsd }: Props = $props();
  const value = $derived(savingsEur(savings, eurPerUsd));
  let open = $state(false);
  const id = $props.id();

  const ROWS: readonly SavingKind[] = ['cache', 'compaction', 'early_stop', 'unchanged'];
  const t = $derived(i18n.m.turn.savings);
</script>

<span class="wrap" class:open>
  <button
    type="button"
    class="chip good savings"
    aria-describedby={id}
    aria-expanded={open}
    onclick={() => (open = !open)}
    onblur={() => (open = false)}>
    <Icon name="bolt" size={12} />{t.saved(formatK(savings.total))}{#if value != null}<span
        class="value">≈ {formatEur(value)}</span
      >{/if}
  </button>
  <span class="tip" role="tooltip" {id}>
    <strong>{t.title}</strong>
    {#each ROWS as kind (kind)}
      <span class="row {kind}">
        <i aria-hidden="true"></i>{t.kinds[kind]}<b>{formatInt(savings[kind])}</b>
      </span>
    {/each}
    <span class="row total">{t.total}<b>{formatInt(savings.total)}</b></span>
    {#if value != null}
      <span class="row money">{t.value}<b>≈ {formatEur(value)}</b></span>
      <small>{t.valueNote}</small>
    {/if}
  </span>
</span>

<style>
  .wrap {
    position: relative;
    display: inline-flex;
  }

  .savings {
    cursor: help;
    font: inherit;
    font-size: var(--text-xs);
    font-weight: 650;
  }

  .tip {
    position: absolute;
    bottom: calc(100% + 8px);
    right: 0;
    z-index: 20;
    display: grid;
    gap: 0.3rem;
    min-width: 16.5rem;
    padding: 0.75rem 0.85rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-strong);
    background: rgb(19 22 35 / 0.97);
    box-shadow: var(--shadow);
    font-size: var(--text-xs);
    color: var(--text-secondary);
    opacity: 0;
    visibility: hidden;
    transform: translateY(4px);
    transition:
      opacity var(--dur-fast) var(--ease-out),
      transform var(--dur-fast) var(--ease-out),
      visibility var(--dur-fast);
    pointer-events: none;
  }

  .wrap:hover .tip,
  .wrap:focus-within .tip,
  .open .tip {
    opacity: 1;
    visibility: visible;
    transform: none;
  }

  strong {
    color: var(--text-primary);
    font-weight: 650;
    margin-bottom: 0.15rem;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 0.45rem;
  }

  .row b {
    margin-left: auto;
    font-variant-numeric: tabular-nums;
    color: var(--text-primary);
  }

  .row i {
    width: 8px;
    height: 8px;
    border-radius: 2px;
  }

  .cache i {
    background: var(--saving-cache);
  }

  .compaction i {
    background: var(--saving-compaction);
  }

  .early_stop i {
    background: var(--saving-early-stop);
  }

  .unchanged i {
    background: var(--saving-unchanged);
  }

  .value {
    margin-left: 0.2rem;
    padding-left: 0.45rem;
    border-left: 1px solid rgb(62 207 142 / 0.35);
  }

  .money b {
    color: #9ff0c9;
  }

  small {
    color: var(--text-muted);
  }

  .total {
    padding-top: 0.3rem;
    margin-top: 0.1rem;
    border-top: 1px solid var(--border);
    font-weight: 650;
  }
</style>
