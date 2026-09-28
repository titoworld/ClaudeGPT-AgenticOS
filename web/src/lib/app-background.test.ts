// Requests the app makes by itself are not owner activity (audit N7). Refreshes after a
// `hello` (every reconnection), after turn events, periodic ones and retries carry
// BACKGROUND_HEADER, and the server checks the session without extending it, like a
// WebSocket ping: an unused tab must not keep the session alive. What the owner does
// (loading the page, logging in, opening, renaming or deleting a conversation, saving
// the settings, a «Torna-ho a provar») still counts. A logout never counts: if it fails,
// the session must not last longer because of it.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { BACKGROUND_HEADER, type ConversationSummary, type RuntimeSettings, type ServerMessage } from './protocol';
import { FakeApi, FakeSocket, hello } from './test-server';
import { usage } from './test-fixtures';

const SAVED: RuntimeSettings = {
  revision: 3,
  default_mode: 'solo',
  default_target: 'claude',
  debate: { rounds: 1, consensus_threshold: 80, synthesizer: 'claude' },
  use_cache: false,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'manual', eur_per_usd: 0.9 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
};

const conversation = (id: number, title: string): ConversationSummary => ({
  id,
  title,
  created_at: '2026-09-28T09:00:00Z',
  updated_at: '2026-09-28T09:30:00Z',
  last_mode: 'solo',
  message_count: 2,
});

let server: FakeApi;
const created: { toLogin(): void }[] = [];

async function freshApp() {
  vi.resetModules();
  const { app } = await import('./app.svelte');
  created.push(app);
  return app;
}

/** Requests made from `index` on, with whether they carried the header. */
const since = (index: number) => server.requests.slice(index);
const fg = (call: string) => ({ call, background: false });
const bg = (call: string) => ({ call, background: true });

/** Runs `step` and returns the requests it made once they have all been sent. */
async function requestsOf(step: () => unknown, settle = 0): Promise<{ call: string; background: boolean }[]> {
  const start = server.requests.length;
  await step();
  await vi.advanceTimersByTimeAsync(settle);
  return since(start);
}

beforeAll(async () => {
  await freshApp();
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  history.replaceState(null, '', '#/');
  server = new FakeApi(SAVED);
  server.conversations = [conversation(7, 'Primera'), conversation(8, 'Segona')];
  server.spend = {
    month: '2026-09',
    fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' },
    by_agent: {
      claude: { api_usd: 0, equivalent_usd: 0, unpriced_calls: 0, budget_eur: null, budget_used: null, plan_eur: null, plan_value: null },
      chatgpt: { api_usd: 0, equivalent_usd: 0, unpriced_calls: 0, budget_eur: null, budget_used: null, plan_eur: null, plan_value: null },
    },
  };
  FakeSocket.all = [];
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
  vi.useFakeTimers();
});

