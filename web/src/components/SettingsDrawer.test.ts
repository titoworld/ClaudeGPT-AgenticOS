// The settings drawer (audit A11, N5): its form is built from settings fetched when
// it opens, it cannot save before they arrive, and a save based on settings changed
// elsewhere (409) reloads them instead of undoing that change.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings } from '../lib/protocol';
import { CONFLICT_DETAIL, deferred, FakeApi, FakeSocket, polyfillDialog } from '../lib/test-server';

const SAVED: RuntimeSettings = {
  revision: 7,
  default_mode: 'solo',
  default_target: 'chatgpt',
  debate: { rounds: 1, consensus_threshold: 70, synthesizer: 'chatgpt' },
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
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: SettingsDrawer } = await import('./SettingsDrawer.svelte');
  return { app, render, cleanup, textOf, flushSync, SettingsDrawer };
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
