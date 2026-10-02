<script lang="ts">
  // The controls of a running refine turn («Perfecciona»): what it is doing, what it has
  // spent against its budget, and the two ways to stop it. «Atura en acabar la ronda»
  // (turn.stop) lets the round in course finish, then it ends with its last version; it
  // says so until it does. «Atura ara» (turn.cancel) stops the calls in course, and the last
  // version is kept all the same. It sticks to the bottom of the conversation while the turn
  // is in view, so the buttons are always at hand, however long the document grows; the
  // conversation knows how tall it is, and keeps what gets the keyboard focus above it
  // (lib/stop-bars.svelte.ts).
  import { app } from '../lib/app.svelte';
  import { approxEur, formatMoney } from '../lib/costs';
  import { liveStatus, spentUsd } from '../lib/refine';
  import { stopBars } from '../lib/stop-bars.svelte';
  import type { TurnView } from '../lib/turns.svelte';
  import Icon from './Icon.svelte';

  interface Props {
    turn: TurnView;
  }

  let { turn }: Props = $props();

  const uid = $props.id();
  let bar: HTMLDivElement | undefined = $state();
  // How tall the bar is, as the browser lays it out, while it is on screen.
  $effect(() => {
    const el = bar;
    if (!el) return;
    const measure = () => stopBars.set(uid, el.offsetHeight);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(el);
    return () => {
      observer?.disconnect();
      stopBars.remove(uid);
    };
  });

  const status = $derived(liveStatus(turn));
  const maxRounds = $derived(turn.options?.refine?.max_rounds ?? null);
  /** «Ronda 3 de 12»; the first answers come before round 1. */
  const roundLabel = $derived(
    status.round > 0 ? `Ronda ${status.round}${maxRounds ? ` de ${maxRounds}` : ''}` : null,
  );
  const budget = $derived(turn.options?.refine?.budget_eur ?? null);
  const spent = $derived(approxEur(spentUsd(turn), app.eurPerUsd));
  /** The round it stops after: as the server confirmed it, else the one in course; never before the merge, which every turn needs. */
  const stopAfter = $derived(
    turn.stoppingRound != null
      ? Math.max(turn.stoppingRound, 1)
      : turn.stopRequested
        ? Math.max(turn.round, 1)
        : null,
  );
  const stopping = $derived(stopAfter == null ? '' : `S'aturarà en acabar la ronda ${stopAfter}`);
</script>

<div class="refine-controls glass" role="region" aria-label="Control del torn «Perfecciona»" bind:this={bar}>
  <p class="status">
    <span class="dot" aria-hidden="true"></span>
    <span class="what">{#if roundLabel}<b>{roundLabel}</b>{' · '}{/if}{status.text}</span>
    {#if spent}
      <span class="spent">{spent}{#if budget != null}&nbsp;de {formatMoney(budget)}{/if}</span>
    {/if}
  </p>
  <!-- Always in the page, so screen readers announce the stop as soon as it is asked. -->
  <p class="sr-only" role="status">{stopping}</p>
  <div class="buttons">
    <!-- Once asked, the same button says when it stops: the keyboard focus stays where it was. -->
    <button
      type="button"
      class="btn"
      class:stopping={!!stopping}
      aria-disabled={stopping ? 'true' : undefined}
      title={stopping ? undefined : "La ronda en curs acaba (revisions i edició) i el torn es tanca amb l'última versió"}
      onclick={() => {
        if (!stopping) app.stopAfterRound();
      }}>
      <Icon name={stopping ? 'clock' : 'stop'} size={15} />{stopping || 'Atura en acabar la ronda'}
    </button>
    <button
      type="button"
      class="btn danger"
      title="Talla les crides en curs; es conserva l'última versió completa"
      onclick={() => app.cancel()}>
      <Icon name="x" size={15} />Atura ara
    </button>
  </div>
</div>

<style>
  .refine-controls {
    position: sticky;
    /* Above «Baixa al final» while it shows (Chat.svelte sets the room it needs, and keeps the
       keyboard focus above this offset and the bar's height). */
    bottom: calc(0.6rem + var(--pill-room, 0rem));
    z-index: 4;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.6rem 1rem;
    padding: 0.6rem 0.7rem 0.6rem 0.95rem;
    border-radius: var(--radius-md);
    background: var(--glass-strong);
    transition: bottom var(--dur-fast) var(--ease-out);
  }

  .status {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.3rem 0.8rem;
    min-width: 0;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .what b {
    color: var(--text-primary);
    font-weight: 650;
  }

  .dot {
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent);
    animation: pulse-dot 1.2s ease-in-out infinite;
  }

  .spent {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .buttons {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .buttons .btn {
    min-height: 2.25rem;
  }

  .btn.stopping,
  .btn.stopping:hover {
    border-style: dashed;
    border-color: rgb(242 180 65 / 0.5);
    background: rgb(242 180 65 / 0.06);
    color: #ffd99a;
    cursor: default;
    transform: none;
  }

  /* A phone: a compact status above, and the two buttons as wide as the bar, on one line. */
  :global(.refine.stacked) .refine-controls {
    gap: 0.45rem;
    padding: 0.5rem 0.55rem 0.55rem;
  }

  :global(.refine.stacked) .status {
    gap: 0.1rem 0.6rem;
    font-size: var(--text-xs);
  }

  :global(.refine.stacked) .buttons {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    width: 100%;
    gap: 0.4rem;
  }

  :global(.refine.stacked) .buttons > * {
    justify-content: center;
    min-height: 2.25rem;
    padding: 0.35rem 0.6rem;
    font-size: var(--text-xs);
    white-space: normal;
    text-align: center;
  }
</style>
