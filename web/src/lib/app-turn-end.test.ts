// What the app refreshes when a turn ends (audit A15): a turn that fails or is cancelled
// may have billed calls too, so the usage windows, the month spend and the conversation
// list are refreshed after any terminal event, as after turn.completed. The live turn
// shows what it cost (N10).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings, ServerMessage, TurnEvent } from './protocol';
import { priced, REFUSED, sequence } from './test-fixtures';
import { FakeApi, FakeSocket, hello } from './test-server';

const SAVED: RuntimeSettings = {
  revision: 2,
  default_mode: 'debate',
  default_target: 'claude',
  debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' },
  use_cache: false,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'manual', eur_per_usd: 0.9 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
};

const NO_SPEND = { api_usd: 0, equivalent_usd: 0, unpriced_calls: 0, budget_eur: null, budget_used: null, plan_eur: null, plan_value: null };
const ANSWER = priced(1200, 800, 0.02);
const TOTAL = priced(7200, 1400, 0.0662);

let server: FakeApi;
const created: { toLogin(): void }[] = [];

async function freshApp() {
  vi.resetModules();
  const { app } = await import('./app.svelte');
  created.push(app);
  return app;
}

beforeEach(() => {
  localStorage.clear();
  history.replaceState(null, '', '#/');
  server = new FakeApi(SAVED);
  server.spend = { month: '2026-09', fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' }, by_agent: { claude: NO_SPEND, chatgpt: NO_SPEND } };
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

const bg = (call: string) => ({ call, background: true });

/** A debate that has billed both first answers and a refused revision, still running. */
function billedDebate(requestId: string): TurnEvent[] {
  const done = (streamId: string, messageId: number) =>
    ({
      type: 'stream.completed', stream_id: streamId, message_id: messageId, usage: ANSWER, latency_ms: 900, ttft_ms: 100,
      agreement: null, unchanged: false, cost_basis: 'api',
    }) as const;
  return sequence(requestId, [
    { type: 'turn.started', conversation_id: 7, turn_id: 40, mode: 'debate', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
    done('c0', 41),
    done('g0', 42),
    { type: 'phase', phase: 'revision', round: 1 },
    { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'g1', agent: 'chatgpt', kind: 'revision', round: 1, model: 'gpt-5' },
    { type: 'stream.failed', stream_id: 'c1', error: { kind: 'invalid', message: 'Claude ha declinat.' }, usage: REFUSED },
  ]);
}

const TERMINAL: Record<'turn.completed' | 'turn.failed' | 'turn.cancelled', (requestId: string, seq: number) => TurnEvent> = {
  'turn.completed': (request_id, seq) => ({
    type: 'turn.completed', request_id, seq, conversation_id: 7, turn_id: 40, final_message_ids: [45], usage: TOTAL,
    savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null }, consensus: null, cached: false,
  }),
  'turn.failed': (request_id, seq) => ({
    type: 'turn.failed', request_id, seq, error: { kind: 'rate_limit', message: 'Límit assolit.' }, usage: TOTAL,
  }),
  'turn.cancelled': (request_id, seq) => ({ type: 'turn.cancelled', request_id, seq, usage: TOTAL }),
};

/**
 * An app whose running debate has already billed some calls. `elsewhere`: a turn another
 * tab started in another conversation, which this one follows too.
 */
async function runningDebate(elsewhere: string | null = null) {
  const app = await freshApp();
  await app.init();
  const socket = FakeSocket.last();
  socket.open();
  const greeting = hello();
  if (greeting.type === 'hello' && elsewhere) greeting.active_turns = [{ request_id: elsewhere, conversation_id: 9, last_seq: 0 }];
  socket.receive(greeting);
  await vi.advanceTimersByTimeAsync(0);
  app.composer.mode = 'debate';
  expect(app.send('Una pregunta cara')).toBe(true);
  const requestId = String(socket.sent.find((m) => m.type === 'turn.start')!.request_id);
  const events = billedDebate(requestId);
  for (const ev of events) socket.receive(ev as ServerMessage);
  await vi.advanceTimersByTimeAsync(5000); // nothing is refreshed while it runs
  return { app, socket, requestId, next: events.length + 1 };
}

describe('when a turn ends (A15)', () => {
  for (const type of Object.keys(TERMINAL) as (keyof typeof TERMINAL)[]) {
    it(`${type}: the conversation list at once, the usage windows and the spend a moment later`, async () => {
      const { app, socket, requestId, next } = await runningDebate();
      const start = server.requests.length;
      socket.receive(TERMINAL[type](requestId, next) as ServerMessage);
      await vi.advanceTimersByTimeAsync(0);
      expect(server.requests.slice(start)).toEqual([bg('GET /api/conversations')]);
      await vi.advanceTimersByTimeAsync(1500);
      expect(server.requests.slice(start)).toEqual([
        bg('GET /api/conversations'),
        bg('GET /api/providers'),
        bg('GET /api/spend'),
      ]);
      // The turn shows what it cost, whatever the way it ended (N10).
      expect(app.turns.get(requestId)?.usage).toEqual(TOTAL);
    });
  }

  it('turns ending close together refresh the usage once', async () => {
    const { socket, requestId, next } = await runningDebate('other-tab');
    const start = server.requests.length;
    socket.receive(TERMINAL['turn.failed'](requestId, next) as ServerMessage);
    await vi.advanceTimersByTimeAsync(1000);
    // The other tab's turn is cancelled a moment later.
    socket.receive(TERMINAL['turn.cancelled']('other-tab', 1) as ServerMessage);
    await vi.advanceTimersByTimeAsync(3000);
    const calls = server.requests.slice(start).map((r) => r.call);
    expect(calls.filter((c) => c === 'GET /api/conversations')).toHaveLength(2);
    expect(calls.filter((c) => c === 'GET /api/providers')).toHaveLength(1);
    expect(calls.filter((c) => c === 'GET /api/spend')).toHaveLength(1);
  });
});
