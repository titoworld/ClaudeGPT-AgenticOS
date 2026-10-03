// The sidebar drawer on phones (audit N21) is modal: while it is open the rest of the
// page is inert (keyboard and screen readers stay in the menu), the focus moves into
// it, Escape closes it, and closing it gives the focus back to the menu button.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { conversations, ConversationServer } from '../lib/test-conversations';
import { textOf } from '../lib/test-render';
import { polyfillDialog } from '../lib/test-server';

async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { prefs } = await import('../lib/prefs.svelte');
  const { render, cleanup } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: Shell } = await import('./Shell.svelte');
  return { app, prefs, render, cleanup, flushSync, Shell };
}

let env: Awaited<ReturnType<typeof load>> | null = null;

/** The app's shell on a narrow (phone) or wide window, with a few conversations. */
async function shell(narrow: boolean) {
  const server = new ConversationServer(conversations(3));
  vi.stubGlobal('fetch', server.fetch);
  env = await load();
  env.prefs.narrow = narrow;
  await env.app.convs.refresh();
  const root = env.render(env.Shell, {});
  return { e: env, root };
}

const menuButton = (root: HTMLElement) => root.querySelector<HTMLButtonElement>('button[aria-label="Obre el menú"]');
const drawer = (root: HTMLElement) => root.querySelector<HTMLElement>('.side')!;
const main = (root: HTMLElement) => root.querySelector<HTMLElement>('main')!;
const inDrawer = (root: HTMLElement) => drawer(root).contains(document.activeElement);
// Svelte sets `inert` as a property (jsdom does not reflect it to the attribute).
const isInert = (el: HTMLElement) => el.inert === true;

function escape(target: Element): void {
  target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  env!.flushSync();
}

/** Opens the drawer with the menu button, as the owner does. */
async function openDrawer(root: HTMLElement): Promise<void> {
  const button = menuButton(root)!;
  button.focus();
  button.click();
  env!.flushSync();
  expect(env!.app.sidebarOpen).toBe(true);
  await vi.waitFor(() => expect(inDrawer(root)).toBe(true));
}

beforeAll(async () => {
  polyfillDialog();
  await load();
});

afterEach(() => {
  env?.cleanup();
  env = null;
  vi.unstubAllGlobals();
});

describe('the drawer on a phone (N21)', () => {
  it('open, the rest of the page is inert and the focus is in the drawer; Escape closes it', async () => {
    // jsdom has no ResizeObserver (the chat follows its content's height).
    vi.stubGlobal('ResizeObserver', class { observe(): void {} unobserve(): void {} disconnect(): void {} });
    const { e, root } = await shell(true);
    expect(isInert(drawer(root))).toBe(true); // closed: off screen and out of reach
    expect(isInert(main(root))).toBe(false);

    await openDrawer(root);
    expect(isInert(main(root))).toBe(true);
    expect(isInert(drawer(root))).toBe(false);
    expect(drawer(root).getAttribute('role')).toBe('dialog');
    expect(drawer(root).getAttribute('aria-modal')).toBe('true');

    escape(document.activeElement!);
    expect(e.app.sidebarOpen).toBe(false);
    expect(isInert(main(root))).toBe(false);
    expect(isInert(drawer(root))).toBe(true);
    await vi.waitFor(() => expect(document.activeElement).toBe(menuButton(root)));
  });

  it('closed with the backdrop or its own button, the focus goes back to the menu button', async () => {
    vi.stubGlobal('ResizeObserver', class { observe(): void {} unobserve(): void {} disconnect(): void {} });
    const { e, root } = await shell(true);

    await openDrawer(root);
    root.querySelector<HTMLButtonElement>('button.backdrop')!.click();
    e.flushSync();
    expect(e.app.sidebarOpen).toBe(false);
    await vi.waitFor(() => expect(document.activeElement).toBe(menuButton(root)));

    await openDrawer(root);
    drawer(root).querySelector<HTMLButtonElement>('button[aria-label="Tanca el menú"]')!.click();
    e.flushSync();
    expect(e.app.sidebarOpen).toBe(false);
    await vi.waitFor(() => expect(document.activeElement).toBe(menuButton(root)));
  });

  it('closed by the app (a conversation picked), the focus does not stay in the hidden drawer', async () => {
    vi.stubGlobal('ResizeObserver', class { observe(): void {} unobserve(): void {} disconnect(): void {} });
    const { e, root } = await shell(true);
    await openDrawer(root);
    drawer(root).querySelector<HTMLAnchorElement>('li.item a')!.focus();
    e.app.sidebarOpen = false; // what syncRoute does on a route change
    e.flushSync();
    await vi.waitFor(() => expect(document.activeElement).toBe(menuButton(root)));
  });

  it('Escape in a dialog opened over the drawer closes only that dialog', async () => {
    vi.stubGlobal('ResizeObserver', class { observe(): void {} unobserve(): void {} disconnect(): void {} });
    const { e, root } = await shell(true);
    await openDrawer(root);
    e.app.paletteOpen = true; // Ctrl+K
    e.flushSync();
    escape(document.querySelector('dialog.palette input')!);
    expect(e.app.sidebarOpen).toBe(true);
    expect(isInert(main(root))).toBe(true);
  });

  it('on a wide window the sidebar is not a drawer: nothing is inert and Escape leaves it alone', async () => {
    vi.stubGlobal('ResizeObserver', class { observe(): void {} unobserve(): void {} disconnect(): void {} });
    const { e, root } = await shell(false);
    expect(isInert(main(root))).toBe(false);
    expect(isInert(drawer(root))).toBe(false);
    expect(drawer(root).getAttribute('role')).toBeNull();
    const search = drawer(root).querySelector<HTMLInputElement>('input[type=search]')!;
    search.focus();
    escape(search);
    expect(e.prefs.sidebarCollapsed).toBe(false);
    expect(document.activeElement).toBe(search);
    expect(isInert(main(root))).toBe(false);
  });
});

