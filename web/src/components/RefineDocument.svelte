<script lang="ts">
  // The living document of a refine turn (Refine, docs/adr/0010-refine-mode.md):
  // the version shown (the current one unless the owner picks another), rendered through the
  // sanitized Markdown renderer, or its diff against the version that was current when it
  // was written; its words against the limit, whether it was cut off, and, once the turn
  // ended, why it stopped and which version it keeps as its answer, if any.
  import { AGENT_LABEL, formatInt } from '../lib/format';
  import { answerForClipboard } from '../lib/hidden-chars';
  import { i18n } from '../lib/i18n/index.svelte';
  import {
    changeKindLabel,
    countWords,
    currentVersion,
    keptVersion,
    previousAccepted,
    reasonText,
    shownDocument,
    stopReasonText,
    type VersionView,
  } from '../lib/refine';
  import { isTerminal, type TurnView } from '../lib/turns.svelte';
  import AgentLabel from './AgentLabel.svelte';
  import CopyButton from './CopyButton.svelte';
  import Icon from './Icon.svelte';
  import LineDiff from './LineDiff.svelte';
  import Markdown from './Markdown.svelte';
  import PlainText from './PlainText.svelte';
  import TruncationNote from './TruncationNote.svelte';

  interface Props {
    turn: TurnView;
    versions: VersionView[];
    /** The version the owner picked (its key); null follows the current one. */
    picked: string | null;
  }

  let { turn, versions, picked = $bindable() }: Props = $props();

  const uid = $props.id();
  const t = $derived(i18n.m.refine.document);
  const ended = $derived(isTerminal(turn.status));
  const auto = $derived(shownDocument(turn, versions));
  const current = $derived(currentVersion(versions));
  /** The version the turn keeps as its answer, once it ended (null: none). */
  const kept = $derived(keptVersion(turn, versions));
  const chosen = $derived(picked ? (versions.find((v) => v.key === picked) ?? null) : null);
  /** The version shown: the one picked, else the one the panel follows. */
  const shown = $derived(chosen ?? (auto?.kind === 'version' ? auto.version : null));
  /** The final message alone, when the turn has none of its versions (see shownDocument). */
  const finalOnly = $derived(!chosen && auto?.kind === 'final' ? auto : null);
  const stream = $derived(shown?.stream ?? finalOnly?.stream ?? null);
  const text = $derived(stream?.text ?? '');
  const number = $derived(shown?.number ?? finalOnly?.number ?? null);
  const author = $derived(stream?.agent ?? null);
  const writing = $derived(shown?.state === 'writing');
  /** It was cut off before its end (ADR 0005): a usable partial document, never a complete one. */
  const cut = $derived(stream?.status === 'done' && stream.truncated);
  const isCurrent = $derived(!!finalOnly || (!!shown && shown === current));
  const base = $derived(shown ? previousAccepted(versions, shown) : null);
  let diff = $state(false);
  const showDiff = $derived(diff && !!base && !writing);

  const words = $derived(shown?.words ?? countWords(text));
  /** The turn's word limit: every version has the same one. */
  const budget = $derived(
    shown?.budgetWords ??
      versions.find((v) => v.budgetWords != null)?.budgetWords ??
      turn.refineRounds.at(-1)?.budgetWords ??
      null,
  );
  const fill = $derived(budget ? Math.min(100, (words / budget) * 100) : 0);

  /**
   * The tone of the current version's chip, by what it is. Its names are the catalog's:
   * `current` in the chip and in the selector, `earlier` from another version.
   */
  const CURRENT_TONE = { running: 'good', kept: 'good', last: '' } as const;
  /** The current version: the one being improved, the turn's answer, or only the last one accepted (the turn kept none). */
  const currentIs = $derived<keyof typeof CURRENT_TONE>(
    !ended ? 'running' : kept != null && kept === current?.number ? 'kept' : 'last',
  );

  const chip = $derived.by((): { text: string; tone: string } | null => {
    if (!shown) return finalOnly ? { text: t.current.kept.chip, tone: 'good' } : null;
    if (shown.state === 'writing') return { text: t.chips.writing, tone: '' };
    if (shown.state === 'interrupted') return { text: t.chips.interrupted, tone: 'warn' };
    if (shown.state === 'rejected') return { text: t.chips.rejected, tone: 'warn' };
    if (!isCurrent) return { text: t.chips.earlier, tone: '' };
    return { text: t.current[currentIs].chip, tone: CURRENT_TONE[currentIs] };
  });

  /** The answer a version 1 copies when nobody could merge them. */
  const copiedFrom = $derived.by(() => {
    const from = shown?.stream.refine?.role === 'version' ? shown.stream.refine.copiedFrom : null;
    return from == null ? null : (turn.streams.find((s) => s.messageId === from)?.agent ?? null);
  });

  const note = $derived.by((): string | null => {
    if (!shown) return null;
    if (shown.state === 'writing') return t.notes.writing(shown.number);
    if (shown.state === 'interrupted') return t.notes.interrupted(shown.number);
    if (shown.state === 'rejected') {
      const reason = reasonText(shown);
      return reason ? t.notes.rejectedBecause(reason) : t.notes.rejected;
    }
    if (copiedFrom) return t.notes.copied(AGENT_LABEL[copiedFrom]);
    if (!isCurrent && current) return t.earlier[currentIs](current.number);
    return null;
  });

  /**
   * Why it ended, once it did, and which version is its answer: only one that was accepted
   * (or stored as its final message) is; a turn that did not complete may keep none.
   */
  const end = $derived.by((): { text: string; icon: 'check' | 'x' | 'info'; tone: string } | null => {
    if (!ended || number == null) return null;
    if (kept == null) {
      const text = current ? t.end.notKept(current.number) : t.end.noneKept;
      return { text, icon: 'info', tone: 'muted' };
    }
    if (turn.status === 'cancelled') {
      // The owner's «Stop now» or a server shutdown: neither says which (Turn.svelte).
      return { text: t.end.cancelled(kept), icon: 'x', tone: 'muted' };
    }
    const answer = t.end.answer(kept);
    if (turn.status !== 'done') return { text: answer, icon: 'info', tone: 'muted' };
    const reason = stopReasonText(turn);
    const round = turn.refineRounds.at(-1)?.round ?? turn.round;
    return { text: reason ? t.end.stopped(round, reason, answer) : answer, icon: 'check', tone: '' };
  });

  function optionLabel(v: VersionView): string {
    const parts = [`v${v.number}`];
    if (v.retry) parts.push(t.options.shortened);
    if (v.state === 'writing') parts.push(t.options.writing);
    else if (v.state === 'interrupted') parts.push(t.options.interrupted);
    else if (v.state === 'rejected') parts.push(t.options.rejected);
    else if (v === current) parts.push(t.current[currentIs].option);
    if (v.stream.status === 'done' && v.stream.truncated) parts.push(t.options.incomplete);
    return parts.join(' · ');
  }

  function choose(e: Event & { currentTarget: HTMLSelectElement }): void {
    const key = e.currentTarget.value;
    // The version the panel follows: picking it follows again.
    picked = auto?.kind === 'version' && auto.version.key === key ? null : key;
  }
