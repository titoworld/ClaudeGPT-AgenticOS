<script lang="ts">
  import { app } from '../lib/app.svelte';
  import { formatMs } from '../lib/format';
  import Icon from './Icon.svelte';

  const conn = app.conn;

  const STATE_TEXT = {
    idle: 'Desconnectat',
    connecting: 'Connectant…',
    open: 'Connectat',
    reconnecting: 'Reconnectant…',
    offline: 'Sense connexió',
    closed: 'Desconnectat',
  } as const;

  const label = $derived(
    conn.status === 'open' && conn.rttMs != null ? formatMs(conn.rttMs) : STATE_TEXT[conn.status],
  );
  const title = $derived(
    conn.status === 'open'
      ? `Connectat en temps real${conn.rttMs != null ? ` · latència ${formatMs(conn.rttMs)}` : ''}`
      : STATE_TEXT[conn.status],
  );
  const retryable = $derived(conn.status === 'reconnecting' || conn.status === 'offline');
</script>

<div class="conn {conn.status}" {title}>
  <span class="dot" aria-hidden="true"></span>
  <span class="label" aria-hidden="true">{label}</span>
  <span class="sr-only" role="status">{STATE_TEXT[conn.status]}</span>
  {#if retryable}
    <button type="button" class="icon-btn small" onclick={() => conn.retryNow()} aria-label="Reintenta ara">
      <Icon name="refresh" size={14} />
    </button>
  {/if}
</div>

<style>
  .conn {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.2rem 0.3rem 0.2rem 0.65rem;
    min-height: 1.9rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: rgb(7 8 13 / 0.4);
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .conn:not(:has(button)) {
    padding-right: 0.7rem;
  }

  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-muted);
  }

  .open .dot {
    background: var(--good);
    box-shadow: 0 0 8px rgb(62 207 142 / 0.9);
  }

  .connecting .dot,
  .reconnecting .dot {
    background: var(--warning);
    animation: pulse-dot 1s ease-in-out infinite;
  }

  .reconnecting,
  .connecting {
    color: #ffd99a;
  }

  .offline .dot {
    background: var(--critical);
  }

  .offline {
    color: #ffb3ba;
  }
</style>
