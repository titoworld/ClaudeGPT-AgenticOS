// Shared fixtures for unit tests (never imported by application code).
import type { Message, TurnEvent, Usage } from './protocol';

export const usage = (input: number, output: number, cacheRead = 0): Usage => ({
  input_tokens: input,
  output_tokens: output,
  cache_read_tokens: cacheRead,
  cache_write_tokens: 0,
  reasoning_tokens: 0,
  cost_usd: null,
});

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
type Draft = DistributiveOmit<TurnEvent, 'seq' | 'request_id'>;

/** Numbers a list of event drafts with seq 1..n for one request id. */
export function sequence(requestId: string, drafts: Draft[]): TurnEvent[] {
  return drafts.map((d, i) => ({ ...d, request_id: requestId, seq: i + 1 }) as TurnEvent);
}

/** A full two-round debate that reaches consensus in round 2. */
export function debateEvents(requestId = 'req-1'): TurnEvent[] {
  return sequence(requestId, [
    { type: 'turn.started', conversation_id: 7, turn_id: 40, mode: 'debate', new_conversation: true },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
    { type: 'stream.delta', stream_id: 'c0', section: 'text', text: 'Hola ' },
    { type: 'stream.delta', stream_id: 'g0', section: 'text', text: 'Bon dia' },
    { type: 'stream.delta', stream_id: 'c0', section: 'text', text: 'món' },
    {
      type: 'stream.completed', stream_id: 'c0', message_id: 41, usage: usage(100, 20), latency_ms: 1200,
      ttft_ms: 300, agreement: null, unchanged: false,
    },
    {
      type: 'stream.completed', stream_id: 'g0', message_id: 42, usage: usage(90, 25, 50), latency_ms: 1500,
      ttft_ms: 400, agreement: null, unchanged: false,
    },
    { type: 'phase', phase: 'revision', round: 1 },
    { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'g1', agent: 'chatgpt', kind: 'revision', round: 1, model: 'gpt-5' },
    { type: 'stream.delta', stream_id: 'c1', section: 'critique', text: '- Falta context' },
    { type: 'stream.delta', stream_id: 'c1', section: 'answer', text: 'Hola món millorat' },
    { type: 'stream.delta', stream_id: 'g1', section: 'critique', text: '- Correcte' },
    {
      type: 'stream.completed', stream_id: 'c1', message_id: 43, usage: usage(200, 30), latency_ms: 900,
      ttft_ms: 200, agreement: 70, unchanged: false,
    },
    {
      type: 'stream.completed', stream_id: 'g1', message_id: 44, usage: usage(210, 10), latency_ms: 800,
      ttft_ms: 210, agreement: 90, unchanged: true,
    },
    { type: 'phase', phase: 'revision', round: 2 },
    { type: 'stream.started', stream_id: 'c2', agent: 'claude', kind: 'revision', round: 2, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'g2', agent: 'chatgpt', kind: 'revision', round: 2, model: 'gpt-5' },
    { type: 'stream.delta', stream_id: 'c2', section: 'critique', text: '- Ara sí' },
    {
      type: 'stream.completed', stream_id: 'c2', message_id: 45, usage: usage(180, 8), latency_ms: 700,
      ttft_ms: 150, agreement: 92, unchanged: true,
    },
    {
      type: 'stream.completed', stream_id: 'g2', message_id: 46, usage: usage(170, 8), latency_ms: 650,
      ttft_ms: 160, agreement: 88, unchanged: true,
    },
    { type: 'phase', phase: 'synthesis', round: 2 },
    { type: 'stream.started', stream_id: 's', agent: 'claude', kind: 'synthesis', round: 2, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 's', section: 'text', text: 'Síntesi final' },
    {
      type: 'stream.completed', stream_id: 's', message_id: 47, usage: usage(300, 60), latency_ms: 2000,
      ttft_ms: 350, agreement: null, unchanged: false,
    },
    {
      type: 'turn.completed', conversation_id: 7, turn_id: 40, final_message_ids: [47], usage: usage(1250, 161, 50),
      savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 3200, total: 3200, cost_usd: 0.004 },
      consensus: { reached: true, round: 2, scores: { claude: 92, chatgpt: 88 } }, cached: false,
    },
  ]);
}

let nextId = 100;
export function message(partial: Partial<Message> & Pick<Message, 'kind' | 'turn_id'>): Message {
  return {
    id: nextId++,
    content: '',
    agent: null,
    round: 0,
    final: false,
    meta: {},
    created_at: '2026-09-27T10:00:00Z',
    ...partial,
  };
}

/** Stored messages of the same debate as `debateEvents`. */
export function debateMessages(): Message[] {
  const t = 40;
  return [
    message({
      id: 40, turn_id: t, kind: 'question', content: 'Pregunta?', final: true,
      meta: { mode: 'debate', options: { debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: true } },
    }),
    message({ id: 41, turn_id: t, kind: 'answer', agent: 'claude', content: 'Hola món', meta: { model: 'claude-sonnet', usage: usage(100, 20), latency_ms: 1200, ttft_ms: 300 } }),
    message({ id: 42, turn_id: t, kind: 'answer', agent: 'chatgpt', content: 'Bon dia', meta: { model: 'gpt-5', usage: usage(90, 25, 50), latency_ms: 1500, ttft_ms: 400 } }),
    message({ id: 43, turn_id: t, kind: 'revision', agent: 'claude', round: 1, content: 'Hola món millorat', meta: { model: 'claude-sonnet', usage: usage(200, 30), critique: '- Falta context', agreement: 70, unchanged: false } }),
    message({ id: 44, turn_id: t, kind: 'revision', agent: 'chatgpt', round: 1, content: 'Bon dia', meta: { model: 'gpt-5', usage: usage(210, 10), critique: '- Correcte', agreement: 90, unchanged: true } }),
    message({ id: 45, turn_id: t, kind: 'revision', agent: 'claude', round: 2, content: 'Hola món millorat', meta: { model: 'claude-sonnet', usage: usage(180, 8), critique: '- Ara sí', agreement: 92, unchanged: true } }),
    message({ id: 46, turn_id: t, kind: 'revision', agent: 'chatgpt', round: 2, content: 'Bon dia', meta: { model: 'gpt-5', usage: usage(170, 8), critique: '', agreement: 88, unchanged: true } }),
    message({ id: 47, turn_id: t, kind: 'synthesis', agent: 'claude', round: 2, content: 'Síntesi final', final: true, meta: { model: 'claude-sonnet', usage: usage(300, 60), latency_ms: 2000, ttft_ms: 350 } }),
  ];
}

