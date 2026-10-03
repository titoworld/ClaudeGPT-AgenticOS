// The command palette's conversation search (audit A12): the server searches every
// conversation, a moment after the last keystroke; more matches than fit are one
// «Mostra'n més converses» away, and «Cap resultat» only appears once the server has
// answered that nothing matches.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationSummary } from '../lib/protocol';
import { conversations, ConversationServer } from '../lib/test-conversations';
import { polyfillDialog } from '../lib/test-server';

const DEBOUNCE = 250;
const ZEBRA = 'Pressupost zebra irrepetible';
const MORE = "Mostra'n més converses";

let server: ConversationServer;

async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: CommandPalette } = await import('./CommandPalette.svelte');
  return { app, render, cleanup, textOf, flushSync, CommandPalette };
}

let env: Awaited<ReturnType<typeof load>> | null = null;

/** The server has `list`, the app has loaded the first page, and the palette is open. */
async function opened(list: ConversationSummary[]) {
  server = new ConversationServer(list);
  vi.stubGlobal('fetch', server.fetch);
  env = await load();
  await env.app.convs.refresh();
  const root = env.render(env.CommandPalette, {});
  env.app.paletteOpen = true;
  env.flushSync();
  return { e: env, root };
}

const input = (root: HTMLElement) => root.querySelector<HTMLInputElement>('input[role=combobox]')!;
const labels = (root: HTMLElement) => [...root.querySelectorAll('[role=option] .label')].map((el) => el.textContent);
const conversationLabels = (root: HTMLElement) => labels(root).filter((l) => l !== MORE);
const listText = (root: HTMLElement) => env!.textOf(root.querySelector('[role=listbox]'));

function type(root: HTMLElement, value: string): void {
  input(root).value = value;
  input(root).dispatchEvent(new Event('input', { bubbles: true }));
  env!.flushSync();
}

function press(root: HTMLElement, key: string): void {
  input(root).dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
  env!.flushSync();
}

async function until(check: () => void): Promise<void> {
  await vi.waitFor(() => {
    env!.flushSync();
    check();
  });
}

beforeAll(async () => {
  polyfillDialog();
  // jsdom does not scroll: the palette keeps the active option in view.
  Element.prototype.scrollIntoView ??= function () {};
  await load();
});

beforeEach(() => {
  history.replaceState(null, '', '#/');
  vi.useFakeTimers();
});

afterEach(() => {
  env?.cleanup();
  env = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('CommandPalette conversation search (A12)', () => {
  it('finds a conversation beyond the loaded pages and opens it', async () => {
    const { e, root } = await opened(conversations(60, { 5: ZEBRA }));
    type(root, 'zebra');
    expect(listText(root)).toContain('Cercant converses…');
    expect(listText(root)).not.toContain('Cap resultat');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(labels(root)).toEqual([ZEBRA]));
    expect(server.lists.at(-1)).toMatchObject({ before: null, q: 'zebra' });

    press(root, 'Enter');
    expect(e.app.paletteOpen).toBe(false);
    expect(location.hash).toBe('#/c/5');
  });

  it('more matches than fit: «Mostra\'n més converses» loads the next ones and the palette stays open', async () => {
    const { e, root } = await opened(conversations(120));
    type(root, 'numero');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(labels(root).at(-1)).toBe(MORE));
    const first = conversationLabels(root);
    expect(first.length).toBeGreaterThanOrEqual(10);
    expect(first[0]).toBe('Conversa número 120');

    // With the keyboard: to the last option, then Enter.
    press(root, 'End');
    press(root, 'Enter');
    expect(e.app.paletteOpen).toBe(true);
    await until(() => expect(conversationLabels(root)).toHaveLength(first.length * 2));
    expect(conversationLabels(root).slice(0, first.length)).toEqual(first);
    expect(new Set(conversationLabels(root)).size).toBe(first.length * 2);
    const [, next] = server.lists.slice(-2);
    expect(next).toMatchObject({ q: 'numero', before: 121 - first.length });
    expect(document.activeElement).toBe(input(root));
  });

  it('says there is no result only once the server has answered', async () => {
    const { root } = await opened(conversations(60, { 5: ZEBRA }));
    type(root, 'qwerty');
    expect(listText(root)).toContain('Cercant converses…');
    expect(listText(root)).not.toContain('Cap resultat');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(listText(root)).toBe('Cap resultat per a «qwerty».'));
  });

  it('actions still show at once while the conversations are searched', async () => {
    const { root } = await opened(conversations(60, { 5: ZEBRA }));
    server.holdLists = true;
    type(root, 'configuracio');
    expect(labels(root)).toEqual(['Configuració']);
    expect(listText(root)).toContain('Cercant converses…');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    server.held[0]!.release();
    await until(() => expect(listText(root)).not.toContain('Cercant'));
    expect(labels(root)).toEqual(['Configuració']);
    expect(listText(root)).not.toContain('Cap resultat');
  });

  it('without a text: the actions and the most recent conversations, no request', async () => {
    const { root } = await opened(conversations(60, { 5: ZEBRA }));
    const sent = server.lists.length;
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    expect(conversationLabels(root)).toEqual(expect.arrayContaining(['Conversa número 60', 'Conversa número 55']));
    expect(conversationLabels(root)).not.toContain('Conversa número 54');
    expect(server.lists).toHaveLength(sent);
  });
});

describe('CommandPalette: the modes', () => {
  it('offers every mode, «Perfecciona» too: choosing it there is the owner choosing it', async () => {
    const { e, root } = await opened(conversations(3));
    type(root, 'mode');
    expect(labels(root)).toEqual(
      expect.arrayContaining(['Canvia al mode Solo', 'Canvia al mode Duel', 'Canvia al mode Consell', 'Canvia al mode Perfecciona']),
    );
    type(root, 'perfecciona');
    expect(labels(root)[0]).toBe('Canvia al mode Perfecciona');
    press(root, 'Enter');
    expect(e.app.composer.mode).toBe('refine');
    expect(e.app.paletteOpen).toBe(false);
  });
});

describe('CommandPalette in English and Spanish', () => {
  afterEach(() => localStorage.removeItem('aos.lang'));

  it('names its commands and groups in the language in force, and finds them by its own keywords', async () => {
    const { e, root } = await opened(conversations(3));
    const { i18n } = await import('../lib/i18n/index.svelte');
    const groups = () => [...root.querySelectorAll('li.group')].map((li) => li.textContent);

    i18n.set('en');
    e.flushSync();
    expect(labels(root)).toEqual([
      'New conversation',
      'Switch to Solo mode',
      'Switch to Duel mode',
      'Switch to Council mode',
      'Switch to Refine mode',
      'Open the dashboard',
      'Settings',
      'Log out',
      'Conversa número 3',
      'Conversa número 2',
      'Conversa número 1',
    ]);
    expect(groups()).toEqual(['Actions', 'Conversations']);
    expect(input(root).placeholder).toBe('Type a command or search for a conversation…');
    type(root, 'statistics');
    expect(labels(root)[0]).toBe('Open the dashboard');
    expect(listText(root)).toContain('Searching conversations…');

    i18n.set('es');
    type(root, 'estadisticas');
    expect(labels(root)[0]).toBe('Abrir el panel');
    expect(listText(root)).toContain('Buscando conversaciones…');
    type(root, '');
    expect(labels(root).slice(0, 2)).toEqual(['Nueva conversación', 'Cambiar al modo Solo']);
    expect(groups()).toEqual(['Acciones', 'Conversaciones']);
  });
});
