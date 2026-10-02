<script lang="ts">
  // A refine turn («Perfecciona», docs/adr/0010-mode-perfecciona.md): both agents answer the
  // brief, the editor merges the answers into a document, and round after round both review
  // it and the editor writes its next version. The view: the first answers (folded once
  // there is a document), the living document with its versions, every round, and, while
  // it runs, the controls to stop it. The same live and after a reload (lib/refine.ts).
  import { untrack } from 'svelte';
  import { app } from '../lib/app.svelte';
  import { prefs } from '../lib/prefs.svelte';
  import { AGENTS } from '../lib/protocol';
  import { refineVersions, roundBlocks, shownDocument } from '../lib/refine';
  import { isTerminal, streamsByAgent, type TurnView } from '../lib/turns.svelte';
  import AnswerCard from './AnswerCard.svelte';
  import Icon from './Icon.svelte';
  import RefineControls from './RefineControls.svelte';
  import RefineDocument from './RefineDocument.svelte';
  import RefineRound from './RefineRound.svelte';
  import StreamStatus from './StreamStatus.svelte';

  interface Props {
    turn: TurnView;
  }

  let { turn }: Props = $props();

  const active = $derived(!isTerminal(turn.status));
  const answers = $derived(streamsByAgent(turn, 'answer', 0));
  const versions = $derived(refineVersions(turn));
  const hasDocument = $derived(shownDocument(turn, versions) != null);
  const blocks = $derived(roundBlocks(turn));
  const answering = $derived(active && (turn.phase === null || turn.phase === 'answer' || turn.phase === 'compaction'));

  /** The version the owner picked in the document (its key); null follows the current one. */
  let picked: string | null = $state(null);
  let documentEl: HTMLElement | undefined = $state();

  // The first answers are open while they are written, and fold once the document has a
  // version, unless the owner opened or closed them.
  let answersOpen = $state(untrack(() => turn.live && !isTerminal(turn.status) && refineVersions(turn).length === 0));
  let answersTouched = false;
  $effect(() => {
    const fold = versions.length > 0;
    untrack(() => {
      if (fold && !answersTouched) answersOpen = false;
    });
  });

  /** A round asks to show one of its versions: pick it, and bring the document into view. */
  function show(key: string): void {
    picked = key;
    documentEl?.scrollIntoView({ behavior: prefs.reducedMotion ? 'auto' : 'smooth', block: 'start' });
  }
</script>

<div class="refine" class:stacked={prefs.narrow}>
  <details class="answers" bind:open={answersOpen}>
    <summary onclick={() => (answersTouched = true)}>
      <Icon name="chevron-right" size={16} class="chev" />
      <span class="title">Respostes inicials</span>
      <span class="hint">la ronda 0: cada IA respon l'encàrrec pel seu compte</span>
      {#if answering}<StreamStatus status="streaming" />{/if}
    </summary>
    <div class="cols">
      {#each AGENTS as agent (agent)}
        <AnswerCard {agent} stream={answers[agent] ?? null} active={answering} title="Resposta inicial" />
      {/each}
    </div>
  </details>

  {#if hasDocument}
    <div class="document" bind:this={documentEl}>
      <RefineDocument {turn} {versions} bind:picked />
    </div>
  {/if}

  {#if blocks.length}
    <section class="rounds" aria-label="Rondes">
      {#each blocks as block, i (block.round)}
        <RefineRound
          {block}
          {versions}
          live={turn.live}
          latest={i === blocks.length - 1}
          {active}
          eurPerUsd={app.eurPerUsd}
          onshow={show} />
      {/each}
    </section>
  {/if}

  {#if active}
    <RefineControls {turn} />
  {/if}
</div>

<style>
  .refine {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.9rem;
    min-width: 0;
  }

  .answers {
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: rgb(15 17 27 / 0.6);
  }

  .answers > summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.3rem 0.7rem;
    padding: 0.65rem 0.9rem;
    cursor: pointer;
    list-style: none;
    border-radius: var(--radius-md);
  }

  .answers > summary::-webkit-details-marker {
    display: none;
  }

  .answers > summary:hover {
    background: rgb(255 255 255 / 0.03);
  }

  .answers > summary :global(.chev) {
    color: var(--text-muted);
    transition: transform var(--dur-fast) var(--ease-out);
  }

  .answers[open] > summary :global(.chev) {
    transform: rotate(90deg);
  }

  .title {
    font-weight: 650;
    font-size: var(--text-sm);
  }

  .hint {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .cols {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.9rem;
    align-items: start;
    padding: 0 0.9rem 0.9rem;
  }

  .document {
    min-width: 0;
    scroll-margin-top: 0.75rem;
  }

  .rounds {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.6rem;
  }

  .stacked .cols {
    grid-template-columns: minmax(0, 1fr);
    padding: 0 0.6rem 0.7rem;
  }

  .stacked .hint {
    display: none;
  }
</style>
