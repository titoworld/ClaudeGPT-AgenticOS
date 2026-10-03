// The settings drawer (audit A11, N5): its form is built from settings fetched when
// it opens, it cannot save before they arrive, and a save based on settings changed
// elsewhere (409) reloads them instead of undoing that change.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOCALE_KEY } from '../lib/i18n/index.svelte';
import type { RuntimeSettings } from '../lib/protocol';
import { invalidAmount } from '../lib/settings';
import { CONFLICT_DETAIL, deferred, FakeApi, FakeSocket, polyfillDialog } from '../lib/test-server';

const SAVED: RuntimeSettings = {
  revision: 7,
  default_mode: 'solo',
  default_target: 'chatgpt',
  debate: { rounds: 1, consensus_threshold: 70, synthesizer: 'chatgpt' },
  refine: { max_rounds: 12, budget_eur: 3, max_words: null, stop_on_convergence: true, convergence_threshold: 90, editor: 'claude' },
  use_cache: false,
  compaction_threshold_tokens: 12000,
  models: { claude: 'claude-opus-4-1', chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: { 'my-model': { input: 1, output: 2, cache_read: 0.1, cache_write: 1.25 } },
  fx: { mode: 'manual', eur_per_usd: 0.9 },
  budgets_eur: { claude: 50, chatgpt: 20 },
  plans_eur: { claude: 90, chatgpt: 23 },
  pdf_in_revisions: 'full',
};

/** A new app (as after a page load) and the drawer, from the same module graph. */
async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { i18n } = await import('../lib/i18n/index.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: SettingsDrawer } = await import('./SettingsDrawer.svelte');
  return { app, i18n, render, cleanup, textOf, flushSync, SettingsDrawer };
}

let server: FakeApi;
let env: Awaited<ReturnType<typeof load>> | null = null;

async function fresh() {
  env = await load();
  return env;
}

/** Mounts the drawer and opens it. */
function openDrawer(e: NonNullable<typeof env>): HTMLElement {
  const root = e.render(e.SettingsDrawer, {});
  e.app.settingsOpen = true;
  e.flushSync();
  return root;
}

const saveButton = (root: HTMLElement) => root.querySelector<HTMLButtonElement>('footer button[type=submit]')!;
const modeRadio = (root: HTMLElement, mode: string) =>
  root.querySelector<HTMLInputElement>(`input[type=radio][value=${mode}]`);
const roundsInput = (root: HTMLElement) =>
  [...root.querySelectorAll('label.field')]
    .find((l) => l.textContent?.startsWith('Rondes de revisió'))
    ?.querySelector('input') ?? null;
const budgetInput = (root: HTMLElement) =>
  root.querySelector<HTMLInputElement>('input[aria-label^="Pressupost mensual d\'API de Claude"]');
const notice = (root: HTMLElement) => env!.textOf(root.querySelector('footer .result'));

function type(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  env!.flushSync();
}

function submit(root: HTMLElement): void {
  root.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  env!.flushSync();
}

/** Lets pending requests and effects run. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  env!.flushSync();
}

beforeAll(async () => {
  polyfillDialog();
  await load(); // compiles the components once, outside the tests' time limit
});

beforeEach(() => {
  server = new FakeApi(SAVED);
  FakeSocket.all = [];
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  env?.cleanup();
  env?.app.toLogin(); // stops this app's timers and socket
  env = null;
  vi.unstubAllGlobals();
});

describe('SettingsDrawer before the settings are loaded (A11)', () => {
  it('opened during the first load waits for it, then saves the saved settings', async () => {
    const e = await fresh();
    const slow = deferred<RuntimeSettings>();
    server.settingsGet = () => slow.promise;
    const entering = e.app.init();
    await vi.waitFor(() => expect(e.app.auth).toBe('ready'));
    const root = openDrawer(e);

    expect(e.textOf(root)).toContain('Carregant la configuració…');
    expect(modeRadio(root, 'solo')).toBeNull(); // no form built from the built-in defaults
    expect(saveButton(root).disabled).toBe(true);
    submit(root);
    await settle();
    expect(server.puts).toEqual([]);

    slow.resolve(structuredClone(SAVED));
    await entering;
    await vi.waitFor(() => expect(modeRadio(root, 'solo')?.checked).toBe(true));
    expect(saveButton(root).disabled).toBe(false);
    submit(root);
    await vi.waitFor(() => expect(server.puts).toHaveLength(1));
    expect(server.puts[0]).toEqual(SAVED); // revision 7 and everything as saved
    await vi.waitFor(() => expect(notice(root)).toBe('Configuració desada.'));
  });

  it('opened after a failed load shows the error, cannot save, and fills in once loaded', async () => {
    const e = await fresh();
    server.settingsGet = () => {
      throw new TypeError('Failed to fetch');
    };
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(e.textOf(root)).toContain("No s'ha pogut carregar la configuració."));
    expect(e.textOf(root)).toContain('No es pot connectar amb el servidor.');
    expect(roundsInput(root)).toBeNull();
    expect(saveButton(root).disabled).toBe(true);
    submit(root);
    await settle();
    expect(server.puts).toEqual([]);

    server.settingsGet = null;
    const retry = [...root.querySelectorAll('button')].find((b) => b.textContent?.includes('Torna-ho a provar'))!;
    retry.click();
    await vi.waitFor(() => expect(roundsInput(root)?.value).toBe('1'));
    type(roundsInput(root)!, '3');
    submit(root);
    await vi.waitFor(() => expect(server.puts).toHaveLength(1));
    expect(server.puts[0]).toEqual({ ...SAVED, debate: { ...SAVED.debate, rounds: 3 } });
  });
});

describe('SettingsDrawer and changes made elsewhere (N5)', () => {
  it('fetches the settings and prices again every time it opens', async () => {
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(budgetInput(root)?.value).toBe('50'));
    const pricingGets = server.count('GET /api/pricing');
    expect(pricingGets).toBeGreaterThan(0);
    e.app.settingsOpen = false;
    e.flushSync();

    // Another device raises the budget; this tab opens the drawer again.
    server.saveElsewhere({ budgets_eur: { claude: 120, chatgpt: 20 } });
    const slow = deferred<RuntimeSettings>();
    server.settingsGet = () => slow.promise;
    e.app.settingsOpen = true;
    e.flushSync();
    expect(e.textOf(root)).toContain('Carregant la configuració…');
    expect(budgetInput(root)).toBeNull(); // not the copy of the last time
    expect(saveButton(root).disabled).toBe(true);
    expect(server.count('GET /api/pricing')).toBe(pricingGets + 1);

    slow.resolve(structuredClone(server.settings));
    await vi.waitFor(() => expect(budgetInput(root)?.value).toBe('120'));
    submit(root);
    await vi.waitFor(() => expect(server.puts).toHaveLength(1));
    expect(server.puts[0]!.revision).toBe(8);
    expect(server.puts[0]!.budgets_eur.claude).toBe(120);
  });

  it('reloads the current settings into the form when they changed after it opened (409)', async () => {
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(roundsInput(root)?.value).toBe('1'));

    // Another tab saves while this drawer is open, then the owner saves here.
    server.saveElsewhere({ budgets_eur: { claude: 120, chatgpt: 20 } });
    type(roundsInput(root)!, '3');
    submit(root);
    await vi.waitFor(() => expect(notice(root)).toBe(CONFLICT_DETAIL));
    expect(server.puts.map((p) => p.revision)).toEqual([7]);
    expect(server.settings.budgets_eur.claude).toBe(120); // not undone
    // The form now shows what is saved, to review and save again.
    expect(budgetInput(root)?.value).toBe('120');
    expect(roundsInput(root)?.value).toBe('1');
    expect(e.app.settings.revision).toBe(8);

    type(roundsInput(root)!, '3');
    submit(root);
    await vi.waitFor(() => expect(notice(root)).toBe('Configuració desada.'));
    expect(server.puts.map((p) => p.revision)).toEqual([7, 8]);
    expect(server.settings.debate.rounds).toBe(3);
    expect(server.settings.budgets_eur.claude).toBe(120);
  });

  it('bases a second save from the same open drawer on the new revision', async () => {
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(roundsInput(root)?.value).toBe('1'));
    type(roundsInput(root)!, '2');
    submit(root);
    await vi.waitFor(() => expect(notice(root)).toBe('Configuració desada.'));
    type(roundsInput(root)!, '4');
    submit(root);
    await vi.waitFor(() => expect(server.puts).toHaveLength(2));
    expect(server.puts.map((p) => p.revision)).toEqual([7, 8]);
    await vi.waitFor(() => expect(server.settings.debate.rounds).toBe(4));
  });
});

describe('SettingsDrawer: what the revisions of a debate get of an attached PDF', () => {
  it('shows the saved choice, «sencer» or «només el text», and saves a change', async () => {
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    const radio = (value: string) => root.querySelector<HTMLInputElement>(`input[type=radio][value=${value}]`);
    await vi.waitFor(() => expect(radio('full')?.checked).toBe(true));
    const group = radio('full')!.closest('fieldset')!;
    expect(e.textOf(group.querySelector('legend'))).toBe('PDF a les revisions');
    expect([...group.querySelectorAll('label')].map((l) => e.textOf(l))).toEqual(['Sencer', 'Només el text']);
    expect(e.textOf(group)).toContain('Les respostes i la síntesi sempre reben el PDF sencer.');
    radio('text')!.click();
    e.flushSync();
    submit(root);
    await vi.waitFor(() => expect(server.puts).toHaveLength(1));
    expect(server.puts[0]).toEqual({ ...SAVED, pdf_in_revisions: 'text' });
  });
});

describe('SettingsDrawer: the defaults of the refine mode («Perfecciona»)', () => {
  const section = (root: HTMLElement) =>
    [...root.querySelectorAll('section')].find((s) => s.querySelector('h3')?.textContent === 'Perfecciona') ?? null;
  const input = (root: HTMLElement, label: string) =>
    [...(section(root)?.querySelectorAll<HTMLInputElement>('input') ?? [])].find((i) =>
      (i.closest('label')?.textContent ?? i.getAttribute('aria-label') ?? '').includes(label),
    ) ?? null;
  const errorOf = (root: HTMLElement, label: string) => {
    const field = input(root, label);
    const id = field?.getAttribute('aria-describedby')?.split(' ').find((x) => x.endsWith('-err'));
    return id ? env!.textOf(root.querySelector(`#${CSS.escape(id)}`)) : null;
  };

  it('never offers refine as the default mode: such a turn only starts when the owner chooses it', async () => {
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(modeRadio(root, 'solo')?.checked).toBe(true));
    expect([...root.querySelectorAll<HTMLInputElement>(`input[type=radio][name$="-mode"]`)].map((r) => r.value)).toEqual([
      'solo',
      'duel',
      'debate',
    ]);
  });

  it('shows the saved ones and saves what the owner changes', async () => {
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(section(root)).not.toBeNull());
    expect(input(root, 'Rondes màximes')?.value).toBe('12');
    expect(input(root, 'Pressupost per torn')?.value).toBe('3');
    expect(input(root, 'Límit automàtic')?.checked).toBe(true);
    expect(input(root, 'Límit de paraules')?.disabled).toBe(true);
    expect(input(root, "S'atura sol")?.checked).toBe(true);
    expect(input(root, 'Llindar de convergència')?.value).toBe('90');

    type(input(root, 'Rondes màximes')!, '20');
    type(input(root, 'Pressupost per torn')!, '4,5');
    input(root, 'Límit automàtic')!.click();
    e.flushSync();
    expect(input(root, 'Límit de paraules')?.disabled).toBe(false);
    type(input(root, 'Límit de paraules')!, '1500');
    section(root)!.querySelector<HTMLInputElement>('input[type=radio][value=chatgpt]')!.click();
    type(input(root, 'Llindar de convergència')!, '80');
    submit(root);
    await vi.waitFor(() => expect(server.puts).toHaveLength(1));
    expect(server.puts[0]!.refine).toEqual({
      max_rounds: 20, budget_eur: 4.5, max_words: 1500, stop_on_convergence: true, convergence_threshold: 80, editor: 'chatgpt',
    });
  });

  it('goes back to the automatic word limit as null', async () => {
    server = new FakeApi({ ...SAVED, refine: { ...SAVED.refine, max_words: 800, stop_on_convergence: false } });
    vi.stubGlobal('fetch', server.fetch);
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(input(root, 'Límit de paraules')?.value).toBe('800'));
    expect(input(root, 'Límit automàtic')?.checked).toBe(false);
    expect(input(root, "S'atura sol")?.checked).toBe(false);
    input(root, 'Límit automàtic')!.click();
    e.flushSync();
    submit(root);
    await vi.waitFor(() => expect(server.puts).toHaveLength(1));
    expect(server.puts[0]!.refine).toMatchObject({ max_words: null, stop_on_convergence: false });
  });

  it('does not save values out of range, and says which', async () => {
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(section(root)).not.toBeNull());
    type(input(root, 'Rondes màximes')!, '1');
    type(input(root, 'Pressupost per torn')!, '1.000');
    input(root, 'Límit automàtic')!.click();
    e.flushSync();
    type(input(root, 'Límit de paraules')!, '');
    submit(root);
    await settle();
    expect(server.puts).toEqual([]);
    expect(errorOf(root, 'Rondes màximes')).toBe("Ha d'estar entre 2 i 50.");
    expect(errorOf(root, 'Pressupost per torn')).toBe(invalidAmount());
    expect(errorOf(root, 'Límit de paraules')).toBe('Cal un número.');
    expect(notice(root)).toBe('Revisa els camps marcats.');
  });
});

describe('SettingsDrawer in English and Spanish (ADR 0011)', () => {
  const headings = (root: HTMLElement) => [...root.querySelectorAll('section h3')].map((h) => env!.textOf(h));
  const field = (root: HTMLElement, label: string) =>
    [...root.querySelectorAll('label.field')].find((l) => l.textContent?.trim().startsWith(label)) ?? null;

  // The language chosen stays in this browser: the next page load must start in Catalan again.
  afterEach(() => localStorage.removeItem(LOCALE_KEY));

  it('names its sections and says what is wrong in Spanish', async () => {
    const e = await fresh();
    e.i18n.set('es');
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(modeRadio(root, 'solo')?.checked).toBe(true));
    expect(e.textOf(root.querySelector('h2'))).toBe('Configuración');
    expect(headings(root)).toEqual([
      'Por defecto',
      'Consejo',
      'Perfecciona',
      'Historial',
      'Modelos',
      'Costes y moneda',
      'Precios',
      'Presupuestos y planes',
      'Idioma',
      'Efectos visuales',
    ]);
    expect(e.textOf(field(root, 'Límite de palabras')?.querySelector('span'))).toBe('Límite de palabras (100–20.000)');

    type(field(root, 'Rondas de revisión')!.querySelector('input')!, '9');
    submit(root);
    await settle();
    expect(server.puts).toEqual([]);
    expect(e.textOf(field(root, 'Rondas de revisión')!.querySelector('.error-text'))).toBe('Tiene que estar entre 0 y 4.');
    expect(notice(root)).toBe('Revisa los campos marcados.');
    expect(saveButton(root).textContent?.trim()).toBe('Guardar la configuración');
  });

  it('repaints itself, the last save included, when the owner picks another language in it', async () => {
    const e = await fresh();
    await e.app.init();
    const root = openDrawer(e);
    await vi.waitFor(() => expect(modeRadio(root, 'solo')?.checked).toBe(true));
    submit(root);
    await vi.waitFor(() => expect(notice(root)).toBe('Configuració desada.'));

    const picker = root.querySelector<HTMLSelectElement>('section.local select')!;
    picker.value = 'en';
    picker.dispatchEvent(new Event('change', { bubbles: true }));
    e.flushSync();
    expect(e.i18n.locale).toBe('en');
    expect(e.textOf(root.querySelector('h2'))).toBe('Settings');
    expect(notice(root)).toBe('Settings saved.');
    expect(e.textOf(field(root, 'Review rounds')?.querySelector('span'))).toBe('Review rounds (0–4)');
    expect(root.querySelector('input[aria-label="Monthly API budget of Claude, in euros"]')).not.toBeNull();
    expect(saveButton(root).textContent?.trim()).toBe('Save settings');
  });
});
