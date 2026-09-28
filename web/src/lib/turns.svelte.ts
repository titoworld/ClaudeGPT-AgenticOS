// Turn view model: one shape for live turns (built from WebSocket events) and
// stored turns (mapped from REST messages), so both render identically.
//
// `applyTurnEvent` is deterministic and has no side effects beyond the turn it
// receives. It mutates that turn in place on purpose: inside a Svelte $state
// proxy, `stream.text += delta` only re-renders the text that changed, instead
// of rebuilding the whole conversation on every token.

import type {
  Agent,
  Consensus,
  ErrorInfo,
  Message,
  MessageKind,
  Phase,
  Savings,
  TurnEvent,
  TurnMode,
  TurnOptions,
  Usage,
} from './protocol';

export type StreamKind = Exclude<MessageKind, 'question'>;
export type StreamStatus = 'streaming' | 'done' | 'failed' | 'interrupted';
export type TurnStatus = 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
/** "api": real API cost; "equivalent": subscription call valued at API prices. */
export type CostBasis = 'api' | 'equivalent';

export interface StreamView {
  id: string;
  agent: Agent;
  kind: StreamKind;
  round: number;
  model: string;
  /** Answer text (the `answer` section for revisions). */
  text: string;
  /** Revision critique (the `critique` section). */
  critique: string;
  status: StreamStatus;
  error: ErrorInfo | null;
  messageId: number | null;
  usage: Usage | null;
  latencyMs: number | null;
  ttftMs: number | null;
  agreement: number | null;
  unchanged: boolean;
  cached: boolean;
  /** Meaning of `usage.cost_usd` (null when unknown). */
  costBasis: CostBasis | null;
}

export interface TurnView {
  /** Stable key for keyed each blocks: request id (live) or `t<turn_id>` (stored). */
  key: string;
  requestId: string | null;
  conversationId: number | null;
  turnId: number | null;
  mode: TurnMode;
  target: Agent | null;
  options: TurnOptions | null;
  question: string;
  createdAt: string;
  status: TurnStatus;
  phase: Phase | null;
  round: number;
  streams: StreamView[];
  lastSeq: number;
  usage: Usage | null;
  savings: Savings | null;
  consensus: Consensus | null;
  cached: boolean;
  error: ErrorInfo | null;
  finalMessageIds: number[];
  /** True when built from WebSocket events in this session. */
  live: boolean;
}

export const DEFAULT_CONSENSUS_THRESHOLD = 85;

export function emptyUsage(): Usage {
  return {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    reasoning_tokens: 0,
    cost_usd: null,
  };
}

export function addUsage(a: Usage, b: Usage): Usage {
  return {
    input_tokens: a.input_tokens + b.input_tokens,
    output_tokens: a.output_tokens + b.output_tokens,
    cache_read_tokens: a.cache_read_tokens + b.cache_read_tokens,
    cache_write_tokens: a.cache_write_tokens + b.cache_write_tokens,
    reasoning_tokens: a.reasoning_tokens + b.reasoning_tokens,
    cost_usd: a.cost_usd == null && b.cost_usd == null ? null : (a.cost_usd ?? 0) + (b.cost_usd ?? 0),
  };
}

export interface NewTurnInput {
  requestId: string;
  question: string;
  mode: TurnMode;
  target?: Agent | null;
  options?: TurnOptions | null;
  conversationId?: number | null;
  createdAt?: string;
}

/** A turn the client just sent (or is resubscribing to), before any event. */
export function createLiveTurn(input: NewTurnInput): TurnView {
  return {
    key: input.requestId,
    requestId: input.requestId,
    conversationId: input.conversationId ?? null,
    turnId: null,
    mode: input.mode,
    target: input.target ?? null,
    options: input.options ?? null,
    question: input.question,
    createdAt: input.createdAt ?? new Date().toISOString(),
    status: 'pending',
    phase: null,
    round: 0,
    streams: [],
    lastSeq: 0,
    usage: null,
    savings: null,
    consensus: null,
    cached: false,
    error: null,
    finalMessageIds: [],
    live: true,
  };
}

function newStream(id: string, agent: Agent, kind: StreamKind, round: number, model: string): StreamView {
  return {
    id,
    agent,
    kind,
    round,
    model,
    text: '',
    critique: '',
    status: 'streaming',
    error: null,
    messageId: null,
    usage: null,
    latencyMs: null,
    ttftMs: null,
    agreement: null,
    unchanged: false,
    cached: false,
    costBasis: null,
  };
}

export const isTerminal = (status: TurnStatus): boolean =>
  status === 'done' || status === 'failed' || status === 'cancelled';

function stopOpenStreams(turn: TurnView, to: StreamStatus): void {
  for (const s of turn.streams) if (s.status === 'streaming') s.status = to;
}

