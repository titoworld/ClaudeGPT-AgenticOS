<script lang="ts">
  // A text file in the viewer: always plain text (never rendered, not even HTML or
  // Markdown), with its hidden characters revealed.
  import { api } from '../lib/api';
  import type { Attachment } from '../lib/protocol';
  import Icon from './Icon.svelte';
  import PlainText from './PlainText.svelte';

  interface Props {
    attachment: Attachment;
  }

  let { attachment }: Props = $props();

  let text: string | null = $state(null);
  let failed = $state(false);

  $effect(() => {
    const id = attachment.id;
    const controller = new AbortController();
    text = null;
    failed = false;
    api.attachmentText(id, { signal: controller.signal }).then(
      (content) => {
        text = content;
      },
      () => {
        if (!controller.signal.aborted) failed = true;
      },
    );
    return () => controller.abort();
  });
</script>

{#if failed}
  <p class="state failed" role="alert"><Icon name="alert" size={15} />No s'ha pogut carregar el fitxer. Pots descarregar-lo.</p>
{:else if text === null}
  <p class="state"><span class="spinner" aria-hidden="true"></span>Carregant…</p>
{:else}
  <pre class="text"><PlainText {text} /></pre>
{/if}

<style>
  .text {
    height: 100%;
    margin: 0;
    padding: 0.9rem 1rem;
    overflow: auto;
    overscroll-behavior: contain;
    font-family: var(--font-mono);
    font-size: 0.8125rem;
    line-height: 1.55;
    color: var(--text-primary);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    tab-size: 4;
  }

  .state {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 1.5rem 1rem;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .failed {
    color: #ffc9ce;
  }

  .spinner {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.15);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }
</style>
