<script lang="ts">
  import { untrack } from 'svelte';
  import { app } from '../lib/app.svelte';
  import { errorMessage } from '../lib/conversations.svelte';
  import { fxText } from '../lib/costs';
  import { lightDismiss, syncDialog } from '../lib/dialog';
  import { AGENT_LABEL } from '../lib/format';
  import { EFFECTS_LABEL, prefs } from '../lib/prefs.svelte';
  import { AGENTS, type Agent, type RuntimeSettings, type TurnMode } from '../lib/protocol';
  import { cleanSettings, LIMITS, normalizeSettings, validateSettings } from '../lib/settings';
  import { MODE_LABEL, PROVIDER_MODE_LABEL } from '../lib/text';
  import type { SceneQuality } from '../scene/types';
  import AgentIcon from './AgentIcon.svelte';
  import AgentLabel from './AgentLabel.svelte';
  import AmountInput from './AmountInput.svelte';
  import Icon from './Icon.svelte';
  import ModelPicker from './ModelPicker.svelte';
  import PriceTable from './PriceTable.svelte';

  const uid = $props.id();
  const MODES: TurnMode[] = ['solo', 'duel', 'debate'];
  const QUALITIES: SceneQuality[] = ['high', 'low', 'off'];

  let dialog: HTMLDialogElement | undefined = $state();
  let form: RuntimeSettings = $state(copy(app.settings));
  let submitted = $state(false);
  let saving = $state(false);
  let result: { ok: boolean; text: string } | null = $state(null);
  /** Model pickers with a custom id that is not valid yet. */
  let pickerInvalid: Record<string, boolean> = $state(noInvalidPickers());

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

  // "Per defecte" here means the provider's own model. The catalog's defaults already
  // include the saved choice, so they only name it while nothing is saved (and the
  // catalog was fetched after the last save that changed it).
  const catalogDefaults = (agent: Agent) => (app.catalogStale ? null : (app.catalog?.[agent] ?? null));
  const providerDefault = (agent: Agent): string | null =>
    app.providers.find((p) => p.agent === agent)?.model ||
    (app.settings.models[agent] == null ? (catalogDefaults(agent)?.default_model ?? null) : null);
  const providerFastDefault = (agent: Agent): string | null =>
    app.settings.fast_models[agent] == null ? (catalogDefaults(agent)?.fast_model ?? null) : null;

  $effect(() => syncDialog(dialog, app.settingsOpen));

  // Fresh copy of the server settings every time the drawer opens.
  $effect(() => {
    if (!app.settingsOpen) return;
    form = copy(untrack(() => app.settings));
    submitted = false;
    result = null;
    pickerInvalid = noInvalidPickers();
    // Untracked: a catalog refresh from inside the drawer must not reset the form.
    untrack(() => {
      void app.loadModels();
      void app.loadPricing();
    });
  });

  async function save(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    submitted = true;
    result = null;
    if (hasErrors) return;
    saving = true;
    try {
      await app.saveSettings(cleanSettings($state.snapshot(form)));
      result = { ok: true, text: 'Configuració desada.' };
    } catch (err) {
      result = { ok: false, text: errorMessage(err, "No s'ha pogut desar la configuració.") };
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
      <h2 id="{uid}-title"><Icon name="settings" size={18} />Configuració</h2>
      <button type="button" class="icon-btn" onclick={() => dialog?.close()} aria-label="Tanca la configuració">
        <Icon name="x" />
      </button>
    </header>

    <form class="form" onsubmit={save} novalidate>
      <div class="body">
        <section>
          <h3>Per defecte</h3>
          <fieldset class="field">
            <legend class="field-label">Mode</legend>
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
            <legend class="field-label">Agent del mode Solo</legend>
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
              <strong>Memòria cau de respostes</strong>
              <small class="hint">Una pregunta idèntica en el mateix context es respon sense cridar cap model.</small>
            </span>
          </label>
        </section>

        <section>
          <h3>Consell</h3>
          <label class="field">
            <span>Rondes de revisió ({LIMITS.rounds.min}–{LIMITS.rounds.max})</span>
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
            <span>Llindar de consens ({LIMITS.consensus_threshold.min}–{LIMITS.consensus_threshold.max})</span>
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
            <small class="hint" id="{uid}-thr-hint">Si tots dos agents arriben a aquest acord, s'aturen les rondes.</small>
            <small class="error-text" id="{uid}-thr-err">{shownErrors.consensus_threshold ?? ''}</small>
          </label>
          <fieldset class="field">
            <legend class="field-label">Sintetitzador</legend>
            <div class="segmented">
              {#each AGENTS as agent (agent)}
                <label>
                  <input type="radio" name="{uid}-synth" value={agent} bind:group={form.debate.synthesizer} />
                  <AgentIcon {agent} size={15} />{AGENT_LABEL[agent]}
                </label>
              {/each}
            </div>
          </fieldset>
        </section>

        <section>
          <h3>Historial</h3>
          <label class="field">
            <span>Compacta l'historial a partir de (tokens)</span>
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
              Entre {LIMITS.compaction_threshold_tokens.min.toLocaleString('ca-ES')} i {LIMITS.compaction_threshold_tokens.max.toLocaleString(
                'ca-ES',
              )}. Els missatges antics es resumeixen per gastar menys.
            </small>
            <small class="error-text" id="{uid}-comp-err">{shownErrors.compaction_threshold_tokens ?? ''}</small>
          </label>
        </section>

        <section>
          <div class="section-head">
            <h3>Models</h3>
            <button
              type="button"
              class="link-btn"
              onclick={() => void app.loadModels(true)}
              disabled={app.catalogLoading}>
              <Icon name="refresh" size={13} />{app.catalogLoading ? 'Actualitzant…' : 'Actualitza la llista'}
            </button>
          </div>
          <p class="hint">
            La llista es consulta al proveïdor. Un model nou que encara no hi surti es pot escriure a
            «Personalitzat…». El principal respon les preguntes; el ràpid fa les tasques internes, com resumir l'historial.
          </p>
          {#each AGENTS as agent (agent)}
            {@const models = app.catalog?.[agent] ?? null}
            {@const mode = modeOf(agent)}
            <div class="agent-block">
              <div class="agent-head">
                <AgentLabel {agent} size={15} />
                {#if mode}<span class="chip">{PROVIDER_MODE_LABEL[mode]}</span>{/if}
                {#if models && !models.live}
                  <span class="chip warn" title="No s'ha pogut consultar el proveïdor.">llista de reserva</span>
                {/if}
              </div>
              <div class="pair">
                <ModelPicker
                  inline
                  label="Principal"
                  value={form.models[agent]}
                  onchange={(v) => (form.models[agent] = v)}
                  {models}
                  defaultModel={providerDefault(agent)}
                  bind:invalid={pickerInvalid[`models.${agent}`]}
                  showErrors={submitted} />
                <ModelPicker
                  inline
                  label="Ràpid"
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
          <h3>Costos i moneda</h3>
          <fieldset class="field">
            <legend class="field-label">Tipus de canvi de dòlars a euros</legend>
            <div class="segmented">
              <label>
                <input type="radio" name="{uid}-fxmode" value="auto" bind:group={form.fx.mode} />Automàtic (BCE)
              </label>
              <label>
                <input type="radio" name="{uid}-fxmode" value="manual" bind:group={form.fx.mode} />Manual
              </label>
            </div>
          </fieldset>
          <label class="field">
            <span>{form.fx.mode === 'auto' ? 'Tipus de reserva' : 'Tipus de canvi'} (€ per 1 $)</span>
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
              {form.fx.mode === 'auto'
                ? "Cada dia es fa servir el tipus de referència del Banc Central Europeu; aquest només s'aplica si no es pot obtenir."
                : 'Tots els imports en euros es calculen amb aquest tipus.'}
            </small>
            <small class="error-text" id="{uid}-fx-err">{shownErrors.eur_per_usd ?? ''}</small>
          </label>
          {#if currentFx}
            <p class="note"><Icon name="info" size={15} /><span>Ara: {fxText(currentFx)}</span></p>
          {/if}
        </section>

        <section>
          <h3>Preus</h3>
          <p class="hint">
            Dòlars per milió de tokens, com els publiquen els proveïdors. En mode subscripció serveixen per
            calcular el valor equivalent. Edita un preu per crear-ne un de propi.
          </p>
          <PriceTable
            bind:prices={form.prices}
            pricing={app.pricing}
            errors={shownErrors}
            loadError={app.pricing ? null : app.pricingError} />
        </section>

        <section>
          <h3>Pressupostos i plans</h3>
          <p class="hint">Imports mensuals en euros (p. ex. 50,5). Deixa-ho en blanc si no en tens.</p>
          <div class="money-grid">
            <span></span>
            <span class="col-head" id="{uid}-budget-head">Pressupost d'API</span>
            <span class="col-head" id="{uid}-plan-head">Preu de la subscripció</span>
            {#each AGENTS as agent (agent)}
              {@const budgetErr = shownErrors[`budgets_eur.${agent}`]}
              {@const planErr = shownErrors[`plans_eur.${agent}`]}
              <AgentLabel {agent} size={15} />
              <div class="money">
                <AmountInput
                  class="input"
                  placeholder="—"
                  bind:value={form.budgets_eur[agent]}
                  aria-label="Pressupost mensual d'API de {AGENT_LABEL[agent]}, en euros"
                  aria-invalid={!!budgetErr} />
                <span class="unit" aria-hidden="true">€</span>
                {#if budgetErr}<small class="error-text">{budgetErr}</small>{/if}
              </div>
              <div class="money">
                <AmountInput
                  class="input"
                  placeholder="—"
                  bind:value={form.plans_eur[agent]}
                  aria-label="Preu mensual de la subscripció de {AGENT_LABEL[agent]}, en euros"
                  aria-invalid={!!planErr} />
                <span class="unit" aria-hidden="true">€</span>
                {#if planErr}<small class="error-text">{planErr}</small>{/if}
              </div>
            {/each}
          </div>
          <p class="hint">
            El pressupost és per a l'ús per API; l'indicador de la barra lateral avisa a partir del 80&nbsp;%.
            El preu del pla serveix per comparar-lo amb el valor aprofitat en mode subscripció.
          </p>
        </section>

        <section class="local">
          <h3>Efectes visuals</h3>
          <p class="hint">Només en aquest navegador. S'apliquen a l'instant.</p>
          <fieldset class="field">
            <legend class="sr-only">Qualitat dels efectes visuals</legend>
            <div class="segmented">
              {#each QUALITIES as q (q)}
                <label>
                  <input
                    type="radio"
                    name="{uid}-fx"
                    value={q}
                    checked={prefs.effects === q}
                    onchange={() => prefs.setEffects(q)} />
                  {EFFECTS_LABEL[q]}
                </label>
              {/each}
            </div>
          </fieldset>
          <p class="note">
            <Icon name="info" size={15} />
            <span>
              L'aplicació respecta la preferència del sistema de reduir el moviment{prefs.reducedMotion
                ? ' (ara activa: les animacions es redueixen al mínim).'
                : '.'}
            </span>
          </p>
        </section>
      </div>

      <footer class="save-row">
        {#if result}
          <p class="result" class:ok={result.ok} role="status">
            <Icon name={result.ok ? 'check' : 'alert'} size={15} />{result.text}
          </p>
        {:else if submitted && hasErrors}
          <p class="result" role="status"><Icon name="alert" size={15} />Revisa els camps marcats.</p>
        {/if}
        <button type="submit" class="btn primary" disabled={saving}>
          {saving ? 'Desant…' : 'Desa la configuració'}
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

  .result.ok {
    color: #9ff0c9;
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

  .narrow {
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
