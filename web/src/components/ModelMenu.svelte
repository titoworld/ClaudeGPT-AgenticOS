<script lang="ts">
  import { app } from '../lib/app.svelte';
  import { AGENT_LABEL } from '../lib/format';
  import { shortModel } from '../lib/models';
  import { placeAbove } from '../lib/popover';
  import { prefs } from '../lib/prefs.svelte';
  import { AGENTS, type Agent } from '../lib/protocol';
  import AgentLabel from './AgentLabel.svelte';
  import Icon from './Icon.svelte';
  import ModelPicker from './ModelPicker.svelte';

  const uid = $props.id();
  const popoverId = `${uid}-models`;
  let button: HTMLButtonElement | undefined = $state();
  let popover: HTMLDivElement | undefined = $state();

  const c = app.composer;
  /** Agents that answer with the current mode (solo: only the target). */
  const shown = $derived<readonly Agent[]>(c.mode === 'solo' ? [c.target] : AGENTS);
  const overridden = $derived(AGENTS.some((a) => prefs.models[a]));
  const summary = $derived(
    shown.map((a) => `${AGENT_LABEL[a]}: ${app.modelFor(a) ?? 'per defecte'}`).join(', '),
  );

  function onToggle(e: Event): void {
    if ((e as ToggleEvent).newState === 'open') void app.loadModels();
  }
</script>

<button
  type="button"
  class="btn ghost models-btn"
  class:custom={overridden}
  popovertarget={popoverId}
  bind:this={button}
  aria-label="Models ({summary}). Canvia els models"
  title={overridden ? 'Models triats per a aquesta pestanya' : 'Models per defecte'}>
  <Icon name="cpu" size={15} />
  <span class="names">
    {#each shown as agent (agent)}
      {@const model = app.modelFor(agent)}
      <span class="name {agent}"><i aria-hidden="true"></i>{model ? shortModel(model) : '—'}</span>
    {/each}
  </span>
</button>

<div
  class="popover glass models-pop"
  id={popoverId}
  popover="auto"
  bind:this={popover}
  ontoggle={onToggle}
  aria-labelledby="{uid}-title"
  {@attach placeAbove(() => button, 360)}>
  <header>
    <h3 id="{uid}-title">Models</h3>
    <p class="hint">S'apliquen als torns següents d'aquesta pestanya.</p>
  </header>

  {#each AGENTS as agent (agent)}
    {@const models = app.catalog?.[agent] ?? null}
    <section class="agent" aria-label="Model de {AGENT_LABEL[agent]}">
      <div class="agent-head">
        <AgentLabel {agent} size={15} />
        {#if models && !models.live}
          <span class="chip" title="No s'ha pogut consultar el proveïdor: es mostra una llista de reserva.">
            llista de reserva
          </span>
        {/if}
      </div>
      <ModelPicker
        label="Model de {AGENT_LABEL[agent]}"
        hideLabel
        value={prefs.models[agent] ?? null}
        onchange={(v) => prefs.setModel(agent, v)}
        {models}
        defaultModel={app.defaultModel(agent)}
        onenter={() => popover?.hidePopover()} />
    </section>
  {/each}

  {#if app.catalogError}
    <p class="error-text" role="status">{app.catalogError}</p>
  {/if}

  <footer>
    <button
      type="button"
      class="btn ghost small"
      onclick={() => void app.loadModels(true)}
      disabled={app.catalogLoading}>
      <Icon name="refresh" size={14} class={app.catalogLoading ? 'spin' : ''} />
      {app.catalogLoading ? 'Actualitzant…' : 'Actualitza la llista'}
    </button>
    {#if overridden}
      <button
        type="button"
        class="btn ghost small"
        onclick={() => prefs.resetModels()}
        title="Torna als models per defecte">
        Restableix
      </button>
    {/if}
  </footer>
</div>

<style>
  .models-btn {
    min-height: 2rem;
    max-width: 100%;
    padding: 0.3rem 0.6rem;
    font-size: var(--text-sm);
    font-weight: 550;
    color: var(--text-secondary);
  }

  .models-btn:hover {
    color: var(--text-primary);
  }

  .models-btn.custom {
    border-color: rgb(139 156 255 / 0.4);
    background: rgb(139 156 255 / 0.1);
    color: var(--text-primary);
  }

  .names {
    display: inline-flex;
    gap: 0.55rem;
    min-width: 0;
    overflow: hidden;
  }

  .name {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    min-width: 0;
    max-width: 7.5rem;
    overflow: hidden;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .name i {
    flex: none;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--claude);
  }

  .name.chatgpt i {
    background: var(--chatgpt);
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
    overflow: auto;
  }

  .popover:popover-open {
    display: grid;
    gap: 0.9rem;
    animation: rise-in var(--dur-fast) var(--ease-out);
  }

  header {
    display: grid;
    gap: 0.15rem;
  }

  h3 {
    font-size: var(--text-sm);
    font-weight: 650;
  }

  .agent {
    display: grid;
    gap: 0.45rem;
  }

  .agent-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    font-size: var(--text-sm);
  }

  .agent-head .chip {
    font-size: 0.68rem;
    font-weight: 550;
  }

  footer {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 0.4rem;
    padding-top: 0.6rem;
    border-top: 1px solid var(--border);
  }

  .small {
    min-height: 1.9rem;
    padding: 0.3rem 0.55rem;
    font-size: var(--text-xs);
    color: var(--text-secondary);
  }

  .small :global(.spin) {
    animation: spin 0.9s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @container (max-width: 34rem) {
    .names {
      display: none;
    }
  }
</style>
