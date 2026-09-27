<script lang="ts">
  import { untrack } from 'svelte';
  import { app } from '../lib/app.svelte';
  import { errorMessage } from '../lib/conversations.svelte';
  import { lightDismiss, syncDialog } from '../lib/dialog';
  import { AGENT_LABEL } from '../lib/format';
  import { EFFECTS_LABEL, prefs } from '../lib/prefs.svelte';
  import { AGENTS, type RuntimeSettings, type TurnMode } from '../lib/protocol';
  import { LIMITS, validateSettings } from '../lib/settings';
  import { MODE_LABEL } from '../lib/text';
  import type { SceneQuality } from '../scene/types';
  import AgentIcon from './AgentIcon.svelte';
  import Icon from './Icon.svelte';

  const uid = $props.id();
  const MODES: TurnMode[] = ['solo', 'duel', 'debate'];
  const QUALITIES: SceneQuality[] = ['high', 'low', 'off'];

  let dialog: HTMLDialogElement | undefined = $state();
  let form: RuntimeSettings = $state(copy(app.settings));
  let submitted = $state(false);
  let saving = $state(false);
  let result: { ok: boolean; text: string } | null = $state(null);

  const errors = $derived(validateSettings(form));
  const shownErrors = $derived(submitted ? errors : {});
  const hasErrors = $derived(Object.keys(errors).length > 0);

  function copy(s: RuntimeSettings): RuntimeSettings {
    return { ...s, debate: { ...s.debate } };
  }

  $effect(() => syncDialog(dialog, app.settingsOpen));

  // Fresh copy of the server settings every time the drawer opens.
  $effect(() => {
    if (!app.settingsOpen) return;
    form = copy(untrack(() => app.settings));
    submitted = false;
    result = null;
  });

  async function save(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    submitted = true;
    result = null;
    if (hasErrors) return;
    saving = true;
    try {
      await app.saveSettings($state.snapshot(form));
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
    width: min(28rem, 100vw);
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
