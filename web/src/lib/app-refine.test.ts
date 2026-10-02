// A refine turn («Perfecciona», docs/adr/0010-mode-perfecciona.md) through the app: it is
// sent with its options, «Atura en acabar la ronda» sends turn.stop and the turn says it
// will stop until turn.stopping confirms it (asked again after a reconnection, given up
// when the server refuses it), and a turn that converged is celebrated like a consensus.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings, ServerMessage, TurnEvent } from './protocol';
import { sequence } from './test-fixtures';
import { refineEvents } from './test-refine';
import { FakeApi, FakeSocket, hello } from './test-server';

const SAVED: RuntimeSettings = {
  revision: 2,
  default_mode: 'debate',
  default_target: 'claude',
  debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' },
  refine: { max_rounds: 8, budget_eur: 1.5, max_words: null, stop_on_convergence: true, convergence_threshold: 90, editor: 'chatgpt' },
  use_cache: false,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'manual', eur_per_usd: 0.9 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
  pdf_in_revisions: 'text',
};

let server: FakeApi;
const created: { toLogin(): void }[] = [];

async function freshApp() {
  vi.resetModules();
  const { app } = await import('./app.svelte');
  const { toasts } = await import('./toasts.svelte');
  const { sceneHost } = await import('./scene-host.svelte');
  created.push(app);
  return { app, toasts, sceneHost };
}

