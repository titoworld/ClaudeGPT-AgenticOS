<script lang="ts">
  import { toasts } from '../lib/toasts.svelte';
  import Icon from './Icon.svelte';
</script>

<div class="toasts" aria-live="polite" aria-atomic="false">
  {#each toasts.items as toast (toast.id)}
    <div class="toast glass {toast.kind}" role={toast.kind === 'error' ? 'alert' : 'status'}>
      <Icon name={toast.kind === 'error' ? 'alert' : toast.kind === 'success' ? 'check' : 'info'} size={16} />
      <span>{toast.text}</span>
      <button type="button" class="icon-btn small" onclick={() => toasts.dismiss(toast.id)} aria-label="Tanca l'avís">
        <Icon name="x" size={14} />
      </button>
    </div>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    right: 1rem;
    bottom: 1rem;
    z-index: 100;
    display: grid;
    gap: 0.5rem;
    width: min(24rem, calc(100vw - 2rem));
    pointer-events: none;
  }

  .toast {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.6rem 0.5rem 0.6rem 0.85rem;
    border-radius: var(--radius-md);
    font-size: var(--text-sm);
    pointer-events: auto;
    animation: rise-in var(--dur-med) var(--ease-out);
  }

  .toast span {
    flex: 1;
  }

  .success :global(.icon:first-child) {
    color: var(--good);
  }

  .error {
    border-color: rgb(255 93 108 / 0.45);
  }

  .error :global(.icon:first-child) {
    color: var(--critical);
  }

  .info :global(.icon:first-child) {
    color: var(--accent);
  }
</style>
