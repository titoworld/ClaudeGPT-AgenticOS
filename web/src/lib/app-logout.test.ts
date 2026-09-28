// A logout the server does not confirm (audit A4). The session cookie is HttpOnly: only
// the server can end the session. So when POST /api/auth/logout answers neither 204 nor
// 401 (a network failure, a 502 while the proxy restarts, a time-out...) the app never
// shows the login screen as if the session had ended: it forgets the session's data,
// closes the socket and locks this browser. The lock survives a reload, which retries
// the logout before trusting /api/auth/state, and it ends only with a logout the server
// confirms (204, or 401: there was no session) or a new login (which ends the old session).
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { GATING_REQUEST_TIMEOUT_MS } from './api';
import type {
  ConversationSummary,
  ModelCatalog,
  MonthSpend,
  ProviderStatus,
  RuntimeSettings,
  ServerMessage,
} from './protocol';
import { DEFAULT_SETTINGS } from './settings';
import { deferred, FakeApi, FakeSocket } from './test-server';

const PENDING = 'aos.logout-pending';

const SAVED: RuntimeSettings = {
  revision: 4,
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
};

const CONVERSATION: ConversationSummary = {
  id: 7,
  title: 'Pla de còpies de seguretat',
  created_at: '2026-09-28T10:00:00Z',
  updated_at: '2026-09-28T10:05:00Z',
  last_mode: 'debate',
  message_count: 4,
};

const agentSpend = { api_usd: 1.5, equivalent_usd: 3, unpriced_calls: 0, budget_eur: 50, budget_used: 0.03, plan_eur: null, plan_value: null };
const SPEND: MonthSpend = {
  month: '2026-09',
  fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' },
  by_agent: { claude: agentSpend, chatgpt: agentSpend },
};

const agentModels = { mode: 'cli' as const, default_model: 'sonnet', fast_model: 'haiku', models: [], live: false };
const CATALOG: ModelCatalog = { claude: agentModels, chatgpt: agentModels };

const PROVIDERS: ProviderStatus[] = [
  { agent: 'claude', mode: 'fake', available: true, model: 'fake-claude', detail: '', limits: [] },
];

const HELLO: ServerMessage = {
  type: 'hello',
  version: '0.2.0',
  providers: PROVIDERS,
  fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' },
  active_turns: [],
};

const badGateway = () => new Response(JSON.stringify({ detail: 'Bad Gateway' }), { status: 502 });
const networkDown = (): Response => {
  throw new TypeError('Failed to fetch');
};

let server: FakeApi;

/** Apps of this test: afterEach sends them to the login screen, where other tabs' changes leave them alone. */
const created: { toLogin(): void }[] = [];

/** A new app, as after a page load, talking to `server`. */
async function freshApp() {
  vi.resetModules();
  const { app } = await import('./app.svelte');
  created.push(app);
  return app;
}

/** Logged in, with the socket open and the session's data loaded. */
async function loggedIn() {
  const app = await freshApp();
  await app.init();
  expect(app.auth).toBe('ready');
  FakeSocket.last().open();
  FakeSocket.last().receive(HELLO);
  await app.loadPricing();
  await vi.waitFor(() => expect(app.catalog).not.toBeNull());
  expect(app.send('Una pregunta que només ha de veure el propietari')).toBe(true);
  app.composer.draft = 'Un esborrany que tampoc';
  // Everything the session showed is in memory now.
  expect(app.settings.revision).toBe(SAVED.revision);
  expect(app.convs.list).toHaveLength(1);
  expect(app.spend).not.toBeNull();
  expect(app.pricing).not.toBeNull();
  expect(app.providers).toHaveLength(1);
  expect(app.turns.unfinished()).toHaveLength(1);
  return app;
}

type App = Awaited<ReturnType<typeof freshApp>>;

/** Nothing of the session is left in memory, and the socket is closed. */
function expectForgotten(app: App): void {
  expect(app.turns.unfinished()).toEqual([]);
  expect(app.turns.forConversation(null)).toEqual([]);
  expect(app.convs.list).toEqual([]);
  expect(app.convs.detail).toBeNull();
  expect(app.settings).toEqual(DEFAULT_SETTINGS);
  expect(app.settingsStatus).toBe('loading');
  expect(app.catalog).toBeNull();
  expect(app.pricing).toBeNull();
  expect(app.spend).toBeNull();
  expect(app.fx).toBeNull();
  expect(app.providers).toEqual([]);
  expect(app.composer.draft).toBe('');
  expect(app.conn.status).toBe('closed');
  expect(FakeSocket.last().readyState).toBe(3);
}

const marker = () => ({
  local: localStorage.getItem(PENDING) !== null,
  session: sessionStorage.getItem(PENDING) !== null,
});

/** Calls made from `index` on. */
const callsFrom = (index: number) => server.calls.slice(index);

