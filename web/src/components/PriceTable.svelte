<script lang="ts">
  import { tick } from 'svelte';
  import { normalizeModel, validateModelId } from '../lib/models';
  import type { ModelPrice, Pricing } from '../lib/protocol';
  import type { SettingsErrors } from '../lib/settings';
  import { LIMITS } from '../lib/settings';
  import Icon from './Icon.svelte';

  interface Props {
    /** The owner's prices being edited (RuntimeSettings.prices of the form). */
    prices: Record<string, ModelPrice>;
    /**
     * The server's price table (defaults with the saved own prices over them): its
     * default prices, and those the own prices replace, are shown read-only.
     */
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
    /** The family id the server prices the model by (normalizeModel). */
    key: string;
    price: ModelPrice;
    custom: boolean;
    /** Of an own price: the default price it replaces, which "restore" brings back. */
    base: ModelPrice | null;
  }

  const ZERO: ModelPrice = { input: 0, output: 0, cache_read: 0, cache_write: 0 };
  const NO_MODEL =
    'Aquest identificador no correspon a cap model (sense el prefix del proveïdor, la data o el context no en queda res).';

  const uid = $props.id();
  let table: HTMLTableElement | undefined = $state();
  let newModel = $state('');
  let addError: string | null = $state(null);
  /** Where the prices of the model just added come from. */
  let addNote: string | null = $state(null);

  const copyPrice = (p: ModelPrice): ModelPrice => ({
    input: p.input,
    output: p.output,
    cache_read: p.cache_read,
    cache_write: p.cache_write,
  });

  /**
   * Default prices by the family id the server matches models on: the default rows,
   * and the default a saved own price replaces (sent with its row, audit A21).
   */
  const defaults = $derived.by(() => {
    const out = new Map<string, { model: string; price: ModelPrice }>();
    for (const row of pricing?.prices ?? []) {
      if (row.source === 'default') out.set(row.key, { model: row.model, price: copyPrice(row) });
      else if (row.default) out.set(row.key, { model: row.key, price: copyPrice(row.default) });
    }
    return out;
  });

  // Own prices are matched to defaults as the server does (normalize_model, N13):
  // "anthropic/claude-opus-5" replaces the default of claude-opus-5.
  const rows = $derived.by(() => {
    const replacing = new Map<string, string>();
    const added: Row[] = [];
    for (const model of Object.keys(prices)) {
      const key = normalizeModel(model);
      if (defaults.has(key) && !replacing.has(key)) replacing.set(key, model);
      else added.push({ model, key, price: prices[model]!, custom: true, base: null });
    }
    const known = [...defaults].map(([key, d]): Row => {
      const model = replacing.get(key);
      return model
        ? { model, key, price: prices[model]!, custom: true, base: d.price }
        : { model: d.model, key, price: d.price, custom: false, base: null };
    });
    known.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    return [...known, ...added];
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
    prices[model] = copyPrice(price);
    void focusRow(model);
  }

  /** Drops an own price: a model with a default price gets it back at once. */
  function remove(model: string): void {
    delete prices[model];
  }

  function add(): void {
    const model = newModel.trim();
    addNote = null;
    addError = validateModelId(model);
    if (addError) return;
    const key = normalizeModel(model);
    if (!key) {
      addError = NO_MODEL;
      return;
    }
    newModel = '';
    // Another id of a model in the table (a vendor prefix, a date...): the server
    // prices both alike, so that row is edited instead of adding a second one.
    const same = rows.find((r) => r.key === key);
    if (same) {
      // Says why the typed id gets no row of its own.
      const alias = model === same.model ? null : `El servidor tracta «${model}» com a «${same.model}»`;
      if (same.custom) {
        addNote = alias ? `${alias}, que ja té un preu propi.` : `«${same.model}» ja té un preu propi.`;
        void focusRow(same.model);
      } else {
        addNote = alias ? `${alias}: se n'edita el preu.` : null;
        edit(same.model, same.price);
      }
      return;
    }
    // A new model starts from the price the server gives it now, that of the longest
    // family id its own starts with (pricing.lookup_price), or else from zero.
    const family = rows
      .filter((r) => r.key && key.startsWith(r.key))
      .reduce<Row | null>((best, r) => (best && best.key.length >= r.key.length ? best : r), null);
    if (family) {
      addNote = `Comença amb els preus de ${family.model}, que són els que s'hi aplicaven fins ara. Revisa'ls.`;
    }
    edit(model, family?.price ?? ZERO);
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
            {#if row.base}
              <span class="tag" title="Preu propi. A sota de cada preu, el per defecte.">propi</span>
            {:else if row.custom}
              <span class="tag" title="Model sense preu per defecte.">afegit</span>
            {/if}
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
                  aria-label="{col.long}: {row.model}, en dòlars per milió de tokens{row.base
                    ? ` (per defecte, ${num(row.base[col.key])})`
                    : ''}"
                  aria-invalid={!!error && !validCell(prices[row.model]?.[col.key])} />
                {#if row.base}
                  <small class="base" title="Preu per defecte" aria-hidden="true">{num(row.base[col.key])}</small>
                {/if}
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
            {:else if row.base}
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
    oninput={() => {
      addError = null;
      addNote = null;
    }}
    onkeydown={onAddKeydown}
    aria-invalid={!!addError}
    aria-describedby="{uid}-add-err" />
  <button type="button" class="btn" onclick={add}><Icon name="plus" size={15} />Afegeix</button>
</div>
<small class="error-text add-error" id="{uid}-add-err">{addError ?? ''}</small>
{#if addNote}
  <small class="hint add-note" role="status">{addNote}</small>
{/if}

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

  .base {
    display: block;
    margin-top: 0.15rem;
    padding-right: 0.3rem;
    font-size: 0.62rem;
    color: var(--text-muted);
    cursor: help;
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
