<script lang="ts">
  import { tick } from 'svelte';
  import { validateModelId } from '../lib/models';
  import type { ModelPrice, Pricing } from '../lib/protocol';
  import type { SettingsErrors } from '../lib/settings';
  import { LIMITS } from '../lib/settings';
  import Icon from './Icon.svelte';

  interface Props {
    /** The owner's prices being edited (RuntimeSettings.prices of the form). */
    prices: Record<string, ModelPrice>;
    /** Server price list; its `default` rows are shown read-only. */
    pricing: Pricing | null;
    /** Validation errors to show (`price:<model>` keys). */
    errors: SettingsErrors;
    loadError?: string | null;
  }

  let { prices = $bindable(), pricing, errors, loadError = null }: Props = $props();

  type PriceKey = keyof ModelPrice;
  const COLUMNS: { key: PriceKey; short: string; long: string }[] = [
    { key: 'input', short: 'Entrada', long: "Entrada (tokens nous d'entrada)" },
    { key: 'output', short: 'Sortida', long: 'Sortida (inclou el raonament)' },
    { key: 'cache_read', short: 'Lect. cau', long: 'Lectura de la memòria cau' },
    { key: 'cache_write', short: 'Escr. cau', long: 'Escriptura a la memòria cau' },
  ];

  interface Row {
    model: string;
    price: ModelPrice;
    custom: boolean;
    /** A default price exists for this model (so "restore" makes sense). */
    hasDefault: boolean;
  }

  const uid = $props.id();
  let table: HTMLTableElement | undefined = $state();
  let newModel = $state('');
  let addError: string | null = $state(null);

  const key = (model: string) => model.trim().toLowerCase();
  const defaults = $derived((pricing?.prices ?? []).filter((p) => p.source === 'default'));

  const rows = $derived.by(() => {
    const own = new Map(Object.keys(prices).map((m) => [key(m), m]));
    const out: Row[] = [];
    const seen = new Set<string>();
    for (const d of defaults) {
      const model = own.get(key(d.model));
      if (model) {
        seen.add(model);
        out.push({ model, price: prices[model]!, custom: true, hasDefault: true });
      } else {
        out.push({ model: d.model, price: d, custom: false, hasDefault: true });
      }
    }
    for (const model of Object.keys(prices)) {
      if (!seen.has(model)) out.push({ model, price: prices[model]!, custom: true, hasDefault: false });
    }
    return out;
  });

  const num = (n: number) => n.toLocaleString('ca-ES', { maximumFractionDigits: 4 });
  const validCell = (v: unknown) =>
    typeof v === 'number' && Number.isFinite(v) && v >= LIMITS.price.min && v <= LIMITS.price.max;

  function setPrice(model: string, field: PriceKey, value: number | null | undefined): void {
    const p = prices[model];
    // An empty or partial number ("-") binds as null: keep it (validation rejects it)
    // instead of NaN, which would be written back and wipe what is being typed.
    if (p) p[field] = (value ?? null) as number;
  }

  async function focusRow(model: string): Promise<void> {
    await tick();
    const input = [...(table?.querySelectorAll<HTMLInputElement>('input[data-model]') ?? [])].find(
      (el) => el.dataset.model === model,
    );
    input?.focus();
    input?.select();
  }

  function edit(model: string, price: ModelPrice): void {
    prices[model] = {
      input: price.input,
      output: price.output,
      cache_read: price.cache_read,
      cache_write: price.cache_write,
    };
    void focusRow(model);
  }

  function remove(model: string): void {
    delete prices[model];
  }

  function add(): void {
    const model = newModel.trim();
    addError = validateModelId(model);
    if (addError) return;
    if (Object.keys(prices).some((m) => key(m) === key(model))) {
      addError = 'Aquest model ja té un preu propi a la taula.';
      return;
    }
    const known = defaults.find((d) => key(d.model) === key(model));
    newModel = '';
    if (known) edit(known.model, known);
    else edit(model, { input: 0, output: 0, cache_read: 0, cache_write: 0 });
  }

  function onAddKeydown(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.isComposing) {
      e.preventDefault(); // not the settings form submission
      add();
    }
  }
</script>