export const priced = (input: number, output: number, costUsd: number): Usage => ({
  ...usage(input, output),
  cost_usd: costUsd,
});

// A duel that compacted the history first, with the numbers the real engine
// produces (fake providers, priced): turn.completed.usage is the two answers
// plus the summary call, which the question stores as meta.compaction_usage.
const SUMMARY = priced(768, 54, 0.003114);
const DUEL_CLAUDE = priced(456, 113, 0.003063);
const DUEL_CHATGPT = priced(456, 122, 0.003198);

export function compactedDuelEvents(requestId = 'req-c'): TurnEvent[] {
  return sequence(requestId, [
    { type: 'phase', phase: 'compaction', round: 0 },
    { type: 'turn.started', conversation_id: 3, turn_id: 9, mode: 'duel', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'fake-claude' },
    { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'fake-chatgpt' },
    {
      type: 'stream.completed', stream_id: 'c', message_id: 10, usage: DUEL_CLAUDE, latency_ms: 5, ttft_ms: 1,
      agreement: null, unchanged: false, cost_basis: 'equivalent',
    },
    {
      type: 'stream.completed', stream_id: 'g', message_id: 11, usage: DUEL_CHATGPT, latency_ms: 5, ttft_ms: 1,
      agreement: null, unchanged: false, cost_basis: 'equivalent',
    },
    {
      type: 'turn.completed', conversation_id: 3, turn_id: 9, final_message_ids: [10, 11],
      usage: priced(1680, 289, 0.009375),
      savings: { cache: 0, compaction: 822, early_stop: 0, unchanged: 0, total: 822, cost_usd: 0.0039 },
      consensus: null, cached: false,
    },
  ]);
}

export function compactedDuelMessages(): Message[] {
  const savings = { cache: 0, compaction: 822, early_stop: 0, unchanged: 0, total: 822, cost_usd: 0.0039 };
  return [
    message({ id: 9, turn_id: 9, kind: 'question', content: 'I ara?', final: true, meta: { mode: 'duel', compaction_usage: SUMMARY } }),
    message({
      id: 10, turn_id: 9, kind: 'answer', agent: 'claude', final: true,
      meta: { model: 'fake-claude', usage: DUEL_CLAUDE, cost_basis: 'equivalent', savings },
    }),
    message({
      id: 11, turn_id: 9, kind: 'answer', agent: 'chatgpt', final: true,
      meta: { model: 'fake-chatgpt', usage: DUEL_CHATGPT, cost_basis: 'equivalent', savings },
    }),
  ];
}

// A three-round debate stopped after revision round 1 (no consensus yet): what
// the client sees live and what the server stored (no synthesis).
const LONG_DEBATE = { debate: { rounds: 3, consensus_threshold: 85, synthesizer: 'claude' as const }, use_cache: false };

export function cancelledDebateEvents(requestId = 'req-x'): TurnEvent[] {
  return sequence(requestId, [
    { type: 'turn.started', conversation_id: 5, turn_id: 20, mode: 'debate', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.completed', stream_id: 'c0', message_id: 21, usage: usage(10, 5), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
    { type: 'stream.completed', stream_id: 'g0', message_id: 22, usage: usage(10, 5), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
    { type: 'phase', phase: 'revision', round: 1 },
    { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'm' },
    { type: 'stream.started', stream_id: 'g1', agent: 'chatgpt', kind: 'revision', round: 1, model: 'm' },
    { type: 'stream.completed', stream_id: 'c1', message_id: 23, usage: usage(20, 5), latency_ms: 1, ttft_ms: 1, agreement: 60, unchanged: false },
    { type: 'stream.completed', stream_id: 'g1', message_id: 24, usage: usage(20, 5), latency_ms: 1, ttft_ms: 1, agreement: 60, unchanged: false },
    { type: 'turn.cancelled' },
  ]);
}

export function cancelledDebateMessages(): Message[] {
  const t = 20;
  return [
    message({ id: 20, turn_id: t, kind: 'question', content: 'Debat llarg', final: true, meta: { mode: 'debate', options: LONG_DEBATE } }),
    message({ id: 21, turn_id: t, kind: 'answer', agent: 'claude', meta: { model: 'm', usage: usage(10, 5) } }),
    message({ id: 22, turn_id: t, kind: 'answer', agent: 'chatgpt', meta: { model: 'm', usage: usage(10, 5) } }),
    message({ id: 23, turn_id: t, kind: 'revision', agent: 'claude', round: 1, meta: { model: 'm', usage: usage(20, 5), agreement: 60 } }),
    message({ id: 24, turn_id: t, kind: 'revision', agent: 'chatgpt', round: 1, meta: { model: 'm', usage: usage(20, 5), agreement: 60 } }),
  ];
}
