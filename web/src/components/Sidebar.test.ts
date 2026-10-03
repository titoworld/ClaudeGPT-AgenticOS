// The sidebar's search (audit A12): the server searches every conversation, not only
// the pages loaded, a moment after the last keystroke; «Mostra'n més» stays during a
// search, an answer for an older text is dropped, and nothing says there is no match
// before the server has said so.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationSummary } from '../lib/protocol';
import { conversations, ConversationServer } from '../lib/test-conversations';
import { polyfillDialog } from '../lib/test-server';

/** How long the search waits after the last keystroke (about 250 ms). */
const DEBOUNCE = 250;
const ZEBRA = 'Pressupost zebra irrepetible';

let server: ConversationServer;

/** A new app (as after a page load) and the sidebar, from the same module graph. */
async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: Sidebar } = await import('./Sidebar.svelte');
  return { app, render, cleanup, textOf, flushSync, Sidebar };
}

let env: Awaited<ReturnType<typeof load>> | null = null;

/** The server has `list`; the app has loaded the first page and the sidebar is shown. */
async function shown(list: ConversationSummary[]) {
  server = new ConversationServer(list);
  vi.stubGlobal('fetch', server.fetch);
  env = await load();
  await env.app.convs.refresh();
  const root = env.render(env.Sidebar, {});
  return { e: env, root };
}

const nav = (root: HTMLElement) => root.querySelector<HTMLElement>('nav.list')!;
const titles = (root: HTMLElement) => [...root.querySelectorAll('nav.list li.item .title')].map((el) => el.textContent);
const button = (root: HTMLElement, text: string) =>
  [...root.querySelectorAll<HTMLButtonElement>('nav.list button')].find((b) => b.textContent?.includes(text));
const searchInput = (root: HTMLElement) => root.querySelector<HTMLInputElement>('input[type=search]')!;

function type(root: HTMLElement, value: string): void {
  const input = searchInput(root);
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  env!.flushSync();
}

/** Waits until `check` passes, flushing the DOM between attempts. */
async function until(check: () => void): Promise<void> {
  await vi.waitFor(() => {
    env!.flushSync();
    check();
  });
}

beforeAll(async () => {
  polyfillDialog();
  await load(); // compiles the components once, outside the tests' time limit
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  env?.cleanup();
  env = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('Sidebar search (A12)', () => {
  it('finds a conversation beyond the first page, and says nothing about a match until the server answers', async () => {
    // 60 conversations: the one searched for is the 56th, beyond the first page.
    const { e, root } = await shown(conversations(60, { 5: ZEBRA }));
    expect(titles(root)).toHaveLength(50);
    expect(e.app.convs.list.some((c) => c.id === 5)).toBe(false);
    const sent = server.lists.length;

    type(root, 'zebra');
    expect(e.textOf(nav(root))).not.toContain('Cap conversa coincideix');
    expect(e.textOf(nav(root))).toContain('Cercant');
    await vi.advanceTimersByTimeAsync(DEBOUNCE - 1);
    expect(server.lists).toHaveLength(sent); // still waiting for more keystrokes

    await vi.advanceTimersByTimeAsync(1);
    await until(() => expect(titles(root)).toEqual([ZEBRA]));
    expect(server.lists.slice(sent)).toEqual([{ limit: 50, before: null, q: 'zebra', background: false }]);
    expect(button(root, "Mostra'n més")).toBeUndefined();
  });

  it('asks once, for the text typed last', async () => {
    const { root } = await shown(conversations(60, { 5: ZEBRA }));
    const sent = server.lists.length;
    type(root, 'z');
    await vi.advanceTimersByTimeAsync(100);
    type(root, 'ze');
    await vi.advanceTimersByTimeAsync(100);
    type(root, '  zeb ');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(titles(root)).toEqual([ZEBRA]));
    expect(server.lists.slice(sent).map((r) => r.q)).toEqual(['zeb']);
  });

  it('keeps «Mostra\'n més» during a search, until every match is shown', async () => {
    const { root } = await shown(conversations(120));
    const sent = server.lists.length;
    type(root, 'NUMERO'); // the server ignores case and accents
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(titles(root)).toHaveLength(50));
    button(root, "Mostra'n més")!.click();
    await until(() => expect(titles(root)).toHaveLength(100));
    button(root, "Mostra'n més")!.click();
    await until(() => expect(titles(root)).toHaveLength(120));
    expect(button(root, "Mostra'n més")).toBeUndefined();
    expect(server.lists.slice(sent)).toEqual([
      { limit: 50, before: null, q: 'NUMERO', background: false },
      { limit: 50, before: 71, q: 'NUMERO', background: false },
      { limit: 50, before: 21, q: 'NUMERO', background: false },
    ]);
    expect(new Set(titles(root)).size).toBe(120);
  });

  it('drops an answer for an older text', async () => {
    const { root } = await shown(conversations(60, { 3: 'Zebres del Serengeti', 5: ZEBRA }));
    server.holdLists = true;
    type(root, 'zeb');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    type(root, 'zebra');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    expect(server.held.map((h) => h.request.q)).toEqual(['zeb', 'zebra']);

    server.held[1]!.release();
    await until(() => expect(titles(root)).toEqual([ZEBRA]));
    server.held[0]!.release(); // «zeb» also matched the Serengeti
    await vi.advanceTimersByTimeAsync(0);
    env!.flushSync();
    expect(titles(root)).toEqual([ZEBRA]);
  });

  it('shows at once the loaded conversations that contain the text, then what the server found', async () => {
    const { root } = await shown(conversations(60));
    server.holdLists = true;
    type(root, 'número 6');
    // Loaded: 60..11. Only «Conversa número 60» contains the text, literally.
    expect(titles(root)).toEqual(['Conversa número 60']);
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    server.held[0]!.release();
    await until(() => expect(titles(root)).toEqual(['Conversa número 60', 'Conversa número 6']));
  });

  it('says there is no match once the server has said so', async () => {
    const { e, root } = await shown(conversations(60, { 5: ZEBRA }));
    type(root, 'qwerty');
    expect(e.textOf(nav(root))).toBe('Cercant…');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(e.textOf(nav(root))).toBe('Cap conversa coincideix amb «qwerty».'));
  });

  it('a search that fails says so and can be tried again', async () => {
    const { e, root } = await shown(conversations(60, { 5: ZEBRA }));
    server.holdLists = true;
    type(root, 'zebra');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    server.held[0]!.fail(503, 'Servei no disponible.');
    await until(() => expect(e.textOf(nav(root))).toContain("No s'ha pogut fer la cerca."));
    expect(e.textOf(nav(root))).not.toContain('Cap conversa coincideix');
    server.holdLists = false;
    button(root, 'Torna-ho a provar')!.click();
    await until(() => expect(titles(root)).toEqual([ZEBRA]));
    expect(e.textOf(nav(root))).not.toContain("No s'ha pogut fer la cerca.");
  });

  it('a rename or a deletion shows in the results at once', async () => {
    const { e, root } = await shown(conversations(60, { 5: ZEBRA }));
    type(root, 'zebra');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(titles(root)).toEqual([ZEBRA]));

    expect(await e.app.renameConversation(5, 'Pressupost zebra revisat')).toBe(true);
    await until(() => expect(titles(root)).toEqual(['Pressupost zebra revisat']));
    await e.app.deleteConversation(5);
    await until(() => expect(titles(root)).toEqual([]));
    expect(server.conversations.some((c) => c.id === 5)).toBe(false);
  });

  it('an empty search shows the whole list again, with its «Mostra\'n més»', async () => {
    const { root } = await shown(conversations(60, { 5: ZEBRA }));
    type(root, 'zebra');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(titles(root)).toEqual([ZEBRA]));
    const sent = server.lists.length;
    type(root, '   ');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    expect(titles(root)).toHaveLength(50);
    expect(button(root, "Mostra'n més")).toBeDefined();
    expect(server.lists).toHaveLength(sent);
  });
});

