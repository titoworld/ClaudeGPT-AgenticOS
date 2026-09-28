<script lang="ts">
  // Compact status of one model stream (text + icon, never color alone).
  import type { StreamStatus } from '../lib/turns.svelte';
  import Icon from './Icon.svelte';

  interface Props {
    /** "truncated": finished, but cut off before the end. */
    status: StreamStatus | 'waiting' | 'truncated';
  }

  let { status }: Props = $props();
</script>

<span class="status {status}">
  {#if status === 'streaming'}
    <span class="bars" aria-hidden="true"><i></i><i></i><i></i></span>Escrivint…
  {:else if status === 'waiting'}
    <span class="dot" aria-hidden="true"></span>Pensant…
  {:else if status === 'done'}
    <Icon name="check" size={13} />Fet
  {:else if status === 'failed'}
    <Icon name="alert" size={13} />Error
  {:else if status === 'truncated'}
    <Icon name="alert" size={13} />Incompleta
  {:else}
    <Icon name="x" size={13} />Interromput
  {/if}
</span>

<style>
  .status {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-muted);
    white-space: nowrap;
  }

  .streaming,
  .waiting {
    color: var(--text-secondary);
  }

  .done {
    color: #9ff0c9;
  }

  .failed {
    color: #ffb3ba;
  }

  .truncated {
    color: #ffd99a;
  }

  .bars {
    display: inline-flex;
    align-items: flex-end;
    gap: 2px;
    height: 10px;
  }

  .bars i {
    width: 2px;
    height: 100%;
    border-radius: 1px;
    background: var(--accent-color, var(--accent));
    animation: bar 0.9s ease-in-out infinite;
    transform-origin: bottom;
  }

  .bars i:nth-child(2) {
    animation-delay: 0.15s;
  }

  .bars i:nth-child(3) {
    animation-delay: 0.3s;
  }

  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-color, var(--accent));
    animation: pulse-dot 1.2s ease-in-out infinite;
  }

  @keyframes bar {
    0%,
    100% {
      transform: scaleY(0.35);
    }
    50% {
      transform: scaleY(1);
    }
  }
</style>
