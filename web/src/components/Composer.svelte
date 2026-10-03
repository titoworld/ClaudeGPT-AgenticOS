<script lang="ts">
  import { untrack } from 'svelte';
  import { app } from '../lib/app.svelte';
  import { ACCEPT } from '../lib/attachments';
  import { charCount, COUNT_FROM, MAX_QUESTION_CHARS } from '../lib/composer.svelte';
  import { AGENT_LABEL, formatInt } from '../lib/format';
  import { i18n } from '../lib/i18n/index.svelte';
  import { placeAbove } from '../lib/popover';
  import { prefs } from '../lib/prefs.svelte';
  import { AGENTS, type TurnMode } from '../lib/protocol';
  import { clamp, formatAmount, LIMITS, parseAmount } from '../lib/settings';
  import { estimateTokens, formatK, MODE_DESCRIPTION, MODE_LABEL } from '../lib/text';
  import { viewer } from '../lib/viewer.svelte';
  import AgentIcon from './AgentIcon.svelte';
  import AttachmentCard from './AttachmentCard.svelte';
  import Icon from './Icon.svelte';
  import ModelMenu from './ModelMenu.svelte';

  const uid = $props.id();
  const popoverId = `${uid}-debate-options`;
  const refinePopoverId = `${uid}-refine-options`;
  const MODES: TurnMode[] = ['solo', 'duel', 'debate', 'refine'];
  const MAX_HEIGHT_RATIO = 0.4;
  /** The word limit a refine turn gets when the owner turns the automatic one off. */
  const DEFAULT_WORDS = 1000;

  let textarea: HTMLTextAreaElement | undefined = $state();
  let optionsButton: HTMLButtonElement | undefined = $state();
  let refineButton: HTMLButtonElement | undefined = $state();
  let fileInput: HTMLInputElement | undefined = $state();
  /** Drag events carrying files over the composer, minus the ones that left it. */
  let dragDepth = $state(0);

  const c = app.composer;
  const tray = c.attachments;
  const running = $derived(!!app.runningTurn);
  const connected = $derived(app.conn.status === 'open');
  /** A turn only starts with the owner's saved settings loaded (audit A11). */
  const settingsReady = $derived(app.settingsStatus === 'ready');
  /** What would be sent: the draft without the space around it. */
  const question = $derived(c.draft.trim());
  /** The question's and its attachments'. */
  const tokens = $derived(estimateTokens(question) + tray.tokens);
  /** Its length as the server counts it (N19). */
  const chars = $derived(charCount(question));
  const tooLong = $derived(chars > MAX_QUESTION_CHARS);
  const showCount = $derived(chars >= MAX_QUESTION_CHARS * COUNT_FROM);
  const canSubmit = $derived(
    running ||
      c.waitingUploads ||
      (connected && settingsReady && question.length > 0 && !tooLong && !tray.failed),
  );
  const t = $derived(i18n.m.composer);
  /** Enter makes a new line with a coarse pointer (phones, tablets): the button sends there (N20). */
  const enterSends = $derived(!c.coarsePointer);
  const sendKeys = $derived(enterSends ? t.keys.enter : `${t.keys.ctrl}+${t.keys.enter}`);
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

  /** Sends (waiting for attachments still uploading), stops the running turn, or stops waiting. */
  function submit(): void {
    if (running) {
      app.cancel();
      return;
    }
    app.sendDraft();
  }

  /**
   * Enter sends where it does not have to make new lines (a fine pointer), Ctrl/Cmd+Enter
   * everywhere (a hardware keyboard on a tablet); Shift+Enter always makes a new line.
   */
  function sends(e: KeyboardEvent): boolean {
    if (e.key !== 'Enter' || e.shiftKey || e.altKey) return false;
    return e.ctrlKey || e.metaKey || enterSends;
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.isComposing) return;
    if (sends(e)) {
      e.preventDefault();
      // While it waits for the attachments, Enter does not stop it: the button or Esc do.
      if (canSubmit && !running && !c.waitingUploads) submit();
    } else if (e.key === 'Escape' && (running || c.waitingUploads)) {
      e.preventDefault();
      if (running) app.cancel();
      else c.waitingUploads = false;
    }
  }

  // ------------------------------------------------------------ attachments

  function onFiles(e: Event): void {
    const input = e.currentTarget as HTMLInputElement;
    const files = [...(input.files ?? [])];
    input.value = ''; // the same file can be picked again
    if (files.length) tray.add(files);
  }

  const carriesFiles = (e: DragEvent): boolean => !!e.dataTransfer && [...e.dataTransfer.types].includes('Files');

  function onDragEnter(e: DragEvent): void {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    dragDepth++;
  }

  function onDragOver(e: DragEvent): void {
    if (!carriesFiles(e)) return;
    e.preventDefault(); // this is where they can be dropped
    e.dataTransfer!.dropEffect = 'copy';
  }

  function onDragLeave(e: DragEvent): void {
    if (carriesFiles(e)) dragDepth = Math.max(0, dragDepth - 1);
  }

  function onDrop(e: DragEvent): void {
    if (!carriesFiles(e)) return;
    e.preventDefault(); // the browser does not open the file
    dragDepth = 0;
    const files = [...e.dataTransfer!.files];
    if (files.length) tray.add(files);
  }

  /** Files dropped anywhere else are not opened by the browser (the app would be left). */
  function refuseDrop(e: DragEvent): void {
    if (e.defaultPrevented || !carriesFiles(e)) return;
    e.preventDefault();
    e.dataTransfer!.dropEffect = 'none';
    if (e.type === 'drop') dragDepth = 0;
  }

  /** A pasted image is attached; text is pasted as text (cells of a spreadsheet bring both). */
  function onPaste(e: ClipboardEvent): void {
    const data = e.clipboardData;
    if (!data || data.getData('text/plain')) return;
    const images = [...data.files].filter((file) => file.type.startsWith('image/'));
    if (!images.length) return;
    e.preventDefault();
    tray.add(images);
  }

  // ------------------------------------------------------------ refine options

  /** The last word limit the owner set, for when they turn the automatic one off again. */
  let lastWords = $state(DEFAULT_WORDS);
  const refineSummary = $derived.by(() => {
    const s = t.refine.summary;
    return [
      s.rounds(c.refineRounds),
      s.budget(formatAmount(c.refineBudget)),
      c.refineWords == null ? s.autoWords : s.words(c.refineWords),
      s.editor(AGENT_LABEL[c.refineEditor]),
      c.refineConverge ? s.converges : s.neverConverges,
    ].join(', ');
  });

  /**
   * A mode's tooltip: its name, then what it does, its first letter lowercased («Solo: one
   * AI answers…») unless it starts with a name («Duel: Claude and ChatGPT…»).
   */
  function modeTitle(mode: TurnMode): string {
    const description = MODE_DESCRIPTION[mode];
    const named = Object.values(AGENT_LABEL).some((name) => description.startsWith(name));
    return `${MODE_LABEL[mode]}: ${named ? description : description.charAt(0).toLowerCase() + description.slice(1)}`;
  }

  /** The budget typed (a decimal comma or point), kept within the server's range; text that is not an amount changes nothing. */
  function setBudget(e: Event & { currentTarget: HTMLInputElement }): void {
    const amount = parseAmount(e.currentTarget.value);
    if (amount != null && Number.isFinite(amount)) {
      c.refineBudget = clamp(Math.round(amount * 100) / 100, LIMITS.refine_budget_eur.min, LIMITS.refine_budget_eur.max);
    }
    e.currentTarget.value = formatAmount(c.refineBudget);
  }

  /** The word limit typed, kept within the server's range; text that is not a number changes nothing. */
  function setWords(e: Event & { currentTarget: HTMLInputElement }): void {
    const words = Number.parseInt(e.currentTarget.value, 10);
    if (Number.isFinite(words)) {
      lastWords = clamp(words, LIMITS.refine_words.min, LIMITS.refine_words.max);
      c.refineWords = lastWords;
    }
    e.currentTarget.value = String(c.refineWords ?? lastWords);
  }

  /** The automatic limit (null), or back to the last one the owner set. */
  function setAutomaticWords(e: Event & { currentTarget: HTMLInputElement }): void {
    if (e.currentTarget.checked) {
      if (c.refineWords != null) lastWords = c.refineWords;
      c.refineWords = null;
    } else {
      c.refineWords = lastWords;
    }
  }
