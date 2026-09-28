// Shared fixtures for unit tests (never imported by application code).
import type { Agent, Message, MessageMeta, TurnEvent, TurnOptions, Usage } from './protocol';
import { addUsage } from './turns.svelte';

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

// A duel where each answer needed a retry after a billed failure (a refusal):
// those calls stored no message, so each final message carries the running
// total of that unstored usage (meta.unstored_usage) and the last one the
// turn's. turn.completed.usage is the answers plus both failed calls.
const FAILED_CLAUDE = priced(400, 12, 0.00138);
const FAILED_CHATGPT = priced(380, 9, 0.00104);

export function retriedDuelEvents(requestId = 'req-r'): TurnEvent[] {
  return sequence(requestId, [
    { type: 'turn.started', conversation_id: 4, turn_id: 30, mode: 'duel', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'fake-claude' },
    { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'fake-chatgpt' },
    {
      type: 'stream.completed', stream_id: 'c', message_id: 31, usage: DUEL_CLAUDE, latency_ms: 5, ttft_ms: 1,
      agreement: null, unchanged: false, cost_basis: 'equivalent',
    },
    {
      type: 'stream.completed', stream_id: 'g', message_id: 32, usage: DUEL_CHATGPT, latency_ms: 5, ttft_ms: 1,
      agreement: null, unchanged: false, cost_basis: 'equivalent',
    },
    {
      type: 'turn.completed', conversation_id: 4, turn_id: 30, final_message_ids: [31, 32],
      usage: [DUEL_CLAUDE, DUEL_CHATGPT, FAILED_CLAUDE, FAILED_CHATGPT].reduce(addUsage), // 1692 in, 256 out
      savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null },
      consensus: null, cached: false,
    },
  ]);
}

