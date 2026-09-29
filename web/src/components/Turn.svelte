<script lang="ts">
  import { app } from '../lib/app.svelte';
  import { attachmentView } from '../lib/attachments';
  import { approxEur, processedTokens, tokenBreakdown, turnCost, turnCostTitle } from '../lib/costs';
  import { AGENT_LABEL, formatInt, formatTime } from '../lib/format';
  import { AGENTS } from '../lib/protocol';
  import { MODE_LABEL } from '../lib/text';
  import {
    DEFAULT_CONSENSUS_THRESHOLD,
    INCOMPLETE_KIND,
    isTerminal,
    keptAnswers,
    pdfChecks,
    revisionRounds,
    shownSynthesis,
    streamsByAgent,
    synthesisNote,
    turnUsage,
    type TurnView,
  } from '../lib/turns.svelte';
  import { viewer } from '../lib/viewer.svelte';
  import AnswerCard from './AnswerCard.svelte';
  import AttachmentCard from './AttachmentCard.svelte';
  import DebateStepper from './DebateStepper.svelte';
  import Icon from './Icon.svelte';
  import PdfChecks from './PdfChecks.svelte';
  import RevisionRound from './RevisionRound.svelte';
  import SavingsChip from './SavingsChip.svelte';

  interface Props {
    turn: TurnView;
    /** Default number of rounds when the turn does not say (settings). */
    plannedRounds: number;
  }

  let { turn, plannedRounds }: Props = $props();

  const active = $derived(!isTerminal(turn.status));
  const answers = $derived(streamsByAgent(turn, 'answer', 0));
  const rounds = $derived(revisionRounds(turn));
  const kept = $derived(keptAnswers(turn));
  // Each synthesis attempt has its own stream: show the one that counts.
  const synthesis = $derived(shownSynthesis(turn));
  const synthesizer = $derived(synthesis?.agent ?? turn.options?.debate.synthesizer ?? 'claude');
  const fallbackNote = $derived(synthesisNote(turn, synthesis));
  const threshold = $derived(turn.options?.debate.consensus_threshold ?? DEFAULT_CONSENSUS_THRESHOLD);
  const soloAgent = $derived(answers.claude ? 'claude' : answers.chatgpt ? 'chatgpt' : (turn.target ?? 'claude'));
  // Claude's check of the question's PDFs for ChatGPT with the subscription (live turns).
  const checks = $derived(pdfChecks(turn));
  // However the turn ended: a failed or cancelled one may have billed calls too.
  const usage = $derived(isTerminal(turn.status) ? turnUsage(turn) : null);
  // Every token its calls processed, the provider's cache included (ADR 0008).
  const usedTokens = $derived(processedTokens(usage));
  const tokensTitle = $derived(usage ? tokenBreakdown(usage) : '');
  const cost = $derived(usage ? turnCost(turn) : null);
  const costText = $derived(cost && cost.totalUsd ? approxEur(cost.totalUsd, app.eurPerUsd) : null);
  const showTotals = $derived(
    usedTokens > 0 || (turn.savings?.total ?? 0) > 0 || (turn.cached && turn.streams.length > 1),
  );
  const showSynthesisSlot = $derived(
    !!synthesis || (active && turn.phase === 'synthesis'),
  );
  const consensusLabel = $derived.by(() => {
    const c = turn.consensus;
    if (!c) return null;
    if (!c.reached) return 'Sense consens';
    const scores = AGENTS.map((a) => c.scores[a]).filter((v) => v != null);
    return `Consens a la ronda ${c.round}${scores.length ? ` · ${scores.join('/')}` : ''}`;
  });
  const consensusTitle = $derived.by(() => {
    const c = turn.consensus;
    if (!c) return '';
    const parts = AGENTS.filter((a) => c.scores[a] != null).map((a) => `${AGENT_LABEL[a]}: ${c.scores[a]}`);
    return `Acord final (llindar ${threshold})${parts.length ? ` — ${parts.join(', ')}` : ''}`;
  });
</script>

