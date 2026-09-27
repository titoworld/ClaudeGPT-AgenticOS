<script lang="ts">
  import { tick, untrack } from 'svelte';
  import { findModel, modelHint, modelOptionLabel, validateModelId } from '../lib/models';
  import type { AgentModels } from '../lib/protocol';

  interface Props {
    /** Visible label (screen-reader only with `hideLabel`). */
    label: string;
    hideLabel?: boolean;
    /** Label in a narrow column to the left of the field. */
    inline?: boolean;
    /** Current choice; null = the default. Always a valid id. */
    value: string | null;
    /** Called with valid choices only (null = back to the default); typed ids on commit (Enter, blur). */
    onchange: (value: string | null) => void;
    /** Catalog of this agent (null while it loads). */
    models: AgentModels | null;
    /** Model used when nothing is chosen, shown as "Per defecte (<model>)". */
    defaultModel?: string | null;
    /** True while a typed id is not valid (lets a form block saving). */
    invalid?: boolean;
    /** Show the validation error even if the field is still empty (after a submit). */
    showErrors?: boolean;
    /** Enter in the custom id field: when given, it replaces the form submission. */
    onenter?: () => void;
  }

  let {
    label,
    hideLabel = false,
    inline = false,
    value,
    onchange,
    models,
    defaultModel = null,
    invalid = $bindable(false),
    showErrors = false,
    onenter,
  }: Props = $props();

  // Sentinels can never be model ids (ids start with a letter or digit).
  const DEFAULT = '__default__';
  const CUSTOM = '__custom__';
  const uid = $props.id();

  let customMode = $state(false);
  let text = $state('');
  let touched = $state(false);
  let input: HTMLInputElement | undefined = $state();
  /** Last value this picker emitted, to tell our own changes from external ones. */
  let emitted: string | null | undefined;

  const listed = $derived(findModel(models, value) != null);
  const isCustom = $derived(customMode || (value != null && !listed));
  const selectValue = $derived(isCustom ? CUSTOM : (value ?? DEFAULT));
  const error = $derived(isCustom ? validateModelId(text) : null);
  const showError = $derived(!!error && (touched || showErrors || text.trim() !== ''));
  const hint = $derived(isCustom ? '' : modelHint(findModel(models, value ?? defaultModel)));

  $effect(() => {
    invalid = !!error;
  });

  // External changes (a reset, settings reloaded) take over the local editing state.
  $effect.pre(() => {
    const v = value;
    const isListed = listed;
    untrack(() => {
      if (v === emitted) return;
      emitted = undefined;
      customMode = false;
      touched = false;
      text = v != null && !isListed ? v : '';
    });
  });

  function emit(v: string | null): void {
    emitted = v;
    if (v !== value) onchange(v);
  }

  async function onSelect(e: Event & { currentTarget: HTMLSelectElement }): Promise<void> {
    const v = e.currentTarget.value;
    if (v === CUSTOM) {
      customMode = true;
      text = value ?? '';
      touched = false;
      await tick();
      input?.focus();
      input?.select();
      return;
    }
    customMode = false;
    text = '';
    emit(v === DEFAULT ? null : v);
  }

  /** A typed id only applies once committed, so half-typed ids never become the choice. */
  function commit(): void {
    touched = true;
    if (isCustom && !validateModelId(text)) emit(text.trim());
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.key !== 'Enter' || e.isComposing) return;
    commit();
    if (!onenter) return; // let the form submit
    e.preventDefault();
    if (!error) onenter();
  }
</script>

<div class="picker" class:inline>
  <label class={hideLabel ? 'sr-only' : 'field-label'} for="{uid}-select">{label}</label>
  <select
    id="{uid}-select"
    class="input select"
    value={selectValue}
    onchange={onSelect}
    aria-describedby="{uid}-hint">
    <option value={DEFAULT}>Per defecte{defaultModel ? ` (${defaultModel})` : ''}</option>
    {#if models?.models.length}
      <optgroup label={models.live ? 'Disponibles ara' : 'Llista de reserva'}>
        {#each models.models as m (m.id)}
          <option value={m.id}>{modelOptionLabel(m)}</option>
        {/each}
      </optgroup>
    {/if}
    <option value={CUSTOM}>Personalitzat…</option>
  </select>
  {#if isCustom}
    <input
      bind:this={input}
      class="input custom"
      type="text"
      bind:value={text}
      onchange={commit}
      onkeydown={onKeydown}
      onblur={() => (touched = true)}
      placeholder="p. ex. claude-opus-5-5 o gpt-6-sol"
      maxlength="100"
      spellcheck="false"
      autocomplete="off"
      autocapitalize="off"
      aria-label="Identificador del model ({label})"
      aria-invalid={showError}
      aria-describedby="{uid}-hint" />
  {/if}
  {#if showError || isCustom || hint}
    <small class={showError ? 'error-text' : 'hint'} id="{uid}-hint">
      {#if showError}
        {error}
      {:else if isCustom}
        L'identificador exacte, encara que el model no surti a la llista.
      {:else}
        {hint}
      {/if}
    </small>
  {/if}
</div>

<style>
  .picker {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.35rem;
    min-width: 0;
  }

  .picker.inline {
    grid-template-columns: 6.5rem minmax(0, 1fr);
    column-gap: 0.75rem;
    align-items: center;
  }

  .inline > :not(label) {
    grid-column: 2;
  }

  .inline > label {
    grid-row: 1;
  }

  .select {
    appearance: none;
    min-height: 2.25rem;
    padding: 0.4rem 2rem 0.4rem 0.65rem;
    font-size: var(--text-sm);
    text-overflow: ellipsis;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23b7bbcc' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 0.6rem center;
    background-size: 14px;
    cursor: pointer;
  }

  .select option,
  .select optgroup {
    background: var(--surface-2);
    color: var(--text-primary);
  }

  .custom {
    min-height: 2.25rem;
    padding: 0.4rem 0.65rem;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
  }
</style>
