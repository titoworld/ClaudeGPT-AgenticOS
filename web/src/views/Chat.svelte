<script lang="ts">
  import { untrack } from 'svelte';
  import Composer from '../components/Composer.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Icon from '../components/Icon.svelte';
  import Markdown from '../components/Markdown.svelte';
  import Turn from '../components/Turn.svelte';
  import { app } from '../lib/app.svelte';
  import { prefs } from '../lib/prefs.svelte';

  const STICK_THRESHOLD_PX = 80;

  let scroller: HTMLDivElement | undefined = $state();
  let content: HTMLDivElement | undefined = $state();
  /** Follow new content (true until the user scrolls up). */
  let stick = $state(true);
  let autoScrolling = false;

  const convs = app.convs;
  const turns = $derived(app.viewTurns);
  const plannedRounds = $derived(app.settings.debate.rounds);
  const showEmpty = $derived(turns.length === 0 && convs.currentId == null);

  function distanceFromBottom(el: HTMLElement): number {
    return el.scrollHeight - el.scrollTop - el.clientHeight;
  }

  let autoTimer: ReturnType<typeof setTimeout> | undefined;

  function toBottom(smooth: boolean): void {
    const el = scroller;
    if (!el) return;
    stick = true;
    if (smooth && !prefs.reducedMotion) {
      autoScrolling = true;
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
      clearTimeout(autoTimer);
      autoTimer = setTimeout(endAutoScroll, 900); // fallback where `scrollend` is missing
    } else {
      el.scrollTop = el.scrollHeight;
    }
  }

  /** A smooth scroll finished: catch up with whatever arrived meanwhile. */
  function endAutoScroll(): void {
    clearTimeout(autoTimer);
    if (!autoScrolling) return;
    autoScrolling = false;
    if (stick && scroller) scroller.scrollTop = scroller.scrollHeight;
  }

  $effect(() => () => clearTimeout(autoTimer));

  // Only a move *up* unsticks: content growing between a programmatic scroll
  // and its scroll event must not look like the user leaving the bottom.
  let lastTop = 0;
  function onScroll(): void {
    const el = scroller;
    if (!el) return;
    const top = el.scrollTop;
    const near = distanceFromBottom(el) < STICK_THRESHOLD_PX;
    if (autoScrolling) {
      // wait for scrollend (or the fallback timer)
    } else if (near) {
      stick = true;
    } else if (top < lastTop - 4) {
      stick = false;
    }
    lastTop = top;
  }

  // Scrolling up with the wheel or a finger stops following the stream at once.
  const scrollIntent = (el: HTMLElement) => {
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) {
        autoScrolling = false;
        stick = false;
      }
    };
    const onTouch = () => {
      autoScrolling = false;
    };
    el.addEventListener('wheel', onWheel, { passive: true });
    el.addEventListener('touchmove', onTouch, { passive: true });
    el.addEventListener('scrollend', endAutoScroll);
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('touchmove', onTouch);
      el.removeEventListener('scrollend', endAutoScroll);
    };
  };

  // Keep following the bottom while content grows (streaming, new turns).
  $effect(() => {
    const el = content;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      if (stick && scroller && !autoScrolling) scroller.scrollTop = scroller.scrollHeight;
    });
    ro.observe(el);
    return () => ro.disconnect();
  });

  // Jump to the end when another conversation opens.
  $effect(() => {
    void convs.currentId;
    untrack(() => requestAnimationFrame(() => toBottom(false)));
  });

  // Sending a question always brings the view down.
  let lastCount = 0;
  $effect(() => {
    const count = turns.length;
    untrack(() => {
      const last = turns.at(-1);
      if (count > lastCount && last?.live && last.status === 'pending') toBottom(true);
      lastCount = count;
    });
  });

  // Screen readers get a short status instead of every streamed token.
  let announce = $state('');
  $effect(() => {
    const running = !!app.runningTurn;
    untrack(() => {
      if (running) announce = 'Generant la resposta…';
      else if (announce) announce = 'Resposta completada.';
    });
  });

  function pickExample(prompt: string): void {
    app.composer.draft = prompt;
    app.focusComposer();
  }
