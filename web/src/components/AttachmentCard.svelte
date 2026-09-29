<script lang="ts">
  // An attachment's card, like claude.ai's: its thumbnail (an image's, a PDF's first
  // page), a text file's first lines or an icon, then its name, type, size, pages and
  // estimated tokens, and its state. Names and lines are plain text, with their hidden
  // characters revealed (PlainText): a file name cannot disguise itself. A PDF the server
  // analysed also warns of its pages without text, with unreadable text and with text
  // that may be hidden (lib/pdf-pages.ts).
  import { loadLines } from '../lib/attachment-lines';
  import { formatBytes, pagesLabel, typeLabel, type AttachmentView } from '../lib/attachments';
  import { pdfNoteLines, type PdfNoteKind } from '../lib/pdf-pages';
  import { formatK } from '../lib/text';
  import Icon, { type IconName } from './Icon.svelte';
  import PlainText from './PlainText.svelte';

  interface Props {
    view: AttachmentView;
    /** Opens its preview (a ready attachment only). */
    onopen?: () => void;
    /** Takes it out (the composer). */
    onremove?: () => void;
    /** Uploads it again (an upload that failed for a reason a retry can fix). */
    onretry?: () => void;
  }

  let { view, onopen, onremove, onretry }: Props = $props();

  /** A thumbnail URL that did not load: the icon shows instead. */
  let broken: string | null = $state(null);
  /** A stored text file's first lines, asked of the server. */
  let fetched: string | null = $state(null);

  const type = $derived(typeLabel(view.kind, view.mime, view.name));
  const icon = $derived(view.kind === 'image' ? 'image' : view.kind === 'text' ? 'file-text' : 'file');
  const thumbnail = $derived(view.thumbnail && view.thumbnail !== broken ? view.thumbnail : null);
  const lines = $derived(view.lines ?? fetched);
  const meta = $derived([view.pages ? pagesLabel(view.pages) : null, formatBytes(view.size)].filter(Boolean).join(' · '));
  const openable = $derived(!!onopen && view.status === 'ready' && view.id !== null);
  const notes = $derived(view.status === 'ready' ? pdfNoteLines(view.pdfNotes) : []);

  const NOTE_ICON: Record<PdfNoteKind, IconName> = { no_text: 'image', garbled: 'info', hidden: 'alert' };

  $effect(() => {
    const id = view.id;
    if (view.kind !== 'text' || view.lines !== null || id === null) return;
    let current = true;
    void loadLines(id).then((text) => {
      if (current) fetched = text;
    });
    return () => {
      current = false;
    };
  });
</script>