{#snippet consensusBadge()}
  {#if consensusLabel && turn.consensus}
    <span class="chip {turn.consensus.reached ? 'good' : 'warn'}" title={consensusTitle}>
      <Icon name={turn.consensus.reached ? 'check' : 'info'} size={12} />{consensusLabel}
    </span>
  {/if}
{/snippet}

<article class="turn" aria-label="Torn: {turn.question.slice(0, 80)}">
  <div class="question">
    {#if turn.attachments.length}
      <ul class="attachments" aria-label="Adjunts">
        {#each turn.attachments as attachment (attachment.id)}
          <li><AttachmentCard view={attachmentView(attachment)} onopen={() => viewer.open(attachment)} /></li>
        {/each}
      </ul>
    {/if}
    <div class="bubble">
      <p>{turn.question || '…'}</p>
    </div>
    <div class="q-meta">
      <span class="mode">
        <Icon name="mode-{turn.mode}" size={13} />
        {MODE_LABEL[turn.mode]}{#if turn.mode === 'solo' && turn.target}&nbsp;· {AGENT_LABEL[turn.target]}{/if}
      </span>
      <time datetime={turn.createdAt}>{formatTime(turn.createdAt)}</time>
    </div>
  </div>

  {#if turn.mode === 'debate'}
    <DebateStepper {turn} {plannedRounds} />
  {/if}

  {#if active && turn.phase === 'compaction'}
    <p class="notice"><Icon name="refresh" size={14} />Compactant l'historial per estalviar tokens…</p>
  {/if}

  {#if checks.length}
    <PdfChecks {checks} eurPerUsd={app.eurPerUsd} />
  {/if}

  {#if turn.mode === 'solo'}
    <AnswerCard agent={soloAgent} stream={answers[soloAgent] ?? null} {active} />
  {:else}
    <div class="cols">
      {#each AGENTS as agent (agent)}
        <AnswerCard
          {agent}
          stream={answers[agent] ?? null}
          active={active && (turn.phase === null || turn.phase === 'answer' || turn.phase === 'compaction')}
          title={turn.mode === 'debate' ? 'Resposta inicial' : undefined} />
      {/each}
    </div>
  {/if}

  {#if turn.mode === 'debate'}
    {#each rounds as group (group.round)}
      <RevisionRound round={group.round} streams={group.streams} {threshold} {active} {kept} />
    {/each}
    {#if showSynthesisSlot}
      <AnswerCard
        agent={synthesizer}
        stream={synthesis}
        {active}
        variant="synthesis"
        badge={consensusBadge}
        note={fallbackNote} />
    {/if}
  {/if}

  {#if turn.status === 'failed' && turn.error?.kind === INCOMPLETE_KIND}
    <!-- Stored turn that never ended: its outcome is still null (or, stored before outcomes, it lacks its answers). -->
    <div class="banner"><Icon name="info" size={16} /><span>{turn.error.message}</span></div>
  {:else if turn.status === 'failed'}
    <div class="banner bad" role="alert">
      <Icon name="alert" size={16} />
      <span><strong>El torn ha fallat.</strong> {turn.error?.message ?? ''}</span>
    </div>
  {:else if turn.status === 'cancelled'}
    <!-- The owner's stop or a server shutdown: neither the event nor the outcome says which. -->
    <div class="banner"><Icon name="x" size={16} /><span>Aquest torn s'ha aturat.</span></div>
  {/if}

  {#if showTotals}
    <footer class="totals">
      {#if usage && usedTokens > 0}
        <span title={tokensTitle}>
          Total <b>{formatInt(usedTokens)}</b> tokens<span class="sr-only"> ({tokensTitle})</span>
        </span>
        {#if cost && costText}
          <span class="cost" title={turnCostTitle(cost, app.eurPerUsd)}>
            {costText}<span class="sr-only"> ({turnCostTitle(cost, app.eurPerUsd)})</span>
          </span>
        {/if}
      {/if}
      {#if turn.cached && turn.streams.length > 1}
        <span class="chip cache"><Icon name="cache" size={12} />Tot des de la memòria cau</span>
      {/if}
      {#if turn.savings && turn.savings.total > 0}
        <SavingsChip savings={turn.savings} eurPerUsd={app.eurPerUsd} />
      {/if}
    </footer>
  {/if}
</article>

<style>
  .turn {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.9rem;
  }

  .question {
    display: grid;
    justify-items: end;
    gap: 0.3rem;
    margin-left: auto;
    max-width: min(46rem, 88%);
  }

  .attachments {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 0.5rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .bubble {
    padding: 0.75rem 1rem;
    border-radius: var(--radius-lg) var(--radius-lg) 6px var(--radius-lg);
    background: linear-gradient(135deg, rgb(43 48 88 / 0.92), rgb(28 32 58 / 0.88));
    border: 1px solid rgb(139 156 255 / 0.32);
    -webkit-backdrop-filter: blur(10px);
    backdrop-filter: blur(10px);
    box-shadow: 0 8px 30px -14px rgb(139 156 255 / 0.5);
    animation: rise-in var(--dur-med) var(--ease-out);
  }

  .bubble p {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .q-meta {
    display: flex;
    gap: 0.6rem;
    font-size: var(--text-xs);
    color: var(--text-secondary);
    text-shadow: 0 1px 6px rgb(0 0 0 / 0.9);
  }

  .mode {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
  }

  .cols {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.9rem;
    align-items: start;
  }

  .notice {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .banner {
    display: flex;
    gap: 0.55rem;
    align-items: flex-start;
    padding: 0.7rem 0.9rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-strong);
    background: rgb(255 255 255 / 0.04);
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .banner.bad {
    border-color: rgb(255 93 108 / 0.4);
    background: rgb(255 93 108 / 0.08);
    color: #ffc9ce;
  }

  .totals {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 0.5rem 0.9rem;
    font-size: var(--text-xs);
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
    text-shadow: 0 1px 6px rgb(0 0 0 / 0.9);
  }

  .totals b,
  .totals .cost {
    color: var(--text-primary);
  }

  .cost {
    cursor: help;
  }

  @media (max-width: 860px) {
    .cols {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