/**
 * Apply one server event to a turn. Idempotent by `seq`: duplicates and older
 * events (replays after a reconnect) are ignored. Returns true if applied.
 */
export function applyTurnEvent(turn: TurnView, ev: TurnEvent): boolean {
  if (ev.request_id !== turn.requestId) return false;
  if (!(ev.seq > turn.lastSeq)) return false;
  turn.lastSeq = ev.seq;

  switch (ev.type) {
    case 'turn.started':
      turn.conversationId = ev.conversation_id;
      turn.turnId = ev.turn_id;
      turn.mode = ev.mode;
      if (turn.status === 'pending') turn.status = 'running';
      break;
    case 'phase':
      turn.phase = ev.phase;
      turn.round = ev.round;
      if (turn.status === 'pending') turn.status = 'running';
      break;
    case 'stream.started': {
      if (turn.status === 'pending') turn.status = 'running';
      if (turn.streams.some((s) => s.id === ev.stream_id)) break;
      turn.streams.push(newStream(ev.stream_id, ev.agent, ev.kind, ev.round, ev.model));
      break;
    }
    case 'stream.delta': {
      const s = turn.streams.find((x) => x.id === ev.stream_id);
      if (!s) break;
      if (ev.section === 'critique') s.critique += ev.text;
      else s.text += ev.text;
      break;
    }
    case 'stream.completed': {
      const s = turn.streams.find((x) => x.id === ev.stream_id);
      if (!s) break;
      s.status = 'done';
      s.messageId = ev.message_id;
      s.usage = ev.usage;
      s.latencyMs = ev.latency_ms;
      s.ttftMs = ev.ttft_ms;
      s.agreement = ev.agreement;
      s.unchanged = ev.unchanged;
      break;
    }
    case 'stream.failed': {
      const s = turn.streams.find((x) => x.id === ev.stream_id);
      if (!s) break;
      s.status = 'failed';
      s.error = ev.error;
      break;
    }
    case 'turn.completed':
      turn.status = 'done';
      turn.conversationId = ev.conversation_id;
      turn.turnId = ev.turn_id;
      turn.finalMessageIds = [...ev.final_message_ids];
      turn.usage = ev.usage;
      turn.savings = ev.savings;
      turn.consensus = ev.consensus;
      turn.cached = ev.cached;
      stopOpenStreams(turn, 'done');
      if (ev.cached) for (const s of turn.streams) s.cached = true;
      break;
    case 'turn.failed':
      turn.status = 'failed';
      turn.error = ev.error;
      stopOpenStreams(turn, 'interrupted');
      break;
    case 'turn.cancelled':
      turn.status = 'cancelled';
      stopOpenStreams(turn, 'interrupted');
      break;
  }
  return true;
}

/** Total usage of a turn: the server total when known, else the sum of its streams. */
export function turnUsage(turn: TurnView): Usage | null {
  if (turn.usage) return turn.usage;
  let total: Usage | null = null;
  for (const s of turn.streams) if (s.usage) total = addUsage(total ?? emptyUsage(), s.usage);
  return total;
}

// ------------------------------------------------------------ view helpers

export interface RoundGroup {
  round: number;
  streams: StreamView[];
}

/** Streams of a given kind, keyed by agent (latest wins). */
export function streamsByAgent(turn: TurnView, kind: StreamKind, round?: number): Partial<Record<Agent, StreamView>> {
  const out: Partial<Record<Agent, StreamView>> = {};
  for (const s of turn.streams) {
    if (s.kind !== kind) continue;
    if (round !== undefined && s.round !== round) continue;
    out[s.agent] = s;
  }
  return out;
}

/** Debate revision rounds present in the turn, ascending. */
export function revisionRounds(turn: TurnView): RoundGroup[] {
  const map = new Map<number, StreamView[]>();
  for (const s of turn.streams) {
    if (s.kind !== 'revision') continue;
    const list = map.get(s.round);
    if (list) list.push(s);
    else map.set(s.round, [s]);
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([round, streams]) => ({ round, streams }));
}

/** Average agreement of the latest revision round that reported any (null if none). */
export function latestAgreement(turn: TurnView): number | null {
  const rounds = revisionRounds(turn);
  for (let i = rounds.length - 1; i >= 0; i--) {
    const values = rounds[i]!.streams.map((s) => s.agreement).filter((v): v is number => v != null);
    if (values.length) return values.reduce((a, b) => a + b, 0) / values.length;
  }
  return null;
}

// ------------------------------------------------------- stored messages

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function asUsage(v: unknown): Usage | null {
  if (!isRecord(v)) return null;
  const u = emptyUsage();
  u.input_tokens = asNumber(v.input_tokens) ?? 0;
  u.output_tokens = asNumber(v.output_tokens) ?? 0;
  u.cache_read_tokens = asNumber(v.cache_read_tokens) ?? 0;
  u.cache_write_tokens = asNumber(v.cache_write_tokens) ?? 0;
  u.reasoning_tokens = asNumber(v.reasoning_tokens) ?? 0;
  u.cost_usd = asNumber(v.cost_usd);
  return u;
}

