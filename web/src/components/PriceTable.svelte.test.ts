// The price table (audit A21, N13): an own price of a model with a default price keeps
// its base price and can be restored, and ids are matched by the family id the
// server prices by (pricing.normalize_model), never by a second row.
import { flushSync } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';
import { i18n, LOCALE_KEY } from '../lib/i18n/index.svelte';
import type { ModelPrice, PriceRow, Pricing } from '../lib/protocol';
import { cleanup, render } from '../lib/test-render';
import PriceTable from './PriceTable.svelte';

afterEach(cleanup);

const OPUS: ModelPrice = { input: 5, output: 25, cache_read: 0.5, cache_write: 6.25 };
const SONNET: ModelPrice = { input: 2, output: 10, cache_read: 0.2, cache_write: 2.5 };
const SOL: ModelPrice = { input: 2, output: 10, cache_read: 0.2, cache_write: 2.5 };
const OWN_OPUS: ModelPrice = { input: 4, output: 20, cache_read: 0.4, cache_write: 5 };
const MINE: ModelPrice = { input: 1, output: 2, cache_read: 0.1, cache_write: 1.25 };

/** A row of GET /api/pricing. */
const priceRow = (
  model: string,
  price: ModelPrice,
  source: PriceRow['source'],
  base: ModelPrice | null = null,
  key = model,
): PriceRow => ({ model, key, ...price, source, default: base });

const fx: Pricing['fx'] = { eur_per_usd: 0.86, as_of: null, source: 'manual' };

/** GET /api/pricing with no own prices saved. */
const defaultsOnly = (): Pricing => ({
  fx,
  prices: [
    priceRow('claude-opus-5', OPUS, 'default'),
    priceRow('claude-sonnet-5', SONNET, 'default'),
    priceRow('gpt-6-sol', SOL, 'default'),
  ],
});

/** GET /api/pricing once the owner saved an own price for claude-opus-5 (under `id`) and added my-model. */
const withOwnOpus = (id = 'claude-opus-5'): Pricing => ({
  fx,
  prices: [
    priceRow(id, OWN_OPUS, 'custom', OPUS, 'claude-opus-5'),
    priceRow('claude-sonnet-5', SONNET, 'default'),
    priceRow('gpt-6-sol', SOL, 'default'),
    priceRow('my-model', MINE, 'custom'),
  ],
});

function mountTable(prices: Record<string, ModelPrice>, pricing: Pricing) {
  const props = $state({ prices: structuredClone(prices), pricing, errors: {} });
  const root = render(PriceTable, props);
  return { props, root };
}

/** What the table shows for a model: tag, cells (inputs as [value]), base prices and action. */
function describeRow(root: HTMLElement, model: string) {
  const tr = [...root.querySelectorAll<HTMLTableRowElement>('table.prices tbody tr')].find(
    (el) => el.querySelector('.id')?.textContent === model,
  );
  if (!tr) return null;
  const tds = [...tr.querySelectorAll('td.num')];
  return {
    tag: tr.querySelector('.tag')?.textContent ?? null,
    cells: tds.map((td) => {
      const input = td.querySelector('input');
      return input ? `[${input.value}]` : (td.textContent ?? '').trim();
    }),
    base: tds.map((td) => td.querySelector('.base')?.textContent?.trim() ?? null),
    action: tr.querySelector('td.actions button')?.getAttribute('aria-label') ?? null,
  };
}

const rowCount = (root: HTMLElement) => root.querySelectorAll('table.prices tbody tr .id').length;

function click(root: HTMLElement, label: string): void {
  const button = root.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
  expect(button, label).not.toBeNull();
  button!.click();
  flushSync();
}

/** Types `id` in the "add a model" box and presses Afegeix (the table focuses a row afterwards). */
async function add(root: HTMLElement, id: string): Promise<void> {
  const input = root.querySelector<HTMLInputElement>('.add input')!;
  input.value = id;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  root.querySelector<HTMLButtonElement>('.add button')!.click();
  flushSync();
  await new Promise((r) => setTimeout(r, 0));
}

