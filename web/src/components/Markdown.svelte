<script lang="ts">
  // Renders untrusted markdown. While streaming, re-renders at most ~20 times
  // per second (backing off to ~6/s when a parse gets expensive) so long
  // answers never block typing or the 3D scene. Code blocks get copy buttons
  // and lazy highlighting once the text is final.
  import { untrack } from 'svelte';
  import { enhanceCodeBlocks } from '../lib/code-blocks';
  import { hasCodeBlocks, renderMarkdown } from '../lib/markdown';

  interface Props {
    text: string;
    streaming?: boolean;
    class?: string;
  }

  let { text, streaming = false, class: className = '' }: Props = $props();

  const MIN_INTERVAL_MS = 50;
  const SLOW_INTERVAL_MS = 160;
  const SLOW_RENDER_MS = 12;

  let el: HTMLDivElement | undefined = $state();
  let raf = 0;
  let lastAt = 0;
  let cost = 0;
  let pending = '';
  let rendered: string | null = null;
  let html = $state.raw(initialHtml());

  function initialHtml(): string {
    if (streaming) return '';
    rendered = text;
    return renderMarkdown(text);
  }

  function renderNow(src: string): void {
    if (src === rendered) return;
    rendered = src;
    const t0 = performance.now();
    html = renderMarkdown(src);
    cost = performance.now() - t0;
    lastAt = t0;
  }

  function frame(now: number): void {
    raf = 0;
    const interval = cost > SLOW_RENDER_MS ? SLOW_INTERVAL_MS : MIN_INTERVAL_MS;
    if (now - lastAt < interval) {
      raf = requestAnimationFrame(frame);
      return;
    }
    renderNow(pending);
  }

  $effect(() => {
    const src = text;
    const live = streaming;
    untrack(() => {
      pending = src;
      if (!live) {
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
        renderNow(src);
        return;
      }
      if (!raf) raf = requestAnimationFrame(frame);
    });
  });

  $effect(() => () => {
    if (raf) cancelAnimationFrame(raf);
  });

  $effect(() => {
    if (!streaming && el && hasCodeBlocks(html)) enhanceCodeBlocks(el);
  });
</script>

<div class="md {className}" class:streaming bind:this={el}>{@html html}</div>
