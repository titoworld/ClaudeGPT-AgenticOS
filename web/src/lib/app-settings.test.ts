// Loading and saving the settings (audit A11, N11): nothing is sent or saved with the
// built-in defaults before the saved settings arrive, a failed load is shown and
// retried, and the saved defaults never overwrite a composer choice made in this tab.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings } from './protocol';
import { GATING_REQUEST_TIMEOUT_MS } from './api';
import { CONFLICT_DETAIL, deferred, FakeApi, FakeSocket, hello } from './test-server';

/** What the owner saved: every default differs from the built-in one. */
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
  pdf_in_revisions: 'text',
};

let server: FakeApi;

/** A new app (as after a page load), talking to `server`. */
async function freshApp() {
  vi.resetModules();
  const { app, SettingsConflictError } = await import('./app.svelte');
  return { app, SettingsConflictError };
}

const turnStarts = (socket: FakeSocket) => socket.sent.filter((m) => m.type === 'turn.start');

beforeAll(async () => {
  await freshApp(); // compiles the modules once, outside the tests' time limit
});

beforeEach(() => {
  server = new FakeApi(SAVED);
  FakeSocket.all = [];
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('before the saved settings arrive (A11)', () => {
  it('sends nothing, then sends with the saved defaults', async () => {
    const { app } = await freshApp();
    const slow = deferred<RuntimeSettings>();
    server.settingsGet = () => slow.promise;
    const entering = app.init();
    await vi.waitFor(() => expect(app.auth).toBe('ready'));
    const socket = FakeSocket.last();
    socket.open();
    expect(app.conn.status).toBe('open');

    expect(app.settingsStatus).toBe('loading');
    expect(app.canSend()).toBe(false);
    expect(app.send('Quina és la capital de Mongòlia?')).toBe(false);
    expect(turnStarts(socket)).toEqual([]);

    slow.resolve(structuredClone(SAVED));
    await entering;
    expect(app.settingsStatus).toBe('ready');
    expect(app.canSend()).toBe(true);
    expect(app.send('Quina és la capital de Mongòlia?')).toBe(true);
    const [start] = turnStarts(socket);
    expect(start).toMatchObject({ mode: 'solo', target: 'chatgpt' });
    expect(start!.options).toEqual({
      debate: { rounds: 1, consensus_threshold: 70, synthesizer: 'chatgpt' },
      use_cache: false,
    });
  });

  it('keeps the composer choices the owner made meanwhile', async () => {
    const { app } = await freshApp();
    const slow = deferred<RuntimeSettings>();
    server.settingsGet = () => slow.promise;
    const entering = app.init();
    await vi.waitFor(() => expect(app.auth).toBe('ready'));
    app.composer.mode = 'duel'; // the owner picks Duel...
    app.composer.useCache = true; // ...and keeps the cache on (the saved default is off)
    slow.resolve(structuredClone(SAVED));
    await entering;
    expect(app.composer.mode).toBe('duel');
    expect(app.composer.useCache).toBe(true);
    // What the owner did not touch takes the saved defaults.
    expect(app.composer.target).toBe('chatgpt');
    expect(app.composer.rounds).toBe(1);
    expect(app.composer.threshold).toBe(70);
    expect(app.composer.synthesizer).toBe('chatgpt');
  });

  it('refuses to save', async () => {
    const { app } = await freshApp();
    const slow = deferred<RuntimeSettings>();
    server.settingsGet = () => slow.promise;
    const entering = app.init();
    await vi.waitFor(() => expect(app.auth).toBe('ready'));
    await expect(app.saveSettings(structuredClone({ ...SAVED, revision: 0 }))).rejects.toThrow(
      "La configuració encara no s'ha carregat.",
    );
    expect(server.puts).toEqual([]);
    slow.resolve(structuredClone(SAVED));
    await entering;
  });
});

describe('a failed load (A11)', () => {
  it('is shown and retried with backoff, and at once on every hello', async () => {
    const { app } = await freshApp();
    vi.useFakeTimers();
    let failing = true;
    server.settingsGet = () => {
      if (failing) throw new TypeError('Failed to fetch');
      return structuredClone(SAVED);
    };
    await app.init();
    expect(app.auth).toBe('ready');
    expect(app.settingsStatus).toBe('error');
    expect(app.settingsError).toBe('No es pot connectar amb el servidor.');
    expect(app.canSend()).toBe(false);
    const gets = () => server.count('GET /api/settings');
    expect(gets()).toBe(1);

    // Retries after 1, 2, 5, 10 and 30 s, then every 30 s.
    for (const [wait, total] of [[1_000, 2], [2_000, 3], [5_000, 4], [10_000, 5], [30_000, 6], [30_000, 7]] as const) {
      await vi.advanceTimersByTimeAsync(wait - 1);
      expect(gets()).toBe(total - 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(gets()).toBe(total);
    }
    expect(app.settingsStatus).toBe('error');

    // The connection comes back: its hello retries at once.
    const socket = FakeSocket.last();
    socket.open();
    socket.receive(hello());
    await vi.advanceTimersByTimeAsync(0);
    expect(gets()).toBe(8);

    // The next retry comes soon again, and it works.
    failing = false;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(gets()).toBe(9);
    expect(app.settingsStatus).toBe('ready');
    expect(app.settingsError).toBeNull();
    expect(app.composer.mode).toBe('solo');
    expect(app.canSend()).toBe(true);

    // Loaded: no more retries, and a hello does not reload them.
    socket.receive(hello());
    await vi.advanceTimersByTimeAsync(120_000);
    expect(gets()).toBe(9);
    expect(server.puts).toEqual([]);
  });

  it('that never answers fails after a while and is retried like a failed one', async () => {
    const { app } = await freshApp();
    vi.useFakeTimers();
    let hang = true;
    server.settingsGet = () => (hang ? new Promise<RuntimeSettings>(() => {}) : structuredClone(SAVED));
    const entering = app.init();
    await vi.advanceTimersByTimeAsync(GATING_REQUEST_TIMEOUT_MS - 1);
    expect(app.settingsStatus).toBe('loading');
    // A hello meanwhile does not start a second request.
    const socket = FakeSocket.last();
    socket.open();
    socket.receive(hello());
    await vi.advanceTimersByTimeAsync(0);
    expect(server.count('GET /api/settings')).toBe(1);

    await vi.advanceTimersByTimeAsync(1);
    await entering;
    expect(app.settingsStatus).toBe('error');
    expect(app.settingsError).toBe('El servidor no ha respost a temps.');
    expect(app.canSend()).toBe(false);

    hang = false;
    await vi.advanceTimersByTimeAsync(1_000); // the first retry
    expect(server.count('GET /api/settings')).toBe(2);
    expect(app.settingsStatus).toBe('ready');
    expect(app.canSend()).toBe(true);
    expect(server.puts).toEqual([]);
  });

  it('can be retried by hand', async () => {
    const { app } = await freshApp();
    server.settingsGet = () => {
      throw new TypeError('Failed to fetch');
    };
    await app.init();
    expect(app.settingsStatus).toBe('error');
    server.settingsGet = null;
    await expect(app.loadSettings()).resolves.toBe(true);
    expect(app.settingsStatus).toBe('ready');
    expect(app.settings.revision).toBe(7);
    expect(app.composer.target).toBe('chatgpt');
  });
});

describe('saving (N11)', () => {
  it('keeps the composer choices and takes only the defaults that changed', async () => {
    const { app } = await freshApp();
    await app.init();
    expect(app.composer.mode).toBe('solo');
    // The owner switches this conversation to a debate with 3 rounds...
    app.composer.mode = 'debate';
    app.composer.rounds = 3;
    // ...then only raises a budget in the drawer and saves.
    const saved = await app.saveSettings({ ...structuredClone(SAVED), budgets_eur: { claude: 80, chatgpt: 20 } });
    expect(app.composer.mode).toBe('debate');
    expect(app.composer.rounds).toBe(3);
    expect(saved.revision).toBe(8);
    expect(app.settings.revision).toBe(8);

    // A default that changed reaches the options the owner did not touch, never the others.
    await app.saveSettings({
      ...structuredClone(server.settings),
      default_mode: 'duel',
      default_target: 'claude',
      debate: { rounds: 4, consensus_threshold: 70, synthesizer: 'claude' },
    });
    expect(app.composer.target).toBe('claude');
    expect(app.composer.synthesizer).toBe('claude');
    expect(app.composer.mode).toBe('debate');
    expect(app.composer.rounds).toBe(3);
    expect(app.composer.useCache).toBe(false);
  });

  it('based on settings changed elsewhere (409) takes the current ones and says so', async () => {
    const { app, SettingsConflictError } = await freshApp();
    await app.init();
    const edited = { ...structuredClone(SAVED), use_cache: true };
    server.saveElsewhere({ default_mode: 'duel', budgets_eur: { claude: 99, chatgpt: null } });

    const error = await app.saveSettings(edited).catch((err: unknown) => err);
    expect((error as Error).message).toBe(CONFLICT_DETAIL);
    expect(error).toBeInstanceOf(SettingsConflictError);
    expect(server.puts.map((p) => p.revision)).toEqual([7]);
    expect(server.settings.use_cache).toBe(false); // nothing was overwritten
    expect(app.settings.revision).toBe(8);
    expect(app.settings.budgets_eur.claude).toBe(99);
    expect(app.composer.mode).toBe('duel'); // an untouched option follows the new default
  });
});

describe('logging out', () => {
  it('forgets that the settings were loaded, and ignores a load of the old session', async () => {
    const { app } = await freshApp();
    await app.init();
    expect(app.settingsStatus).toBe('ready');

    const old = deferred<RuntimeSettings>();
    server.settingsGet = () => old.promise;
    const oldLoad = app.loadSettings(); // e.g. the drawer was opening
    await app.logout();
    expect(app.auth).toBe('login');
    expect(app.settingsStatus).toBe('loading');
    old.resolve(structuredClone(SAVED));
    await expect(oldLoad).resolves.toBe(false);
    expect(app.settingsStatus).toBe('loading');

    // Back in: nothing is sent until this session has loaded them.
    const next = deferred<RuntimeSettings>();
    server.settingsGet = () => next.promise;
    const entering = app.login('contrasenya', '123456');
    await vi.waitFor(() => expect(app.auth).toBe('ready'));
    FakeSocket.last().open();
    expect(app.canSend()).toBe(false);
    next.resolve(structuredClone(SAVED));
    await entering;
    expect(app.settingsStatus).toBe('ready');
    expect(app.canSend()).toBe(true);
  });
});