describe('the shell in English and Spanish', () => {
  beforeEach(() => {
    // jsdom has no ResizeObserver (the chat follows its content's height).
    vi.stubGlobal('ResizeObserver', class { observe(): void {} unobserve(): void {} disconnect(): void {} });
  });
  afterEach(() => localStorage.removeItem('aos.lang'));

  it('names the menu, the search, the connection and the page in the language in force', async () => {
    const { e, root } = await shell(true);
    const { i18n } = await import('../lib/i18n/index.svelte');
    const menu = () => root.querySelector<HTMLButtonElement>('header button.icon-btn')!;

    i18n.set('en');
    e.flushSync();
    expect(menu().getAttribute('aria-label')).toBe('Open the menu');
    expect(textOf(root.querySelector('.palette-text'))).toBe('Search and commands');
    expect(textOf(root.querySelector('header h1'))).toBe('New conversation');
    expect(textOf(root.querySelector('.conn [role=status]'))).toBe('Disconnected');
    expect(document.title).toBe('ClaudeGPT OS');
    menu().click();
    e.flushSync();
    expect(drawer(root).getAttribute('aria-label')).toBe('Menu');
    expect(root.querySelector('button.backdrop')!.getAttribute('aria-label')).toBe('Close the menu');

    i18n.set('es');
    e.flushSync();
    expect(drawer(root).getAttribute('aria-label')).toBe('Menú');
    expect(root.querySelector('button.backdrop')!.getAttribute('aria-label')).toBe('Cerrar el menú');
    expect(textOf(root.querySelector('.palette-text'))).toBe('Búsqueda y comandos');
    expect(textOf(root.querySelector('header h1'))).toBe('Nueva conversación');
    expect(textOf(root.querySelector('.conn [role=status]'))).toBe('Desconectado');
  });

  it("the page's title is the conversation's, even one called like a new conversation", async () => {
    const server = new ConversationServer(conversations(3, { 2: 'New conversation' }));
    vi.stubGlobal('fetch', server.fetch);
    env = await load();
    await env.app.convs.refresh();
    const { i18n } = await import('../lib/i18n/index.svelte');
    i18n.set('en');
    env.render(env.Shell, {});
    expect(document.title).toBe('ClaudeGPT OS');
    env.app.convs.currentId = 2;
    env.flushSync();
    expect(document.title).toBe('New conversation · ClaudeGPT OS');
    env.app.convs.currentId = 3;
    env.flushSync();
    expect(document.title).toBe('Conversa número 3 · ClaudeGPT OS');
  });
});
