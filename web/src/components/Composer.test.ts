// The composer before the saved settings arrive (audit A11): it cannot send, it says
// why, a failed load offers «Torna-ho a provar», and the owner's choices survive the load.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings } from '../lib/protocol';
import { deferred, FakeApi, FakeSocket } from '../lib/test-server';

const SAVED: RuntimeSettings = {
  revision: 3,
  default_mode: 'solo',
  default_target: 'chatgpt',
  debate: { rounds: 1, consensus_threshold: 70, synthesizer: 'chatgpt' },
  use_cache: false,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'auto', eur_per_usd: 0.86 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
};

/** A new app (as after a page load) and the composer, from the same module graph. */
async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: Composer } = await import('./Composer.svelte');
  return { app, render, cleanup, textOf, flushSync, Composer };
}

let server: FakeApi;
let env: Awaited<ReturnType<typeof load>> | null = null;

async function fresh() {
  env = await load();
  return env;
}

/** Logs in with the settings request pending, mounts the composer and opens the socket. */
async function start(getSettings: () => RuntimeSettings | Promise<RuntimeSettings>) {
  const e = await fresh();
  server.settingsGet = getSettings;
  const entering = e.app.init();
  await vi.waitFor(() => expect(e.app.auth).toBe('ready'));
  const socket = FakeSocket.last();
  socket.open();
  const root = e.render(e.Composer, {});
  const textarea = root.querySelector('textarea')!;
  textarea.value = 'Quina és la capital de Mongòlia?';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  e.flushSync();
  return { e, entering, socket, root, textarea };
}

const sendButton = (root: HTMLElement) => root.querySelector<HTMLButtonElement>('button.send')!;
const turnStarts = (socket: FakeSocket) => socket.sent.filter((m) => m.type === 'turn.start');

function pressEnter(textarea: HTMLTextAreaElement): void {
  textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  env!.flushSync();
}

beforeAll(async () => {
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

describe('Composer before the saved settings arrive (A11)', () => {
  it('cannot send until they load, and says so', async () => {
    const slow = deferred<RuntimeSettings>();
    const { e, entering, socket, root, textarea } = await start(() => slow.promise);
    expect(sendButton(root).disabled).toBe(true);
    expect(e.textOf(root.querySelector('.kbd-hint'))).toBe('Carregant la configuració…');
    pressEnter(textarea);
    sendButton(root).click();
    e.flushSync();
    expect(turnStarts(socket)).toEqual([]);

    slow.resolve(structuredClone(SAVED));
    await entering;
    e.flushSync();
    expect(sendButton(root).disabled).toBe(false);
    pressEnter(textarea);
    expect(turnStarts(socket)).toHaveLength(1);
    expect(turnStarts(socket)[0]).toMatchObject({ mode: 'solo', target: 'chatgpt' });
  });

  it('keeps a mode picked while they load', async () => {
    const slow = deferred<RuntimeSettings>();
    const { e, entering, root } = await start(() => slow.promise);
    const duel = root.querySelector<HTMLInputElement>('input[type=radio][value=duel]')!;
    duel.click();
    e.flushSync();
    slow.resolve(structuredClone(SAVED));
    await entering;
    e.flushSync();
    expect(duel.checked).toBe(true);
    expect(e.app.composer.mode).toBe('duel');
  });

  it('shows a failed load with «Torna-ho a provar»', async () => {
    let failing = true;
    const { e, entering, socket, root } = await start(() => {
      if (failing) throw new TypeError('Failed to fetch');
      return structuredClone(SAVED);
    });
    await entering;
    e.flushSync();
    const alert = root.querySelector('[role=alert]');
    expect(e.textOf(alert)).toContain("No s'ha pogut carregar la configuració.");
    expect(e.textOf(alert)).toContain('No es pot connectar amb el servidor.');
    expect(sendButton(root).disabled).toBe(true);

    failing = false;
    const gets = server.count('GET /api/settings');
    const retry = [...root.querySelectorAll('button')].find((b) => b.textContent?.includes('Torna-ho a provar'))!;
    retry.click();
    await vi.waitFor(() => expect(e.app.settingsStatus).toBe('ready'));
    e.flushSync();
    expect(server.count('GET /api/settings')).toBe(gets + 1);
    expect(root.querySelector('[role=alert]')).toBeNull();
    expect(sendButton(root).disabled).toBe(false);
    sendButton(root).click();
    e.flushSync();
    expect(turnStarts(socket)).toHaveLength(1);
  });
});
