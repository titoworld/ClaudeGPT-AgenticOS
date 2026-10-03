<script lang="ts">
  import { untrack } from 'svelte';
  import { AGENT_LABEL } from '../lib/format';
  import { i18n } from '../lib/i18n/index.svelte';
  import { critiqueMarkdown } from '../lib/markdown';
  import { AGENTS, type Agent } from '../lib/protocol';
  import type { StreamView } from '../lib/turns.svelte';
  import AgentLabel from './AgentLabel.svelte';
  import AgreementMeter from './AgreementMeter.svelte';
  import Icon from './Icon.svelte';
  import Markdown from './Markdown.svelte';
  import PdfReadingBadge from './PdfReadingBadge.svelte';
  import PlainText from './PlainText.svelte';
  import StreamStatus from './StreamStatus.svelte';
  import TruncationNote from './TruncationNote.svelte';

  interface Props {
    round: number;
    streams: StreamView[];
    threshold: number;
    /** Whether the turn is still running. */
    active: boolean;
    /**
     * Revisions of the turn that keep the previous answer (`keptAnswers`), with the
     * stream that wrote it: their content is that answer, not a revised one, so a
     * cut-off reply marks the revision, and the answer only if it was cut before.
     */
    kept: ReadonlyMap<string, StreamView | null>;
  }

  let { round, streams, threshold, active, kept }: Props = $props();

  const byAgent = $derived(
    Object.fromEntries(streams.map((s) => [s.agent, s])) as Partial<Record<Agent, StreamView>>,
  );
  const running = $derived(streams.some((s) => s.status === 'streaming'));
  // Open while it runs (live), collapsed when reloaded from history.
  let open = $state(untrack(() => active));
  const t = $derived(i18n.m.turn.review);
</script>

<details class="round" class:running bind:open>
  <summary>
    <Icon name="chevron-right" size={16} class="chev" />
    <span class="title">{i18n.m.turn.steps.review(round)}</span>
    <span class="scores">
      {#each AGENTS as agent (agent)}
        {@const s = byAgent[agent]}
        <span class="score {agent}">
          <i aria-hidden="true"></i>{AGENT_LABEL[agent]}
          <b>{s?.agreement ?? '—'}</b>{#if s?.unchanged}<span class="sr-only"> ({t.unchangedScore})</span>{/if}
        </span>
      {/each}
    </span>
    {#if running}<StreamStatus status="streaming" />{/if}
  </summary>

  <div class="cols">
    {#each AGENTS as agent (agent)}
      {@const s = byAgent[agent]}
      {@const keeps = !!s && (s.unchanged || kept.has(s.id))}
      {@const from = s ? kept.get(s.id) : undefined}
      {@const cut = s?.status === 'done' && s.truncated}
      <section class="col {agent}" aria-label={t.of(AGENT_LABEL[agent])}>
        <header>
          <AgentLabel {agent} size={16} />
          {#if s?.unchanged}
            <span class="chip good"><Icon name="check" size={12} />{t.unchanged}</span>
            {#if s.unchangedNote}
              <span class="unchanged-note"
                ><span class="sr-only">{t.modelNote}</span> {t.quote.open}<PlainText text={s.unchangedNote} />{t.quote.close}</span>
            {/if}
          {/if}
          {#if s?.pdfReading.length}
            <PdfReadingBadge readings={s.pdfReading} />
          {/if}
          {#if cut}
            <StreamStatus status="truncated" />
          {:else if s && s.status !== 'done'}
            <StreamStatus status={s.status} />
          {/if}
          {#if !s && active}<StreamStatus status="waiting" />{/if}
        </header>
        <AgreementMeter {agent} value={s?.agreement ?? null} {threshold} />
        {#if s?.critique}
          <div class="critique">
            <h4>{t.critique}</h4>
            <Markdown text={critiqueMarkdown(s.critique)} streaming={s.status === 'streaming' && !s.text} />
          </div>
        {/if}
        {#if s?.status === 'failed'}
          <p class="problem"><Icon name="alert" size={14} />{s.error?.message ?? t.error}</p>
        {/if}
        {#if s?.text && !keeps}
          <details class="revised" open={s.status === 'streaming'}>
            <summary>{t.revised}</summary>
            <Markdown text={s.text} streaming={s.status === 'streaming'} />
          </details>
        {/if}
        {#if cut && keeps}
          <!-- What was cut is the critique or the new answer: the answer kept is not. -->
          <TruncationNote lead={t.incomplete} reason={s.finishReason} detail={t.keepsPrevious} compact />
        {:else if cut}
          <TruncationNote reason={s.finishReason} compact />
        {:else if keeps && !s.unchanged}
          <p class="kept-note"><Icon name="info" size={14} />{t.keepsPrevious}</p>
        {/if}
        {#if s?.status === 'done' && keeps && from?.truncated}
          <TruncationNote lead={t.keptIncomplete} reason={from.finishReason} compact />
        {/if}
      </section>
    {/each}
  </div>
</details>

<style>
  .round {
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: rgb(15 17 27 / 0.82);
    -webkit-backdrop-filter: blur(10px);
    backdrop-filter: blur(10px);
    animation: rise-in var(--dur-med) var(--ease-out);
  }

  .round.running {
    border-color: rgb(139 156 255 / 0.35);
  }

  .round > summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.4rem 0.75rem;
    padding: 0.7rem 0.9rem;
    cursor: pointer;
    list-style: none;
    border-radius: var(--radius-md);
  }

  .round > summary::-webkit-details-marker {
    display: none;
  }

  .round > summary:hover {
    background: rgb(255 255 255 / 0.03);
  }

  .round > summary :global(.chev) {
    color: var(--text-muted);
    transition: transform var(--dur-fast) var(--ease-out);
  }

  .round[open] > summary :global(.chev) {
    transform: rotate(90deg);
  }

  .title {
    font-weight: 650;
    font-size: var(--text-sm);
  }

  .scores {
    display: inline-flex;
    gap: 0.8rem;
    margin-right: auto;
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .score {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
  }

  .score i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--claude);
  }

  .score.chatgpt i {
    background: var(--chatgpt);
  }

  .score b {
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
  }

  .cols {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem;
    padding: 0 0.9rem 0.9rem;
  }

  .col {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.6rem;
    align-content: start;
    min-width: 0;
    padding: 0.75rem 0.85rem;
    border-radius: var(--radius-sm);
    background: rgb(255 255 255 / 0.025);
    border-left: 3px solid var(--claude);
  }

  .col.chatgpt {
    border-left-color: var(--chatgpt);
  }

  .col header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .unchanged-note {
    min-width: 0;
    font-size: var(--text-xs);
    font-style: italic;
    color: var(--text-muted);
    overflow-wrap: anywhere;
  }

  h4 {
    margin: 0 0 0.3rem;
    font-size: var(--text-xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
  }

  .critique :global(.md) {
    font-size: var(--text-sm);
  }

  .revised > summary {
    cursor: pointer;
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-secondary);
  }

  .revised[open] > summary {
    margin-bottom: 0.4rem;
  }

  .revised :global(.md) {
    font-size: var(--text-sm);
  }

  .problem {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: var(--text-sm);
    color: #ffb3ba;
  }

  .kept-note {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  @media (max-width: 720px) {
    .cols {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
