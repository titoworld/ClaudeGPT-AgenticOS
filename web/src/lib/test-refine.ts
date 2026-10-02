// Fixtures of a refine turn («Perfecciona», docs/adr/0010-refine-mode.md) for unit
// tests: what the client sees live and what the server stores of the same turn, with the
// wire of docs/PROTOCOL.md. Never imported by application code.
import type {
  Message,
  MessageMeta,
  RefineChange,
  RefineMeta,
  RefineOptions,
  RefineStopReason,
  TurnEvent,
  TurnOptions,
  TurnOutcome,
  Usage,
} from './protocol';
import { message, outcome, priced, sequence } from './test-fixtures';
import { addUsage, emptyUsage } from './turns.svelte';

export const REFINE_OPTIONS: RefineOptions = {
  max_rounds: 6,
  budget_eur: 2,
  max_words: null,
  stop_on_convergence: true,
  convergence_threshold: 90,
  editor: 'claude',
};

export const REFINE_TURN_OPTIONS: TurnOptions = {
  debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' },
  refine: REFINE_OPTIONS,
  use_cache: true,
};

/** Words as the engine counts them (`domain.words`: runs of non-space). */
export const countWords = (text: string): number => text.split(/\s+/).filter(Boolean).length;

export const QUESTION = 'Escriu un pla de llançament per a la beta.';
export const CLAUDE_ANSWER = '## Pla de llançament\n\n1. Beta tancada\n2. Llançament públic';
export const CHATGPT_ANSWER = 'Proposo tres fases: beta, llançament i seguiment.';
export const V1 = '# Pla de llançament\n\n- Fase 1: beta tancada\n- Fase 2: llançament públic\n- Fase 3: seguiment';
export const V2 =
  '# Pla de llançament\n\n- Fase 1: beta tancada\n- Fase 2: llançament públic, abans del 15 de novembre\n- Fase 3: seguiment';
/** Round 3's version, over the word limit, and its shortening, still over it. */
export const V3_LONG = `${V2}\n\n${'mètrica '.repeat(320).trim()}`;
export const V3_SHORT = `${V2}\n\n${'mètrica '.repeat(305).trim()}`;
export const BUDGET_WORDS = 300;
export const OVER_BUDGET = 'La nova versió passava del límit de paraules.';

const MERGE_CHANGES = [
  { kind: 'merge', text: "L'estructura en fases de Claude" },
  { kind: 'merge', text: 'La fase de seguiment de ChatGPT' },
];
const R2_CLAUDE_CHANGES = [{ kind: 'defect', text: 'Fase 2: no té data — sense data no es pot planificar' }];
const E2_CHANGES = [{ kind: 'defect', text: 'La fase 2 té data' }];
const R3_CHATGPT_CHANGES = [{ kind: 'requirement', text: "Fase 3: falten les mètriques que demana l'encàrrec" }];
const E3_CHANGES = [{ kind: 'requirement', text: 'Mètriques de seguiment' }];

// What each call billed (API prices), and so each round and the turn.
const A_CLAUDE = priced(400, 300, 0.0057);
const A_CHATGPT = priced(380, 280, 0.0033);
const MERGE = priced(1200, 350, 0.0089);
const R2_CLAUDE = priced(900, 120, 0.0045);
export const R2_CHATGPT = priced(880, 60, 0.0016);
const E2 = priced(1500, 360, 0.0099);
export const R3_CLAUDE = priced(950, 40, 0.0035);
const R3_CHATGPT = priced(930, 90, 0.0021);
const E3 = priced(1600, 900, 0.0183);
const S3 = priced(1700, 420, 0.0114);
const NO_CALL: Usage = { ...emptyUsage(), cost_usd: 0 };

const sum = (...parts: Usage[]): Usage => parts.reduce(addUsage);
export const ROUND_USAGE = { 1: MERGE, 2: sum(R2_CLAUDE, R2_CHATGPT, E2), 3: sum(R3_CLAUDE, R3_CHATGPT, E3, S3) } as const;
export const ROUND_TOTAL = {
  1: sum(A_CLAUDE, A_CHATGPT, MERGE),
  2: sum(A_CLAUDE, A_CHATGPT, MERGE, ROUND_USAGE[2]),
  3: sum(A_CLAUDE, A_CHATGPT, MERGE, ROUND_USAGE[2], ROUND_USAGE[3]),
} as const;

const NO_SAVINGS = { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null };

const version = (n: number, text: string, accepted: boolean, reason: string | null, changelog = E2_CHANGES): RefineMeta => ({
  role: 'version',
  version: n,
  words: countWords(text),
  budget_words: BUDGET_WORDS,
  accepted,
  reason,
  changelog,
});
const review = (score: number | null, changes: { kind: string; text: string }[]): RefineMeta => ({
  role: 'review',
  score,
  unchanged: changes.length === 0,
  changes,
});
const final = (stopReason: RefineStopReason): RefineMeta => ({
  role: 'final',
  version: 2,
  words: countWords(V2),
  budget_words: BUDGET_WORDS,
  stop_reason: stopReason,
});

type Draft = Parameters<typeof sequence>[1][number];

/** What a reply that was cut off at the output limit says, live and stored. */
const CUT = { truncated: true, finish_reason: 'max_tokens' } as const;