beforeEach(() => {
  localStorage.clear();
  history.replaceState(null, '', '#/');
  server = new FakeApi(SAVED);
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

/** A logged-in app with the settings loaded and the socket open. */
async function ready() {
  const env = await freshApp();
  await env.app.init();
  const socket = FakeSocket.last();
  socket.open();
  socket.receive(hello());
  await vi.advanceTimersByTimeAsync(0);
  return { ...env, socket };
}

const sent = (socket: FakeSocket, type: string) => socket.sent.filter((m) => m.type === type);

/** The events of a refine turn the server runs for `requestId` up to round 2's reviews. */
function upToReviews(requestId: string): TurnEvent[] {
  const events = refineEvents(requestId);
  return events.slice(0, events.findIndex((e) => e.type === 'phase' && e.phase === 'edit' && e.round === 2));
}

/** A refine turn sent from the composer, which the server has run up to round 2's reviews. */
async function runningRefine() {
  const env = await ready();
  env.app.composer.mode = 'refine';
  expect(env.app.send('Escriu un pla de llançament per a la beta.')).toBe(true);
  const requestId = String(sent(env.socket, 'turn.start')[0]!.request_id);
  const events = upToReviews(requestId);
  for (const ev of events) env.socket.receive(ev as ServerMessage);
  await vi.advanceTimersByTimeAsync(0);
  return { ...env, requestId, next: events.length + 1 };
}

describe('sending a refine turn', () => {
  it('carries its options, the saved defaults with what the owner changed', async () => {
    const { app, socket } = await ready();
    app.composer.mode = 'refine';
    app.composer.refineRounds = 20;
    app.composer.refineWords = 900;
    expect(app.send('Escriu un pla')).toBe(true);
    const [start] = sent(socket, 'turn.start');
    expect(start).toMatchObject({ mode: 'refine', text: 'Escriu un pla' });
    expect((start!.options as { refine: unknown }).refine).toEqual({
      max_rounds: 20, budget_eur: 1.5, max_words: 900, stop_on_convergence: true, convergence_threshold: 90, editor: 'chatgpt',
    });
    // The live turn knows its limits before any event.
    expect(app.viewTurns.at(-1)?.options?.refine?.max_rounds).toBe(20);
  });

  it('the other modes send no refine options', async () => {
    const { app, socket } = await ready();
    app.composer.mode = 'duel';
    expect(app.send('Una pregunta')).toBe(true);
    expect(sent(socket, 'turn.start')[0]!.options).not.toHaveProperty('refine');
  });
});

describe('«Atura en acabar la ronda» (turn.stop)', () => {
  it('asks the running refine turn once, and says it will stop until turn.stopping confirms the round', async () => {
    const { app, socket, requestId, next } = await runningRefine();
    app.stopAfterRound();
    app.stopAfterRound(); // a second click asks nothing more
    expect(sent(socket, 'turn.stop')).toEqual([{ type: 'turn.stop', request_id: requestId }]);
    const turn = app.runningTurn!;
    expect(turn.stopRequested).toBe(true);

    socket.receive({ type: 'turn.stopping', request_id: requestId, seq: next, round: 2 });
    expect(turn.stoppingRound).toBe(2);
    expect(turn.stopRequested).toBe(false);
    app.stopAfterRound(); // it is already stopping
    expect(sent(socket, 'turn.stop')).toHaveLength(1);
  });

  it('is only for refine turns', async () => {
    const { app, socket } = await ready();
    app.composer.mode = 'debate';
    expect(app.send('Una pregunta')).toBe(true);
    const requestId = String(sent(socket, 'turn.start')[0]!.request_id);
    for (const ev of sequence(requestId, [
      { type: 'turn.started', conversation_id: 3, turn_id: 4, mode: 'debate', new_conversation: true },
      { type: 'phase', phase: 'answer', round: 0 },
    ])) socket.receive(ev as ServerMessage);
    app.stopAfterRound();
    expect(sent(socket, 'turn.stop')).toEqual([]);
  });

  it('without a connection it says so and changes nothing', async () => {
    const { app, socket, toasts } = await runningRefine();
    socket.readyState = 3;
    socket.onclose?.({ code: 1006 });
    app.stopAfterRound();
    expect(app.runningTurn?.stopRequested).toBe(false);
    expect(toasts.items.at(-1)?.text).toBe("Sense connexió: no s'ha pogut demanar que s'aturi en acabar la ronda.");
  });

  it('a refusal of the server leaves the turn running, and says why', async () => {
    const { app, socket, toasts, requestId } = await runningRefine();
    app.stopAfterRound();
    const refusal = "Només un torn «Perfecciona» es pot aturar en acabar la ronda; per aturar-lo ara, cancel·la'l.";
    socket.receive({ type: 'error', code: 'invalid', message: refusal, request_id: requestId });
    const turn = app.runningTurn!;
    expect(turn.status).toBe('running');
    expect(turn.stopRequested).toBe(false);
    expect(toasts.items.at(-1)?.text).toBe(refusal);
  });

  it('asks again after a reconnection, until the server has said turn.stopping', async () => {
    const { app, socket, requestId } = await runningRefine();
    app.stopAfterRound();
    // The connection drops before the server read it.
    socket.readyState = 3;
    socket.onclose?.({ code: 1006 });
    await vi.advanceTimersByTimeAsync(1000);
    const again = FakeSocket.last();
    expect(again).not.toBe(socket);
    again.open();
    again.receive({ ...hello(), active_turns: [{ request_id: requestId, conversation_id: 12, last_seq: 99 }] } as ServerMessage);
    expect(again.sent.filter((m) => m.type === 'turn.subscribe' || m.type === 'turn.stop').map((m) => m.type)).toEqual([
      'turn.subscribe',
      'turn.stop',
    ]);
    expect(app.runningTurn?.stopRequested).toBe(true);
  });

  it('«Atura ara» is turn.cancel, as for any turn', async () => {
    const { app, socket, requestId } = await runningRefine();
    app.cancel();
    expect(sent(socket, 'turn.cancel')).toEqual([{ type: 'turn.cancel', request_id: requestId }]);
  });
});

describe('the end of a refine turn: one that converged, or where nobody found anything left to change, is a consensus', () => {
  for (const reason of ['converged', 'unchanged', 'owner', 'budget'] as const) {
    it(`stop_reason ${reason}`, async () => {
      const { app, socket, sceneHost } = await ready();
      const flash = vi.spyOn(sceneHost, 'flash');
      app.composer.mode = 'refine';
      expect(app.send('Escriu un pla')).toBe(true);
      const requestId = String(sent(socket, 'turn.start')[0]!.request_id);
      for (const ev of refineEvents(requestId, reason)) socket.receive(ev as ServerMessage);
      expect(app.turns.get(requestId)?.stopReason).toBe(reason);
      expect(flash.mock.calls).toEqual(reason === 'converged' || reason === 'unchanged' ? [['consensus']] : []);
    });
  }
});
