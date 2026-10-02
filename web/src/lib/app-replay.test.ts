import { afterAll, describe, expect, it, vi } from 'vitest';
import type { ConversationDetail, Message, ServerMessage } from './protocol';
import { message, sequence } from './test-fixtures';

/** WebSocket double: the app's Connection creates it on connect. */
class FakeSocket {
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  send(data: string): void {
    this.sent.push(JSON.parse(data) as Record<string, unknown>);
  }
  close(): void {
    this.readyState = 3;
  }
  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(msg: ServerMessage): void {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

// Two conversations, each with a debate still running: only its question is stored.
const stored = vi.hoisted(() => new Map<number, Message[]>());

vi.mock('./api', () => {
  class ApiError extends Error {
    status = 500;
    retryAfter = null;
  }
  const fail = async () => {
    throw new ApiError('no');
  };
  return {
    ApiError,
    setUnauthorizedHandler: () => {},
    api: {
      authState: async () => ({ authenticated: true, setup_required: false }),
      settings: fail,
      models: fail,
      providers: fail,
      pricing: fail,
      spend: fail,
      conversations: async () => [],
      conversation: async (id: number): Promise<ConversationDetail> => ({
        id, title: 't', created_at: '', updated_at: '', last_mode: 'debate', message_count: 1, summary: null,
        messages: stored.get(id) ?? [],
      }),
    },
  };
});

import { app } from './app.svelte';

const question = (turnId: number, text: string): Message =>
  message({
    id: turnId, turn_id: turnId, kind: 'question', content: text, final: true,
    meta: { mode: 'debate', options: { debate: { rounds: 4, consensus_threshold: 70, synthesizer: 'chatgpt' }, use_cache: false } },
  });
stored.set(3, [question(5, 'I ara?')]);
stored.set(4, [question(8, 'I després?')]);

const started = (requestId: string, conversationId: number, turnId: number) =>
  sequence(requestId, [
    { type: 'turn.started', conversation_id: conversationId, turn_id: turnId, mode: 'debate', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
  ]);

describe('running turns replayed after a page reload (F5)', () => {
  vi.stubGlobal('WebSocket', FakeSocket);
  afterAll(() => vi.unstubAllGlobals());

  it('keep the options, target and question the turn was started with', async () => {
    await app.init();
    await app.syncRoute({ name: 'chat', id: 3 });
    const ws = FakeSocket.all.at(-1)!;
    ws.open();
    ws.receive({
      type: 'hello', version: '0.2.0', providers: [], fx: { eur_per_usd: 0.9, as_of: null, source: 'manual' },
      active_turns: [
        { request_id: 'r1', conversation_id: 3, last_seq: 2 },
        { request_id: 'r2', conversation_id: 4, last_seq: 2 },
      ],
    });
    expect(ws.sent.filter((m) => m.type === 'turn.subscribe').map((m) => m.request_id)).toEqual(['r1', 'r2']);

    // The open conversation is already loaded when its turn is replayed.
    for (const ev of started('r1', 3, 5)) ws.receive(ev);
    const r1 = app.viewTurns.find((t) => t.requestId === 'r1')!;
    expect(r1.question).toBe('I ara?');
    expect(r1.options).toEqual({ debate: { rounds: 4, consensus_threshold: 70, synthesizer: 'chatgpt' }, use_cache: false });

    // Another conversation's turn is replayed first and filled in when it is opened.
    for (const ev of started('r2', 4, 8)) ws.receive(ev);
    expect(app.turns.get('r2')!.options).toBeNull();
    await app.syncRoute({ name: 'chat', id: 4 });
    const r2 = app.viewTurns.find((t) => t.requestId === 'r2')!;
    expect(r2.question).toBe('I després?');
    expect(r2.options?.debate.consensus_threshold).toBe(70);
    expect(r2.options?.debate.rounds).toBe(4);
  });
});