</script>

<svelte:window ondragover={refuseDrop} ondrop={refuseDrop} />

{#if app.settingsStatus === 'error'}
  <div class="settings-error glass" role="alert">
    <Icon name="alert" size={16} />
    <p>
      <strong>{t.settingsError.title}</strong>
      {app.settingsError ?? ''}
      {t.settingsError.detail}
    </p>
    <button type="button" class="btn" disabled={app.settingsLoading} onclick={() => void app.loadSettings()}>
      <Icon name="refresh" size={14} />{app.settingsLoading ? t.settingsError.retrying : t.settingsError.retry}
    </button>
  </div>
{/if}

<form
  class="composer glass"
  class:running
  class:dragging={dragDepth > 0}
  ondragenter={onDragEnter}
  ondragover={onDragOver}
  ondragleave={onDragLeave}
  ondrop={onDrop}
  onsubmit={(e) => {
    e.preventDefault();
    submit();
  }}>
  {#if tray.items.length}
    <ul class="attachments" aria-label={t.attachments}>
      {#each tray.items as item (item.key)}
        {@const uploaded = item.attachment}
        <li>
          <AttachmentCard
            view={item}
            onopen={uploaded ? () => viewer.open(uploaded) : undefined}
            onremove={() => tray.remove(item.key)}
            onretry={() => tray.retry(item.key)} />
        </li>
      {/each}
    </ul>
  {/if}
  <label class="sr-only" for="{uid}-input">{t.question}</label>
  <div class="draft">
    <textarea
      id="{uid}-input"
      bind:this={textarea}
      bind:value={c.draft}
      onkeydown={onKeydown}
      onpaste={onPaste}
      rows="1"
      placeholder={t.placeholder}
      aria-describedby={showCount ? `${uid}-hint ${uid}-count` : `${uid}-hint`}
      enterkeyhint={enterSends ? 'send' : 'enter'}
      spellcheck="true"></textarea>
    <!-- Always in the page, so screen readers announce the text when it appears. -->
    <p class="too-long" role="status">{#if tooLong}<Icon name="alert" size={14} /><span>{t.tooLong(formatInt(MAX_QUESTION_CHARS))}</span>{/if}</p>
    <p class="upload-note" role="status">{#if c.waitingUploads}<span class="note-spinner" aria-hidden="true"></span><span>{t.waitingUploads}</span>{/if}</p>
  </div>

  <div class="toolbar">
    <div class="controls">
      <button
        type="button"
        class="icon-btn attach"
        aria-label={t.attach}
        title={t.attachTitle}
        onclick={() => fileInput?.click()}>
        <Icon name="paperclip" size={18} />
      </button>
      <input bind:this={fileInput} type="file" multiple accept={ACCEPT} hidden onchange={onFiles} />

      <fieldset class="segmented modes">
        <legend class="sr-only">{t.mode}</legend>
        {#each MODES as mode (mode)}
          <label title={modeTitle(mode)}>
            <input type="radio" name="{uid}-mode" value={mode} bind:group={c.mode} />
            <Icon name="mode-{mode}" size={15} />
            <span class="mode-name">{MODE_LABEL[mode]}</span>
          </label>
        {/each}
      </fieldset>

      {#if c.mode === 'solo'}
        <fieldset class="segmented targets">
          <legend class="sr-only">{t.target}</legend>
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
          aria-label={t.debate.options(c.rounds, c.threshold, AGENT_LABEL[c.synthesizer])}>
          <Icon name="sliders" size={15} />
          <span>{t.debate.rounds(c.rounds)} · {c.threshold}</span>
        </button>
      {:else if c.mode === 'refine'}
        <button
          type="button"
          class="btn ghost options-btn refine-options-btn"
          popovertarget={refinePopoverId}
          bind:this={refineButton}
          aria-label={t.refine.options(refineSummary)}>
          <Icon name="sliders" size={15} />
          <span>{t.refine.button(c.refineRounds, formatAmount(c.refineBudget))}</span>
        </button>
      {/if}

      <ModelMenu />

      {#if c.mode !== 'refine'}
        <!-- A refine turn never uses the turn cache: each round reviews a new version. -->
        <label class="cache" title={t.cacheTitle}>
          <input type="checkbox" class="switch" bind:checked={c.useCache} />
          <Icon name="cache" size={15} />
          <span>{t.cache}</span>
        </label>
      {/if}
    </div>

    <div class="actions">
      {#if showCount}
        <span class="char-count" class:over={tooLong} id="{uid}-count">
          {formatInt(chars)} / {formatInt(MAX_QUESTION_CHARS)}<span class="sr-only">{` ${t.characters}`}</span>
        </span>
      {/if}
      <span class="kbd-hint" class:idle={connected && settingsReady && !question && !tray.items.length} id="{uid}-hint">
        {#if !connected}
          {t.status.offline}
        {:else if app.settingsStatus === 'loading'}
          {t.status.loadingSettings}
        {:else if !settingsReady}
          {t.status.noSettings}
        {:else if question || tray.items.length}
          {t.status.tokens(formatK(tokens))}
        {:else if enterSends}
          <kbd>{t.keys.enter}</kbd> {t.status.toSend}
          <span class="sr-only">{t.status.enterHelp}</span>
        {:else}
          <kbd>{t.keys.ctrl}</kbd>+<kbd>{t.keys.enter}</kbd> {t.status.toSend}
          <span class="sr-only">{t.status.ctrlEnterHelp}</span>
        {/if}
      </span>
      <button
        type="submit"
        class="send"
        class:stop={running}
        class:waiting={c.waitingUploads}
        disabled={!canSubmit}
        aria-label={running ? t.send.stop : c.waitingUploads ? t.send.stopWaiting : t.send.label}
        title={running
          ? t.send.stopTitle
          : c.waitingUploads
            ? t.send.waitingTitle
            : !connected
              ? t.status.offline
              : !settingsReady
                ? t.send.noSettings
                : tooLong
                  ? t.send.tooLong
                  : tray.failed
                    ? t.send.failed
                    : t.send.title(sendKeys)}>
        {#if c.waitingUploads}
          <span class="send-spinner" aria-hidden="true"></span>
        {:else}
          <Icon name={running ? 'stop' : 'send'} size={18} />
        {/if}
      </button>
    </div>
  </div>

  {#if dragDepth > 0}
    <div class="drop-overlay" aria-hidden="true">
      <Icon name="paperclip" size={18} />{t.drop}
    </div>
  {/if}
</form>

<div class="popover glass" id={popoverId} popover="auto" {@attach placeAbove(() => optionsButton, 300)}>
  <h3>{t.debate.title}</h3>
  <label class="field">
    <span>{t.debate.reviewRounds} <b>{c.rounds}</b></span>
    <input type="range" min={LIMITS.rounds.min} max={LIMITS.rounds.max} step="1" bind:value={c.rounds} />
    <small class="hint">{t.debate.reviewRoundsHint}</small>
  </label>
  <label class="field">
    <span>{t.debate.threshold} <b>{c.threshold}</b></span>
    <input
      type="range"
      min={LIMITS.consensus_threshold.min}
      max={LIMITS.consensus_threshold.max}
      step="1"
      bind:value={c.threshold} />
    <small class="hint">{t.debate.thresholdHint}</small>
  </label>
  <fieldset class="field">
    <legend class="field-label">{t.debate.synthesizer}</legend>
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

<div class="popover glass refine-options" id={refinePopoverId} popover="auto" {@attach placeAbove(() => refineButton, 340)}>
  <h3>{t.refine.title}</h3>
  <p class="hint">{MODE_DESCRIPTION.refine} {t.refine.final}</p>
  <label class="field">
    <span>{t.refine.maxRounds} <b>{c.refineRounds}</b></span>
    <input
      type="range"
      min={LIMITS.refine_rounds.min}
      max={LIMITS.refine_rounds.max}
      step="1"
      bind:value={c.refineRounds} />
    <small class="hint">{t.refine.roundsHint}</small>
  </label>
  <label class="field">
    <span>{t.refine.budget}</span>
    <input
      class="input amount"
      type="text"
      inputmode="decimal"
      autocomplete="off"
      spellcheck="false"
      value={formatAmount(c.refineBudget)}
      onchange={setBudget} />
    <small class="hint">
      {t.refine.budgetHint(formatAmount(LIMITS.refine_budget_eur.min), formatAmount(LIMITS.refine_budget_eur.max))}
    </small>
  </label>
  <div class="field words">
    <label class="toggle">
      <input type="checkbox" class="switch" checked={c.refineWords == null} onchange={setAutomaticWords} />
      <span>{t.refine.autoWords}</span>
    </label>
    <label class="inline">
      <span>{t.refine.words}</span>
      <input
        class="input"
        type="number"
        inputmode="numeric"
        min={LIMITS.refine_words.min}
        max={LIMITS.refine_words.max}
        step="50"
        disabled={c.refineWords == null}
        placeholder={c.refineWords == null ? t.refine.automatic : ''}
        value={c.refineWords ?? ''}
        onchange={setWords} />
    </label>
    <small class="hint">{t.refine.wordsHint}</small>
  </div>
  <fieldset class="field">
    <legend class="field-label">{t.refine.editor}</legend>
    <div class="segmented">
      {#each AGENTS as agent (agent)}
        <label>
          <input type="radio" name="{uid}-editor" value={agent} bind:group={c.refineEditor} />
          <AgentIcon {agent} size={15} />
          <span>{AGENT_LABEL[agent]}</span>
        </label>
      {/each}
    </div>
    <small class="hint">{t.refine.editorHint}</small>
  </fieldset>
  <label class="toggle">
    <input type="checkbox" class="switch" bind:checked={c.refineConverge} />
    <span>{t.refine.converge}</span>
  </label>
  {#if c.refineConverge}
    <label class="field">
      <span>{t.refine.convergeThreshold} <b>{c.refineThreshold}</b></span>
      <input
        type="range"
        min={LIMITS.refine_threshold.min}
        max={LIMITS.refine_threshold.max}
        step="1"
        bind:value={c.refineThreshold} />
      <small class="hint">{t.refine.convergeHint}</small>
    </label>
  {:else}
    <small class="hint">{t.refine.neverConvergesHint}</small>
  {/if}
</div>

<style>
  .composer {
    position: relative;
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

  .composer.dragging {
    border-color: rgb(139 156 255 / 0.7);
    box-shadow:
      var(--shadow),
      0 0 0 3px rgb(139 156 255 / 0.2);
  }

  .attachments {
    display: flex;
    gap: 0.5rem;
    margin: 0;
    padding: 0.1rem 0.1rem 0.3rem;
    list-style: none;
    overflow-x: auto;
    overscroll-behavior-x: contain;
  }

  .drop-overlay {
    position: absolute;
    inset: 0;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 2px dashed rgb(139 156 255 / 0.75);
    border-radius: inherit;
    background: rgb(13 15 23 / 0.88);
    color: var(--text-primary);
    font-size: var(--text-sm);
    font-weight: 600;
    pointer-events: none;
  }

  .attach {
    width: 2rem;
    height: 2rem;
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

  .draft {
    display: grid;
    min-width: 0;
  }

  .too-long {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin: 0.2rem 0.5rem 0;
    font-size: var(--text-xs);
    color: #ffc9ce;
  }

  /* Empty (the question fits): it takes no room. */
  .too-long:empty {
    margin: 0;
  }

  .too-long > :global(.icon) {
    flex: none;
    color: var(--critical);
  }

  .upload-note {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    margin: 0.2rem 0.5rem 0;
    font-size: var(--text-xs);
    color: var(--accent);
  }

  /* Not waiting for attachments: it takes no room. */
  .upload-note:empty {
    margin: 0;
  }

  .note-spinner {
    flex: none;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 2px solid rgb(139 156 255 / 0.25);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }

  .char-count {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .char-count.over {
    color: var(--critical);
    font-weight: 600;
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

  .settings-error {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 0.5rem;
    padding: 0.55rem 0.6rem 0.55rem 0.85rem;
    border-radius: var(--radius-md);
    border-color: rgb(255 93 108 / 0.45);
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .settings-error > :global(.icon) {
    flex: none;
    color: var(--critical);
  }

  .settings-error p {
    flex: 1;
  }

  .settings-error strong {
    color: #ffc9ce;
    font-weight: 600;
  }

  .settings-error .btn {
    flex: none;
    min-height: 2rem;
    padding: 0.3rem 0.7rem;
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

  .send.waiting {
    background: rgb(255 255 255 / 0.12);
    box-shadow: 0 0 0 1px var(--border-strong);
  }

  .send-spinner {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.25);
    border-top-color: #fff;
    animation: spin 0.8s linear infinite;
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

  /* More options than the debate's: it scrolls when the screen is short. */
  .popover.refine-options {
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .refine-options .toggle {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    font-size: var(--text-sm);
    color: var(--text-secondary);
    cursor: pointer;
  }

  .refine-options .words {
    gap: 0.45rem;
  }

  .refine-options .inline {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .refine-options .inline .input {
    width: 7.5rem;
    min-height: 2.1rem;
    padding: 0.3rem 0.55rem;
  }

  .refine-options .input:disabled {
    opacity: 0.5;
  }

  .refine-options .amount {
    width: 7.5rem;
    min-height: 2.1rem;
    padding: 0.3rem 0.55rem;
  }

  /* The idle "Enter to send" hint gives way to the controls first. */
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