{#snippet content()}
  <span class="thumb {view.kind ?? 'file'}" aria-hidden="true">
    {#if thumbnail}
      <img src={thumbnail} alt="" loading="lazy" decoding="async" onerror={() => (broken = thumbnail)} />
    {:else if view.kind === 'text' && lines}
      <span class="lines"><PlainText text={lines} /></span>
    {:else}
      <Icon name={icon} size={26} />
    {/if}
    <span class="badge">{type}</span>
    {#if view.status === 'uploading'}
      <span class="busy"><span class="spinner"></span></span>
    {/if}
  </span>
  <span class="info">
    <span class="name" title={view.name}><PlainText text={view.name} /></span>
    <span class="meta"><span class="sr-only">{type}, </span>{meta}</span>
    {#if view.status === 'uploading'}
      <span class="meta state">Pujant…</span>
    {:else if view.status === 'error'}
      <span class="error-msg" role="alert">{view.error}</span>
    {:else if view.tokens}
      <span class="meta">≈ {formatK(view.tokens)} tokens</span>
    {/if}
    {#each notes as note (note.kind)}
      <span class="pdf-note {note.kind}" class:warning={note.kind === 'hidden'} title={note.description}>
        <Icon name={NOTE_ICON[note.kind]} size={11} />
        <span class="note-text"
          >{note.label}: pàg.&nbsp;{#each note.pages as part, i (i)}{#if i > 0}{', '}{/if}<span class="page-part">{part}</span
            >{/each}</span
        > <span class="sr-only">({note.description})</span>
      </span>
    {/each}
  </span>
{/snippet}

<div class="att" class:error={view.status === 'error'} class:uploading={view.status === 'uploading'}>
  {#if openable}
    <button type="button" class="body" title="Obre la vista prèvia" onclick={onopen}>{@render content()}</button>
  {:else}
    <div class="body">{@render content()}</div>
  {/if}
  {#if view.status === 'error' && view.retryable && onretry}
    <button type="button" class="retry" onclick={onretry}>Torna-ho a provar</button>
  {/if}
  {#if onremove}
    <button type="button" class="remove" aria-label="Treu {view.name}" title="Treu l'adjunt" onclick={onremove}>
      <Icon name="x" size={12} />
    </button>
  {/if}
</div>

<style>
  .att {
    position: relative;
    display: grid;
    gap: 0.3rem;
    width: 8.75rem;
    flex: none;
  }

  .body {
    display: grid;
    grid-template-rows: 5.25rem auto;
    width: 100%;
    padding: 0;
    overflow: hidden;
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-md);
    background: rgb(10 12 20 / 0.72);
    color: inherit;
    font: inherit;
    text-align: left;
  }

  button.body {
    cursor: zoom-in;
    transition:
      border-color var(--dur-fast) var(--ease-out),
      transform var(--dur-fast) var(--ease-out);
  }

  button.body:hover {
    border-color: rgb(139 156 255 / 0.55);
  }

  button.body:active {
    transform: translateY(1px);
  }

  .error .body {
    border-color: rgb(255 93 108 / 0.55);
  }

  .thumb {
    position: relative;
    display: grid;
    place-items: center;
    overflow: hidden;
    background: rgb(255 255 255 / 0.04);
    color: var(--text-muted);
  }

  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  /* A page reads from the top, on paper. */
  .thumb.pdf img {
    object-position: top;
    background: #fff;
  }

  .lines {
    align-self: stretch;
    justify-self: stretch;
    padding: 0.4rem 0.5rem;
    overflow: hidden;
    font-family: var(--font-mono);
    font-size: 0.56rem;
    line-height: 1.4;
    color: var(--text-secondary);
    white-space: pre;
    mask-image: linear-gradient(to bottom, #000 55%, transparent);
  }

  .badge {
    position: absolute;
    left: 0.35rem;
    bottom: 0.35rem;
    padding: 0 0.35rem;
    border-radius: 4px;
    background: rgb(4 5 9 / 0.72);
    color: #fff;
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    line-height: 1.5;
  }

  .busy {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    background: rgb(7 8 13 / 0.55);
  }

  .spinner {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.2);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }

  .info {
    display: grid;
    gap: 0.05rem;
    min-width: 0;
    padding: 0.35rem 0.5rem 0.45rem;
  }

  .name {
    overflow: hidden;
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .meta {
    overflow: hidden;
    font-size: 0.68rem;
    color: var(--text-muted);
    white-space: nowrap;
    text-overflow: ellipsis;
    font-variant-numeric: tabular-nums;
  }

  .state {
    color: var(--accent);
  }

  /* A warning of the server's analysis of a PDF's pages: it wraps, a page list is information. */
  .pdf-note {
    display: flex;
    align-items: flex-start;
    gap: 0.25rem;
    margin-top: 0.1rem;
    font-size: 0.68rem;
    line-height: 1.35;
    color: var(--text-secondary);
    overflow-wrap: anywhere;
  }

  .pdf-note :global(.icon) {
    margin-top: 0.1rem;
    color: var(--text-muted);
  }

  .page-part {
    white-space: nowrap;
  }

  .pdf-note.warning {
    color: #ffd99a;
  }

  .pdf-note.warning :global(.icon) {
    color: var(--warning);
  }

  .error-msg {
    font-size: 0.68rem;
    line-height: 1.35;
    color: #ff9aa4;
    overflow-wrap: anywhere;
  }

  .retry {
    justify-self: start;
    padding: 0.1rem 0.4rem;
    border: none;
    border-radius: 6px;
    background: none;
    color: var(--accent);
    font-size: var(--text-xs);
    font-weight: 600;
  }

  .retry:hover {
    background: rgb(139 156 255 / 0.1);
  }

  .remove {
    position: absolute;
    top: 0.3rem;
    right: 0.3rem;
    display: grid;
    place-items: center;
    width: 1.5rem;
    height: 1.5rem;
    padding: 0;
    border: 1px solid rgb(255 255 255 / 0.25);
    border-radius: 50%;
    background: rgb(4 5 9 / 0.78);
    color: #fff;
  }

  .remove:hover {
    background: rgb(255 93 108 / 0.85);
    border-color: transparent;
  }
</style>
