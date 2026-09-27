<script lang="ts">
  import { formatInt, formatMs } from '../lib/format';
  import type { StreamView } from '../lib/turns.svelte';
  import Icon from './Icon.svelte';

  interface Props {
    stream: StreamView;
  }

  let { stream }: Props = $props();
  const usage = $derived(stream.usage);
</script>

<footer class="meta">
  {#if stream.model}
    <span class="model" title="Model">{stream.model}</span>
  {/if}
  {#if usage}
    <span
      class="item"
      title="{formatInt(usage.input_tokens)} tokens d'entrada · {formatInt(usage.output_tokens)} de sortida">
      {formatInt(usage.input_tokens)} → {formatInt(usage.output_tokens)} tokens
    </span>
    {#if usage.cache_read_tokens > 0}
      <span class="item" title="Tokens d'entrada reaprofitats de la memòria cau del proveïdor">
        {formatInt(usage.cache_read_tokens)} en memòria cau
      </span>
    {/if}
  {/if}
  {#if stream.latencyMs != null}
    <span class="item" title="Temps total de resposta"><Icon name="clock" size={12} />{formatMs(stream.latencyMs)}</span>
  {/if}
  {#if stream.ttftMs != null}
    <span class="item" title="Temps fins al primer token">TTFT {formatMs(stream.ttftMs)}</span>
  {/if}
  {#if stream.cached}
    <span class="chip cache"><Icon name="cache" size={12} />des de la memòria cau</span>
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
</style>