</script>

<div class="chat">
  <div
    class="scroller"
    bind:this={scroller}
    onscroll={onScroll}
    {@attach scrollIntent}>
    <div class="stream" bind:this={content}>
      {#if showEmpty}
        <EmptyState onPick={pickExample} />
      {:else}
        {#if convs.loading && turns.length === 0}
          <div class="loading" aria-live="polite">
            <span class="spinner" aria-hidden="true"></span>Carregant la conversa…
          </div>
        {/if}
        {#if convs.loadError}
          <div class="load-error" role="alert">
            <Icon name="alert" size={16} />
            <span>{convs.loadError}</span>
            <button type="button" class="btn" onclick={() => void convs.reload()}>Torna-ho a provar</button>
          </div>
        {/if}
        {#if convs.detail?.summary}
          <details class="summary">
            <summary><Icon name="refresh" size={14} />Part anterior compactada per estalviar tokens</summary>
            <Markdown text={convs.detail.summary} />
          </details>
        {/if}
        {#each turns as turn (turn.key)}
          <Turn {turn} {plannedRounds} />
        {/each}
        {#if !convs.loading && !convs.loadError && turns.length === 0}
          <p class="empty-conv">Aquesta conversa encara no té missatges.</p>
        {/if}
      {/if}
    </div>
  </div>

  <p class="sr-only" role="status">{announce}</p>

  <div class="composer-wrap">
    {#if !stick && turns.length > 0}
      <button type="button" class="to-bottom glass" onclick={() => toBottom(true)}>
        <Icon name="arrow-down" size={16} />Baixa al final
      </button>
    {/if}
    <Composer />
  </div>
</div>

<style>
  .chat {
    position: relative;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) auto;
    height: 100%;
    min-height: 0;
  }

  .scroller {
    overflow-y: auto;
    overflow-x: hidden;
    overscroll-behavior: contain;
    scrollbar-gutter: stable both-edges;
  }

  .stream {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 2.2rem;
    align-content: start;
    min-height: 100%;
    max-width: 72rem;
    margin: 0 auto;
    padding: 1.5rem clamp(0.75rem, 3vw, 2rem) 2rem;
  }

  .loading,
  .empty-conv {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.6rem;
    padding: 3rem 0;
    color: var(--text-muted);
  }

  .spinner {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.15);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }

  .load-error {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.75rem 1rem;
    border-radius: var(--radius-md);
    border: 1px solid rgb(255 93 108 / 0.4);
    background: rgb(255 93 108 / 0.08);
    color: #ffc9ce;
  }

  .load-error span {
    flex: 1;
  }

  .summary {
    padding: 0.6rem 0.9rem;
    border-radius: var(--radius-md);
    border: 1px dashed var(--border-strong);
    color: var(--text-secondary);
    font-size: var(--text-sm);
  }

  .summary summary {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    cursor: pointer;
  }

  .summary :global(.md) {
    margin-top: 0.6rem;
    font-size: var(--text-sm);
  }

  .to-bottom {
    position: absolute;
    left: 50%;
    bottom: calc(100% + 0.75rem);
    z-index: 5;
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.45rem 0.9rem;
    border-radius: 999px;
    color: var(--text-primary);
    font-size: var(--text-sm);
    font-weight: 600;
    transform: translateX(-50%);
    animation: rise-in var(--dur-fast) var(--ease-out);
  }

  .composer-wrap {
    position: relative;
    width: 100%;
    max-width: 58rem;
    margin: 0 auto;
    padding: 0 clamp(0.5rem, 2vw, 1.5rem) max(0.9rem, env(safe-area-inset-bottom));
  }
</style>