afterEach(() => {
  for (const app of created.splice(0)) app.toLogin();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('requests the app makes by itself (N7)', () => {
  it('the header is the one the server looks for', () => {
    expect(BACKGROUND_HEADER).toBe('X-AOS-Background');
  });

  it('refreshes after hello, turn events and reconnections carry the header; the page load does not', async () => {
    const app = await freshApp();
    const load = await requestsOf(() => app.init());
    expect(load.map((r) => r.call).sort()).toEqual(
      [
        'GET /api/auth/state',
        'GET /api/conversations',
        'GET /api/models',
        'GET /api/providers',
        'GET /api/settings',
        'GET /api/spend',
      ].sort(),
    );
    expect(load.every((r) => !r.background)).toBe(true);

    // First connection: hello refreshes the month spend.
    const socket = FakeSocket.last();
    expect(await requestsOf(() => {
      socket.open();
      socket.receive(hello());
    })).toEqual([bg('GET /api/spend')]);

    // A turn: the new conversation and the end of the turn refresh the list, and a
    // moment later the usage windows and the spend.
    expect(app.send('Quina és la capital de Mongòlia?')).toBe(true);
    const requestId = String(socket.sent.find((m) => m.type === 'turn.start')!.request_id);
    const event = (msg: object) => socket.receive(msg as ServerMessage);
    expect(await requestsOf(() =>
      event({ type: 'turn.started', request_id: requestId, seq: 1, conversation_id: 7, turn_id: 70, mode: 'solo', new_conversation: true }),
    )).toEqual([bg('GET /api/conversations')]);
    expect(await requestsOf(() =>
      event({
        type: 'turn.completed', request_id: requestId, seq: 2, conversation_id: 7, turn_id: 70, final_message_ids: [71],
        usage: usage(10, 20), savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null },
        consensus: null, cached: false,
      }),
    2000)).toEqual([bg('GET /api/conversations'), bg('GET /api/providers'), bg('GET /api/spend')]);

    // The connection drops and comes back: the new hello refreshes the spend again.
    const reconnect = await requestsOf(async () => {
      socket.readyState = 3;
      socket.onclose?.({ code: 1006 });
      await vi.advanceTimersByTimeAsync(1000);
      const next = FakeSocket.last();
      expect(next).not.toBe(socket);
      next.open();
      next.receive(hello());
    });
    expect(reconnect).toEqual([bg('GET /api/spend')]);

    // The server has forgotten a finished turn of the open conversation: reload it.
    expect(await requestsOf(() => FakeSocket.last().receive({ type: 'turn.unknown', request_id: requestId }))).toEqual([
      bg('GET /api/conversations/7'),
    ]);
  });

  it('retries of a failed settings load carry it; the owner’s «Torna-ho a provar» does not', async () => {
    let failing = true;
    server.settingsGet = () => {
      if (failing) throw new TypeError('Failed to fetch');
      return structuredClone(SAVED);
    };
    const app = await freshApp();
    await app.init();
    expect(app.settingsStatus).toBe('error');
    expect(server.requests.filter((r) => r.call === 'GET /api/settings')).toEqual([fg('GET /api/settings')]);

    // The automatic retry (1 s later) and the one every hello makes.
    expect(await requestsOf(() => undefined, 1000)).toEqual([bg('GET /api/settings')]);
    const socket = FakeSocket.last();
    const onHello = await requestsOf(() => {
      socket.open();
      socket.receive(hello());
    });
    expect(onHello).toContainEqual(bg('GET /api/settings'));
    expect(onHello.every((r) => r.background)).toBe(true);

    // The owner presses «Torna-ho a provar».
    failing = false;
    expect(await requestsOf(() => app.loadSettings())).toEqual([fg('GET /api/settings')]);
    expect(app.settingsStatus).toBe('ready');
  });
});

describe('what the owner does (N7)', () => {
  it('counts as activity: no header', async () => {
    const app = await freshApp();
    await app.init();
    await vi.advanceTimersByTimeAsync(0);
    const socket = FakeSocket.last();
    socket.open();
    socket.receive(hello());
    await vi.advanceTimersByTimeAsync(0);

    expect(await requestsOf(() => app.syncRoute({ name: 'chat', id: 8 }))).toEqual([fg('GET /api/conversations/8')]);
    expect(await requestsOf(() => app.renameConversation(8, 'Segona, amb un títol millor'))).toEqual([
      fg('PATCH /api/conversations/8'),
    ]);
    expect(await requestsOf(() => app.convs.loadMore())).toEqual([fg('GET /api/conversations')]);
    expect(await requestsOf(() => app.deleteConversation(8))).toEqual([fg('DELETE /api/conversations/8')]);
    expect(await requestsOf(() => app.saveSettings({ ...structuredClone(SAVED), use_cache: true }))).toEqual([
      fg('PUT /api/settings'),
      fg('GET /api/pricing'),
      fg('GET /api/spend'),
    ]);
    expect(await requestsOf(() => app.loadModels(true))).toEqual([fg('GET /api/models')]);
    expect(await requestsOf(() => app.loadSettings())).toEqual([fg('GET /api/settings')]);
  });

  it('logging in counts; logging out never does', async () => {
    server.session = false;
    const app = await freshApp();
    await app.init();
    expect(app.auth).toBe('login');
    const login = await requestsOf(() => app.login('contrasenya', '123456'));
    expect(login[0]).toEqual(fg('POST /api/auth/login'));
    expect(login.every((r) => !r.background)).toBe(true);
    expect(await requestsOf(() => app.logout())).toEqual([bg('POST /api/auth/logout')]);
  });
});
