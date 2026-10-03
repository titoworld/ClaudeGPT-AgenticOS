<script lang="ts">
  // A PDF in the viewer, page by page, drawn by PDF.js (loaded only now: lib/load-pdf.ts).
  import { contentUrl } from '../lib/attachments';
  import { i18n } from '../lib/i18n/index.svelte';
  import { loadPdf } from '../lib/load-pdf';
  import type { PdfDocument } from '../lib/pdf';
  import type { Attachment } from '../lib/protocol';
  import Icon from './Icon.svelte';

  interface Props {
    attachment: Attachment;
  }

  let { attachment }: Props = $props();

  /** The widest a page is drawn, in CSS pixels. */
  const MAX_WIDTH = 1100;
  /** Where the frame's width is unknown (not laid out yet). */
  const DEFAULT_WIDTH = 800;

  let doc: PdfDocument | null = $state.raw(null);
  let status: 'loading' | 'ready' | 'error' = $state('loading');
  let page = $state(1);
  let width = $state(0);
  let canvas: HTMLCanvasElement | undefined = $state();
  let frame: HTMLDivElement | undefined = $state();

  const pages = $derived.by(() => doc?.pages ?? attachment.pages ?? 0);
  const t = $derived(i18n.m.attachments.pdf);

  // Opens the document; a closed viewer (or another attachment) closes it.
  $effect(() => {
    const url = contentUrl(attachment.id);
    let open = true;
    let opened: PdfDocument | null = null;
    void (async () => {
      try {
        const { openPdf } = await loadPdf();
        opened = await openPdf({ url });
        if (!open) return void opened.destroy();
        doc = opened;
        status = 'ready';
      } catch {
        if (open) status = 'error';
      }
    })();
    return () => {
      open = false;
      void opened?.destroy();
    };
  });

  // The frame's width, followed as it changes (a phone turned, a window resized).
  $effect(() => {
    const el = frame;
    if (!el) return;
    width = el.clientWidth;
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (Math.abs(el.clientWidth - width) > 16) width = el.clientWidth;
    });
    observer.observe(el);
    return () => observer.disconnect();
  });

  // Draws the current page.
  $effect(() => {
    const [d, target, number] = [doc, canvas, page];
    if (!d || !target) return;
    const cssWidth = Math.min(width || DEFAULT_WIDTH, MAX_WIDTH);
    void d.render(number, target, cssWidth).catch(() => {
      status = 'error';
    });
  });

  function go(number: number): void {
    if (number < 1 || number > pages || number === page) return;
    page = number;
    frame?.scrollTo?.({ top: 0 });
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === 'ArrowRight' || e.key === 'PageDown') go(page + 1);
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp') go(page - 1);
    else return;
    e.preventDefault();
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="pdf">
  <div class="frame" bind:this={frame}>
    {#if status === 'error'}
      <p class="failed" role="alert">{t.failed}</p>
    {:else}
      {#if status === 'loading'}
        <p class="loading"><span class="spinner" aria-hidden="true"></span>{t.loading}</p>
      {/if}
      <canvas bind:this={canvas} class:hidden={status !== 'ready'}></canvas>
    {/if}
  </div>
  {#if status !== 'error' && pages > 0}
    <nav class="pager" aria-label={t.pages}>
      <button type="button" class="icon-btn" aria-label={t.previous} disabled={page <= 1} onclick={() => go(page - 1)}>
        <Icon name="chevron-left" />
      </button>
      <span aria-live="polite">{t.page(page, pages)}</span>
      <button
        type="button"
        class="icon-btn"
        aria-label={t.next}
        disabled={page >= pages}
        onclick={() => go(page + 1)}>
        <Icon name="chevron-right" />
      </button>
    </nav>
  {/if}
</div>

<style>
  .pdf {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto;
    height: 100%;
    min-height: 0;
  }

  .frame {
    display: grid;
    justify-items: center;
    align-content: start;
    min-height: 0;
    overflow: auto;
    padding: 0.75rem;
    overscroll-behavior: contain;
  }

  canvas {
    display: block;
    max-width: 100%;
    height: auto;
    background: #fff;
    border-radius: 4px;
    box-shadow: 0 6px 28px rgb(0 0 0 / 0.5);
  }

  canvas.hidden {
    visibility: hidden;
  }

  .loading,
  .failed {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 2rem 0;
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

  .pager {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem max(0.6rem, env(safe-area-inset-bottom));
    border-top: 1px solid var(--border);
    font-size: var(--text-sm);
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
  }
</style>