beforeAll(async () => {
  await freshApp(); // compiles the modules once, outside the tests' time limit
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  server = new FakeApi(SAVED);
  server.conversations = [CONVERSATION];
  server.spend = SPEND;
  server.catalog = CATALOG;
  server.providers = PROVIDERS;
  FakeSocket.all = [];
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  for (const app of created.splice(0)) app.toLogin();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

describe('a logout the server does not confirm (A4)', () => {
  it.each([
    ['a 502 from the proxy', badGateway, "El servidor ha respost amb un error."],
    ['a network failure', networkDown, 'No es pot connectar amb el servidor.'],
  ])('%s locks this browser instead of showing the login screen', async (_name, answer, reason) => {
    const app = await loggedIn();
    server.logoutAnswer = answer;
    const before = server.calls.length;

    await app.logout();

    expect(app.auth).toBe('locked');
    expect(app.logoutBusy).toBe(false);
    expect(app.logoutError).toBe(reason);
    expectForgotten(app);
    expect(marker()).toEqual({ local: true, session: true });
    // The session is still valid on the server, and the app did not ask it anything else.
    expect(server.session).toBe(true);
    expect(callsFrom(before)).toEqual(['POST /api/auth/logout']);
  });

  it('a logout that never answers gives up after the time limit and locks', async () => {
    vi.useFakeTimers();
    const app = await loggedIn();
    server.logoutAnswer = () => new Promise<Response>(() => {});
    const done = app.logout();
    await vi.advanceTimersByTimeAsync(GATING_REQUEST_TIMEOUT_MS);
    await done;
    expect(app.auth).toBe('locked');
    expect(app.logoutError).toBe('El servidor no ha respost a temps.');
    expect(marker()).toEqual({ local: true, session: true });
  });

  it('shows nothing of the session while the logout is under way', async () => {
    const app = await loggedIn();
    const answer = deferred<Response>();
    server.logoutAnswer = () => answer.promise;
    const done = app.logout();
    expect(app.auth).toBe('locked');
    expect(app.logoutBusy).toBe(true);
    expectForgotten(app);
    // A reload now retries it (the request may never have reached the server).
    expect(marker()).toEqual({ local: true, session: true });
    answer.resolve(new Response(null, { status: 204 }));
    await done;
    expect(app.auth).toBe('login');
    expect(app.logoutBusy).toBe(false);
    expect(marker()).toEqual({ local: false, session: false });
  });

  it('a confirmed logout (204) shows the login screen and leaves no marker', async () => {
    const app = await loggedIn();
    await app.logout();
    expect(app.auth).toBe('login');
    expectForgotten(app);
    expect(server.session).toBe(false);
    expect(marker()).toEqual({ local: false, session: false });
  });

  it('a 401 means there was no session left: logged out too', async () => {
    const app = await loggedIn();
    server.session = false; // e.g. it expired meanwhile
    await app.logout();
    expect(app.auth).toBe('login');
    expect(marker()).toEqual({ local: false, session: false });
  });

  it('answers of requests of the ended session change nothing', async () => {
    const app = await loggedIn();
    const list = deferred<ConversationSummary[]>();
    const pricing = deferred<void>();
    const original = server.fetch;
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'https://aos.test').pathname;
      if (path === '/api/conversations') {
        const page = await list.promise;
        return new Response(JSON.stringify(page), { status: 200 });
      }
      if (path === '/api/pricing') await pricing.promise;
      return original(input, init);
    });
    const refresh = app.convs.refresh();
    const prices = app.loadPricing();
    server.logoutAnswer = badGateway;
    await app.logout();
    list.resolve([CONVERSATION]);
    pricing.resolve();
    await Promise.all([refresh, prices]);
    expect(app.auth).toBe('locked');
    expect(app.convs.list).toEqual([]);
    expect(app.convs.listLoading).toBe(false);
    expect(app.pricing).toBeNull();
  });
});

