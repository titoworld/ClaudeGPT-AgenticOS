<script lang="ts">
  import { spendLine } from '../lib/costs';
  import { i18n } from '../lib/i18n/index.svelte';
  import { limitLevel, resetText, shortUntil } from '../lib/limits';
  import Icon from './Icon.svelte';
  import type { MonthSpend, ProviderStatus } from '../lib/protocol';
  import { PROVIDER_MODE_LABEL } from '../lib/text';
  import AgentLabel from './AgentLabel.svelte';

  interface Props {
    provider: ProviderStatus;
    /** Month-to-date spend of all agents (null until loaded). */
    spend?: MonthSpend | null;
  }

  let { provider, spend = null }: Props = $props();
  const t = $derived(i18n.m.app.provider);
  const month = $derived.by(() => {
    const own = spend?.by_agent[provider.agent];
    return spend && own ? spendLine(provider.mode, own, spend.fx, spend.month) : null;
  });
  const uid = $props.id();
  let open = $state(false);

  const limits = $derived(provider.limits.filter((l) => l.used_percent != null || l.resets_at));
</script>

<div class="provider {provider.agent}">
  <button
    type="button"
    class="head"
    aria-describedby="{uid}-detail"
    aria-expanded={open}
    onclick={() => (open = !open)}
    onblur={() => (open = false)}>
    <AgentLabel agent={provider.agent} size={16} />
    <span class="avail" class:on={provider.available} title={provider.available ? t.available : t.unavailable}>
      <i aria-hidden="true"></i><span class="sr-only">{provider.available ? t.available : t.unavailable}</span>
    </span>
    <span class="mode chip">{PROVIDER_MODE_LABEL[provider.mode]}</span>
  </button>
  <div class="model" title={provider.model}>{provider.model || '—'}</div>

  {#each limits as limit (limit.window)}
    {@const used = limit.used_percent}
    {@const lvl = limitLevel(limit.status, used)}
    <div class="limit {lvl}">
      <span class="window">{limit.window}</span>
      <span
        class="bar"
        role="meter"
        aria-label={t.windowUsage(limit.window)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={used ?? undefined}
        aria-valuetext={used == null ? t.unknown : t.percent(Math.round(used))}>
        <span class="fill" style:width="{Math.min(100, used ?? 0)}%"></span>
      </span>
      <span class="pct">{used == null ? '—' : t.percent(Math.round(used))}</span>
      {#if limit.resets_at}
        <span class="reset" title={resetText(limit.resets_at)}>
          <Icon name="refresh" size={10} /><span aria-hidden="true">{shortUntil(limit.resets_at)}</span>
          <span class="sr-only">{resetText(limit.resets_at)}</span>
        </span>
      {:else}
        <span></span>
      {/if}
    </div>
  {/each}

  {#if month}
    {@const ratio = month.ratio}
    <div class="month {month.kind} {month.level}" title={month.title}>
      {#if ratio != null}
        <div class="limit">
          <span class="window">{t.month}</span>
          <span
            class="bar"
            role="meter"
            aria-label={month.kind === 'budget' ? t.budgetUsed : t.planValueUsed}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.min(100, Math.round(ratio * 100))}
            aria-valuetext={month.title}>
            <span class="fill" style:width="{Math.min(100, ratio * 100)}%"></span>
          </span>
          <span class="pct">{month.percent}</span>
          <span></span>
        </div>
      {/if}
      <div class="caption" aria-hidden={ratio != null}>
        {#if ratio == null}<span class="window">{t.month}</span>{/if}
        <span>{month.label} <b>{month.amount}</b></span>
      </div>
    </div>
  {/if}

  <div class="tip" class:open role="tooltip" id="{uid}-detail">{provider.detail || t.noDetails}</div>
</div>

<style>
  .provider {
    position: relative;
    display: grid;
    gap: 0.3rem;
    padding: 0.55rem 0.65rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
    background: rgb(255 255 255 / 0.025);
  }

  .head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0;
    border: none;
    background: none;
    text-align: left;
    font-size: var(--text-sm);
    cursor: help;
  }

  .avail {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    margin-left: auto;
  }

  .avail i {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--critical);
    box-shadow: 0 0 8px rgb(255 93 108 / 0.8);
  }

  .avail.on i {
    background: var(--good);
    box-shadow: 0 0 8px rgb(62 207 142 / 0.8);
  }

  .mode {
    font-size: 0.68rem;
    padding: 0 0.45rem;
  }

  .model {
    overflow: hidden;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    color: var(--text-muted);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .limit {
    display: grid;
    grid-template-columns: 1.6rem 1fr 2.4rem 3.1rem;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.7rem;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .pct {
    text-align: right;
  }

  .bar {
    position: relative;
    height: 5px;
    border-radius: 999px;
    background: rgb(255 255 255 / 0.08);
    overflow: hidden;
  }

  .fill {
    position: absolute;
    inset: 0 auto 0 0;
    border-radius: inherit;
    background: var(--good);
    transition: width 600ms var(--ease-out);
  }

  .warn .fill {
    background: var(--warning);
  }

  .bad .fill {
    background: var(--critical);
  }

  .warn .pct {
    color: #ffd99a;
  }

  .bad .pct {
    color: #ffb3ba;
  }

  .month {
    display: grid;
    gap: 0.15rem;
    cursor: help;
  }

  /* Amounts under the bar, aligned with it (or next to "mes" when there is no bar). */
  .caption {
    display: grid;
    grid-template-columns: 1.6rem minmax(0, 1fr);
    gap: 0.4rem;
    font-size: 0.7rem;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .caption > span:last-child {
    grid-column: 2;
  }

  .caption b {
    font-weight: 550;
    color: var(--text-secondary);
  }

  .month.plan .fill {
    background: var(--accent);
  }

  .month.warn :is(.pct, b) {
    color: #ffd99a;
  }

  .month.bad :is(.pct, b) {
    color: #ffb3ba;
  }

  .reset {
    display: inline-flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.2rem;
    white-space: nowrap;
  }

  .tip {
    position: absolute;
    left: 0;
    right: 0;
    bottom: calc(100% + 6px);
    z-index: 10;
    padding: 0.6rem 0.7rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-strong);
    background: var(--glass-strong);
    box-shadow: var(--shadow);
    font-size: var(--text-xs);
    color: var(--text-secondary);
    opacity: 0;
    visibility: hidden;
    transition:
      opacity var(--dur-fast) var(--ease-out),
      visibility var(--dur-fast);
    pointer-events: none;
  }

  .provider:hover .tip,
  .provider:focus-within .tip,
  .tip.open {
    opacity: 1;
    visibility: visible;
  }
</style>
