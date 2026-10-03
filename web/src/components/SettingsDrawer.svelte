<script lang="ts">
  import { i18n } from '../lib/i18n/index.svelte';
  import LanguagePicker from './LanguagePicker.svelte';
  import { untrack } from 'svelte';
  import { app, SettingsConflictError } from '../lib/app.svelte';
  import { errorMessage } from '../lib/conversations.svelte';
  import { fxText } from '../lib/costs';
  import { lightDismiss, syncDialog } from '../lib/dialog';
  import { AGENT_LABEL, formatInt } from '../lib/format';
  import { prefs } from '../lib/prefs.svelte';
  import { AGENTS, type Agent, type DefaultMode, type RuntimeSettings } from '../lib/protocol';
  import { cleanSettings, formatAmount, LIMITS, normalizeSettings, validateSettings } from '../lib/settings';
  import { MODE_LABEL, PROVIDER_MODE_LABEL } from '../lib/text';
  import type { SceneQuality } from '../scene/types';
  import AgentIcon from './AgentIcon.svelte';
  import AgentLabel from './AgentLabel.svelte';
  import AmountInput from './AmountInput.svelte';
  import Icon from './Icon.svelte';
  import ModelPicker from './ModelPicker.svelte';
  import PriceTable from './PriceTable.svelte';

  const uid = $props.id();
  /** Never Refine: a turn that runs until the owner stops it is only started on purpose. */
  const MODES: DefaultMode[] = ['solo', 'duel', 'debate'];
  /** The word limit a refine turn gets when the owner turns the automatic one off. */
  const DEFAULT_WORDS = 1000;
  const QUALITIES: SceneQuality[] = ['high', 'low', 'off'];

  const t = $derived(i18n.m.settings);
  /** A field's label with its range of whole numbers, as the language writes them. */
  const ranged = (label: string, range: { min: number; max: number }) =>
    t.withRange(label, formatInt(range.min), formatInt(range.max));

  let dialog: HTMLDialogElement | undefined = $state();
  /**
   * The form shows the settings fetched when the drawer opened: until they arrive it
   * cannot be edited or saved, so a save never stands in the built-in defaults or an
   * older copy for what is stored (audit A11, N5).
   */
  let formState: 'loading' | 'ready' | 'error' = $state('loading');
  let form: RuntimeSettings = $state(copy(app.settings));
  let submitted = $state(false);
  let saving = $state(false);
  /** What the last save did; its text is made when shown, so it follows a change of language. */
  let result: { kind: 'ok' | 'warn' | 'error'; text: () => string } | null = $state(null);
  /** Bumped at every opening: answers meant for an earlier one leave the form alone. */
  let opening = 0;
  /** Model pickers with a custom id that is not valid yet. */
  let pickerInvalid: Record<string, boolean> = $state(noInvalidPickers());
  /** The last word limit set, for when the owner turns the automatic one off again. */
  let lastWords = $state(DEFAULT_WORDS);
  const automaticWords = $derived(form.refine.max_words === null);

  const errors = $derived(validateSettings(form));
  const shownErrors = $derived(submitted ? errors : {});
  const hasErrors = $derived(Object.keys(errors).length > 0 || Object.values(pickerInvalid).some(Boolean));
  const currentFx = $derived(app.pricing?.fx ?? app.fx);

  function copy(s: RuntimeSettings): RuntimeSettings {
    return normalizeSettings($state.snapshot(s));
  }

  function noInvalidPickers(): Record<string, boolean> {
    return Object.fromEntries(AGENTS.flatMap((a) => [`models.${a}`, `fast_models.${a}`]).map((k) => [k, false]));
  }

  const modeOf = (agent: Agent) =>
    app.catalog?.[agent].mode ?? app.providers.find((p) => p.agent === agent)?.mode ?? null;

  // "Default" here means the provider's own model. The catalog's defaults already
  // include the saved choice, so they only name it while nothing is saved (and the
  // catalog was fetched after the last save that changed it).
  const catalogDefaults = (agent: Agent) => (app.catalogStale ? null : (app.catalog?.[agent] ?? null));
  const providerDefault = (agent: Agent): string | null =>
    app.providers.find((p) => p.agent === agent)?.model ||
    (app.settings.models[agent] == null ? (catalogDefaults(agent)?.default_model ?? null) : null);
  const providerFastDefault = (agent: Agent): string | null =>
    app.settings.fast_models[agent] == null ? (catalogDefaults(agent)?.fast_model ?? null) : null;

  $effect(() => syncDialog(dialog, app.settingsOpen));

  // Every opening fetches the settings (and prices) again and builds the form from
  // them, so a change saved in another tab or device since is not undone (N5).
  $effect(() => {
    if (!app.settingsOpen) return;
    // Untracked: a catalog refresh from inside the drawer must not reset the form.
    untrack(() => {
      void reload();
      void app.loadModels();
      void app.loadPricing();
    });
  });

  // A load that works while the drawer shows the error (an automatic retry, the
  // composer's button) fills the form.
  $effect(() => {
    const settings = app.settings;
    untrack(() => {
      if (app.settingsOpen && formState === 'error' && app.settingsStatus === 'ready') fill(settings);
    });
  });

  async function reload(): Promise<void> {
    const ticket = ++opening;
    formState = 'loading';
    result = null;
    const loaded = await app.loadSettings();
    if (ticket !== opening || !app.settingsOpen) return;
    if (loaded) fill(app.settings);
    else formState = 'error';
  }

  /** The automatic word limit (null), or a number (the last one set); an empty input is NaN, which validation reports. */
  function setAutomaticWords(e: Event & { currentTarget: HTMLInputElement }): void {
    const words = form.refine.max_words;
    if (e.currentTarget.checked) {
      if (words !== null && Number.isFinite(words)) lastWords = words;
      form.refine.max_words = null;
    } else {
      form.refine.max_words = lastWords;
    }
  }

  function setWords(e: Event & { currentTarget: HTMLInputElement }): void {
    form.refine.max_words = e.currentTarget.valueAsNumber; // NaN when empty
  }

  /** Shows `settings` (the stored ones) in the form, ready to edit. */
  function fill(settings: RuntimeSettings): void {
    form = copy(settings);
    lastWords = settings.refine.max_words ?? DEFAULT_WORDS;
    submitted = false;
    pickerInvalid = noInvalidPickers();
    formState = 'ready';
  }

  async function save(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (formState !== 'ready' || saving) return;
    submitted = true;
    result = null;
    if (hasErrors) return;
    saving = true;
    const ticket = opening;
    try {
      // The form carries the revision it was loaded with: the server refuses it (409)
      // if the settings changed since.
      const saved = await app.saveSettings(cleanSettings($state.snapshot(form)));
      if (ticket !== opening) return;
      fill(saved); // with the new revision, for the next save
      result = { kind: 'ok', text: () => t.drawer.saved };
    } catch (err) {
      if (ticket !== opening) return;
      if (err instanceof SettingsConflictError) {
        // Show what is stored now, to review and save again, instead of overwriting it.
        fill(app.settings);
        const message = err.message;
        result = { kind: 'warn', text: () => message };
      } else {
        result = { kind: 'error', text: () => errorMessage(err, t.drawer.saveFailed) };
      }
    } finally {
      saving = false;
    }
  }
