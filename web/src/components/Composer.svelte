<script lang="ts">
  import { untrack } from 'svelte';
  import { app } from '../lib/app.svelte';
  import { AGENT_LABEL } from '../lib/format';
  import { placeAbove } from '../lib/popover';
  import { prefs } from '../lib/prefs.svelte';
  import { AGENTS, type TurnMode } from '../lib/protocol';
  import { LIMITS } from '../lib/settings';
  import { estimateTokens, formatK, MODE_LABEL } from '../lib/text';
  import AgentIcon from './AgentIcon.svelte';
  import Icon from './Icon.svelte';
  import ModelMenu from './ModelMenu.svelte';

  const uid = $props.id();
  const popoverId = `${uid}-debate-options`;
  const MODES: TurnMode[] = ['solo', 'duel', 'debate'];
  const MAX_HEIGHT_RATIO = 0.4;

  let textarea: HTMLTextAreaElement | undefined = $state();
  let optionsButton: HTMLButtonElement | undefined = $state();

  const c = app.composer;
  const running = $derived(!!app.runningTurn);
  const connected = $derived(app.conn.status === 'open');
  const tokens = $derived(estimateTokens(c.draft.trim()));
  const canSubmit = $derived(running || (connected && c.draft.trim().length > 0));
  const supportsFieldSizing = typeof CSS !== 'undefined' && CSS.supports('field-sizing', 'content');

  function autosize(): void {
    if (!textarea || supportsFieldSizing) return;
    textarea.style.height = 'auto';
    const max = Math.round(window.innerHeight * MAX_HEIGHT_RATIO);
    textarea.style.height = `${Math.min(textarea.scrollHeight, max)}px`;
  }

  $effect(() => {
    void c.draft;
    untrack(autosize);
  });

  // Focus when asked (new conversation, example picked); not on phones at mount.
  let firstFocus = true;
  $effect(() => {
    void app.focusComposerTick;
    untrack(() => {
      if (firstFocus && prefs.narrow) {
        firstFocus = false;
        return;
      }
      firstFocus = false;
      textarea?.focus();
    });
  });

  function submit(): void {
    if (running) {
      app.cancel();
      return;
    }
    if (app.send(c.draft)) c.draft = '';
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.isComposing) return;
    if (e.key === 'Enter' && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (canSubmit && !running) submit();
    } else if (e.key === 'Escape' && running) {
      e.preventDefault();
      app.cancel();
    }
  }
</script>