</script>

{#snippet applied()}
  {#if shown?.changelog.length}
    <div class="applied">
      <span class="applied-label">{t.applied}</span>
      <ul>
        {#each shown.changelog as change, i (i)}
          <li><span class="kind {change.kind}">{changeKindLabel(change.kind)}</span> <PlainText text={change.text} /></li>
        {/each}
      </ul>
    </div>
  {/if}
{/snippet}

<section class="refine-doc" aria-labelledby="{uid}-title">
  <header>
    <div class="heading">
      <Icon name="mode-refine" size={18} />
      <h3 id="{uid}-title">{number == null ? t.title : i18n.m.refine.version(number)}</h3>
      {#if chip}<span class="chip {chip.tone}">{chip.text}</span>{/if}
      {#if cut}<span class="chip warn">{t.chips.incomplete}</span>{/if}
    </div>
    <div class="tools">
      {#if versions.length}
        <label class="pick">
          <span>{t.picker}</span>
          <select class="input" value={shown?.key ?? ''} onchange={choose}>
            {#each versions as v (v.key)}
              <option value={v.key}>{optionLabel(v)}</option>
            {/each}
          </select>
        </label>
        <button
          type="button"
          class="btn ghost diff-toggle"
          aria-pressed={showDiff}
          disabled={!base || writing}
          title={!base ? t.noEarlier : writing ? t.compareLater : t.showChanges(base.number)}
          onclick={() => (diff = !diff)}>
          <Icon name="diff" size={15} />{t.changes}
        </button>
      {/if}
      {#if text && !writing}
        <CopyButton {text} label={t.copy} prepare={answerForClipboard} />
      {/if}
    </div>
  </header>

  {#if end}
    <p class="doc-end {end.tone}">
      <Icon name={end.icon} size={15} /><span>{end.text}</span>
    </p>
  {/if}

  <div class="facts">
    {#if budget}
      <span
        class="words"
        class:over={words > budget}
        role="meter"
        aria-label={t.wordsMeter}
        aria-valuemin={0}
        aria-valuemax={budget}
        aria-valuenow={Math.min(words, budget)}
        aria-valuetext={t.wordsOf(`${words}`, `${budget}`)}>
        <span class="track" aria-hidden="true"><span class="fill" style:width="{fill}%"></span></span>
        {t.wordsOf(formatInt(words), formatInt(budget))}
      </span>
    {:else if text}
      <span class="words">{i18n.m.refine.words(words, formatInt(words))}</span>
    {/if}
    {#if author}<span class="by">{t.by} <AgentLabel agent={author} size={14} /></span>{/if}
  </div>

  {#if note}
    <p class="doc-note" class:warn={shown?.state === 'rejected' || shown?.state === 'interrupted'}>
      <Icon name={shown?.state === 'rejected' ? 'alert' : 'info'} size={14} /><span>{note}</span>
    </p>
  {/if}

  {#if cut && stream}
    <TruncationNote reason={stream.finishReason} lead={t.incomplete} />
  {/if}

  <div class="doc-body" aria-busy={writing}>
    {#if showDiff && base && shown}
      {#key `${base.key}>${shown.key}`}
        <LineDiff
          before={base.stream.text}
          after={text}
          label={t.diff(shown.number, base.number)}
          header={applied} />
      {/key}
    {:else if text}
      <Markdown {text} streaming={writing} />
    {:else}
      <div class="skeleton" aria-hidden="true"><i></i><i></i><i></i></div>
    {/if}
  </div>
</section>

<style>
  .refine-doc {
    --accent-color: var(--accent);
    --caret: #c9b8ff;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.7rem;
    min-width: 0;
    padding: 1rem 1.15rem 1.1rem;
    border-radius: var(--radius-md);
    border: 1px solid rgb(139 156 255 / 0.35);
    background: rgb(16 18 30 / 0.9);
    box-shadow:
      0 0 40px -14px rgb(221 107 59 / 0.3),
      0 0 60px -16px rgb(26 163 131 / 0.3);
    animation: rise-in var(--dur-med) var(--ease-out);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem 0.9rem;
  }

  .heading {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
  }

  .heading :global(.icon) {
    color: #d6a5ff;
  }

  h3 {
    font-size: var(--text-lg);
    font-weight: 700;
  }

  .tools {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .pick {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .pick select {
    width: auto;
    min-height: 2.1rem;
    max-width: 16rem;
    padding: 0.3rem 0.55rem;
    font-size: var(--text-sm);
  }

  .diff-toggle {
    min-height: 2.1rem;
    padding: 0.3rem 0.7rem;
    border-color: var(--border-strong);
  }

  .diff-toggle[aria-pressed='true'] {
    border-color: rgb(139 156 255 / 0.6);
    background: rgb(139 156 255 / 0.16);
  }

  .doc-end {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.55rem 0.75rem;
    border-radius: var(--radius-sm);
    border: 1px solid rgb(62 207 142 / 0.35);
    background: rgb(62 207 142 / 0.08);
    font-size: var(--text-sm);
    color: #c9f7e2;
  }

  .doc-end.muted {
    border-color: var(--border-strong);
    background: rgb(255 255 255 / 0.04);
    color: var(--text-secondary);
  }

  .doc-end :global(.icon) {
    flex: none;
    margin-top: 0.15rem;
  }

  .facts {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.4rem 1rem;
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }

  .words {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
  }

  .track {
    position: relative;
    width: 5.5rem;
    height: 5px;
    border-radius: 999px;
    background: rgb(255 255 255 / 0.1);
    overflow: hidden;
  }

  .fill {
    position: absolute;
    inset: 0 auto 0 0;
    border-radius: inherit;
    background: linear-gradient(90deg, var(--claude), #b35ad6, var(--chatgpt));
    transition: width 600ms var(--ease-out);
  }

  .words.over {
    color: #ffd99a;
  }

  .words.over .fill {
    background: var(--warning);
  }

  .by {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }

  .doc-note {
    display: flex;
    align-items: flex-start;
    gap: 0.45rem;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .doc-note.warn {
    color: #ffd99a;
  }

  .doc-note :global(.icon) {
    flex: none;
    margin-top: 0.15rem;
  }

  /* In the panel's grid, which spaces its parts. */
  .refine-doc > :global(.truncation-note) {
    margin-top: 0;
  }

  .doc-body {
    min-width: 0;
  }

  .applied {
    display: grid;
    gap: 0.3rem;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .applied-label {
    font-size: var(--text-xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
  }

  .applied ul {
    display: grid;
    gap: 0.25rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .applied li {
    overflow-wrap: anywhere;
  }

  .skeleton {
    display: grid;
    gap: 0.55rem;
    padding: 0.2rem 0;
  }

  .skeleton i {
    height: 0.7rem;
    border-radius: 6px;
    background: linear-gradient(
      90deg,
      rgb(255 255 255 / 0.04) 20%,
      rgb(139 156 255 / 0.22) 50%,
      rgb(255 255 255 / 0.04) 80%
    );
    background-size: 200% 100%;
    animation: shimmer 1.6s linear infinite;
  }

  .skeleton i:nth-child(2) {
    width: 88%;
  }

  .skeleton i:nth-child(3) {
    width: 62%;
  }

  /* The kinds of change: a word, never a colour alone. */
  :global(.refine .kind) {
    display: inline-block;
    margin-right: 0.3rem;
    padding: 0 0.4rem;
    border-radius: 999px;
    border: 1px solid var(--border-strong);
    font-family: var(--font-sans);
    font-size: 0.68rem;
    font-weight: 650;
    line-height: 1.55;
    color: var(--text-secondary);
    white-space: nowrap;
  }

  :global(.refine .kind.defect) {
    border-color: rgb(255 93 108 / 0.45);
    color: #ffb3ba;
  }

  :global(.refine .kind.requirement) {
    border-color: rgb(242 180 65 / 0.45);
    color: #ffd99a;
  }

  :global(.refine .kind.simplification) {
    border-color: rgb(62 207 142 / 0.45);
    color: #9ff0c9;
  }

  :global(.refine .kind.clarity) {
    border-color: rgb(139 156 255 / 0.5);
    color: #c5ceff;
  }

  :global(.refine.stacked) .refine-doc {
    padding: 0.85rem 0.85rem 0.95rem;
  }

  :global(.refine.stacked) header,
  :global(.refine.stacked) .tools {
    align-items: stretch;
  }

  :global(.refine.stacked) .tools {
    width: 100%;
  }

  :global(.refine.stacked) .pick {
    flex: 1;
    min-width: 0;
  }

  :global(.refine.stacked) .pick select {
    flex: 1;
    min-width: 0;
    max-width: none;
  }
</style>