</script>

<dialog
  bind:this={dialog}
  class="drawer"
  aria-labelledby="{uid}-title"
  onclose={() => (app.settingsOpen = false)}
  {@attach lightDismiss}>
  <div class="panel glass">
    <header>
      <h2 id="{uid}-title"><Icon name="settings" size={18} />{t.drawer.title}</h2>
      <button type="button" class="icon-btn" onclick={() => dialog?.close()} aria-label={t.drawer.close}>
        <Icon name="x" />
      </button>
    </header>

    <form class="form" onsubmit={save} novalidate>
      <div class="body">
        {#if formState !== 'ready'}
          <div class="load-state" class:error={formState === 'error'} role={formState === 'error' ? 'alert' : 'status'}>
            {#if formState === 'loading'}
              <span class="spinner" aria-hidden="true"></span>
              <p>{t.drawer.loading}</p>
            {:else}
              <Icon name="alert" size={16} />
              <p><strong>{t.drawer.loadFailed}</strong> {app.settingsError ?? ''}</p>
              <button type="button" class="btn" disabled={app.settingsLoading} onclick={() => void reload()}>
                <Icon name="refresh" size={14} />{app.settingsLoading ? t.drawer.retrying : t.drawer.retry}
              </button>
            {/if}
          </div>
        {:else}
          <section>
            <h3>{t.defaults.title}</h3>
            <fieldset class="field">
              <legend class="field-label">{t.defaults.mode}</legend>
              <div class="segmented">
                {#each MODES as mode (mode)}
                  <label>
                    <input type="radio" name="{uid}-mode" value={mode} bind:group={form.default_mode} />
                    <Icon name="mode-{mode}" size={15} />{MODE_LABEL[mode]}
                  </label>
                {/each}
              </div>
            </fieldset>
            <fieldset class="field">
              <legend class="field-label">{t.defaults.soloAgent}</legend>
              <div class="segmented">
                {#each AGENTS as agent (agent)}
                  <label>
                    <input type="radio" name="{uid}-target" value={agent} bind:group={form.default_target} />
                    <AgentIcon {agent} size={15} />{AGENT_LABEL[agent]}
                  </label>
                {/each}
              </div>
            </fieldset>
            <label class="toggle">
              <input type="checkbox" class="switch" bind:checked={form.use_cache} />
              <span>
                <strong>{t.defaults.cache}</strong>
                <small class="hint">{t.defaults.cacheHint}</small>
              </span>
            </label>
          </section>

          <section>
            <h3>{MODE_LABEL.debate}</h3>
            <label class="field">
              <span>{ranged(t.council.rounds, LIMITS.rounds)}</span>
              <input
                class="input"
                type="number"
                inputmode="numeric"
                min={LIMITS.rounds.min}
                max={LIMITS.rounds.max}
                step="1"
                bind:value={form.debate.rounds}
                aria-invalid={!!shownErrors.rounds}
                aria-describedby="{uid}-rounds-err" />
              <small class="error-text" id="{uid}-rounds-err">{shownErrors.rounds ?? ''}</small>
            </label>
            <label class="field">
              <span>{ranged(t.council.threshold, LIMITS.consensus_threshold)}</span>
              <input
                class="input"
                type="number"
                inputmode="numeric"
                min={LIMITS.consensus_threshold.min}
                max={LIMITS.consensus_threshold.max}
                step="1"
                bind:value={form.debate.consensus_threshold}
                aria-invalid={!!shownErrors.consensus_threshold}
                aria-describedby="{uid}-thr-err {uid}-thr-hint" />
              <small class="hint" id="{uid}-thr-hint">{t.council.thresholdHint}</small>
              <small class="error-text" id="{uid}-thr-err">{shownErrors.consensus_threshold ?? ''}</small>
            </label>
            <fieldset class="field">
              <legend class="field-label">{t.council.synthesizer}</legend>
              <div class="segmented">
                {#each AGENTS as agent (agent)}
                  <label>
                    <input type="radio" name="{uid}-synth" value={agent} bind:group={form.debate.synthesizer} />
                    <AgentIcon {agent} size={15} />{AGENT_LABEL[agent]}
                  </label>
                {/each}
              </div>
            </fieldset>
            <fieldset class="field">
              <legend class="field-label">{t.council.pdf}</legend>
              <div class="segmented">
                <label>
                  <input type="radio" name="{uid}-pdf" value="full" bind:group={form.pdf_in_revisions} />{t.council.pdfFull}
                </label>
                <label>
                  <input type="radio" name="{uid}-pdf" value="text" bind:group={form.pdf_in_revisions} />{t.council.pdfText}
                </label>
              </div>
              <small class="hint">{t.council.pdfHint}</small>
            </fieldset>
          </section>

          <section>
            <h3>{MODE_LABEL.refine}</h3>
            <p class="hint">{t.refine.intro}</p>
            <label class="field">
              <span>{ranged(t.refine.maxRounds, LIMITS.refine_rounds)}</span>
              <input
                class="input narrow"
                type="number"
                inputmode="numeric"
                min={LIMITS.refine_rounds.min}
                max={LIMITS.refine_rounds.max}
                step="1"
                bind:value={form.refine.max_rounds}
                aria-invalid={!!shownErrors['refine.max_rounds']}
                aria-describedby="{uid}-refine-rounds-hint {uid}-refine-rounds-err" />
              <small class="hint" id="{uid}-refine-rounds-hint">{t.refine.maxRoundsHint}</small>
              <small class="error-text" id="{uid}-refine-rounds-err">{shownErrors['refine.max_rounds'] ?? ''}</small>
            </label>
            <label class="field refine-budget">
              <span>{t.refine.budget}</span>
              <!-- An emptied amount is null until it is one again: validation reports it. -->
              <AmountInput
                class="input"
                bind:value={() => form.refine.budget_eur, (v) => (form.refine.budget_eur = v as number)}
                aria-invalid={!!shownErrors['refine.budget_eur']}
                aria-describedby="{uid}-refine-budget-hint {uid}-refine-budget-err" />
              <small class="hint" id="{uid}-refine-budget-hint">
                {t.refine.budgetHint(formatAmount(LIMITS.refine_budget_eur.min), formatAmount(LIMITS.refine_budget_eur.max))}
              </small>
              <small class="error-text" id="{uid}-refine-budget-err">{shownErrors['refine.budget_eur'] ?? ''}</small>
            </label>
            <label class="toggle">
              <input type="checkbox" class="switch" checked={automaticWords} onchange={setAutomaticWords} />
              <span>
                <strong>{t.refine.automaticWords}</strong>
                <small class="hint">{t.refine.automaticWordsHint}</small>
              </span>
            </label>
            <label class="field">
              <span>{ranged(t.refine.words, LIMITS.refine_words)}</span>
              <input
                class="input narrow"
                type="number"
                inputmode="numeric"
                min={LIMITS.refine_words.min}
                max={LIMITS.refine_words.max}
                step="50"
                disabled={automaticWords}
                placeholder={automaticWords ? t.refine.automatic : ''}
                value={automaticWords || Number.isNaN(form.refine.max_words) ? '' : form.refine.max_words}
                oninput={setWords}
                aria-invalid={!!shownErrors['refine.max_words']}
                aria-describedby="{uid}-refine-words-err" />
              <small class="error-text" id="{uid}-refine-words-err">{shownErrors['refine.max_words'] ?? ''}</small>
            </label>
            <fieldset class="field">
              <legend class="field-label">{t.refine.editor}</legend>
              <div class="segmented">
                {#each AGENTS as agent (agent)}
                  <label>
                    <input type="radio" name="{uid}-editor" value={agent} bind:group={form.refine.editor} />
                    <AgentIcon {agent} size={15} />{AGENT_LABEL[agent]}
                  </label>
                {/each}
              </div>
              <small class="hint">{t.refine.editorHint}</small>
            </fieldset>
            <label class="toggle">
              <input type="checkbox" class="switch" bind:checked={form.refine.stop_on_convergence} />
              <span>
                <strong>{t.refine.stopOnConvergence}</strong>
                <small class="hint">{t.refine.stopOnConvergenceHint}</small>
              </span>
            </label>
            <label class="field">
              <span>{ranged(t.refine.threshold, LIMITS.refine_threshold)}</span>
              <input
                class="input narrow"
                type="number"
                inputmode="numeric"
                min={LIMITS.refine_threshold.min}
                max={LIMITS.refine_threshold.max}
                step="1"
                disabled={!form.refine.stop_on_convergence}
                bind:value={form.refine.convergence_threshold}
                aria-invalid={!!shownErrors['refine.convergence_threshold']}
                aria-describedby="{uid}-refine-threshold-err" />
              <small class="error-text" id="{uid}-refine-threshold-err">{shownErrors['refine.convergence_threshold'] ?? ''}</small>
            </label>
          </section>

          <section>
            <h3>{t.history.title}</h3>
            <label class="field">
              <span>{t.history.compaction}</span>
              <input
                class="input"
                type="number"
                inputmode="numeric"
                min={LIMITS.compaction_threshold_tokens.min}
                max={LIMITS.compaction_threshold_tokens.max}
                step="500"
                bind:value={form.compaction_threshold_tokens}
                aria-invalid={!!shownErrors.compaction_threshold_tokens}
                aria-describedby="{uid}-comp-err {uid}-comp-hint" />
              <small class="hint" id="{uid}-comp-hint">
                {t.history.compactionHint(
                  formatInt(LIMITS.compaction_threshold_tokens.min),
                  formatInt(LIMITS.compaction_threshold_tokens.max),
                )}
              </small>
              <small class="error-text" id="{uid}-comp-err">{shownErrors.compaction_threshold_tokens ?? ''}</small>
            </label>
          </section>

          <section>
            <div class="section-head">
              <h3>{t.models.title}</h3>
              <button
                type="button"
                class="link-btn"
                onclick={() => void app.loadModels(true)}
                disabled={app.catalogLoading}>
                <Icon name="refresh" size={13} />{app.catalogLoading ? t.models.refreshing : t.models.refresh}
              </button>
            </div>
            <p class="hint">{t.models.hint}</p>
            {#each AGENTS as agent (agent)}
              {@const models = app.catalog?.[agent] ?? null}
              {@const mode = modeOf(agent)}
              <div class="agent-block">
                <div class="agent-head">
                  <AgentLabel {agent} size={15} />
                  {#if mode}<span class="chip">{PROVIDER_MODE_LABEL[mode]}</span>{/if}
                  {#if models && !models.live}
                    <span class="chip warn" title={t.models.fallbackTitle}>{t.models.fallback}</span>
                  {/if}
                </div>
                <div class="pair">
                  <ModelPicker
                    inline
                    label={t.models.main}
                    value={form.models[agent]}
                    onchange={(v) => (form.models[agent] = v)}
                    {models}
                    defaultModel={providerDefault(agent)}
                    bind:invalid={pickerInvalid[`models.${agent}`]}
                    showErrors={submitted} />
                  <ModelPicker
                    inline
                    label={t.models.fast}
                    value={form.fast_models[agent]}
                    onchange={(v) => (form.fast_models[agent] = v)}
                    {models}
                    defaultModel={providerFastDefault(agent)}
                    bind:invalid={pickerInvalid[`fast_models.${agent}`]}
                    showErrors={submitted} />
                </div>
              </div>
            {/each}
            {#if app.catalogError}
              <p class="error-text" role="status">{app.catalogError}</p>
            {/if}
          </section>

          <section>
            <h3>{t.costs.title}</h3>
            <fieldset class="field">
              <legend class="field-label">{t.costs.fxMode}</legend>
              <div class="segmented">
                <label>
                  <input type="radio" name="{uid}-fxmode" value="auto" bind:group={form.fx.mode} />{t.costs.fxAuto}
                </label>
                <label>
                  <input type="radio" name="{uid}-fxmode" value="manual" bind:group={form.fx.mode} />{t.costs.fxManual}
                </label>
              </div>
            </fieldset>
            <label class="field">
              <span>{form.fx.mode === 'auto' ? t.costs.fallbackRate : t.costs.rate}</span>
              <input
                class="input narrow"
                type="number"
                inputmode="decimal"
                min={LIMITS.eur_per_usd.min}
                max={LIMITS.eur_per_usd.max}
                step="0.0001"
                bind:value={form.fx.eur_per_usd}
                aria-invalid={!!shownErrors.eur_per_usd}
                aria-describedby="{uid}-fx-hint {uid}-fx-err" />
              <small class="hint" id="{uid}-fx-hint">
                {form.fx.mode === 'auto' ? t.costs.fxAutoHint : t.costs.fxManualHint}
              </small>
              <small class="error-text" id="{uid}-fx-err">{shownErrors.eur_per_usd ?? ''}</small>
            </label>
            {#if currentFx}
              <p class="note"><Icon name="info" size={15} /><span>{t.costs.now(fxText(currentFx))}</span></p>
            {/if}
          </section>

          <section>
            <h3>{t.prices.title}</h3>
            <p class="hint">{t.prices.hint}</p>
            <PriceTable
              bind:prices={form.prices}
              pricing={app.pricing}
              errors={shownErrors}
              loadError={app.pricing ? null : app.pricingError} />
          </section>

          <section>
            <h3>{t.money.title}</h3>
            <p class="hint">{t.money.hint}</p>
            <div class="money-grid">
              <span></span>
              <span class="col-head" id="{uid}-budget-head">{t.money.budgetHead}</span>
              <span class="col-head" id="{uid}-plan-head">{t.money.planHead}</span>
              {#each AGENTS as agent (agent)}
                {@const budgetErr = shownErrors[`budgets_eur.${agent}`]}
                {@const planErr = shownErrors[`plans_eur.${agent}`]}
                <AgentLabel {agent} size={15} />
                <div class="money">
                  <AmountInput
                    class="input"
                    placeholder="—"
                    bind:value={form.budgets_eur[agent]}
                    aria-label={t.money.budgetLabel(AGENT_LABEL[agent])}
                    aria-invalid={!!budgetErr} />
                  <span class="unit" aria-hidden="true">€</span>
                  {#if budgetErr}<small class="error-text">{budgetErr}</small>{/if}
                </div>
                <div class="money">
                  <AmountInput
                    class="input"
                    placeholder="—"
                    bind:value={form.plans_eur[agent]}
                    aria-label={t.money.planLabel(AGENT_LABEL[agent])}
                    aria-invalid={!!planErr} />
                  <span class="unit" aria-hidden="true">€</span>
                  {#if planErr}<small class="error-text">{planErr}</small>{/if}
                </div>
              {/each}
            </div>
            <p class="hint">{t.money.footer}</p>
          </section>
        {/if}

        <section class="local">
          <h3>{i18n.m.common.language}</h3>
          <LanguagePicker />
        </section>

        <section class="local">
          <h3>{t.effects.title}</h3>
          <p class="hint">{t.effects.hint}</p>
          <fieldset class="field">
            <legend class="sr-only">{t.effects.quality}</legend>
            <div class="segmented">
              {#each QUALITIES as q (q)}
                <label>
                  <input
                    type="radio"
                    name="{uid}-fx"
                    value={q}
                    checked={prefs.effects === q}
                    onchange={() => prefs.setEffects(q)} />
                  {t.effects.levels[q]}
                </label>
              {/each}
            </div>
          </fieldset>
          <p class="note">
            <Icon name="info" size={15} />
            <span>{prefs.reducedMotion ? t.effects.reducedMotionOn : t.effects.reducedMotion}</span>
          </p>
        </section>
      </div>

      <footer class="save-row">
        {#if result}
          <p class="result {result.kind}" role="status">
            <Icon name={result.kind === 'ok' ? 'check' : 'alert'} size={15} />{result.text()}
          </p>
        {:else if formState === 'ready' && submitted && hasErrors}
          <p class="result error" role="status"><Icon name="alert" size={15} />{t.drawer.checkFields}</p>
        {/if}
        <button type="submit" class="btn primary" disabled={saving || formState !== 'ready'}>
          {saving ? t.drawer.saving : t.drawer.save}
        </button>
      </footer>
    </form>
  </div>
</dialog>

<style>
  .drawer {
    margin: 0 0 0 auto;
    width: min(32rem, 100vw);
    height: 100dvh;
    max-height: 100dvh;
  }

  .panel {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    height: 100%;
    border-width: 0 0 0 1px;
    border-radius: var(--radius-lg) 0 0 var(--radius-lg);
  }

  .drawer[open] .panel {
    animation: slide-in var(--dur-med) var(--ease-out);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 1rem 1rem 0.8rem 1.3rem;
    border-bottom: 1px solid var(--border);
  }

  h2 {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: var(--text-lg);
    font-weight: 650;
  }

  .form {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto;
    min-height: 0;
  }

  .body {
    display: grid;
    align-content: start;
    gap: 1.4rem;
    padding: 1.2rem 1.3rem 1.6rem;
    overflow-y: auto;
  }

  section {
    display: grid;
    gap: 0.9rem;
  }

  h3 {
    font-size: var(--text-xs);
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
  }

  fieldset {
    margin: 0;
    padding: 0;
    border: none;
    min-width: 0;
  }

  fieldset .segmented {
    margin-top: 0.35rem;
  }

  .toggle {
    display: flex;
    align-items: flex-start;
    gap: 0.7rem;
    cursor: pointer;
  }

  .toggle span {
    display: grid;
    gap: 0.15rem;
    font-size: var(--text-sm);
  }

  .toggle .switch {
    margin-top: 0.15rem;
  }

  .save-row {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.8rem;
    padding: 0.8rem 1.3rem max(0.9rem, env(safe-area-inset-bottom));
    border-top: 1px solid var(--border);
    background: rgb(13 15 23 / 0.6);
  }

  .result {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-right: auto;
    font-size: var(--text-sm);
    color: #ffb3ba;
  }

  .result :global(.icon) {
    flex: none;
  }

  .result.ok {
    color: #9ff0c9;
  }

  .result.warn {
    color: var(--warning);
  }

  .load-state {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.9rem 1rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .load-state.error {
    flex-wrap: wrap;
    border-color: rgb(255 93 108 / 0.45);
    background: rgb(255 93 108 / 0.08);
  }

  .load-state p {
    flex: 1;
    min-width: 12rem;
  }

  .load-state strong {
    color: #ffc9ce;
    font-weight: 600;
  }

  .load-state.error > :global(.icon) {
    flex: none;
    color: var(--critical);
  }

  .spinner {
    flex: none;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.15);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }

  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .link-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.2rem 0.35rem;
    border: none;
    border-radius: 6px;
    background: none;
    font-size: var(--text-xs);
    color: var(--accent);
  }

  .link-btn:hover:not(:disabled) {
    background: rgb(139 156 255 / 0.1);
  }

  .agent-block {
    display: grid;
    gap: 0.55rem;
    padding: 0.75rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: rgb(255 255 255 / 0.02);
  }

  .agent-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: var(--text-sm);
  }

  .agent-head .chip {
    font-size: 0.68rem;
    font-weight: 550;
  }

  .pair {
    display: grid;
    gap: 0.6rem;
  }

  .narrow,
  /* The input lives in AmountInput, outside this component's scope. */
  .refine-budget :global(.input) {
    max-width: 10rem;
  }

  .money-grid {
    display: grid;
    grid-template-columns: auto repeat(2, minmax(0, 1fr));
    align-items: center;
    gap: 0.5rem 0.7rem;
    font-size: var(--text-sm);
  }

  .col-head {
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-muted);
  }

  .money {
    position: relative;
    display: grid;
    gap: 0.2rem;
  }

  /* The input lives in AmountInput, outside this component's scope. */
  .money :global(.input) {
    min-height: 2.25rem;
    padding: 0.4rem 1.7rem 0.4rem 0.65rem;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .unit {
    position: absolute;
    top: 0;
    right: 0.65rem;
    height: 2.25rem;
    display: grid;
    place-items: center;
    font-size: var(--text-sm);
    color: var(--text-muted);
    pointer-events: none;
  }

  .local {
    padding-top: 1.2rem;
    border-top: 1px solid var(--border);
  }

  .note {
    display: flex;
    gap: 0.5rem;
    align-items: flex-start;
    font-size: var(--text-sm);
    color: var(--text-secondary);
  }

  .note :global(.icon) {
    margin-top: 0.2rem;
    color: var(--accent);
  }

  @keyframes slide-in {
    from {
      transform: translateX(24px);
      opacity: 0;
    }
    to {
      transform: none;
      opacity: 1;
    }
  }

  @media (max-width: 560px) {
    .panel {
      border-radius: 0;
    }
  }
</style>
