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
