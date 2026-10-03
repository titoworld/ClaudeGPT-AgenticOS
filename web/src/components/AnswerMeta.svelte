<script lang="ts">
  import { app } from '../lib/app.svelte';
  import { streamCost } from '../lib/costs';
  import { formatInt, formatMs } from '../lib/format';
  import { i18n } from '../lib/i18n/index.svelte';
  import type { StreamView } from '../lib/turns.svelte';
  import Icon from './Icon.svelte';
  import PdfReadingBadge from './PdfReadingBadge.svelte';

  interface Props {
    stream: StreamView;
  }

  let { stream }: Props = $props();
  const usage = $derived(stream.usage);
  const cost = $derived(streamCost(stream, app.eurPerUsd));
  const t = $derived(i18n.m.turn);
</script>

<footer class="meta">
  {#if stream.model}
    <span class="model" title={t.meta.model}>{stream.model}</span>
  {/if}
  {#if usage}
    <span class="item" title={t.meta.tokensTitle(formatInt(usage.input_tokens), formatInt(usage.output_tokens))}>
      {t.meta.tokens(formatInt(usage.input_tokens), formatInt(usage.output_tokens))}
    </span>
    {#if usage.cache_read_tokens > 0}
      <span class="item" title={t.meta.cacheReadTitle}>
        {t.tokens.cacheRead(formatInt(usage.cache_read_tokens))}
      </span>
    {/if}
    {#if usage.cache_write_tokens > 0}
      <span class="item" title={t.meta.cacheWriteTitle}>
        {t.tokens.cacheWrite(formatInt(usage.cache_write_tokens))}
      </span>
    {/if}
  {/if}
  {#if cost}
    <span class="item cost" class:equivalent={stream.costBasis === 'equivalent'} title={cost.title}>
      {cost.text}<span class="sr-only"> ({cost.title})</span>
    </span>
  {/if}
  {#if stream.latencyMs != null}
    <span class="item" title={t.meta.latency}><Icon name="clock" size={12} />{formatMs(stream.latencyMs)}</span>
  {/if}
  {#if stream.ttftMs != null}
    <span class="item" title={t.meta.ttft}>TTFT {formatMs(stream.ttftMs)}</span>
  {/if}
  {#if stream.cached}
    <span class="chip cache"><Icon name="cache" size={12} />{t.meta.cached}</span>
  {/if}
  {#if stream.pdfReading.length}
    <PdfReadingBadge readings={stream.pdfReading} />
  {/if}
</footer>

<style>
  .meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem 0.9rem;
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .model {
    font-family: var(--font-mono);
    color: var(--text-secondary);
  }

  .item {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
  }

  .cost {
    color: var(--text-secondary);
    cursor: help;
  }

  /* Included in the subscription: a value, not money spent. */
  .cost.equivalent {
    text-decoration: underline dotted rgb(255 255 255 / 0.3);
    text-underline-offset: 3px;
  }
</style>
