<script lang="ts">
  // The preview of an attachment, in the app (lib/viewer.svelte.ts): an image in a
  // lightbox, a PDF page by page, a text file as plain text; each with a download
  // button (PDFs and text files are served as downloads, never as pages of the app).
  import { contentUrl, formatBytes, pagesLabel, typeLabel } from '../lib/attachments';
  import { lightDismiss, syncDialog } from '../lib/dialog';
  import { i18n } from '../lib/i18n/index.svelte';
  import { viewer } from '../lib/viewer.svelte';
  import Icon from './Icon.svelte';
  import PdfPages from './PdfPages.svelte';
  import PlainText from './PlainText.svelte';
  import TextPreview from './TextPreview.svelte';

  const uid = $props.id();
  let dialog: HTMLDialogElement | undefined = $state();

  const current = $derived(viewer.current);
  const meta = $derived.by(() => {
    const a = current;
    if (!a) return '';
    const parts = [typeLabel(a.kind, a.mime, a.name)];
    if (a.pages) parts.push(pagesLabel(a.pages));
    if (a.kind === 'image' && a.width && a.height) parts.push(`${a.width} × ${a.height} px`);
    parts.push(formatBytes(a.size));
    return parts.join(' · ');
  });

  $effect(() => syncDialog(dialog, current !== null));
</script>

<dialog
  bind:this={dialog}
  class="viewer"
  class:image={current?.kind === 'image'}
  aria-labelledby="{uid}-title"
  onclose={() => viewer.close()}
  {@attach lightDismiss}>
  {#if current}
    <div class="panel glass">
      <header>
        <div class="title">
          <h2 id="{uid}-title"><PlainText text={current.name} /></h2>
          <p class="meta">{meta}</p>
        </div>
        <a class="btn ghost download" href={contentUrl(current.id)} download={current.name}>
          <Icon name="download" size={15} /><span class="label">{i18n.m.attachments.viewer.download}</span>
        </a>
        <button type="button" class="icon-btn" aria-label={i18n.m.attachments.viewer.close} onclick={() => dialog?.close()}>
          <Icon name="x" />
        </button>
      </header>
      <div class="content">
        {#key current.id}
          {#if current.kind === 'image'}
            <img src={contentUrl(current.id)} alt={current.name} />
          {:else if current.kind === 'pdf'}
            <PdfPages attachment={current} />
          {:else}
            <TextPreview attachment={current} />
          {/if}
        {/key}
      </div>
    </div>
  {/if}
</dialog>

<style>
  .viewer {
    margin: auto;
    width: min(60rem, calc(100vw - 1.5rem));
    height: min(92dvh, 64rem);
  }

  /* As tall as the image (a modal dialog is fixed to the viewport: auto would fill it). */
  .viewer.image {
    width: fit-content;
    min-width: min(24rem, calc(100vw - 1.5rem));
    max-width: calc(100vw - 1.5rem);
    height: fit-content;
    max-height: 94dvh;
  }

  .panel {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    height: 100%;
    max-height: inherit;
    overflow: hidden;
    border-radius: var(--radius-lg);
    background: var(--glass-strong);
  }

  .viewer[open] .panel {
    animation: rise-in var(--dur-med) var(--ease-out);
  }

  header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.7rem 0.7rem 0.7rem 1.1rem;
    border-bottom: 1px solid var(--border);
  }

  .title {
    flex: 1;
    min-width: 0;
  }

  h2 {
    overflow: hidden;
    font-size: var(--text-md);
    font-weight: 650;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .meta {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .download {
    flex: none;
    text-decoration: none;
  }

  .content {
    min-height: 0;
    overflow: hidden;
  }

  img {
    display: block;
    max-width: 100%;
    max-height: calc(94dvh - 4.5rem);
    margin: 0 auto;
    object-fit: contain;
  }

  @media (max-width: 560px) {
    .viewer:not(.image) {
      width: 100vw;
      height: 100dvh;
      max-height: 100dvh;
    }

    .viewer:not(.image) .panel {
      border-radius: 0;
    }

    .download .label {
      display: none;
    }
  }
</style>
