<script lang="ts">
  // What changed between two versions of a document, line by line (lib/line-diff.ts):
  // removed and added lines with a little context, the unchanged ones further away folded
  // (a click shows them). Model text, so plain text nodes only (PlainText): never HTML.
  import type { Snippet } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import { formatInt } from '../lib/format';
  import { i18n } from '../lib/i18n/index.svelte';
  import { diffHunks, diffStats, lineDiff } from '../lib/line-diff';
  import PlainText from './PlainText.svelte';

  interface Props {
    before: string;
    after: string;
    /** What the list is, for screen readers ("Changes in version 2 since version 1"). */
    label: string;
    /** Shown above the lines (the changes the editor says it applied). */
    header?: Snippet;
  }

  let { before, after, label, header }: Props = $props();

  const lines = $derived(lineDiff(before, after));
  const parts = $derived(diffHunks(lines, 2));
  const stats = $derived(diffStats(lines));
  /** Folded parts the owner opened (the parent mounts a new diff for other texts). */
  const unfolded = new SvelteSet<number>();

  const t = $derived(i18n.m.refine.diff);
</script>

<div class="line-diff">
  {#if header}{@render header()}{/if}
  <p class="diff-stats">
    <span class="added">+{formatInt(stats.added)}</span> <span class="removed">−{formatInt(stats.removed)}</span> {t.lines}
  </p>
  {#if stats.added + stats.removed === 0}
    <p class="none">{t.none}</p>
  {/if}
  <ol aria-label={label}>
    {#each parts as part, i (i)}
      {#if part.kind === 'lines' || unfolded.has(i)}
        {#each part.lines as line, j (j)}
          <li class={line.op}>
            {#if line.op !== 'same'}<span class="sr-only">{line.op === 'add' ? t.added : t.removed}</span>{' '}{/if}<span
              class="text">{#if line.text}<PlainText text={line.text} />{:else}&nbsp;{/if}</span>
          </li>
        {/each}
      {:else}
        <li class="skip">
          <button type="button" class="unfold" onclick={() => unfolded.add(i)}
            >{t.unchanged(part.count, formatInt(part.count))}</button>
        </li>
      {/if}
    {/each}
  </ol>
</div>

<style>
  .line-diff {
    display: grid;
    gap: 0.5rem;
    min-width: 0;
  }

  .diff-stats {
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .added {
    color: #9ff0c9;
  }

  .removed {
    color: #ffb3ba;
  }

  .none {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  ol {
    margin: 0;
    padding: 0.3rem 0;
    list-style: none;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: rgb(5 6 10 / 0.6);
    font-family: var(--font-mono);
    font-size: 0.8125rem;
    line-height: 1.55;
  }

  li {
    position: relative;
    padding: 0.05rem 0.75rem 0.05rem 1.6rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  /* The sign is decoration: the hidden prefix says it (and colour is never alone). */
  li::before {
    position: absolute;
    left: 0.6rem;
    color: var(--text-muted);
  }

  li.add {
    background: rgb(62 207 142 / 0.12);
    color: #c9f7e2;
  }

  li.add::before {
    content: '+' / '';
    color: #9ff0c9;
  }

  li.del {
    background: rgb(255 93 108 / 0.12);
    color: #ffd0d5;
    text-decoration: line-through;
    text-decoration-color: rgb(255 179 186 / 0.45);
  }

  li.del::before {
    content: '−' / '';
    color: #ffb3ba;
  }

  li.same {
    color: var(--text-secondary);
  }

  li.skip {
    padding: 0.15rem 0.75rem;
  }

  .unfold {
    padding: 0.1rem 0.45rem;
    white-space: nowrap;
    border: 1px dashed var(--border-strong);
    border-radius: 6px;
    background: none;
    color: var(--text-muted);
    font-family: var(--font-sans);
    font-size: var(--text-xs);
  }

  .unfold:hover {
    color: var(--text-primary);
    border-color: rgb(255 255 255 / 0.3);
  }
</style>