export function retriedDuelMessages(): Message[] {
  const savings = { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null };
  return [
    message({ id: 30, turn_id: 30, kind: 'question', content: 'I ara?', final: true, meta: { mode: 'duel' } }),
    message({
      id: 31, turn_id: 30, kind: 'answer', agent: 'claude', final: true,
      meta: { model: 'fake-claude', usage: DUEL_CLAUDE, cost_basis: 'equivalent', savings, unstored_usage: FAILED_CLAUDE },
    }),
    message({
      id: 32, turn_id: 30, kind: 'answer', agent: 'chatgpt', final: true,
      meta: {
        model: 'fake-chatgpt', usage: DUEL_CHATGPT, cost_basis: 'equivalent', savings,
        unstored_usage: addUsage(FAILED_CLAUDE, FAILED_CHATGPT),
      },
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

// Debates whose synthesis is not the first attempt, with the events the real
// engine emits (engine.py `_synthesize`, `_store_degraded_synthesis`): every
// attempt opens its own stream (the chosen synthesizer, then the other agent,
// then a copy of an answer when nobody could synthesize, stored as `degraded`)
// and only the stored synthesis is in final_message_ids. The store keeps that
// one alone: failed attempts leave no message.
export const QUICK_DEBATE: TurnOptions = {
  debate: { rounds: 0, consensus_threshold: 85, synthesizer: 'claude' },
  use_cache: false,
};
const NO_CONSENSUS = { reached: false, round: 0, scores: {} };
const NO_SAVINGS = { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null };

/** Both answers of a debate without revision rounds, up to the synthesis phase. */
function quickDebateAnswers(): Draft[] {
  return [
    { type: 'turn.started', conversation_id: 8, turn_id: 60, mode: 'debate', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.delta', stream_id: 'c0', section: 'text', text: 'Resposta de Claude' },
    { type: 'stream.delta', stream_id: 'g0', section: 'text', text: 'Resposta de ChatGPT' },
    { type: 'stream.completed', stream_id: 'c0', message_id: 61, usage: usage(10, 5), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
    { type: 'stream.completed', stream_id: 'g0', message_id: 62, usage: usage(10, 5), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
    { type: 'phase', phase: 'synthesis', round: 0 },
  ];
}

const quickDebateCompleted = (finalId: number): Draft => ({
  type: 'turn.completed', conversation_id: 8, turn_id: 60, final_message_ids: [finalId], usage: usage(40, 20),
  savings: NO_SAVINGS, consensus: NO_CONSENSUS, cached: false,
});

/** Claude's synthesis is cut off midway; ChatGPT's (message 63) is the final one. */
export function fallbackSynthesisEvents(requestId = 'req-f'): TurnEvent[] {
  return sequence(requestId, [
    ...quickDebateAnswers(),
    { type: 'stream.started', stream_id: 's1', agent: 'claude', kind: 'synthesis', round: 0, model: 'm' },
    { type: 'stream.delta', stream_id: 's1', section: 'text', text: 'Síntesi a mig' },
    { type: 'stream.failed', stream_id: 's1', error: { kind: 'unavailable', message: 'Connexió tallada.' } },
    { type: 'stream.started', stream_id: 's2', agent: 'chatgpt', kind: 'synthesis', round: 0, model: 'm' },
    { type: 'stream.delta', stream_id: 's2', section: 'text', text: 'Síntesi de ChatGPT' },
    { type: 'stream.completed', stream_id: 's2', message_id: 63, usage: usage(20, 10), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
    quickDebateCompleted(63),
  ]);
}

/** Both syntheses fail: Claude's answer is stored as the final one (message 63). */
export function degradedSynthesisEvents(requestId = 'req-d'): TurnEvent[] {
  return sequence(requestId, [
    ...quickDebateAnswers(),
    { type: 'stream.started', stream_id: 's1', agent: 'claude', kind: 'synthesis', round: 0, model: 'm' },
    { type: 'stream.failed', stream_id: 's1', error: { kind: 'unavailable', message: 'Error de Claude.' } },
    { type: 'stream.started', stream_id: 's2', agent: 'chatgpt', kind: 'synthesis', round: 0, model: 'm' },
    { type: 'stream.failed', stream_id: 's2', error: { kind: 'unavailable', message: 'Error de ChatGPT.' } },
    { type: 'stream.started', stream_id: 's3', agent: 'claude', kind: 'synthesis', round: 0, model: 'm' },
    { type: 'stream.delta', stream_id: 's3', section: 'text', text: 'Resposta de Claude' },
    { type: 'stream.completed', stream_id: 's3', message_id: 63, usage: usage(0, 0), latency_ms: 0, ttft_ms: null, agreement: null, unchanged: false },
    quickDebateCompleted(63),
  ]);
}

/** Claude fails its first answer: ChatGPT's answer becomes the final one at once (message 63). */
export function oneSurvivorDebateEvents(requestId = 'req-o'): TurnEvent[] {
  return sequence(requestId, [
    { type: 'turn.started', conversation_id: 8, turn_id: 60, mode: 'debate', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.failed', stream_id: 'c0', error: { kind: 'timeout', message: 'Temps esgotat.' } },
    { type: 'stream.delta', stream_id: 'g0', section: 'text', text: 'Resposta de ChatGPT' },
    { type: 'stream.completed', stream_id: 'g0', message_id: 62, usage: usage(10, 5), latency_ms: 1, ttft_ms: 1, agreement: null, unchanged: false },
    { type: 'phase', phase: 'synthesis', round: 0 },
    { type: 'stream.started', stream_id: 's1', agent: 'chatgpt', kind: 'synthesis', round: 0, model: 'm' },
    { type: 'stream.delta', stream_id: 's1', section: 'text', text: 'Resposta de ChatGPT' },
    { type: 'stream.completed', stream_id: 's1', message_id: 63, usage: usage(0, 0), latency_ms: 0, ttft_ms: null, agreement: null, unchanged: false },
    quickDebateCompleted(63),
  ]);
}

/** Stored messages of those debates: the question, the answers and the final synthesis (id 63). */
export function quickDebateMessages(synthesis: { agent: Agent; content: string; degraded?: boolean }, answers: Agent[] = ['claude', 'chatgpt']): Message[] {
  const t = 60;
  const answer = (agent: Agent, id: number) =>
    message({ id, turn_id: t, kind: 'answer', agent, content: `Resposta de ${agent === 'claude' ? 'Claude' : 'ChatGPT'}`, meta: { model: 'm', usage: usage(10, 5), latency_ms: 1, ttft_ms: 1 } });
  const called = !synthesis.degraded;
  return [
    message({ id: 60, turn_id: t, kind: 'question', content: 'Pregunta?', final: true, meta: { mode: 'debate', options: QUICK_DEBATE } }),
    ...(answers.includes('claude') ? [answer('claude', 61)] : []),
    ...(answers.includes('chatgpt') ? [answer('chatgpt', 62)] : []),
    message({
      id: 63, turn_id: t, kind: 'synthesis', agent: synthesis.agent, content: synthesis.content, final: true,
      meta: {
        model: 'm', usage: called ? usage(20, 10) : usage(0, 0), latency_ms: called ? 1 : 0, ttft_ms: called ? 1 : null,
        consensus: NO_CONSENSUS, savings: NO_SAVINGS, ...(synthesis.degraded ? { degraded: true } : {}),
      },
    }),
  ];
}

// A one-round debate whose revisions both keep the previous answer, with the
// events the real engine emits (engine.py `_call`): the kept answer is streamed
// again as the revision's answer section and stored as its content. Claude's
// reply is cut off in its critique (max_tokens), before any answer, so it is not
// reported as unchanged; ChatGPT answers UNCHANGED with a note, keeping a first
// answer that had been cut off itself (content filter). Live, Claude's first
// answer ends with a line break the stored content does not keep.
const KEPT_DEBATE: TurnOptions = {
  debate: { rounds: 1, consensus_threshold: 85, synthesizer: 'claude' },
  use_cache: false,
};

type CompletedDraft = Extract<Draft, { type: 'stream.completed' }>;

export function keptRevisionEvents(requestId = 'req-k'): TurnEvent[] {
  const done = (streamId: string, messageId: number, extra: Partial<CompletedDraft> = {}): Draft => ({
    type: 'stream.completed', stream_id: streamId, message_id: messageId, usage: usage(10, 5), latency_ms: 1, ttft_ms: 1,
    agreement: null, unchanged: false, ...extra,
  });
  return sequence(requestId, [
    { type: 'turn.started', conversation_id: 9, turn_id: 80, mode: 'debate', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.delta', stream_id: 'c0', section: 'text', text: 'Hola ' },
    { type: 'stream.delta', stream_id: 'g0', section: 'text', text: 'Bon dia' },
    { type: 'stream.delta', stream_id: 'c0', section: 'text', text: 'món\n' },
    done('c0', 81),
    done('g0', 82, { truncated: true }),
    { type: 'phase', phase: 'revision', round: 1 },
    { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'm' },
    { type: 'stream.started', stream_id: 'g1', agent: 'chatgpt', kind: 'revision', round: 1, model: 'm' },
    { type: 'stream.delta', stream_id: 'c1', section: 'critique', text: '- Falta cont' },
    { type: 'stream.delta', stream_id: 'g1', section: 'critique', text: '- Correcte' },
    { type: 'stream.delta', stream_id: 'c1', section: 'answer', text: 'Hola món' },
    done('c1', 83, { truncated: true }),
    { type: 'stream.delta', stream_id: 'g1', section: 'answer', text: 'Bon dia' },
    done('g1', 84, { agreement: 90, unchanged: true }),
    { type: 'phase', phase: 'synthesis', round: 1 },
    { type: 'stream.started', stream_id: 's', agent: 'claude', kind: 'synthesis', round: 1, model: 'm' },
    { type: 'stream.delta', stream_id: 's', section: 'text', text: 'Síntesi' },
    done('s', 85),
    {
      type: 'turn.completed', conversation_id: 9, turn_id: 80, final_message_ids: [85], usage: usage(50, 25),
      savings: NO_SAVINGS, consensus: { reached: false, round: 1, scores: { chatgpt: 90 } }, cached: false,
    },
  ]);
}

export function keptRevisionMessages(): Message[] {
  const t = 80;
  const meta = (extra: MessageMeta = {}): MessageMeta => ({ model: 'm', usage: usage(10, 5), latency_ms: 1, ttft_ms: 1, ...extra });
  return [
    message({ id: 80, turn_id: t, kind: 'question', content: 'Pregunta?', final: true, meta: { mode: 'debate', options: KEPT_DEBATE } }),
    message({ id: 81, turn_id: t, kind: 'answer', agent: 'claude', content: 'Hola món', meta: meta() }),
    message({
      id: 82, turn_id: t, kind: 'answer', agent: 'chatgpt', content: 'Bon dia',
      meta: meta({ truncated: true, finish_reason: 'content_filter' }),
    }),
    message({
      id: 83, turn_id: t, kind: 'revision', agent: 'claude', round: 1, content: 'Hola món',
      meta: meta({ critique: '- Falta cont', agreement: null, unchanged: false, truncated: true, finish_reason: 'max_tokens' }),
    }),
    message({
      id: 84, turn_id: t, kind: 'revision', agent: 'chatgpt', round: 1, content: 'Bon dia',
      meta: meta({ critique: '- Correcte', agreement: 90, unchanged: true, unchanged_note: 'ja ho cobreix' }),
    }),
    message({
      id: 85, turn_id: t, kind: 'synthesis', agent: 'claude', round: 1, content: 'Síntesi', final: true,
      meta: meta({ consensus: { reached: false, round: 1, scores: { chatgpt: 90 } }, savings: NO_SAVINGS }),
    }),
  ];
}