describe('Sidebar in English and Spanish', () => {
  /** The language of this test's module graph (the one the sidebar reads). */
  const language = () => import('../lib/i18n/index.svelte').then((m) => m.i18n);

  afterEach(() => localStorage.removeItem('aos.lang'));

  it('shows its texts in the language in force, and repaints them when it changes', async () => {
    const now = new Date().toISOString();
    const { e, root } = await shown(conversations(3).map((c) => ({ ...c, updated_at: now })));
    const i18n = await language();
    const headings = () => [...root.querySelectorAll('nav.list h2')].map((h) => h.textContent);
    const links = () => e.textOf(root.querySelector('nav.links'));

    i18n.set('en');
    e.flushSync();
    expect(headings()).toEqual(['Today']);
    expect(e.textOf(root.querySelector('.btn.new'))).toBe('New conversation');
    expect(searchInput(root).placeholder).toBe('Search conversations');
    expect(root.querySelector('aside')!.getAttribute('aria-label')).toBe('Conversations and navigation');
    expect(links()).toBe('Dashboard Settings Log out');
    expect(root.querySelector('li.item button')!.getAttribute('aria-label')).toBe('Rename “Conversa número 3”');

    i18n.set('es');
    e.flushSync();
    expect(headings()).toEqual(['Hoy']);
    expect(e.textOf(root.querySelector('.btn.new'))).toBe('Nueva conversación');
    expect(searchInput(root).placeholder).toBe('Buscar conversaciones');
    expect(links()).toBe('Panel Configuración Cerrar sesión');
    expect(root.querySelector('li.item button')!.getAttribute('aria-label')).toBe('Cambiar el nombre de «Conversa número 3»');
  });

  it('says what a search found, in English and in Spanish', async () => {
    const { e, root } = await shown(conversations(60, { 5: ZEBRA }));
    const i18n = await language();
    const status = () => e.textOf(root.querySelector('[role=status]'));
    i18n.set('en');
    type(root, 'zebra');
    expect(e.textOf(nav(root))).toContain('Searching…');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(titles(root)).toEqual([ZEBRA]));
    expect(status()).toBe('1 conversation found.');

    type(root, 'cap-ni-una');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(e.textOf(nav(root))).toBe('No conversation matches “cap-ni-una”.'));
    i18n.set('es');
    e.flushSync();
    expect(e.textOf(nav(root))).toBe('Ninguna conversación coincide con «cap-ni-una».');

    type(root, 'número');
    await vi.advanceTimersByTimeAsync(DEBOUNCE);
    await until(() => expect(status()).toBe('50 conversaciones encontradas, y hay más.'));
    expect(button(root, 'Mostrar más')).toBeDefined();
  });
});