const addError = (root: HTMLElement) => root.querySelector('.add-error')?.textContent?.trim() ?? '';
const addNote = (root: HTMLElement) => root.querySelector('.add-note')?.textContent?.trim() ?? '';
const ZERO: ModelPrice = { input: 0, output: 0, cache_read: 0, cache_write: 0 };

describe('PriceTable: an own price of a model with a default price (A21)', () => {
  it('stays «propi» after saving, shows the base price and restores it at once', async () => {
    const { props, root } = mountTable({ 'claude-opus-5': OWN_OPUS, 'my-model': MINE }, withOwnOpus());
    expect(describeRow(root, 'claude-opus-5')).toEqual({
      tag: 'propi',
      cells: ['[4]', '[20]', '[0.4]', '[5]'],
      base: ['5', '25', '0,5', '6,25'],
      action: 'Restaura el preu per defecte de claude-opus-5',
    });
    expect(describeRow(root, 'my-model')).toMatchObject({ tag: 'afegit', action: 'Elimina el preu de my-model' });

    click(root, 'Restaura el preu per defecte de claude-opus-5');
    expect(Object.keys(props.prices)).toEqual(['my-model']);
    expect(describeRow(root, 'claude-opus-5')).toEqual({
      tag: null,
      cells: ['5', '25', '0,5', '6,25'],
      base: [null, null, null, null],
      action: 'Edita el preu de claude-opus-5',
    });
    // Adding it again starts from its default price, not from zeros.
    await add(root, 'claude-opus-5');
    expect(props.prices['claude-opus-5']).toEqual(OPUS);
    expect(describeRow(root, 'claude-opus-5')?.tag).toBe('propi');
  });

  it('knows the base price of an own price saved under another id of the model', () => {
    const { props, root } = mountTable({ 'anthropic/claude-opus-5': OWN_OPUS }, withOwnOpus('anthropic/claude-opus-5'));
    expect(describeRow(root, 'anthropic/claude-opus-5')).toMatchObject({
      tag: 'propi',
      base: ['5', '25', '0,5', '6,25'],
      action: 'Restaura el preu per defecte de anthropic/claude-opus-5',
    });
    expect(describeRow(root, 'claude-opus-5')).toBeNull();
    click(root, 'Restaura el preu per defecte de anthropic/claude-opus-5');
    expect(props.prices).toEqual({});
    expect(describeRow(root, 'anthropic/claude-opus-5')).toBeNull();
    expect(describeRow(root, 'claude-opus-5')).toMatchObject({ tag: null, cells: ['5', '25', '0,5', '6,25'] });
  });

  it('shows the base price of an own price that is not saved yet', () => {
    const { props, root } = mountTable({}, defaultsOnly());
    click(root, 'Edita el preu de claude-opus-5');
    expect(props.prices).toEqual({ 'claude-opus-5': OPUS });
    expect(describeRow(root, 'claude-opus-5')).toMatchObject({ tag: 'propi', base: ['5', '25', '0,5', '6,25'] });
  });
});