describe('while a logout is pending (A4)', () => {
  /** Logged in, then a logout the server did not confirm. */
  async function locked() {
    const app = await loggedIn();
    server.logoutAnswer = badGateway;
    await app.logout();
    expect(app.auth).toBe('locked');
    return app;
  }

  it('a reload retries the logout before trusting /api/auth/state, and stays locked while it fails', async () => {
    await locked();
    const before = server.calls.length;
    const sockets = FakeSocket.all.length;

    const app = await freshApp(); // the page is reloaded
    await app.init();

    expect(callsFrom(before)).toEqual(['POST /api/auth/logout']);
    expect(app.auth).toBe('locked');
    expect(app.logoutError).toBe('El servidor ha respost amb un error.');
    expect(FakeSocket.all).toHaveLength(sockets); // no socket either
    expect(marker()).toEqual({ local: true, session: true });
  });

  it('a reload finishes the logout once the server answers, and only then asks for the state', async () => {
    await locked();
    server.logoutAnswer = null; // the proxy is back
    const before = server.calls.length;

    const app = await freshApp();
    await app.init();

    expect(callsFrom(before)).toEqual(['POST /api/auth/logout', 'GET /api/auth/state']);
    expect(server.session).toBe(false);
    expect(app.auth).toBe('login');
    expect(marker()).toEqual({ local: false, session: false });
  });

  it('a new tab (the marker only in localStorage) retries it too', async () => {
    await locked();
    sessionStorage.clear(); // another tab has its own sessionStorage
    const before = server.calls.length;
    const app = await freshApp();
    await app.init();
    expect(callsFrom(before)).toEqual(['POST /api/auth/logout']);
    expect(app.auth).toBe('locked');
  });

  it('a reload with the marker only in sessionStorage (localStorage unavailable) retries it too', async () => {
    sessionStorage.setItem(PENDING, '1');
    server.logoutAnswer = badGateway;
    const app = await freshApp();
    await app.init();
    expect(server.calls).toEqual(['POST /api/auth/logout']);
    expect(app.auth).toBe('locked');
  });

  it('«Torna-ho a provar» finishes it', async () => {
    const app = await locked();
    await app.retryLogout();
    expect(app.auth).toBe('locked'); // still failing
    expect(server.count('POST /api/auth/logout')).toBe(2);

    server.logoutAnswer = null;
    await app.retryLogout();
    expect(app.auth).toBe('login');
    expect(server.session).toBe(false);
    expect(marker()).toEqual({ local: false, session: false });
  });

  it('logging in again ends it: the old session ends with the login and the app opens', async () => {
    const app = await locked();
    await app.login('contrasenya', '123456');
    expect(app.auth).toBe('ready');
    expect(marker()).toEqual({ local: false, session: false });

    // A reload goes straight in: nothing is pending any more.
    const before = server.calls.length;
    const reloaded = await freshApp();
    await reloaded.init();
    expect(callsFrom(before)[0]).toBe('GET /api/auth/state');
    expect(server.count('POST /api/auth/logout')).toBe(1);
    expect(reloaded.auth).toBe('ready');
  });

  it('never enters automatically, even from the other screens', async () => {
    const app = await locked();
    await app.checkAuth(); // e.g. «Torna-ho a provar» of the unreachable screen
    expect(app.auth).toBe('locked');
    expect(server.count('GET /api/auth/state')).toBe(1); // only the one of the first load
  });

  it('still locks when the browser storage is unavailable', async () => {
    const app = await loggedIn();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('The quota has been exceeded.', 'QuotaExceededError');
    });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Access is denied for this document.', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('Access is denied for this document.', 'SecurityError');
    });
    server.logoutAnswer = networkDown;
    await app.logout();
    expect(app.auth).toBe('locked');
    await app.checkAuth();
    expect(app.auth).toBe('locked'); // this page remembers it

    server.logoutAnswer = null;
    await app.retryLogout();
    expect(app.auth).toBe('login');
  });
});

describe('other tabs of this browser (A4)', () => {
  const storageEvent = (newValue: string | null) =>
    window.dispatchEvent(new StorageEvent('storage', { key: PENDING, newValue, oldValue: newValue ? null : '1' }));

  it('a logout started in another tab locks this one too, and it helps finish it', async () => {
    const app = await loggedIn();
    server.logoutAnswer = badGateway;
    const before = server.calls.length;
    localStorage.setItem(PENDING, '1'); // what the other tab writes
    storageEvent('1');
    expect(app.auth).toBe('locked');
    expectForgotten(app);
    await vi.waitFor(() => expect(app.logoutBusy).toBe(false));
    expect(callsFrom(before)).toEqual(['POST /api/auth/logout']);
    expect(app.auth).toBe('locked');
  });

  it('once another tab has finished it, a locked tab asks the server again', async () => {
    const app = await loggedIn();
    server.logoutAnswer = badGateway;
    await app.logout();
    expect(app.auth).toBe('locked');

    // The other tab got a 204: the session is over and it removed the marker.
    server.logoutAnswer = null;
    server.session = false;
    localStorage.removeItem(PENDING);
    storageEvent(null);
    await vi.waitFor(() => expect(app.auth).toBe('login'));
    expect(marker()).toEqual({ local: false, session: false });
  });

  it('once the owner has logged in again in another tab, a locked tab opens the app', async () => {
    const app = await loggedIn();
    server.logoutAnswer = badGateway;
    await app.logout();
    server.logoutAnswer = null;

    // The login in the other tab ended the old session and removed the marker.
    localStorage.removeItem(PENDING);
    storageEvent(null);
    await vi.waitFor(() => expect(app.auth).toBe('ready'));
    expect(marker()).toEqual({ local: false, session: false });
  });
});