<form
  class="composer glass"
  class:running
  onsubmit={(e) => {
    e.preventDefault();
    submit();
  }}>
  <label class="sr-only" for="{uid}-input">Pregunta</label>
  <textarea
    id="{uid}-input"
    bind:this={textarea}
    bind:value={c.draft}
    onkeydown={onKeydown}
    rows="1"
    placeholder="Pregunta el que vulguis…"
    aria-describedby="{uid}-hint"
    enterkeyhint="send"
    spellcheck="true"></textarea>

  <div class="toolbar">
    <div class="controls">
      <fieldset class="segmented modes">
        <legend class="sr-only">Mode</legend>
        {#each MODES as mode (mode)}
          <label title={MODE_LABEL[mode]}>
            <input type="radio" name="{uid}-mode" value={mode} bind:group={c.mode} />
            <Icon name="mode-{mode}" size={15} />
            <span class="mode-name">{MODE_LABEL[mode]}</span>
          </label>
        {/each}
      </fieldset>

      {#if c.mode === 'solo'}
        <fieldset class="segmented targets">
          <legend class="sr-only">Qui respon</legend>
          {#each AGENTS as agent (agent)}
            <label class={agent}>
              <input type="radio" name="{uid}-target" value={agent} bind:group={c.target} />
              <AgentIcon {agent} size={15} />
              <span>{AGENT_LABEL[agent]}</span>
            </label>
          {/each}
        </fieldset>
      {:else if c.mode === 'debate'}
        <button
          type="button"
          class="btn ghost options-btn"
          popovertarget={popoverId}
          bind:this={optionsButton}
          aria-label="Opcions del consell: {c.rounds} rondes, llindar {c.threshold}, sintetitza {AGENT_LABEL[
            c.synthesizer
          ]}">
          <Icon name="sliders" size={15} />
          <span>{c.rounds} {c.rounds === 1 ? 'ronda' : 'rondes'} · {c.threshold}</span>
        </button>
      {/if}

      <ModelMenu />

      <label class="cache" title="Reutilitza respostes idèntiques anteriors sense cridar cap model">
        <input type="checkbox" class="switch" bind:checked={c.useCache} />
        <Icon name="cache" size={15} />
        <span>Memòria cau</span>
      </label>
    </div>

    <div class="actions">
      <span class="kbd-hint" class:idle={connected && !c.draft.trim()} id="{uid}-hint">
        {#if !connected}
          Sense connexió
        {:else if c.draft.trim()}
          ≈ {formatK(tokens)} tokens
        {:else}
          <kbd>Enter</kbd> per enviar
          <span class="sr-only">. Maj+Enter fa un salt de línia i Esc atura el torn en curs.</span>
        {/if}
      </span>
      <button
        type="submit"
        class="send"
        class:stop={running}
        disabled={!canSubmit}
        aria-label={running ? 'Atura el torn (Esc)' : 'Envia'}
        title={running ? 'Atura (Esc)' : connected ? 'Envia (Enter)' : 'Sense connexió'}>
        <Icon name={running ? 'stop' : 'send'} size={18} />
      </button>
    </div>
  </div>
</form>

<div class="popover glass" id={popoverId} popover="auto" {@attach placeAbove(() => optionsButton, 300)}>
  <h3>Opcions del consell</h3>
  <label class="field">
    <span>Rondes de revisió <b>{c.rounds}</b></span>
    <input type="range" min={LIMITS.rounds.min} max={LIMITS.rounds.max} step="1" bind:value={c.rounds} />
    <small class="hint">0 = sense revisions: respostes i síntesi directa.</small>
  </label>
  <label class="field">
    <span>Llindar de consens <b>{c.threshold}</b></span>
    <input
      type="range"
      min={LIMITS.consensus_threshold.min}
      max={LIMITS.consensus_threshold.max}
      step="1"
      bind:value={c.threshold} />
    <small class="hint">Si tots dos superen aquest acord, s'aturen les rondes.</small>
  </label>
  <fieldset class="field">
    <legend class="field-label">Sintetitzador</legend>
    <div class="segmented">
      {#each AGENTS as agent (agent)}
        <label>
          <input type="radio" name="{uid}-synth" value={agent} bind:group={c.synthesizer} />
          <AgentIcon {agent} size={15} />
          <span>{AGENT_LABEL[agent]}</span>
        </label>
      {/each}
    </div>
  </fieldset>
</div>

<style>
  .composer {
    container-type: inline-size;
    display: grid;
    gap: 0.5rem;
    padding: 0.7rem 0.75rem 0.6rem;
    border-radius: var(--radius-lg);
    transition:
      border-color var(--dur-med) var(--ease-out),
      box-shadow var(--dur-med) var(--ease-out);
  }

  .composer:focus-within {
    border-color: rgb(139 156 255 / 0.45);
    box-shadow:
      var(--shadow),
      0 0 0 3px rgb(139 156 255 / 0.12);
  }

  textarea {
    field-sizing: content;
    width: 100%;
    min-height: 2.6rem;
    max-height: 40vh;
    padding: 0.45rem 0.5rem;
    border: none;
    outline: none;
    resize: none;
    background: transparent;
    color: var(--text-primary);
    font-size: 1rem;
    line-height: 1.5;
  }

  textarea::placeholder {
    color: var(--text-muted);
  }

  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .controls {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.4rem;
    min-width: 0;
  }

  fieldset {
    margin: 0;
    min-width: 0;
  }

  .segmented.modes label,
  .segmented.targets label {
    padding: 0.28rem 0.6rem;
  }

  .targets label.claude:has(input:checked) {
    box-shadow: inset 0 0 0 1px rgb(221 107 59 / 0.55);
    background: var(--claude-soft);
  }

  .targets label.chatgpt:has(input:checked) {
    box-shadow: inset 0 0 0 1px rgb(26 163 131 / 0.55);
    background: var(--chatgpt-soft);
  }

  .options-btn {
    min-height: 2rem;
    padding: 0.3rem 0.6rem;
    font-variant-numeric: tabular-nums;
  }

  .cache {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.3rem 0.5rem;
    border-radius: var(--radius-sm);
    font-size: var(--text-sm);
    color: var(--text-secondary);
    cursor: pointer;
  }

  .cache:hover {
    color: var(--text-primary);
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex: none;
  }

  .kbd-hint {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .send {
    display: grid;
    place-items: center;
    width: 2.5rem;
    height: 2.5rem;
    border: none;
    border-radius: 50%;
    background: linear-gradient(135deg, var(--claude), #b35ad6 50%, var(--chatgpt));
    color: #fff;
    box-shadow: 0 6px 22px -6px rgb(179 90 214 / 0.7);
    transition:
      transform var(--dur-fast) var(--ease-out),
      opacity var(--dur-fast) var(--ease-out),
      filter var(--dur-fast) var(--ease-out);
  }

  .send:hover:not(:disabled) {
    transform: scale(1.06);
    filter: brightness(1.1);
  }

  .send:disabled {
    opacity: 0.35;
    box-shadow: none;
  }

  .send.stop {
    background: rgb(255 255 255 / 0.12);
    box-shadow: 0 0 0 1px var(--border-strong);
    animation: stop-glow 1.8s ease-in-out infinite;
  }

  @keyframes stop-glow {
    50% {
      box-shadow:
        0 0 0 1px var(--border-strong),
        0 0 22px -2px rgb(139 156 255 / 0.55);
    }
  }

  .popover {
    position: fixed;
    inset: auto;
    margin: 0;
    padding: 1rem;
    border-radius: var(--radius-md);
    background: var(--glass-strong);
    color: var(--text-primary);
    display: none;
    overflow: visible;
  }

  .popover:popover-open {
    display: grid;
    gap: 0.9rem;
    animation: rise-in var(--dur-fast) var(--ease-out);
  }

  .popover h3 {
    font-size: var(--text-sm);
    font-weight: 650;
  }

  .popover .field span {
    display: flex;
    justify-content: space-between;
  }

  .popover b {
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
  }

  .popover fieldset {
    border: none;
    padding: 0;
  }

  /* The idle "Enter per enviar" hint gives way to the controls first. */
  @container (max-width: 60rem) {
    .kbd-hint.idle {
      display: none;
    }
  }

  @container (max-width: 44rem) {
    .kbd-hint {
      display: none;
    }
  }

  @media (max-width: 560px) {
    .mode-name {
      display: none;
    }

    .modes label:has(input:checked) .mode-name {
      display: inline;
    }

    .cache span {
      display: none;
    }
  }
</style>