describe('PriceTable: adding a model (A21, N13)', () => {
  it('edits the row of the same model instead of adding a second one at zero', async () => {
    const { props, root } = mountTable({}, defaultsOnly());
    const rows = rowCount(root);
    for (const id of ['anthropic/claude-opus-5', 'claude-opus-5-20260101', 'Claude-Opus-5[1m]', 'anthropic.claude-opus-5']) {
      await add(root, id);
      expect(props.prices, id).toEqual({ 'claude-opus-5': OPUS });
      expect(describeRow(root, id), id).toBeNull();
      expect(describeRow(root, 'claude-opus-5')?.tag, id).toBe('propi');
      expect(rowCount(root), id).toBe(rows);
      expect(addError(root), id).toBe('');
      // Says why the typed id did not get a row of its own.
      expect(addNote(root), id).toBe(`El servidor tracta «${id}» com a «claude-opus-5»: se n'edita el preu.`);
      click(root, 'Restaura el preu per defecte de claude-opus-5');
    }
  });

  it('goes to the own price the model already has instead of adding another', async () => {
    const own = { 'anthropic/claude-opus-5': OWN_OPUS, 'my-model': MINE };
    const { props, root } = mountTable(own, withOwnOpus('anthropic/claude-opus-5'));
    await add(root, 'claude-opus-5');
    expect(props.prices).toEqual(own);
    expect(document.activeElement).toBe(root.querySelector('input[data-model="anthropic/claude-opus-5"]'));
    expect(addNote(root)).toBe(
      'El servidor tracta «claude-opus-5» com a «anthropic/claude-opus-5», que ja té un preu propi.',
    );
    await add(root, 'MY-MODEL');
    expect(props.prices).toEqual(own);
    expect(document.activeElement).toBe(root.querySelector('input[data-model="my-model"]'));
    expect(addNote(root)).toBe('El servidor tracta «MY-MODEL» com a «my-model», que ja té un preu propi.');
    await add(root, 'my-model');
    expect(addNote(root)).toBe('«my-model» ja té un preu propi.');
  });

  it('adds no note when the typed id is the default row itself', async () => {
    const { props, root } = mountTable({}, defaultsOnly());
    await add(root, 'claude-opus-5');
    expect(props.prices).toEqual({ 'claude-opus-5': OPUS });
    expect(addNote(root)).toBe('');
  });

  it('starts a new model of a priced family from the price it gets now, and an unknown one from zero', async () => {
    const { props, root } = mountTable({}, defaultsOnly());
    await add(root, 'gpt-6-sol-mini');
    expect(props.prices['gpt-6-sol-mini']).toEqual(SOL);
    expect(describeRow(root, 'gpt-6-sol-mini')).toMatchObject({ tag: 'afegit', base: [null, null, null, null] });
    expect(addNote(root)).toBe("Comença amb els preus de gpt-6-sol, que són els que s'hi aplicaven fins ara. Revisa'ls.");
    await add(root, 'local-llm');
    expect(props.prices['local-llm']).toEqual(ZERO);
    expect(addNote(root)).toBe('');
  });

  it('refuses an id that names no model', async () => {
    const { props, root } = mountTable({}, defaultsOnly());
    await add(root, 'openai/');
    expect(props.prices).toEqual({});
    expect(addError(root)).toBe(
      'Aquest identificador no correspon a cap model (sense el prefix del proveïdor, la data o el context no en queda res).',
    );
    await add(root, 'amb espais');
    expect(props.prices).toEqual({});
    expect(addError(root)).toContain('Identificador no vàlid');
  });
});

describe('PriceTable in English and Spanish (ADR 0011)', () => {
  afterEach(() => {
    i18n.set('ca');
    localStorage.removeItem(LOCALE_KEY);
  });

  it('names its columns and actions, writes prices and notes in the language in force', async () => {
    i18n.set('en');
    const { root } = mountTable({ 'claude-opus-5': OWN_OPUS, 'my-model': MINE }, withOwnOpus());
    const heads = [...root.querySelectorAll('table.prices thead th')].map((th) => th.textContent?.trim());
    expect(heads).toEqual(['Model', 'Input', 'Output', 'Cache read', 'Cache write', 'Actions']);
    expect(describeRow(root, 'claude-opus-5')).toMatchObject({
      tag: 'own',
      base: ['5', '25', '0.5', '6.25'],
      action: 'Restore the default price of claude-opus-5',
    });
    expect(describeRow(root, 'claude-sonnet-5')?.cells).toEqual(['2', '10', '0.2', '2.5']);
    await add(root, 'MY-MODEL');
    expect(addNote(root)).toBe('The server treats “MY-MODEL” as “my-model”, which already has its own price.');

    // The note on screen follows a change of language, like the rest of the table.
    i18n.set('es');
    flushSync();
    expect(addNote(root)).toBe('El servidor trata «MY-MODEL» como «my-model», que ya tiene un precio propio.');
    expect(describeRow(root, 'claude-opus-5')).toMatchObject({ tag: 'propio', base: ['5', '25', '0,5', '6,25'] });
    await add(root, 'openai/');
    expect(addError(root)).toBe(
      'Este identificador no corresponde a ningún modelo (sin el prefijo del proveedor, la fecha o el contexto no queda nada).',
    );
  });
});