function asSavings(v: unknown): Savings | null {
  if (!isRecord(v)) return null;
  const cache = asNumber(v.cache) ?? 0;
  const compaction = asNumber(v.compaction) ?? 0;
  const early_stop = asNumber(v.early_stop) ?? 0;
  const unchanged = asNumber(v.unchanged) ?? 0;
  return {
    cache,
    compaction,
    early_stop,
    unchanged,
    total: asNumber(v.total) ?? cache + compaction + early_stop + unchanged,
    cost_usd: asNumber(v.cost_usd),
  };
}

function asConsensus(v: unknown): Consensus | null {
  if (!isRecord(v) || typeof v.reached !== 'boolean') return null;
  const scores: Partial<Record<Agent, number>> = {};
  if (isRecord(v.scores)) {
    const c = asNumber(v.scores.claude);
    const g = asNumber(v.scores.chatgpt);
    if (c != null) scores.claude = c;
    if (g != null) scores.chatgpt = g;
  }
  return { reached: v.reached, round: asNumber(v.round) ?? 0, scores };
}

function asOptions(v: unknown): TurnOptions | null {
  if (!isRecord(v) || !isRecord(v.debate)) return null;
  const d = v.debate;
  const synthesizer = d.synthesizer === 'chatgpt' ? 'chatgpt' : 'claude';
  return {
    debate: {
      rounds: asNumber(d.rounds) ?? 2,
      consensus_threshold: asNumber(d.consensus_threshold) ?? DEFAULT_CONSENSUS_THRESHOLD,
      synthesizer,
    },
    use_cache: v.use_cache !== false,
  };
}

/** Consensus of a stored debate, from its last revision round. */
export function deriveConsensus(turn: TurnView): Consensus | null {
  const rounds = revisionRounds(turn);
  const last = rounds.at(-1);
  if (!last) return null;
  const threshold = turn.options?.debate.consensus_threshold ?? DEFAULT_CONSENSUS_THRESHOLD;
  const scores: Partial<Record<Agent, number>> = {};
  for (const s of last.streams) if (s.agreement != null) scores[s.agent] = s.agreement;
  const values = Object.values(scores);
  const reached = values.length === 2 && values.every((v) => v >= threshold);
  return { reached, round: last.round, scores };
}

function inferMode(streams: StreamView[]): TurnMode {
  if (streams.some((s) => s.kind === 'revision' || s.kind === 'synthesis')) return 'debate';
  const agents = new Set(streams.map((s) => s.agent));
  return agents.size > 1 ? 'duel' : 'solo';
}

/** Error of a stored turn that never finished (cancelled, failed or still running). */
export const INCOMPLETE_KIND = 'incomplete';
const incomplete = (): ErrorInfo => ({ kind: INCOMPLETE_KIND, message: 'Aquest torn no es va completar.' });

/**
 * Whether stored messages hold a finished turn: every finished debate stores a
 * synthesis (real, degraded or replayed from the cache); solo and duel turns
 * store their answers.
 */
function storedTurnFinished(mode: TurnMode, streams: StreamView[]): boolean {
  if (!streams.length) return false;
  return mode !== 'debate' || streams.some((s) => s.kind === 'synthesis');
}

/**
 * Map stored messages (oldest first) into turns. Turn-level `savings`,
 * `consensus` and `usage` are read from any message meta when the backend
 * stores them; otherwise consensus is derived from the last revision round.
 * The total usage is the answers' plus the compaction summary's
 * (`question.meta.compaction_usage`) plus the billed calls that stored no
 * message (`unstored_usage` of the last final message, a running total), as in
 * the live `turn.completed`.
 */