const done = (
  streamId: string,
  messageId: number,
  used: Usage,
  refine?: RefineMeta,
  latency = 900,
  ttft: number | null = 200,
  cut = false,
): Draft => ({
  type: 'stream.completed',
  stream_id: streamId,
  message_id: messageId,
  usage: used,
  latency_ms: latency,
  ttft_ms: ttft,
  agreement: null,
  unchanged: false,
  cost_basis: 'api',
  ...(cut ? CUT : {}),
  ...(refine ? { refine } : {}),
});

/** Round 0 to the end of round 2: two answers, the merge (version 1) and version 2. */
function firstRounds(): Draft[] {
  return [
    { type: 'turn.started', conversation_id: 12, turn_id: 70, mode: 'refine', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-6' },
    { type: 'stream.delta', stream_id: 'c0', section: 'text', text: CLAUDE_ANSWER },
    { type: 'stream.delta', stream_id: 'g0', section: 'text', text: CHATGPT_ANSWER },
    done('c0', 71, A_CLAUDE),
    done('g0', 72, A_CHATGPT),
    { type: 'phase', phase: 'edit', round: 1 },
    { type: 'stream.started', stream_id: 'e1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'e1', section: 'answer', text: V1 },
    { type: 'stream.delta', stream_id: 'e1', section: 'critique', text: "- [merge] L'estructura en fases de Claude" },
    done('e1', 73, MERGE, version(1, V1, true, null, MERGE_CHANGES)),
    {
      type: 'refine.round', round: 1, version: 1, accepted: true, reason: null, words: countWords(V1),
      budget_words: BUDGET_WORDS, changes: MERGE_CHANGES, proposals: { claude: null, chatgpt: null },
      scores: { claude: null, chatgpt: null }, converged: false, usage: ROUND_USAGE[1], total: ROUND_TOTAL[1],
    },
    { type: 'phase', phase: 'review', round: 2 },
    { type: 'stream.started', stream_id: 'r2c', agent: 'claude', kind: 'revision', round: 2, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'r2g', agent: 'chatgpt', kind: 'revision', round: 2, model: 'gpt-6' },
    { type: 'stream.delta', stream_id: 'r2c', section: 'critique', text: '- [defect] Fase 2: no té data' },
    { type: 'stream.delta', stream_id: 'r2g', section: 'critique', text: 'UNCHANGED' },
    done('r2c', 74, R2_CLAUDE, review(70, R2_CLAUDE_CHANGES)),
    done('r2g', 75, R2_CHATGPT, review(85, [])),
    { type: 'phase', phase: 'edit', round: 2 },
    { type: 'stream.started', stream_id: 'e2', agent: 'claude', kind: 'revision', round: 2, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'e2', section: 'answer', text: V2 },
    { type: 'stream.delta', stream_id: 'e2', section: 'critique', text: '- [defect] La fase 2 té data' },
    done('e2', 76, E2, version(2, V2, true, null)),
    {
      type: 'refine.round', round: 2, version: 2, accepted: true, reason: null, words: countWords(V2),
      budget_words: BUDGET_WORDS, changes: E2_CHANGES, proposals: { claude: 1, chatgpt: 0 },
      scores: { claude: 70, chatgpt: 85 }, converged: false, usage: ROUND_USAGE[2], total: ROUND_TOTAL[2],
    },
  ];
}

/** Round 3's reviews, as far as they go before the owner's «Atura ara». */
function thirdRoundReviews(): Draft[] {
  return [
    { type: 'phase', phase: 'review', round: 3 },
    { type: 'stream.started', stream_id: 'r3c', agent: 'claude', kind: 'revision', round: 3, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'r3g', agent: 'chatgpt', kind: 'revision', round: 3, model: 'gpt-6' },
  ];
}

/** The final message: the current version, stored without a call. */
function finalMessage(stopReason: RefineStopReason): Draft[] {
  return [
    { type: 'stream.started', stream_id: 'f', agent: 'claude', kind: 'synthesis', round: 3, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'f', section: 'text', text: V2 },
    done('f', 81, NO_CALL, final(stopReason), 0, null),
  ];
}

/**
 * A refine turn of three rounds: the merge, version 2 (Claude found a defect), and round
 * 3, whose version went over the word limit twice (rejected). The owner asked it to stop
 * during round 3 (turn.stop), so it ends after it with version 2. `upTo`: the events
 * before the one of that type (and round), to see the turn while it runs.
 */
export function refineEvents(requestId = 'req-p', stopReason: RefineStopReason = 'owner'): TurnEvent[] {
  return sequence(requestId, [
    ...firstRounds(),
    ...thirdRoundReviews(),
    { type: 'turn.stopping', round: 3 },
    { type: 'stream.delta', stream_id: 'r3c', section: 'critique', text: 'UNCHANGED' },
    { type: 'stream.delta', stream_id: 'r3g', section: 'critique', text: '- [requirement] Fase 3: falten les mètriques' },
    done('r3c', 77, R3_CLAUDE, review(92, [])),
    done('r3g', 78, R3_CHATGPT, review(88, R3_CHATGPT_CHANGES)),
    { type: 'phase', phase: 'edit', round: 3 },
    { type: 'stream.started', stream_id: 'e3', agent: 'claude', kind: 'revision', round: 3, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'e3', section: 'answer', text: V3_LONG },
    done('e3', 79, E3, version(3, V3_LONG, false, OVER_BUDGET, E3_CHANGES)),
    { type: 'stream.started', stream_id: 's3', agent: 'claude', kind: 'revision', round: 3, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 's3', section: 'answer', text: V3_SHORT },
    done('s3', 80, S3, version(3, V3_SHORT, false, OVER_BUDGET, E3_CHANGES)),
    {
      type: 'refine.round', round: 3, version: 2, accepted: false, reason: OVER_BUDGET, words: countWords(V2),
      budget_words: BUDGET_WORDS, changes: [], proposals: { claude: 0, chatgpt: 1 },
      scores: { claude: 92, chatgpt: 88 }, converged: false, usage: ROUND_USAGE[3], total: ROUND_TOTAL[3],
    },
    ...finalMessage(stopReason),
    {
      type: 'turn.completed', conversation_id: 12, turn_id: 70, final_message_ids: [81], usage: ROUND_TOTAL[3],
      savings: NO_SAVINGS, consensus: null, cached: false, stop_reason: stopReason,
    },
  ]);
}

/** The events of `refineEvents` before the first one that `stop` matches. */
export function refineEventsUntil(stop: (ev: TurnEvent) => boolean, requestId = 'req-p'): TurnEvent[] {
  const events = refineEvents(requestId);
  const end = events.findIndex(stop);
  return end < 0 ? events : events.slice(0, end);
}

/**
 * The same turn stopped with «Atura ara» during round 3's reviews: version 2 is kept as final.
 * `streamed` false: as the server sends it, which stores that final message without
 * streaming it (the engine writes it while the turn's events have stopped).
 */
export function cancelledRefineEvents(requestId = 'req-q', streamed = true): TurnEvent[] {
  return sequence(requestId, [
    ...firstRounds(),
    ...thirdRoundReviews(),
    { type: 'stream.delta', stream_id: 'r3c', section: 'critique', text: '- [clarity] Fase' },
    ...(streamed ? finalMessage('owner') : []),
    { type: 'turn.cancelled', usage: ROUND_TOTAL[2] },
  ]);
}

const meta = (used: Usage, refine?: RefineMeta, extra: MessageMeta = {}): MessageMeta => ({
  model: 'claude-sonnet',
  usage: used,
  latency_ms: 900,
  ttft_ms: 200,
  cost_basis: 'api',
  ...(refine ? { refine } : {}),
  ...extra,
});

/** The stored messages of `refineEvents` (the turn that ended after round 3). */
export function refineMessages(stopReason: RefineStopReason = 'owner'): Message[] {
  const t = 70;
  return [
    message({
      id: 70, turn_id: t, kind: 'question', content: QUESTION, final: true,
      meta: {
        mode: 'refine', target: 'claude', options: { debate: REFINE_TURN_OPTIONS.debate, use_cache: true },
        refine: REFINE_OPTIONS,
        outcome: outcome({ status: 'completed', usage: ROUND_TOTAL[3], final_message_ids: [81], stop_reason: stopReason }),
      },
    }),
    ...storedFirstRounds(),
    message({ id: 77, turn_id: t, kind: 'revision', agent: 'claude', round: 3, content: 'UNCHANGED', meta: meta(R3_CLAUDE, review(92, [])) }),
    message({
      id: 78, turn_id: t, kind: 'revision', agent: 'chatgpt', round: 3, content: '- [requirement] Fase 3: falten les mètriques',
      meta: { ...meta(R3_CHATGPT, review(88, R3_CHATGPT_CHANGES)), model: 'gpt-6' },
    }),
    message({ id: 79, turn_id: t, kind: 'revision', agent: 'claude', round: 3, content: V3_LONG, meta: meta(E3, version(3, V3_LONG, false, OVER_BUDGET, E3_CHANGES)) }),
    message({ id: 80, turn_id: t, kind: 'revision', agent: 'claude', round: 3, content: V3_SHORT, meta: meta(S3, version(3, V3_SHORT, false, OVER_BUDGET, E3_CHANGES)) }),
    storedFinal(3, stopReason),
  ];
}

/** The stored messages of `cancelledRefineEvents`: round 3's reviews were never stored. */
export function cancelledRefineMessages(): Message[] {
  return [
    message({
      id: 70, turn_id: 70, kind: 'question', content: QUESTION, final: true,
      meta: {
        mode: 'refine', target: 'claude', options: { debate: REFINE_TURN_OPTIONS.debate, use_cache: true },
        refine: REFINE_OPTIONS,
        outcome: outcome({ status: 'cancelled', usage: ROUND_TOTAL[2], final_message_ids: [81], stop_reason: 'owner' }),
      },
    }),
    ...storedFirstRounds(),
    storedFinal(3, 'owner'),
  ];
}

function storedFirstRounds(): Message[] {
  const t = 70;
  return [
    message({ id: 71, turn_id: t, kind: 'answer', agent: 'claude', content: CLAUDE_ANSWER, meta: meta(A_CLAUDE) }),
    message({ id: 72, turn_id: t, kind: 'answer', agent: 'chatgpt', content: CHATGPT_ANSWER, meta: { ...meta(A_CHATGPT), model: 'gpt-6' } }),
    message({ id: 73, turn_id: t, kind: 'revision', agent: 'claude', round: 1, content: V1, meta: meta(MERGE, version(1, V1, true, null, MERGE_CHANGES)) }),
    message({ id: 74, turn_id: t, kind: 'revision', agent: 'claude', round: 2, content: '- [defect] Fase 2: no té data', meta: meta(R2_CLAUDE, review(70, R2_CLAUDE_CHANGES)) }),
    message({ id: 75, turn_id: t, kind: 'revision', agent: 'chatgpt', round: 2, content: 'UNCHANGED', meta: { ...meta(R2_CHATGPT, review(85, [])), model: 'gpt-6' } }),
    message({ id: 76, turn_id: t, kind: 'revision', agent: 'claude', round: 2, content: V2, meta: meta(E2, version(2, V2, true, null)) }),
  ];
}

function storedFinal(round: number, stopReason: RefineStopReason): Message {
  return message({
    id: 81, turn_id: 70, kind: 'synthesis', agent: 'claude', round, content: V2, final: true,
    meta: { ...meta(NO_CALL, final(stopReason)), latency_ms: 0, ttft_ms: null, copied_from: 76, savings: NO_SAVINGS },
  });
}

// ---------------------------------------------------------------------------------------
// Turns that end otherwise: live, as the server sends them, and stored, as the engine
// stores them (orchestrator/engine.py, the P8 review).

/** Why a version or a review did not count (engine.py REFINE_*). */
export const INCOMPLETE = "L'editor no ha escrit cap versió completa.";
export const NO_CHANGES = 'La revisió no té la llista de canvis que se li demanava.';
export const INTERNAL_ERROR = { kind: 'internal', message: "S'ha produït un error intern i el torn s'ha aturat." };
const down = (agent: 'claude' | 'chatgpt') => ({ kind: 'unavailable', message: `${agent === 'claude' ? 'Claude' : 'ChatGPT'} no respon.` });

/** A merge cut off at the output limit: no complete version. */
export const V1_CUT = '# Pla de llançament\n\n- Fase 1: beta tancada\n- Fase 2: llan';
const MERGE_CHATGPT = priced(1180, 340, 0.0051);
const ANSWERS = sum(A_CLAUDE, A_CHATGPT);
/** A billed attempt another model declined before the call was served (ADR 0008). */
export const DECLINED = priced(800, 0, 0.004);

const finalOf = (version: number, text: string, stopReason: RefineStopReason): RefineMeta => ({
  role: 'final',
  version,
  words: countWords(text),
  budget_words: BUDGET_WORDS,
  stop_reason: stopReason,
});

/** What a message stored without a call says it billed (the engine's empty usage, without a price). */
const UNPRICED: Usage = emptyUsage();

/** Version 1 stored without a call: a copy of Claude's answer (message 71). */
const COPY: RefineMeta = {
  role: 'version', version: 1, words: countWords(CLAUDE_ANSWER), budget_words: BUDGET_WORDS, accepted: true, reason: null,
  changelog: [], copied_from: 71,
};

/** The end of round 1, the merge (no reviews). */
const roundEnd = (round: number, version: number, text: string, usage: Usage, total: Usage, changes: RefineChange[] = []): Draft => ({
  type: 'refine.round', round, version, accepted: true, reason: null, words: countWords(text),
  budget_words: BUDGET_WORDS, changes, proposals: { claude: null, chatgpt: null },
  scores: { claude: null, chatgpt: null }, converged: false, usage, total,
});

/** Round 0 (both answers; `cut`: both cut off at the output limit), and the merge starts. */
function answersThenMerge(cut = false): Draft[] {
  return [
    { type: 'turn.started', conversation_id: 12, turn_id: 70, mode: 'refine', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-6' },
    { type: 'stream.delta', stream_id: 'c0', section: 'text', text: CLAUDE_ANSWER },
    { type: 'stream.delta', stream_id: 'g0', section: 'text', text: CHATGPT_ANSWER },
    done('c0', 71, A_CLAUDE, undefined, 900, 200, cut),
    done('g0', 72, A_CHATGPT, undefined, 900, 200, cut),
    { type: 'phase', phase: 'edit', round: 1 },
  ];
}

/** The question of a stored refine turn, with how it ended. */
const storedQuestion = (result: TurnOutcome | null): Message =>
  message({
    id: 70, turn_id: 70, kind: 'question', content: QUESTION, final: true,
    meta: {
      mode: 'refine', target: 'claude', options: { debate: REFINE_TURN_OPTIONS.debate, use_cache: true },
      refine: REFINE_OPTIONS, outcome: result,
    },
  });

const storedAnswers = (cut = false): Message[] => [
  message({ id: 71, turn_id: 70, kind: 'answer', agent: 'claude', content: CLAUDE_ANSWER, meta: meta(A_CLAUDE, undefined, cut ? CUT : {}) }),
  message({
    id: 72, turn_id: 70, kind: 'answer', agent: 'chatgpt', content: CHATGPT_ANSWER,
    meta: { ...meta(A_CHATGPT, undefined, cut ? CUT : {}), model: 'gpt-6' },
  }),
];

/** A final message: the version stored again as the turn's answer, without a call. */
const storedFinalOf = (
  id: number, agent: 'claude' | 'chatgpt', round: number, text: string, refine: RefineMeta, copiedFrom: number, extra: MessageMeta = {},
): Message =>
  message({
    id, turn_id: 70, kind: 'synthesis', agent, round, content: text, final: true,
    meta: { ...meta(UNPRICED, refine, extra), latency_ms: 0, ttft_ms: null, copied_from: copiedFrom, savings: NO_SAVINGS },
  });

/** «Atura ara» while Claude merges the answers: there is no version yet, so it keeps none. */
export function mergeCancelledEvents(requestId = 'req-m'): TurnEvent[] {
  return sequence(requestId, [
    ...answersThenMerge(),
    { type: 'stream.started', stream_id: 'e1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'e1', section: 'answer', text: '# Pla de llançament\n\n- Fase 1' },
    { type: 'turn.cancelled', usage: ANSWERS },
  ]);
}

/** The stored messages of `mergeCancelledEvents`: the merge was never stored, nor any final message. */
export function mergeCancelledMessages(): Message[] {
  return [storedQuestion(outcome({ status: 'cancelled', usage: ANSWERS })), ...storedAnswers()];
}

/**
 * Claude's merge passes the owner's word limit, so Claude shortens it in round 1 (the
 * shortening is version 1), and «Atura ara» comes in round 2.
 */
export function shortenedMergeEvents(requestId = 'req-s'): TurnEvent[] {
  return sequence(requestId, [
    ...answersThenMerge(),
    { type: 'stream.started', stream_id: 'e1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'e1', section: 'answer', text: V3_LONG },
    done('e1', 73, MERGE, version(1, V3_LONG, false, OVER_BUDGET, [])),
    { type: 'stream.started', stream_id: 's1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 's1', section: 'answer', text: V1 },
    done('s1', 74, MERGE, version(1, V1, true, null, [])),
    { type: 'turn.cancelled', usage: sum(ANSWERS, MERGE, MERGE) },
  ]);
}

/** Claude's merge is cut off (not accepted), and «Atura ara» comes while ChatGPT merges: no version is kept. */
export function rejectedMergeCancelledEvents(requestId = 'req-r'): TurnEvent[] {
  return sequence(requestId, [
    ...answersThenMerge(),
    { type: 'stream.started', stream_id: 'e1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'e1', section: 'answer', text: V1_CUT },
    done('e1', 73, MERGE, version(1, V1_CUT, false, INCOMPLETE, []), 900, 200, true),
    { type: 'stream.started', stream_id: 'g1', agent: 'chatgpt', kind: 'revision', round: 1, model: 'gpt-6' },
    { type: 'stream.delta', stream_id: 'g1', section: 'answer', text: '# Pla' },
    { type: 'turn.cancelled', usage: sum(ANSWERS, MERGE) },
  ]);
}

export function rejectedMergeCancelledMessages(): Message[] {
  return [
    storedQuestion(outcome({ status: 'cancelled', usage: sum(ANSWERS, MERGE) })),
    ...storedAnswers(),
    message({
      id: 73, turn_id: 70, kind: 'revision', agent: 'claude', round: 1, content: V1_CUT,
      meta: meta(MERGE, version(1, V1_CUT, false, INCOMPLETE, []), CUT),
    }),
  ];
}

/**
 * Every answer and merge cut off at the output limit: version 1 copies Claude's cut-off
 * answer (no call), and, as the owner asked during the merge, the turn ends after round 1
 * with that copy as its answer: the final message keeps the mark.
 */
export function truncatedCopyEvents(requestId = 'req-t'): TurnEvent[] {
  const merges = sum(MERGE, MERGE_CHATGPT);
  return sequence(requestId, [
    ...answersThenMerge(true),
    { type: 'stream.started', stream_id: 'e1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'e1', section: 'answer', text: V1_CUT },
    done('e1', 73, MERGE, version(1, V1_CUT, false, INCOMPLETE, []), 900, 200, true),
    { type: 'turn.stopping', round: 1 },
    { type: 'stream.started', stream_id: 'g1', agent: 'chatgpt', kind: 'revision', round: 1, model: 'gpt-6' },
    { type: 'stream.delta', stream_id: 'g1', section: 'answer', text: V1_CUT },
    done('g1', 74, MERGE_CHATGPT, version(1, V1_CUT, false, INCOMPLETE, []), 900, 200, true),
    { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'c1', section: 'answer', text: CLAUDE_ANSWER },
    done('c1', 75, UNPRICED, COPY, 0, null, true),
    roundEnd(1, 1, CLAUDE_ANSWER, merges, sum(ANSWERS, merges)),
    { type: 'stream.started', stream_id: 'f', agent: 'claude', kind: 'synthesis', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'f', section: 'text', text: CLAUDE_ANSWER },
    done('f', 76, UNPRICED, finalOf(1, CLAUDE_ANSWER, 'owner'), 0, null, true),
    {
      type: 'turn.completed', conversation_id: 12, turn_id: 70, final_message_ids: [76], usage: sum(ANSWERS, merges),
      savings: NO_SAVINGS, consensus: null, cached: false, stop_reason: 'owner',
    },
  ]);
}

export function truncatedCopyMessages(): Message[] {
  const merges = sum(MERGE, MERGE_CHATGPT);
  return [
    storedQuestion(outcome({ status: 'completed', usage: sum(ANSWERS, merges), final_message_ids: [76], stop_reason: 'owner' })),
    ...storedAnswers(true),
    message({
      id: 73, turn_id: 70, kind: 'revision', agent: 'claude', round: 1, content: V1_CUT,
      meta: meta(MERGE, version(1, V1_CUT, false, INCOMPLETE, []), CUT),
    }),
    message({
      id: 74, turn_id: 70, kind: 'revision', agent: 'chatgpt', round: 1, content: V1_CUT,
      meta: { ...meta(MERGE_CHATGPT, version(1, V1_CUT, false, INCOMPLETE, []), CUT), model: 'gpt-6' },
    }),
    message({
      id: 75, turn_id: 70, kind: 'revision', agent: 'claude', round: 1, content: CLAUDE_ANSWER,
      meta: { ...meta(UNPRICED, COPY, CUT), latency_ms: 0, ttft_ms: null },
    }),
    storedFinalOf(76, 'claude', 1, CLAUDE_ANSWER, finalOf(1, CLAUDE_ANSWER, 'owner'), 75, CUT),
  ];
}

/** `refineEvents`, but ChatGPT's review of round 2 came back without its list of changes: a billed failure. */
export function failedReviewEvents(requestId = 'req-p'): TurnEvent[] {
  return refineEvents(requestId).map((e): TurnEvent => {
    if (e.type === 'stream.completed' && e.stream_id === 'r2g') {
      return {
        type: 'stream.failed', request_id: e.request_id, seq: e.seq, stream_id: 'r2g',
        error: { kind: 'invalid', message: NO_CHANGES }, usage: R2_CHATGPT,
      };
    }
    if (e.type === 'refine.round' && e.round === 2) {
      return { ...e, proposals: { claude: 1, chatgpt: null }, scores: { claude: 70, chatgpt: null } };
    }
    return e;
  });
}

/** The stored messages of `failedReviewEvents`: the failure stored no message, only its place in the outcome. */
export function failedReviewMessages(): Message[] {
  return refineMessages()
    .filter((m) => m.id !== 75)
    .map((m) => {
      if (m.kind === 'question') {
        const failures = [{ agent: 'chatgpt' as const, kind: 'invalid', message: NO_CHANGES, round: 2 }];
        return { ...m, meta: { ...m.meta, outcome: outcome({ status: 'completed', usage: ROUND_TOTAL[3], final_message_ids: [81], stop_reason: 'owner', failures }) } };
      }
      return m.id === 81 ? { ...m, meta: { ...m.meta, unstored_usage: R2_CHATGPT } } : m;
    });
}

/** `refineEvents`, but round 3's edit was served after another model declined it: a billed attempt of its own. */
export function declinedEditEvents(requestId = 'req-p'): TurnEvent[] {
  const total = sum(ROUND_TOTAL[3], DECLINED);
  return refineEvents(requestId).map((e): TurnEvent => {
    if (e.type === 'refine.round' && e.round === 3) return { ...e, usage: sum(ROUND_USAGE[3], DECLINED), total };
    if (e.type === 'turn.completed') return { ...e, usage: total };
    return e;
  });
}

/** The stored messages of `declinedEditEvents`: the attempt is on the message the edit stored. */
export function declinedEditMessages(): Message[] {
  const total = sum(ROUND_TOTAL[3], DECLINED);
  return refineMessages().map((m) => {
    if (m.kind === 'question') {
      return { ...m, meta: { ...m.meta, outcome: outcome({ status: 'completed', usage: total, final_message_ids: [81], stop_reason: 'owner' }) } };
    }
    if (m.id === 79) return { ...m, meta: { ...m.meta, declined: [{ model: 'claude-opus', usage: DECLINED }] } };
    return m.id === 81 ? { ...m, meta: { ...m.meta, unstored_usage: DECLINED } } : m;
  });
}

/** Round 1 of `refineEvents`, up to its end. */
const roundOne = (): Draft[] => {
  const drafts = firstRounds();
  return drafts.slice(0, drafts.findIndex((d) => d.type === 'phase' && d.phase === 'review'));
};

/** «Atura ara» while ChatGPT reviews version 1, once Claude found nothing to change: round 2 never ended. */
export function reviewCancelledEvents(requestId = 'req-v'): TurnEvent[] {
  return sequence(requestId, [
    ...roundOne(),
    { type: 'phase', phase: 'review', round: 2 },
    { type: 'stream.started', stream_id: 'r2c', agent: 'claude', kind: 'revision', round: 2, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'r2g', agent: 'chatgpt', kind: 'revision', round: 2, model: 'gpt-6' },
    { type: 'stream.delta', stream_id: 'r2c', section: 'critique', text: 'UNCHANGED' },
    done('r2c', 74, R2_CLAUDE, review(95, [])),
    { type: 'stream.delta', stream_id: 'r2g', section: 'critique', text: '- [clarity] Fa' },
    { type: 'turn.cancelled', usage: sum(ROUND_TOTAL[1], R2_CLAUDE) },
  ]);
}

export function reviewCancelledMessages(): Message[] {
  return [
    storedQuestion(outcome({ status: 'cancelled', usage: sum(ROUND_TOTAL[1], R2_CLAUDE), final_message_ids: [75], stop_reason: 'owner' })),
    ...storedFirstRounds().filter((m) => m.round <= 1),
    message({ id: 74, turn_id: 70, kind: 'revision', agent: 'claude', round: 2, content: 'UNCHANGED', meta: meta(R2_CLAUDE, review(95, [])) }),
    storedFinalOf(75, 'claude', 2, V1, finalOf(1, V1, 'owner'), 73),
  ];
}

/** «Atura ara» while Claude shortens round 3's version, which was over the word limit: round 3 never ended. */
export function shortenCancelledEvents(requestId = 'req-s'): TurnEvent[] {
  const events = refineEventsUntil((e) => e.type === 'stream.completed' && e.stream_id === 's3', requestId);
  const usage = sum(ROUND_TOTAL[2], R3_CLAUDE, R3_CHATGPT, E3);
  return [...events, { type: 'turn.cancelled', request_id: requestId, seq: events.length + 1, usage }];
}

/** The stored messages of `shortenCancelledEvents`: the over-budget version, and no shortening. */
export function shortenCancelledMessages(): Message[] {
  const usage = sum(ROUND_TOTAL[2], R3_CLAUDE, R3_CHATGPT, E3);
  return refineMessages()
    .filter((m) => m.id !== 80)
    .map((m) =>
      m.kind === 'question'
        ? { ...m, meta: { ...m.meta, outcome: outcome({ status: 'cancelled', usage, final_message_ids: [81], stop_reason: 'owner' }) } }
        : m,
    );
}

/**
 * Claude cannot merge, so ChatGPT writes version 1; in round 2 ChatGPT edits first (Claude's
 * edit failed before) and cannot either, so Claude writes version 2. The owner stops it then.
 */
export function editorFallbackEvents(requestId = 'req-e'): TurnEvent[] {
  return sequence(requestId, [
    ...answersThenMerge(),
    { type: 'stream.started', stream_id: 'e1c', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.failed', stream_id: 'e1c', error: down('claude') },
    { type: 'stream.started', stream_id: 'e1g', agent: 'chatgpt', kind: 'revision', round: 1, model: 'gpt-6' },
    { type: 'stream.delta', stream_id: 'e1g', section: 'answer', text: V1 },
    done('e1g', 73, MERGE, version(1, V1, true, null, MERGE_CHANGES)),
    roundEnd(1, 1, V1, MERGE, ROUND_TOTAL[1], MERGE_CHANGES),
    { type: 'phase', phase: 'review', round: 2 },
    { type: 'stream.started', stream_id: 'r2c', agent: 'claude', kind: 'revision', round: 2, model: 'claude-sonnet' },
    { type: 'stream.started', stream_id: 'r2g', agent: 'chatgpt', kind: 'revision', round: 2, model: 'gpt-6' },
    done('r2c', 74, R2_CLAUDE, review(70, R2_CLAUDE_CHANGES)),
    done('r2g', 75, R2_CHATGPT, review(85, [])),
    { type: 'turn.stopping', round: 2 },
    { type: 'phase', phase: 'edit', round: 2 },
    { type: 'stream.started', stream_id: 'e2g', agent: 'chatgpt', kind: 'revision', round: 2, model: 'gpt-6' },
    { type: 'stream.failed', stream_id: 'e2g', error: down('chatgpt') },
    { type: 'stream.started', stream_id: 'e2c', agent: 'claude', kind: 'revision', round: 2, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'e2c', section: 'answer', text: V2 },
    done('e2c', 76, E2, version(2, V2, true, null)),
    {
      type: 'refine.round', round: 2, version: 2, accepted: true, reason: null, words: countWords(V2),
      budget_words: BUDGET_WORDS, changes: E2_CHANGES, proposals: { claude: 1, chatgpt: 0 },
      scores: { claude: 70, chatgpt: 85 }, converged: false, usage: ROUND_USAGE[2], total: ROUND_TOTAL[2],
    },
    { type: 'stream.started', stream_id: 'f', agent: 'claude', kind: 'synthesis', round: 2, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'f', section: 'text', text: V2 },
    done('f', 77, UNPRICED, final('owner'), 0, null),
    {
      type: 'turn.completed', conversation_id: 12, turn_id: 70, final_message_ids: [77], usage: ROUND_TOTAL[2],
      savings: NO_SAVINGS, consensus: null, cached: false, stop_reason: 'owner',
    },
  ]);
}

export function editorFallbackMessages(): Message[] {
  const failures = [
    { agent: 'claude' as const, ...down('claude'), round: 1 },
    { agent: 'chatgpt' as const, ...down('chatgpt'), round: 2 },
  ];
  return [
    storedQuestion(outcome({ status: 'completed', usage: ROUND_TOTAL[2], final_message_ids: [77], stop_reason: 'owner', failures })),
    ...storedAnswers(),
    message({
      id: 73, turn_id: 70, kind: 'revision', agent: 'chatgpt', round: 1, content: V1,
      meta: { ...meta(MERGE, version(1, V1, true, null, MERGE_CHANGES)), model: 'gpt-6' },
    }),
    ...storedFirstRounds().filter((m) => m.id === 74 || m.id === 75 || m.id === 76),
    storedFinalOf(77, 'claude', 2, V2, final('owner'), 76),
  ];
}

/** Neither can merge the answers: version 1 is a copy of Claude's (no call), and the owner stops it then. */
export function copiedMergeEvents(requestId = 'req-k'): TurnEvent[] {
  return sequence(requestId, [
    ...answersThenMerge(),
    { type: 'turn.stopping', round: 1 },
    { type: 'stream.started', stream_id: 'e1c', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.failed', stream_id: 'e1c', error: down('claude') },
    { type: 'stream.started', stream_id: 'e1g', agent: 'chatgpt', kind: 'revision', round: 1, model: 'gpt-6' },
    { type: 'stream.failed', stream_id: 'e1g', error: down('chatgpt') },
    { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'c1', section: 'answer', text: CLAUDE_ANSWER },
    done('c1', 73, UNPRICED, COPY, 0, null),
    // The round's usage is the difference of the turn's running totals: zero, priced.
    roundEnd(1, 1, CLAUDE_ANSWER, NO_CALL, sum(ANSWERS, NO_CALL)),
    { type: 'stream.started', stream_id: 'f', agent: 'claude', kind: 'synthesis', round: 1, model: 'claude-sonnet' },
    { type: 'stream.delta', stream_id: 'f', section: 'text', text: CLAUDE_ANSWER },
    done('f', 74, UNPRICED, finalOf(1, CLAUDE_ANSWER, 'owner'), 0, null),
    {
      type: 'turn.completed', conversation_id: 12, turn_id: 70, final_message_ids: [74], usage: ANSWERS,
      savings: NO_SAVINGS, consensus: null, cached: false, stop_reason: 'owner',
    },
  ]);
}

export function copiedMergeMessages(): Message[] {
  const failures = [
    { agent: 'claude' as const, ...down('claude'), round: 1 },
    { agent: 'chatgpt' as const, ...down('chatgpt'), round: 1 },
  ];
  return [
    storedQuestion(outcome({ status: 'completed', usage: ANSWERS, final_message_ids: [74], stop_reason: 'owner', failures })),
    ...storedAnswers(),
    message({
      id: 73, turn_id: 70, kind: 'revision', agent: 'claude', round: 1, content: CLAUDE_ANSWER,
      meta: { ...meta(UNPRICED, COPY), latency_ms: 0, ttft_ms: null },
    }),
    storedFinalOf(74, 'claude', 1, CLAUDE_ANSWER, finalOf(1, CLAUDE_ANSWER, 'owner'), 73),
  ];
}

/**
 * Round 3's shortening fails, so round 3 ends without a new version (over the word limit),
 * and «Atura ara» comes as round 4 starts: three rounds ended.
 */
export function shortenFailedEvents(requestId = 'req-f'): TurnEvent[] {
  const before = refineEventsUntil((e) => e.type === 'stream.completed' && e.stream_id === 's3', requestId);
  const round3 = sum(R3_CLAUDE, R3_CHATGPT, E3);
  const total = sum(ROUND_TOTAL[2], round3);
  let seq = before.length;
  const next = (draft: Draft): TurnEvent => ({ ...draft, request_id: requestId, seq: ++seq }) as TurnEvent;
  return [
    ...before,
    next({ type: 'stream.failed', stream_id: 's3', error: down('claude') }),
    next({
      type: 'refine.round', round: 3, version: 2, accepted: false, reason: OVER_BUDGET, words: countWords(V2),
      budget_words: BUDGET_WORDS, changes: [], proposals: { claude: 0, chatgpt: 1 }, scores: { claude: 92, chatgpt: 88 },
      converged: false, usage: round3, total,
    }),
    next({ type: 'phase', phase: 'review', round: 4 }),
    next({ type: 'stream.started', stream_id: 'r4c', agent: 'claude', kind: 'revision', round: 4, model: 'claude-sonnet' }),
    next({ type: 'turn.cancelled', usage: total }),
  ];
}

export function shortenFailedMessages(): Message[] {
  const total = sum(ROUND_TOTAL[2], R3_CLAUDE, R3_CHATGPT, E3);
  const failures = [{ agent: 'claude' as const, ...down('claude'), round: 3 }];
  return refineMessages()
    .filter((m) => m.id !== 80)
    .map((m) => {
      if (m.kind === 'question') {
        return { ...m, meta: { ...m.meta, outcome: outcome({ status: 'cancelled', usage: total, final_message_ids: [81], stop_reason: 'owner', failures }) } };
      }
      return m.id === 81 ? { ...m, round: 4 } : m;
    });
}

/** What ChatGPT's refusal to answer billed, and Claude's check of the question's PDF for it. */
export const REFUSAL = priced(900, 40, 0.0029);
export const PDF_CHECK = priced(2000, 150, 0.0081);

/**
 * ChatGPT refuses to answer (a billed failure) after Claude checked the question's PDF for
 * it, so Claude goes on alone: it merges its own answer into version 1, and the owner stops
 * it then. The outcome's total has both calls no message says.
 */
export function refusedAnswerMessages(): Message[] {
  const usage = sum(A_CLAUDE, PDF_CHECK, REFUSAL, MERGE);
  const failures = [{ agent: 'chatgpt' as const, kind: 'invalid', message: 'ChatGPT ha declinat la petició.', round: 0 }];
  return [
    storedQuestion(outcome({ status: 'completed', usage, final_message_ids: [74], stop_reason: 'owner', failures })),
    storedAnswers()[0]!,
    message({ id: 73, turn_id: 70, kind: 'revision', agent: 'claude', round: 1, content: V1, meta: meta(MERGE, version(1, V1, true, null, MERGE_CHANGES)) }),
    storedFinalOf(74, 'claude', 1, V1, finalOf(1, V1, 'owner'), 73, { unstored_usage: REFUSAL }),
  ];
}
