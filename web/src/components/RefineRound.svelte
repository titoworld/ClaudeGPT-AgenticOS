<script lang="ts">
  // A round of a refine turn (Refine): how it ended (the version it wrote, or why
  // not; the changes it applied and their kinds; the words against the limit; what each
  // agent proposed and how it scored the document; what the round and the turn cost), its
  // reviews in a collapsible part, and the versions the editor wrote in it. While the round
  // runs, its block is open; once the next one starts, it folds (unless the owner opened or
  // closed it). Model text is shown as plain text (PlainText) or sanitized Markdown.
  import { untrack } from 'svelte';
  import { approxEur } from '../lib/costs';
  import { AGENT_LABEL, formatInt } from '../lib/format';
  import { i18n } from '../lib/i18n/index.svelte';
  import { critiqueMarkdown } from '../lib/markdown';
  import { AGENTS, type Agent } from '../lib/protocol';
  import { changeKindLabel, reasonText, type RoundBlock, type VersionView } from '../lib/refine';
  import AgentLabel from './AgentLabel.svelte';
  import Icon from './Icon.svelte';
  import Markdown from './Markdown.svelte';
  import PlainText from './PlainText.svelte';
  import RichText from './RichText.svelte';
  import StreamStatus from './StreamStatus.svelte';

  interface Props {
    block: RoundBlock;
    /** The turn's versions: the ones this round wrote are shown with it. */
    versions: VersionView[];
    /** The turn was followed live in this tab (a reloaded one starts folded). */
    live: boolean;
    /** It is the turn's last round so far. */
    latest: boolean;
    /** The turn still runs. */
    active: boolean;
    eurPerUsd: number;
    /** Show this version (its key) in the document. */
    onshow: (key: string) => void;
  }

  let { block, versions, live, latest, active, eurPerUsd, onshow }: Props = $props();

  const t = $derived(i18n.m.refine.round);
  const summary = $derived(block.summary);
  /** Why the round wrote no new version, in the language of the interface. */
  const reason = $derived(summary ? reasonText(summary) : null);
  const running = $derived(active && !summary);
  const roundCost = $derived(approxEur(summary?.usage?.cost_usd, eurPerUsd));
  const totalCost = $derived(approxEur(summary?.total?.cost_usd, eurPerUsd));

  /** The owner opened or closed it: it stays as they left it. */
  let touched = false;
  let open = $state(untrack(() => live && latest));
  $effect(() => {
    const unfold = live && latest;
    untrack(() => {
      if (!touched) open = unfold;
    });
  });
  let reviewsOpen = $state(untrack(() => live && latest && !block.summary));

  function review(agent: Agent) {
    const s = block.reviews[agent];
    return s?.refine?.role === 'review' ? s.refine : null;
  }

  /** What an agent proposed this round, in words. */
  function proposals(agent: Agent): string {
    const n = summary ? summary.proposals[agent] : (review(agent)?.changes.length ?? null);
    if (n === 0) return t.noChanges;
    if (n != null) return t.proposals(n, formatInt(n));
    const s = block.reviews[agent];
    if (s?.status === 'streaming') return t.reviewing;
    if (s?.status === 'failed') return t.failed;
    if (s?.status === 'interrupted') return t.interrupted;
    return t.noReview;
  }

  const score = (agent: Agent): number | null => (summary ? summary.scores[agent] : (review(agent)?.score ?? null));

  /** The versions this round wrote, with the editor's calls that failed. */
  const edits = $derived(
    block.edits.map((s) => ({ stream: s, version: versions.find((v) => v.key === s.id) ?? null })),
  );

  function editText(version: VersionView): string {
    const name = version.retry ? t.shortened(version.number) : i18n.m.refine.version(version.number);
    const edit = t.edit;
    if (version.state === 'writing') return edit.writing(name, i18n.m.refine.words(version.words, formatInt(version.words)));
    if (version.state === 'interrupted') return edit.interrupted(name);
    // Cut off before its end (ADR 0005): never complete, accepted or not.
    if (version.stream.truncated) return version.state === 'accepted' ? edit.acceptedIncomplete(name) : edit.rejectedIncomplete(name);
    return version.state === 'accepted' ? edit.accepted(name) : edit.rejected(name);
  }
</script>