export function turnsFromMessages(messages: Message[], conversationId: number | null = null): TurnView[] {
  const groups = new Map<number, Message[]>();
  for (const m of messages) {
    const list = groups.get(m.turn_id);
    if (list) list.push(m);
    else groups.set(m.turn_id, [m]);
  }

  const turns: TurnView[] = [];
  for (const [turnId, group] of groups) {
    const question = group.find((m) => m.kind === 'question');
    const qmeta = question?.meta ?? {};
    const streams: StreamView[] = [];
    let savings: Savings | null = null;
    let consensus: Consensus | null = null;
    let usage: Usage | null = null;
    let unstored: Usage | null = null;
    let unstoredId = -Infinity;

    for (const m of group) {
      if (m.kind === 'question' || !m.agent) continue;
      const meta = m.meta ?? {};
      // Every final message carries the running total: the last stored has the turn's.
      const pending = m.final && m.id > unstoredId ? asUsage(meta.unstored_usage) : null;
      if (pending) [unstored, unstoredId] = [pending, m.id];
      const s = newStream(`m${m.id}`, m.agent, m.kind, m.round, typeof meta.model === 'string' ? meta.model : '');
      s.text = m.content;
      s.critique = typeof meta.critique === 'string' ? meta.critique : '';
      s.status = 'done';
      s.messageId = m.id;
      s.usage = asUsage(meta.usage);
      s.latencyMs = asNumber(meta.latency_ms);
      s.ttftMs = asNumber(meta.ttft_ms);
      s.agreement = asNumber(meta.agreement);
      s.unchanged = meta.unchanged === true;
      s.cached = meta.cached === true;
      s.costBasis = meta.cost_basis === 'api' || meta.cost_basis === 'equivalent' ? meta.cost_basis : null;
      streams.push(s);
      savings = asSavings(meta.savings) ?? savings;
      consensus = asConsensus(meta.consensus) ?? consensus;
      usage = asUsage(meta.turn_usage) ?? usage;
    }
    savings = asSavings(qmeta.savings) ?? savings;
    consensus = asConsensus(qmeta.consensus) ?? consensus;
    const compaction = asUsage(qmeta.compaction_usage);
    const extra = compaction && unstored ? addUsage(compaction, unstored) : (compaction ?? unstored);
    if (!usage && extra) {
      usage = streams.reduce((total, s) => (s.usage ? addUsage(total, s.usage) : total), extra);
    }

    const mode: TurnMode =
      qmeta.mode === 'solo' || qmeta.mode === 'duel' || qmeta.mode === 'debate' ? qmeta.mode : inferMode(streams);
    const target: Agent | null = qmeta.target === 'claude' || qmeta.target === 'chatgpt' ? qmeta.target : null;
    const last = streams.at(-1);
    const finished = storedTurnFinished(mode, streams);

    const turn: TurnView = {
      key: `t${turnId}`,
      requestId: null,
      conversationId,
      turnId,
      mode,
      target,
      options: asOptions(qmeta.options),
      question: question?.content ?? '',
      createdAt: question?.created_at ?? group[0]!.created_at,
      status: finished ? 'done' : 'failed',
      phase: last ? (last.kind === 'revision' ? 'revision' : last.kind === 'synthesis' ? 'synthesis' : 'answer') : null,
      round: last?.round ?? 0,
      streams,
      lastSeq: 0,
      usage,
      savings,
      consensus,
      cached: streams.length > 0 && streams.every((s) => s.cached),
      error: finished ? null : incomplete(),
      finalMessageIds: group.filter((m) => m.final && m.kind !== 'question').map((m) => m.id),
      live: false,
    };
    if (mode === 'debate' && !turn.consensus) turn.consensus = deriveConsensus(turn);
    turns.push(turn);
  }
  return turns;
}

/**
 * Combine the stored turns of a conversation with the live turns of this
 * session: a live turn replaces the stored turn with the same turn id, and
 * turns are ordered by turn id (turns not started yet go last).
 */
export function mergeTurns(stored: readonly TurnView[], live: readonly TurnView[]): TurnView[] {
  const liveIds = new Set(live.map((t) => t.turnId).filter((id): id is number => id != null));
  const all = [...stored.filter((t) => t.turnId == null || !liveIds.has(t.turnId)), ...live];
  return all
    .map((t, i) => ({ t, i }))
    .sort((a, b) => (a.t.turnId ?? Infinity) - (b.t.turnId ?? Infinity) || a.i - b.i)
    .map(({ t }) => t);
}

// ------------------------------------------------------------- registry

/** Live turns of this session, by request id (reactive). */
export class TurnRegistry {
  turns: Record<string, TurnView> = $state({});

  /** Adds a turn and returns the reactive proxy to mutate from now on. */
  add(turn: TurnView): TurnView {
    const id = turn.requestId;
    if (!id) throw new Error('Live turns need a request id');
    this.turns[id] = turn;
    return this.turns[id]!;
  }

  get(requestId: string): TurnView | undefined {
    return this.turns[requestId];
  }

  remove(requestId: string): void {
    delete this.turns[requestId];
  }

  /** Apply an event; returns the turn if it changed. */
  apply(ev: TurnEvent): TurnView | null {
    const turn = this.turns[ev.request_id];
    if (!turn) return null;
    return applyTurnEvent(turn, ev) ? turn : null;
  }

  unfinished(): TurnView[] {
    return Object.values(this.turns).filter((t) => !isTerminal(t.status));
  }

  forConversation(conversationId: number | null): TurnView[] {
    return Object.values(this.turns).filter((t) => t.conversationId === conversationId);
  }

  clear(): void {
    this.turns = {};
  }
}