<div class="wrap">
  <table class="prices" bind:this={table}>
    <caption class="sr-only">Preus en dòlars per milió de tokens</caption>
    <thead>
      <tr>
        <th scope="col">Model</th>
        {#each COLUMNS as col (col.key)}
          <th scope="col" class="num" title={col.long}>{col.short}</th>
        {/each}
        <th scope="col"><span class="sr-only">Accions</span></th>
      </tr>
    </thead>
    <tbody>
      {#each rows as row (row.model)}
        {@const error = errors[`price:${row.model}`]}
        <tr class:custom={row.custom}>
          <th scope="row" class="model" title={row.model}>
            <span class="id">{row.model}</span>
            {#if row.custom}<span class="tag">{row.hasDefault ? 'propi' : 'afegit'}</span>{/if}
          </th>
          {#each COLUMNS as col (col.key)}
            <td class="num">
              {#if row.custom}
                <input
                  class="cell"
                  type="number"
                  inputmode="decimal"
                  min={LIMITS.price.min}
                  max={LIMITS.price.max}
                  step="any"
                  data-model={row.model}
                  bind:value={() => prices[row.model]?.[col.key], (v) => setPrice(row.model, col.key, v)}
                  aria-label="{col.long}: {row.model}, en dòlars per milió de tokens"
                  aria-invalid={!!error && !validCell(prices[row.model]?.[col.key])} />
              {:else}
                {num(row.price[col.key])}
              {/if}
            </td>
          {/each}
          <td class="actions">
            {#if !row.custom}
              <button
                type="button"
                class="icon-btn small"
                onclick={() => edit(row.model, row.price)}
                aria-label="Edita el preu de {row.model}"
                title="Edita (crea un preu propi)">
                <Icon name="edit" size={14} />
              </button>
            {:else if row.hasDefault}
              <button
                type="button"
                class="icon-btn small"
                onclick={() => remove(row.model)}
                aria-label="Restaura el preu per defecte de {row.model}"
                title="Restaura el preu per defecte">
                <Icon name="refresh" size={14} />
              </button>
            {:else}
              <button
                type="button"
                class="icon-btn small"
                onclick={() => remove(row.model)}
                aria-label="Elimina el preu de {row.model}"
                title="Elimina">
                <Icon name="trash" size={14} />
              </button>
            {/if}
          </td>
        </tr>
        {#if error}
          <tr class="error-row">
            <td colspan={COLUMNS.length + 2}><small class="error-text">{error}</small></td>
          </tr>
        {/if}
      {:else}
        <tr>
          <td colspan={COLUMNS.length + 2} class="empty">
            {loadError ?? (pricing ? 'Encara no hi ha cap preu.' : 'Carregant els preus…')}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

{#if loadError && rows.length}
  <p class="error-text">{loadError}</p>
{/if}

<div class="add">
  <label class="sr-only" for="{uid}-new">Identificador del model nou</label>
  <input
    id="{uid}-new"
    class="input"
    type="text"
    placeholder="Afegeix un model (p. ex. gpt-6-sol-mini)"
    maxlength="100"
    spellcheck="false"
    autocomplete="off"
    autocapitalize="off"
    bind:value={newModel}
    oninput={() => (addError = null)}
    onkeydown={onAddKeydown}
    aria-invalid={!!addError}
    aria-describedby="{uid}-add-err" />
  <button type="button" class="btn" onclick={add}><Icon name="plus" size={15} />Afegeix</button>
</div>
<small class="error-text" id="{uid}-add-err">{addError ?? ''}</small>

<style>
  .wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: rgb(7 8 13 / 0.35);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-xs);
    font-variant-numeric: tabular-nums;
  }

  th,
  td {
    padding: 0.35rem 0.45rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }

  tbody tr:last-child > * {
    border-bottom: none;
  }

  thead th {
    font-size: 0.68rem;
    font-weight: 600;
    color: var(--text-muted);
    white-space: nowrap;
    cursor: help;
  }

  .num {
    text-align: right;
    color: var(--text-secondary);
  }

  .model {
    max-width: 10.5rem;
    font-weight: 500;
    color: var(--text-primary);
  }

  .id {
    display: block;
    overflow: hidden;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tag {
    font-size: 0.62rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--accent);
  }

  tr.custom {
    background: rgb(139 156 255 / 0.06);
  }

  .cell {
    width: 3.9rem;
    padding: 0.2rem 0.3rem;
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    background: rgb(7 8 13 / 0.6);
    font-size: var(--text-xs);
    text-align: right;
    font-variant-numeric: tabular-nums;
    appearance: textfield;
  }

  .cell::-webkit-inner-spin-button,
  .cell::-webkit-outer-spin-button {
    appearance: none;
    margin: 0;
  }

  .cell:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 2px rgb(139 156 255 / 0.25);
  }

  .cell[aria-invalid='true'] {
    border-color: var(--critical);
  }

  .actions {
    width: 2rem;
    padding: 0.1rem 0.2rem;
    text-align: right;
  }

  .error-row td {
    padding-top: 0;
  }

  .empty {
    padding: 0.8rem;
    color: var(--text-muted);
  }

  .add {
    display: flex;
    gap: 0.5rem;
  }

  .add .input {
    min-height: 2.25rem;
    padding: 0.4rem 0.65rem;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
  }

  .add .btn {
    flex: none;
  }

  small:empty {
    display: none;
  }
</style>