<details class="refine-round" class:running bind:open>
  <summary onclick={() => (touched = true)}>
    <Icon name="chevron-right" size={16} class="chev" />
    <span class="title">{t.title(block.round)}</span>
    {#if block.round === 1}<span class="tag">{t.merge}</span>{/if}
    {#if summary}
      {#if summary.accepted}
        <span class="chip good">{i18n.m.refine.version(summary.version)}</span>
      {:else}
        <span class="chip warn">{t.noNewVersion}</span>
      {/if}
      {#if summary.converged}<span class="chip good">{t.converges}</span>{/if}
      <span class="meta">{i18n.m.refine.words(summary.words, formatInt(summary.words))}</span>
      {#if roundCost}<span class="meta cost">{roundCost}</span>{/if}
    {:else if running}
      <span class="chip">{t.inProgress}</span>
      <StreamStatus status="streaming" />
    {:else}
      <span class="chip">{t.interrupted}</span>
    {/if}
  </summary>

  <div class="round-body">
    {#if summary}
      {#if reason}
        <p class="reason"><Icon name="info" size={14} /><span>{reason}</span></p>
      {/if}
      {#if summary.changes.length}
        <div class="applied">
          <h4>{t.applied}</h4>
          <ul class="changes">
            {#each summary.changes as change, i (i)}
              <li><span class="kind {change.kind}">{changeKindLabel(change.kind)}</span> <PlainText text={change.text} /></li>
            {/each}
          </ul>
        </div>
      {/if}
      <dl class="facts">
        <div>
          <dt>{t.words}</dt>
          <dd class:over={summary.words > summary.budgetWords}>
            {t.ofLimit(formatInt(summary.words), formatInt(summary.budgetWords))}
          </dd>
        </div>
        {#if roundCost}
          <div>
            <dt>{t.cost}</dt>
            <dd>{roundCost}</dd>
          </div>
        {/if}
        {#if totalCost}
          <div>
            <dt>{t.total}</dt>
            <dd>{totalCost}</dd>
          </div>
        {/if}
      </dl>
    {/if}

    {#if block.round >= 2}
      <ul class="agents" aria-label={t.reviewsOf(block.round)}>
        {#each AGENTS as agent (agent)}
          {@const value = score(agent)}
          <li class={agent}>
            <AgentLabel {agent} size={15} />
            <span class="proposals">{proposals(agent)}</span>
            {#if value != null}<span class="score"><RichText text={t.score(value)} /></span>{/if}
          </li>
        {/each}
      </ul>

      <details class="reviews" bind:open={reviewsOpen}>
        <summary>{t.reviews}</summary>
        <div class="cols">
          {#each AGENTS as agent (agent)}
            {@const s = block.reviews[agent]}
            {@const done = review(agent)}
            <section class="review {agent}" aria-label={t.reviewBy(AGENT_LABEL[agent])}>
              <header>
                <AgentLabel {agent} size={15} />
                {#if s && s.status !== 'done'}<StreamStatus status={s.status} />{/if}
              </header>
              {#if done}
                {#if done.changes.length}
                  <ul class="changes">
                    {#each done.changes as change, i (i)}
                      <li><span class="kind {change.kind}">{changeKindLabel(change.kind)}</span> <PlainText text={change.text} /></li>
                    {/each}
                  </ul>
                {:else}
                  <p class="unchanged"><Icon name="check" size={14} />{t.noChanges}</p>
                {/if}
              {:else if s?.status === 'failed'}
                <p class="problem"><Icon name="alert" size={14} /><span>{s.error?.message ?? t.error}</span></p>
              {:else if s?.critique}
                <Markdown text={critiqueMarkdown(s.critique)} streaming={s.status === 'streaming'} />
              {:else if !s}
                <p class="empty">{running ? t.notStarted : t.noReviewDone}</p>
              {/if}
            </section>
          {/each}
        </div>
      </details>
    {/if}

    {#if edits.length}
      <ul class="edits">
        {#each edits as edit (edit.stream.id)}
          <li>
            {#if edit.version}
              <span>{editText(edit.version)}</span>
              <button type="button" class="link-btn" onclick={() => onshow(edit.version!.key)}>
                {edit.version.retry ? t.showShortened(edit.version.number) : t.show(edit.version.number)}
              </button>
            {:else}
              <span class="problem">
                <Icon name="alert" size={14} />{t.editFailed(AGENT_LABEL[edit.stream.agent])}
                {edit.stream.error?.message ?? t.errorInline}
              </span>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</details>

<style>
  .refine-round {
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: rgb(15 17 27 / 0.82);
    -webkit-backdrop-filter: blur(10px);
    backdrop-filter: blur(10px);
    animation: rise-in var(--dur-med) var(--ease-out);
  }

  .refine-round.running {
    border-color: rgb(139 156 255 / 0.35);
  }

  .refine-round > summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.4rem 0.7rem;
    padding: 0.65rem 0.9rem;
    cursor: pointer;
    list-style: none;
    border-radius: var(--radius-md);
  }

  .refine-round > summary::-webkit-details-marker {
    display: none;
  }

  .refine-round > summary:hover {
    background: rgb(255 255 255 / 0.03);
  }

  .refine-round > summary :global(.chev) {
    color: var(--text-muted);
    transition: transform var(--dur-fast) var(--ease-out);
  }

  .refine-round[open] > summary :global(.chev) {
    transform: rotate(90deg);
  }

  .title {
    font-weight: 650;
    font-size: var(--text-sm);
  }

  .tag {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .meta {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .cost {
    margin-left: auto;
    color: var(--text-secondary);
  }

  .round-body {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.75rem;
    padding: 0 0.9rem 0.9rem;
  }

  .reason {
    display: flex;
    align-items: flex-start;
    gap: 0.4rem;
    font-size: var(--text-sm);
    color: #ffd99a;
  }

  .reason :global(.icon) {
    flex: none;
    margin-top: 0.15rem;
  }

  h4 {
    margin: 0 0 0.3rem;
    font-size: var(--text-xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
  }

  .changes {
    display: grid;
    gap: 0.3rem;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .changes li {
    overflow-wrap: anywhere;
  }

  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 1.4rem;
    margin: 0;
    font-size: var(--text-xs);
    font-variant-numeric: tabular-nums;
  }

  .facts div {
    display: flex;
    gap: 0.4rem;
  }

  dt {
    color: var(--text-muted);
  }

  dd {
    margin: 0;
    color: var(--text-secondary);
    font-weight: 600;
  }

  dd.over {
    color: #ffd99a;
  }

  .agents {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.4rem;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: var(--text-sm);
  }

  .agents li {
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.3rem 0.6rem;
  }

  .proposals {
    color: var(--text-secondary);
  }

  .score {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  /* The score's figure, which RichText draws. */
  .score :global(b) {
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
  }

  .reviews > summary {
    cursor: pointer;
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-secondary);
  }

  .reviews[open] > summary {
    margin-bottom: 0.5rem;
  }

  .cols {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.6rem;
  }

  .review {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.5rem;
    align-content: start;
    min-width: 0;
    padding: 0.65rem 0.8rem;
    border-radius: var(--radius-sm);
    background: rgb(255 255 255 / 0.025);
    border-left: 3px solid var(--claude);
  }

  .review.chatgpt {
    border-left-color: var(--chatgpt);
  }

  .review header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .review :global(.md) {
    font-size: var(--text-sm);
  }

  .unchanged {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: var(--text-sm);
    color: #9ff0c9;
  }

  .problem {
    display: inline-flex;
    align-items: flex-start;
    gap: 0.4rem;
    font-size: var(--text-sm);
    color: #ffb3ba;
  }

  .problem :global(.icon) {
    flex: none;
    margin-top: 0.15rem;
  }

  .empty {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  .edits {
    display: grid;
    gap: 0.35rem;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .edits li {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.2rem 0.7rem;
  }

  .link-btn {
    padding: 0.15rem 0.35rem;
    border: none;
    border-radius: 6px;
    background: none;
    font-size: var(--text-xs);
    color: var(--accent);
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  .link-btn:hover {
    background: rgb(139 156 255 / 0.1);
  }

  :global(.refine.stacked) .cols {
    grid-template-columns: minmax(0, 1fr);
  }

  :global(.refine.stacked) .refine-round > summary {
    padding: 0.6rem 0.75rem;
  }

  :global(.refine.stacked) .round-body {
    padding: 0 0.75rem 0.8rem;
  }
</style>
