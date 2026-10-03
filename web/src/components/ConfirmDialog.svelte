<script lang="ts">
  import { lightDismiss, syncDialog } from '../lib/dialog';
  import { i18n } from '../lib/i18n/index.svelte';

  interface Props {
    open: boolean;
    title: string;
    message: string;
    /** The confirming button; «Delete» (in the language in force) by default. */
    confirmLabel?: string;
    onConfirm: () => void;
    onClose: () => void;
  }

  let { open, title, message, confirmLabel, onConfirm, onClose }: Props = $props();
  const t = $derived(i18n.m.app.confirm);
  let dialog: HTMLDialogElement | undefined = $state();
  const uid = $props.id();

  $effect(() => syncDialog(dialog, open));
</script>

<dialog
  bind:this={dialog}
  class="confirm"
  aria-labelledby="{uid}-title"
  aria-describedby="{uid}-msg"
  onclose={onClose}
  {@attach lightDismiss}>
  <div class="panel glass">
    <h2 id="{uid}-title">{title}</h2>
    <p id="{uid}-msg">{message}</p>
    <div class="buttons">
      <button type="button" class="btn ghost" onclick={() => dialog?.close()}>{t.cancel}</button>
      <button
        type="button"
        class="btn danger"
        onclick={() => {
          onConfirm();
          dialog?.close();
        }}>{confirmLabel ?? t.delete}</button>
    </div>
  </div>
</dialog>

<style>
  .confirm {
    margin: auto;
    width: min(26rem, calc(100vw - 2rem));
  }

  .panel {
    display: grid;
    gap: 0.8rem;
    padding: 1.3rem 1.3rem 1.1rem;
    border-radius: var(--radius-lg);
  }

  h2 {
    font-size: var(--text-lg);
    font-weight: 650;
  }

  p {
    color: var(--text-secondary);
    font-size: var(--text-sm);
  }

  .buttons {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.3rem;
  }

  .confirm[open] .panel {
    animation: rise-in var(--dur-med) var(--ease-out);
  }
</style>
