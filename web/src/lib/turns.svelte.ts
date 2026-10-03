// Turn view model: one shape for live turns (built from WebSocket events) and
// stored turns (mapped from REST messages), so both render identically.
//
// `applyTurnEvent` is deterministic and has no side effects beyond the turn it
// receives. It mutates that turn in place on purpose: inside a Svelte $state
// proxy, `stream.text += delta` only re-renders the text that changed, instead
// of rebuilding the whole conversation on every token.

import { i18n } from './i18n/index.svelte';
import { AGENT_LABEL } from './format';
import { AGENTS } from './protocol';
import type {
  Agent,
  Attachment,
  Consensus,
  ErrorInfo,
  Message,
  MessageKind,
  PdfCheckState,
  PdfNotes,
  PdfReading,
  Phase,
  RefineChange,
  RefineOptions,
  RefineReasonCode,
  RefineStopReason,
  Savings,
  TurnEvent,
  TurnFailure,
  TurnMode,
  TurnOptions,
  Usage,
} from './protocol';
import { DEFAULT_SETTINGS } from './settings';

export type StreamKind = Exclude<MessageKind, 'question'>;
export type StreamStatus = 'streaming' | 'done' | 'failed' | 'interrupted';
export type TurnStatus = 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
/** "api": real API cost; "equivalent": subscription call valued at API prices. */
export type CostBasis = 'api' | 'equivalent';

/**
 * What a call of a refine turn is (docs/adr/0010-refine-mode.md): a first answer, a
 * review of the current version, a version of the editor (the merge of round 1, a later
 * edit or its shortening) or the final version (the current one, stored without a call).
 */
export type RefineRole = 'answer' | 'review' | 'version' | 'final';

/** A refine message's `meta.refine` (its stream.completed brings it live), as the view reads it. */
export type RefineInfo =
  | { role: 'review'; score: number | null; unchanged: boolean; changes: RefineChange[] }
  | {
      role: 'version';
      version: number;
      words: number | null;
      budgetWords: number | null;
      /** It became the current version. */
      accepted: boolean;
      /** Why it did not, as the server wrote it (in the turn's language). */
      reason: string | null;
      /** The code of `reason`: what the client's logic reads, and the text it shows (lib/refine.ts reasonText). */
      reasonCode: RefineReasonCode | null;
      changelog: RefineChange[];
      /** Version 1 stored without a call, copying this answer (nobody could merge). */
      copiedFrom: number | null;
    }
  | { role: 'final'; version: number; words: number | null; budgetWords: number | null; stopReason: RefineStopReason | null };

/**
 * The end of a refine round, as `refine.round` says it (a stored turn rebuilds it from its
 * messages). `version`: the current version after the round; `accepted`: the round wrote
 * a new one; `reason`: why not, as the server wrote it (rebuilt from the messages, only the
 * reason of a version that was not accepted), and `reasonCode` its code; `proposals` and
 * `scores`: null for an agent without a review.
 */
export interface RefineRoundView {
  round: number;
  version: number;
  accepted: boolean;
  reason: string | null;
  reasonCode: RefineReasonCode | null;
  words: number;
  budgetWords: number;
  changes: RefineChange[];
  proposals: Record<Agent, number | null>;
  scores: Record<Agent, number | null>;
  converged: boolean;
  /** What the round's calls billed (null when nothing says). */
  usage: Usage | null;
  /** What the turn had billed by its end. */
  total: Usage | null;
}

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
  /** Cut off before the end: a usable partial answer, never a complete one. */
  truncated: boolean;
  /** Why it stopped (`finish_reason`, from the live event or the stored message). */
  finishReason: string | null;
  /** Unchanged revision: the model's short note (live event or stored message). */
  unchangedNote: string | null;
  /** Synthesis stored without calling any model: an agent's latest answer kept as final. */
  degraded: boolean;
  /**
   * ChatGPT's messages when it cannot open PDFs (the subscription): how it read each PDF of
   * the question (live event or stored message); empty otherwise.
   */
  pdfReading: PdfReading[];
  /** Refine turns: what the call is (live, from the phase it started in until its meta says); null in other modes. */
  refineRole: RefineRole | null;
  /** Refine turns: the message's `meta.refine` (live, from its stream.completed); null until then and in other modes. */
  refine: RefineInfo | null;
}

/**
 * Where a PDF's check stands in a live turn: as its latest `pdf.check` says, or "interrupted"
 * when the turn ended while Claude was still checking it. The server sends no end for such a
 * check (it stops with the turn), and nobody read the PDF through it.
 */
export type PdfCheckStatus = PdfCheckState | 'interrupted';

/**
 * Claude's check of a PDF of the question for ChatGPT with the subscription, as the turn's
 * `pdf.check` events tell it (docs/adr/0009-attachments.md). Pages as in PdfReading.
 */
export interface PdfCheckView {
  attachmentId: number;
  name: string;
  state: PdfCheckStatus;
  claudePages: number[];
  hiddenPages: number[];
  uncheckedPages: number[];
  /** An earlier turn's check, stored: no call was made for it in this turn. */
  reused: boolean;
  /** What this turn's calls for the PDF billed (null while checking, or reused). */
  usage: Usage | null;
  /** Why pages remain unchecked (the server's text, in the turn's language); null when none does. */
  reason: string | null;
  /** Meaning of `usage.cost_usd`: the app sets it from Claude's provider mode. */
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
  /** The question's attachments, in order (live: those sent; stored: `meta.attachments`). */
  attachments: Attachment[];
  /**
   * Claude's check of the question's PDFs for ChatGPT, one per PDF, as the events came
   * (`pdfChecks` orders them); a check the turn's end found running is "interrupted". Live
   * only: a stored turn has none, its ChatGPT messages say how it read them
   * (`StreamView.pdfReading`).
   */
  pdfChecks: PdfCheckView[];
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
  /**
   * The turn compacted the history first (live: its `compaction` phase; stored: the
   * question's `compaction_usage`). The summary call is in the total but on no stream,
   * and either agent may have written it.
   */
  compacted: boolean;
  /** True when built from WebSocket events in this session. */
  live: boolean;
  /** Refine turns: the end of every round so far, in order (`refine.round`, or rebuilt from the stored messages). */
  refineRounds: RefineRoundView[];
  /** Refine turns: why it ended with its last version (turn.completed, the final message, or the outcome). */
  stopReason: RefineStopReason | null;
  /** Refine turns: the round after which it stops, as `turn.stopping` said (the owner's turn.stop). */
  stoppingRound: number | null;
  /** This tab sent turn.stop and the server has not answered turn.stopping yet. */
  stopRequested: boolean;
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
  attachments?: readonly Attachment[];
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
    attachments: [...(input.attachments ?? [])],
    pdfChecks: [],
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
    compacted: false,
    live: true,
    refineRounds: [],
    stopReason: null,
    stoppingRound: null,
    stopRequested: false,
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
    truncated: false,
    finishReason: null,
    unchangedNote: null,
    degraded: false,
    pdfReading: [],
    refineRole: null,
    refine: null,
  };
}

/**
 * What a call of a live refine turn is, before its meta says: the first answers and the
 * final version have their own kind, and a revision is a review or a version by the part
 * of the round it started in (the phase event comes first).
 */
function liveRefineRole(kind: StreamKind, phase: Phase | null): RefineRole | null {
  if (kind === 'answer') return 'answer';
  if (kind === 'synthesis') return 'final';
  return phase === 'review' ? 'review' : phase === 'edit' ? 'version' : null;
}

export const isTerminal = (status: TurnStatus): boolean =>
  status === 'done' || status === 'failed' || status === 'cancelled';

function stopOpenStreams(turn: TurnView, to: StreamStatus): void {
  for (const s of turn.streams) if (s.status === 'streaming') s.status = to;
}

/**
 * The turn ended while Claude still checked these PDFs: their checks stopped with it (a
 * cancelled or failed turn stops its check, and the server sends no end for it). A completed
 * turn ends its checks first: for it, this only keeps a check from looking as if it still ran.
 */
function stopOpenChecks(turn: TurnView): void {
  for (const c of turn.pdfChecks) if (c.state === 'checking') c.state = 'interrupted';
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
      if (ev.phase === 'compaction') turn.compacted = true;
      if (turn.status === 'pending') turn.status = 'running';
      break;
    case 'pdf.check': {
      if (turn.status === 'pending') turn.status = 'running';
      const facts = {
        name: ev.name,
        state: ev.state,
        claudePages: asPages(ev.claude_pages),
        hiddenPages: asPages(ev.hidden_pages),
        uncheckedPages: asPages(ev.unchecked_pages),
        reused: ev.reused === true,
        usage: ev.usage ?? null,
        reason: asText(ev.reason),
      };
      const known = turn.pdfChecks.find((c) => c.attachmentId === ev.attachment_id);
      if (known) Object.assign(known, facts);
      else turn.pdfChecks.push({ attachmentId: ev.attachment_id, ...facts, costBasis: null });
      break;
    }
    case 'stream.started': {
      if (turn.status === 'pending') turn.status = 'running';
      if (turn.streams.some((s) => s.id === ev.stream_id)) break;
      const stream = newStream(ev.stream_id, ev.agent, ev.kind, ev.round, ev.model);
      if (turn.mode === 'refine') stream.refineRole = liveRefineRole(ev.kind, turn.phase);
      turn.streams.push(stream);
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
      s.truncated = ev.truncated === true;
      s.finishReason = asText(ev.finish_reason);
      s.unchangedNote = s.unchanged ? asText(ev.unchanged_note) : null;
      s.pdfReading = asPdfReadings(ev.pdf_reading);
      s.refine = asRefineInfo(ev.refine);
      if (s.refine) s.refineRole = s.refine.role;
      // The final version says why the turn ended (a cancelled one's event does not).
      if (s.refine?.role === 'final') turn.stopReason ??= s.refine.stopReason;
      break;
    }
    case 'refine.round': {
      const round = asRoundView(ev);
      const known = turn.refineRounds.findIndex((r) => r.round === round.round);
      if (known >= 0) turn.refineRounds[known] = round;
      else turn.refineRounds.push(round);
      break;
    }
    case 'turn.stopping':
      turn.stoppingRound = asNumber(ev.round) ?? turn.round;
      turn.stopRequested = false;
      break;
    case 'stream.failed': {
      const s = turn.streams.find((x) => x.id === ev.stream_id);
      if (!s) break;
      s.status = 'failed';
      s.error = ev.error;
      // What the failed call was billed (a refusal, an empty reply), when the server knows.
      s.usage = ev.usage ?? null;
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
      turn.stopReason = asStopReason(ev.stop_reason) ?? turn.stopReason;
      turn.stopRequested = false;
      stopOpenStreams(turn, 'done');
      stopOpenChecks(turn);
      if (ev.cached) for (const s of turn.streams) s.cached = true;
      break;
    case 'turn.failed':
      turn.status = 'failed';
      turn.error = ev.error;
      // A turn that fails or is cancelled may have billed calls too: its total (N10).
      turn.usage = ev.usage ?? turn.usage;
      turn.stopRequested = false;
      stopOpenStreams(turn, 'interrupted');
      stopOpenChecks(turn);
      break;
    case 'turn.cancelled':
      turn.status = 'cancelled';
      turn.usage = ev.usage ?? turn.usage;
      turn.stopRequested = false;
      stopOpenStreams(turn, 'interrupted');
      stopOpenChecks(turn);
      break;
  }
  return true;
}

/**
 * Total usage of a turn: the server total when known (every terminal event carries it),
 * else the sum of its streams, failed calls included.
 */
export function turnUsage(turn: TurnView): Usage | null {
  if (turn.usage) return turn.usage;
  let total: Usage | null = null;
  for (const s of turn.streams) if (s.usage) total = addUsage(total ?? emptyUsage(), s.usage);
  return total;
}

// ------------------------------------------------------------ view helpers

/**
 * The checks of the turn's PDFs in the order of its attachments (the events come as each
 * check starts or ends); those of attachments the turn does not know yet (a turn replayed
 * after a reload, until its question is loaded) after them, in the order they came.
 */
export function pdfChecks(turn: TurnView): PdfCheckView[] {
  const order = new Map(turn.attachments.map((a, i) => [a.id, i]));
  const at = (check: PdfCheckView) => order.get(check.attachmentId) ?? Number.MAX_SAFE_INTEGER;
  return turn.pdfChecks
    .map((check, i) => ({ check, i }))
    .sort((a, b) => at(a.check) - at(b.check) || a.i - b.i)
    .map(({ check }) => check);
}

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

/** Synthesis attempts of a debate, in the order they started (one stream each). */
export function synthesisAttempts(turn: TurnView): StreamView[] {
  return turn.streams.filter((s) => s.kind === 'synthesis');
}

/**
 * The synthesis a debate shows. The engine opens a stream for each attempt (the
 * chosen synthesizer, then the other agent, then an answer kept as final when
 * nobody could synthesize), so the first one is not necessarily the result: the
 * turn's final message wins, else the latest attempt that finished, else the
 * latest one (in progress, or the last that failed).
 */
export function shownSynthesis(turn: TurnView): StreamView | null {
  const attempts = synthesisAttempts(turn);
  const final = attempts.find((s) => s.messageId != null && turn.finalMessageIds.includes(s.messageId));
  if (final) return final;
  for (let i = attempts.length - 1; i >= 0; i--) if (attempts[i]!.status === 'done') return attempts[i]!;
  return attempts.at(-1) ?? null;
}

/**
 * Note for a synthesis that is not the chosen synthesizer's own (null when it
 * is, or when there is none). A stored turn keeps only its final message, so
 * the note relies on what the live and the stored view share: the `degraded`
 * flag and the chosen synthesizer. Live, the failed attempts say it earlier.
 */
export function synthesisNote(turn: TurnView, shown: StreamView | null = shownSynthesis(turn)): string | null {
  if (!shown) return null;
  const attempts = synthesisAttempts(turn);
  const failed = attempts.slice(0, attempts.indexOf(shown)).filter((s) => s.status !== 'done').map((s) => s.agent);
  const who = AGENT_LABEL[shown.agent];
  // Nobody synthesized and an answer was kept as final: the stored message says so;
  // live, it follows a failed attempt of the same agent (both attempts failed) or a
  // failed first answer (then the engine does not attempt any synthesis).
  const degraded =
    shown.degraded ||
    failed.includes(shown.agent) ||
    turn.streams.some((s) => s.kind === 'answer' && s.status === 'failed');
  const texts = i18n.m.turn.synthesis;
  if (degraded) return texts.degraded(who);
  const chosen = turn.options?.debate.synthesizer;
  const missing = failed.find((agent) => agent !== shown.agent) ?? (chosen !== shown.agent ? chosen : undefined);
  if (!missing) return null;
  const lead = texts.failed(AGENT_LABEL[missing]);
  if (shown.status === 'done') return texts.doneBy(lead, who);
  if (shown.status === 'streaming') return texts.writingBy(lead, who);
  return texts.triedBy(lead, who);
}

/**
 * Debate revisions that keep the agent's previous answer instead of writing a new
 * one, each with the stream that wrote the answer it keeps (null when the turn
 * does not have it). An unchanged revision keeps it, and so does a reply that
 * brought no new answer, such as one cut off in its critique: its content is the
 * previous answer again (PROTOCOL.md), so what was cut is the revision, not the
 * answer it shows. Live, the engine streams that answer again as the revision's
 * answer. Only finished calls count, as in the engine: a failed revision leaves
 * the answer before it, and one still streaming may be a new answer that begins
 * with the previous one.
 */
export function keptAnswers(turn: TurnView): Map<string, StreamView | null> {
  const kept = new Map<string, StreamView | null>();
  for (const agent of AGENTS) {
    const own = turn.streams
      .filter((s) => s.agent === agent && (s.kind === 'answer' || s.kind === 'revision'))
      .sort((a, b) => a.round - b.round);
    let origin: StreamView | null = null; // the stream that wrote the agent's latest answer
    let latest = ''; // that answer, trimmed like the stored content
    for (const s of own) {
      if (s.status !== 'done') continue;
      const text = s.text.trim();
      if (s.kind === 'revision' && (s.unchanged || (latest !== '' && text === latest))) kept.set(s.id, origin);
      else [origin, latest] = [s, text];
    }
  }
  return kept;
}

/**
 * How fit for the brief the latest reviews of a refine turn find the document: the
 * average score of the latest round whose reviews gave any (null before the first).
 */
export function latestRefineScore(turn: TurnView): number | null {
  let round = -1;
  let scores: number[] = [];
  for (const s of turn.streams) {
    const score = s.refine?.role === 'review' ? s.refine.score : null;
    if (score == null || s.round < round) continue;
    if (s.round > round) [round, scores] = [s.round, []];
    scores.push(score);
  }
  return scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
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

/** A non-empty string without surrounding whitespace, else null. */
function asText(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null;
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

const asCount = (v: unknown): number | null =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null;

const asScore = (v: unknown): number | null => {
  const n = asCount(v);
  return n != null && n <= 100 ? n : null;
};

/** Every reason a refine turn may stop for (`stop_reason`). */
export const STOP_REASONS: readonly RefineStopReason[] = ['owner', 'converged', 'unchanged', 'max_rounds', 'budget', 'failed'];

const asStopReason = (v: unknown): RefineStopReason | null =>
  STOP_REASONS.find((reason) => reason === v) ?? null;

/**
 * Why a refine round wrote no new version, or a version was not accepted (`reason_code`,
 * orchestrator/engine.py, docs/PROTOCOL.md): the client's logic reads the code, never the
 * text, and shows its own text for each one (lib/refine.ts reasonText).
 */
export const REFINE_REASON_CODES: readonly RefineReasonCode[] = [
  'over_budget',
  'incomplete',
  'identical',
  'nothing_to_change',
  'failed_round',
];

/**
 * The reasons of the turns stored before `reason_code` existed, by their code: they have the
 * text alone, and it was always Catalan then (legacy data, not texts of the interface).
 */
const LEGACY_REASONS: ReadonlyMap<string, RefineReasonCode> = new Map([
  ['La nova versió passava del límit de paraules.', 'over_budget'],
  ["L'editor no ha escrit cap versió completa.", 'incomplete'],
  ["La nova versió és igual a l'anterior.", 'identical'],
  ['Cap dels dos hi ha trobat res a canviar.', 'nothing_to_change'],
  ['Els models han fallat i la ronda no ha escrit cap versió.', 'failed_round'],
]);

/**
 * The code of a reason: its `reason_code` when the server sent one (null for a code this
 * client does not know, whose text is then shown as it came), else, for what was stored
 * before the codes, the code of its text.
 */
function asReasonCode(code: unknown, reason: unknown): RefineReasonCode | null {
  if (code !== undefined) return REFINE_REASON_CODES.find((known) => known === code) ?? null;
  return typeof reason === 'string' ? (LEGACY_REASONS.get(reason) ?? null) : null;
}

/** The changes of a review or a version; entries without a kind or a text are left out. */
const asChanges = (v: unknown): RefineChange[] =>
  Array.isArray(v)
    ? v.filter(isRecord).flatMap((c) =>
        typeof c.kind === 'string' && typeof c.text === 'string' && c.text.trim() ? [{ kind: c.kind, text: c.text }] : [],
      )
    : [];

/** A refine message's `meta.refine`, or null when it is not one. */
function asRefineInfo(v: unknown): RefineInfo | null {
  if (!isRecord(v)) return null;
  if (v.role === 'review') {
    return { role: 'review', score: asScore(v.score), unchanged: v.unchanged === true, changes: asChanges(v.changes) };
  }
  const version = asCount(v.version);
  if (version == null) return null;
  const words = asCount(v.words);
  const budgetWords = asCount(v.budget_words);
  if (v.role === 'version') {
    return {
      role: 'version',
      version,
      words,
      budgetWords,
      accepted: v.accepted === true,
      reason: asText(v.reason),
      reasonCode: asReasonCode(v.reason_code, v.reason),
      changelog: asChanges(v.changelog),
      copiedFrom: asCount(v.copied_from),
    };
  }
  if (v.role === 'final') return { role: 'final', version, words, budgetWords, stopReason: asStopReason(v.stop_reason) };
  return null;
}

/** A refine turn's options (the question's `meta.refine`), the missing ones as the defaults. */
function asRefineOptions(v: unknown): RefineOptions | null {
  if (!isRecord(v)) return null;
  const d = DEFAULT_SETTINGS.refine;
  return {
    max_rounds: asCount(v.max_rounds) ?? d.max_rounds,
    budget_eur: asNumber(v.budget_eur) ?? d.budget_eur,
    max_words: asCount(v.max_words),
    stop_on_convergence: typeof v.stop_on_convergence === 'boolean' ? v.stop_on_convergence : d.stop_on_convergence,
    convergence_threshold: asScore(v.convergence_threshold) ?? d.convergence_threshold,
    editor: v.editor === 'chatgpt' ? 'chatgpt' : v.editor === 'claude' ? 'claude' : d.editor,
  };
}

const perAgent = (v: unknown, read: (x: unknown) => number | null): Record<Agent, number | null> => ({
  claude: isRecord(v) ? read(v.claude) : null,
  chatgpt: isRecord(v) ? read(v.chatgpt) : null,
});

function asRoundView(ev: Extract<TurnEvent, { type: 'refine.round' }>): RefineRoundView {
  return {
    round: asCount(ev.round) ?? 0,
    version: asCount(ev.version) ?? 0,
    accepted: ev.accepted === true,
    reason: asText(ev.reason),
    reasonCode: asReasonCode(ev.reason_code, ev.reason),
    words: asCount(ev.words) ?? 0,
    budgetWords: asCount(ev.budget_words) ?? 0,
    changes: asChanges(ev.changes),
    proposals: perAgent(ev.proposals, asCount),
    scores: perAgent(ev.scores, asScore),
    converged: ev.converged === true,
    usage: asUsage(ev.usage),
    total: asUsage(ev.total),
  };
}

/** Page numbers (from 1) of a list; anything else in it is left out. */
const asPages = (v: unknown): number[] =>
  Array.isArray(v) ? v.filter((n): n is number => typeof n === 'number' && Number.isInteger(n) && n >= 1) : [];

/** The warnings of a stored PDF's pages (`pdf_notes`), or null when there are none it can read. */
function asPdfNotes(v: unknown): PdfNotes | null {
  if (!isRecord(v)) return null;
  return { no_text: asPages(v.no_text), garbled: asPages(v.garbled), hidden: asPages(v.hidden) };
}

/** How ChatGPT read one PDF (`pdf_reading`), or null when the entry is not one. */
function asPdfReading(v: unknown): PdfReading | null {
  if (!isRecord(v)) return null;
  const id = asNumber(v.attachment_id);
  if (id == null || !Number.isInteger(id) || typeof v.name !== 'string' || typeof v.checked !== 'boolean') return null;
  return {
    attachment_id: id,
    name: v.name,
    checked: v.checked,
    claude_pages: asPages(v.claude_pages),
    hidden_pages: asPages(v.hidden_pages),
    unchecked_pages: asPages(v.unchecked_pages),
    reason: asText(v.reason),
  };
}

const asPdfReadings = (v: unknown): PdfReading[] =>
  Array.isArray(v) ? v.map(asPdfReading).filter((r): r is PdfReading => r != null) : [];

const ATTACHMENT_KINDS: readonly string[] = ['image', 'pdf', 'text'];

/** An attachment of a stored question (`meta.attachments`), or null when it is not one. */
function asAttachment(v: unknown): Attachment | null {
  if (!isRecord(v)) return null;
  const id = asNumber(v.id);
  if (id == null || id < 1 || !Number.isInteger(id)) return null;
  if (typeof v.name !== 'string' || typeof v.kind !== 'string' || !ATTACHMENT_KINDS.includes(v.kind)) return null;
  return {
    id,
    name: v.name,
    kind: v.kind as Attachment['kind'],
    mime: typeof v.mime === 'string' ? v.mime : '',
    size: asNumber(v.size) ?? 0,
    pages: asNumber(v.pages),
    width: asNumber(v.width),
    height: asNumber(v.height),
    sha256: typeof v.sha256 === 'string' ? v.sha256 : '',
    created_at: typeof v.created_at === 'string' ? v.created_at : '',
    has_thumbnail: v.has_thumbnail === true,
    text_available: v.text_available === true,
    estimated_tokens: asNumber(v.estimated_tokens) ?? 0,
    pdf_notes: v.kind === 'pdf' ? asPdfNotes(v.pdf_notes) : null,
  };
}

const asAttachments = (v: unknown): Attachment[] =>
  Array.isArray(v) ? v.map(asAttachment).filter((a): a is Attachment => a != null) : [];

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

/**
 * Error of a stored turn that never finished: its outcome is still null (the server
 * stopped or crashed during it), or, stored before outcomes existed, it has no answer
 * (a debate: no synthesis).
 */
export const INCOMPLETE_KIND = 'incomplete';
const incomplete = (): ErrorInfo => ({ kind: INCOMPLETE_KIND, message: i18n.m.turn.incomplete });

/**
 * Whether stored messages hold a finished turn, for turns stored without an outcome:
 * every finished debate stores a synthesis (real, degraded or replayed from the cache),
 * and every refine turn its final version; solo and duel turns store their answers.
 */
function storedTurnFinished(mode: TurnMode, streams: StreamView[]): boolean {
  if (!streams.length) return false;
  return (mode !== 'debate' && mode !== 'refine') || streams.some((s) => s.kind === 'synthesis');
}

/** How a stored turn ended (`question.meta.outcome`, ADR 0007), as the view uses it. */
interface StoredOutcome {
  status: 'done' | 'failed' | 'cancelled';
  error: ErrorInfo | null;
  failures: TurnFailure[];
  /** The turn's total; null only when unreadable. */
  usage: Usage | null;
  savings: Savings | null;
  consensus: Consensus | null;
  finalMessageIds: number[] | null;
  cached: boolean;
  stopReason: RefineStopReason | null;
}

function outcomeStatus(v: unknown): StoredOutcome['status'] | null {
  switch (v) {
    case 'completed':
      return 'done';
    case 'failed':
      return 'failed';
    case 'cancelled':
      return 'cancelled';
    default:
      return null;
  }
}

const errorKind = (v: unknown): string => (typeof v === 'string' && v ? v : 'unavailable');

function asError(v: unknown): ErrorInfo | null {
  if (!isRecord(v)) return null;
  const message = asText(v.message);
  return message ? { kind: errorKind(v.kind), message } : null;
}

function asFailure(v: unknown): TurnFailure | null {
  if (!isRecord(v) || (v.agent !== 'claude' && v.agent !== 'chatgpt')) return null;
  const round = asNumber(v.round);
  if (round == null || round < 0 || !Number.isInteger(round)) return null;
  return { agent: v.agent, kind: errorKind(v.kind), message: asText(v.message) ?? '', round };
}

/** The outcome of a stored turn, or null when there is none it can read. */
function asOutcome(v: unknown): StoredOutcome | null {
  if (!isRecord(v)) return null;
  const status = outcomeStatus(v.status);
  if (!status) return null;
  const failures = Array.isArray(v.failures) ? v.failures.map(asFailure) : [];
  const ids = Array.isArray(v.final_message_ids) ? v.final_message_ids.map(asNumber) : null;
  return {
    status,
    error: status === 'failed' ? asError(v.error) : null,
    failures: failures.filter((f): f is TurnFailure => f != null),
    usage: asUsage(v.usage),
    savings: asSavings(v.savings),
    consensus: asConsensus(v.consensus),
    finalMessageIds: ids?.filter((id): id is number => id != null) ?? null,
    cached: v.cached === true,
    stopReason: asStopReason(v.stop_reason),
  };
}

/** Where a call goes in a turn: first answers, each revision round, then the synthesis. */
const stage = (s: StreamView): number =>
  s.kind === 'answer' ? 0 : s.kind === 'revision' ? s.round : Number.MAX_SAFE_INTEGER;

/**
 * The calls of a stored turn that failed (`outcome.failures`, in the order they failed)
 * as failed streams where the live turn had them, since they stored no message. Round 0
 * is an agent's first answer and a later round a debate's revision, unless the agent
 * already has that call (stored, or failed before): then it was an attempt at the
 * debate's synthesis, which runs in the round of the last revision. Failed synthesis
 * attempts go before the synthesis that counts, as they came before it.
 */
function withFailures(streams: StreamView[], failures: TurnFailure[], mode: TurnMode): StreamView[] {
  if (!failures.length) return streams;
  const taken = new Set(streams.filter((s) => s.kind !== 'synthesis').map((s) => `${s.agent} ${s.kind} ${s.round}`));
  const failed: StreamView[] = [];
  failures.forEach((f, i) => {
    let kind: StreamKind = mode === 'debate' && f.round > 0 ? 'revision' : 'answer';
    const call = `${f.agent} ${kind} ${f.round}`;
    if (taken.has(call)) {
      if (mode !== 'debate') return; // an answer that was stored after all: it is what counts
      kind = 'synthesis';
    } else {
      taken.add(call);
    }
    const s = newStream(`f${i}`, f.agent, kind, f.round, '');
    s.status = 'failed';
    s.error = { kind: f.kind, message: f.message };
    failed.push(s);
  });
  const attempt = (s: StreamView) => Number(s.kind === 'synthesis' && s.messageId == null);
  return [...streams, ...failed]
    .map((s, i) => ({ s, i }))
    .sort((a, b) => stage(a.s) - stage(b.s) || attempt(b.s) - attempt(a.s) || a.i - b.i)
    .map(({ s }) => s);
}

/** Where a call of a refine round goes: its reviews, then the versions, then the final one. */
const REFINE_ORDER: Record<RefineRole, number> = { answer: 0, review: 1, version: 2, final: 3 };

/**
 * The calls of a stored refine turn in the order they ran, with those that failed
 * (`outcome.failures`, which stored no message) where they failed: a failure in round 0
 * was a first answer, in round 1 a merge, and in a later round a review, unless the
 * agent's review of that round is there (stored or failed before): then it was an edit.
 *
 * A round's parallel calls (the answers, the reviews) started Claude's first; its editor
 * calls ran editor after editor (orchestrator/engine.py `_editors`): the turn's `editor`
 * first, unless its merge or edit had failed in an earlier round and the other's had not;
 * each editor's version, then its shortening, or its call that failed; and a version 1
 * copied without a call came after them all.
 */
function withRefineFailures(streams: StreamView[], failures: TurnFailure[], editor: Agent): StreamView[] {
  const reviewed = new Set(streams.filter((s) => s.refineRole === 'review').map((s) => `${s.agent} ${s.round}`));
  const failed: StreamView[] = [];
  failures.forEach((f, i) => {
    if (f.round === 0 && streams.some((s) => s.agent === f.agent && s.kind === 'answer')) return; // retried: its answer counts
    const call = `${f.agent} ${f.round}`;
    const role: RefineRole = f.round === 0 ? 'answer' : f.round === 1 || reviewed.has(call) ? 'version' : 'review';
    if (role === 'review') reviewed.add(call);
    const s = newStream(`f${i}`, f.agent, role === 'answer' ? 'answer' : 'revision', f.round, '');
    s.status = 'failed';
    s.error = { kind: f.kind, message: f.message };
    s.refineRole = role;
    failed.push(s);
  });
  // The editors of each round, in the order they wrote: those whose edit failed before go last.
  const editors = new Map<number, Agent[]>();
  const failedBefore = new Set<Agent>();
  for (const round of [...new Set(failed.map((s) => s.round))].sort((a, b) => a - b)) {
    const order = [editor, ...AGENTS.filter((a) => a !== editor)];
    editors.set(round, [...order.filter((a) => !failedBefore.has(a)), ...order.filter((a) => failedBefore.has(a))]);
    for (const s of failed) if (s.round === round && s.refineRole === 'version') failedBefore.add(s.agent);
  }
  const editorRank = (s: StreamView): number => {
    if (s.refine?.role === 'version' && s.refine.copiedFrom != null) return AGENTS.length * 2; // a copy, last
    const order = editors.get(s.round) ?? [editor, ...AGENTS.filter((a) => a !== editor)];
    return order.indexOf(s.agent) * 2 + (s.status === 'failed' ? 1 : 0);
  };
  const order = (s: StreamView) => REFINE_ORDER[s.refineRole ?? 'version'];
  return [...streams, ...failed]
    .map((s, i) => ({ s, i }))
    .sort(
      (a, b) =>
        a.s.round - b.s.round ||
        order(a.s) - order(b.s) ||
        (a.s.refineRole === 'version'
          ? editorRank(a.s) - editorRank(b.s)
          : AGENTS.indexOf(a.s.agent) - AGENTS.indexOf(b.s.agent)) ||
        a.i - b.i,
    )
    .map(({ s }) => s);
}

const plus = (a: Usage | null, b: Usage | null): Usage | null => (a && b ? addUsage(a, b) : (a ?? b));

/** Less than this is a rounding error of costs summed and subtracted, never a call (USD). */
const ROUNDING_USD = 1e-9;

/** What `a` has more than `b`, field by field (never below zero); null when that is nothing. */
function minusUsage(a: Usage, b: Usage | null): Usage | null {
  const left = (x: number, y: number | undefined) => Math.max(0, x - (y ?? 0));
  const rest: Usage = {
    input_tokens: left(a.input_tokens, b?.input_tokens),
    output_tokens: left(a.output_tokens, b?.output_tokens),
    cache_read_tokens: left(a.cache_read_tokens, b?.cache_read_tokens),
    cache_write_tokens: left(a.cache_write_tokens, b?.cache_write_tokens),
    reasoning_tokens: left(a.reasoning_tokens, b?.reasoning_tokens),
    cost_usd: a.cost_usd == null ? null : left(a.cost_usd, b?.cost_usd ?? 0),
  };
  const tokens = rest.input_tokens + rest.output_tokens + rest.cache_read_tokens + rest.cache_write_tokens + rest.reasoning_tokens;
  return tokens > 0 || (rest.cost_usd ?? 0) > ROUNDING_USD ? rest : null;
}

/** The billed attempts other models declined before a call was served (`meta.declined`), summed. */
const asDeclined = (v: unknown): Usage | null =>
  Array.isArray(v) ? v.filter(isRecord).reduce<Usage | null>((sum, a) => plus(sum, asUsage(a.usage)), null) : null;

/** What a stored refine turn says of its calls, for `storedRefineRounds`. */
interface StoredRefineCalls {
  /** Its calls in the order they ran, those that failed included. */
  calls: StreamView[];
  /** The attempts other models declined before a call was served, by its stream id. */
  declined: Map<string, Usage>;
  compaction: Usage | null;
  /** `unstored_usage` of its final message: the billed calls that stored no message. */
  unstored: Usage | null;
  outcome: StoredOutcome | null;
  completed: boolean;
  stopReason: RefineStopReason | null;
}

/**
 * What each round of a stored refine turn billed besides its stored calls: what the turn's
 * total (`outcome.usage`) has that no message says. Its failed calls billed what its final
 * message's `unstored_usage` has besides the attempts declined (their messages say those);
 * the rest (Claude's check of a PDF for ChatGPT) came before round 1. Without a final
 * message, all of it goes to the failures. The outcome does not say what each failure
 * billed: when they failed in more than one round, the last of them gets it all, so the
 * totals from it on are exact, and those before it at most what they were.
 */
function unattributed(input: StoredRefineCalls, known: Usage | null): Map<number, Usage> {
  const extra = new Map<number, Usage>();
  const add = (round: number, usage: Usage | null) => {
    const sum = plus(extra.get(round) ?? null, usage);
    if (sum) extra.set(round, sum);
  };
  const total = input.outcome?.usage;
  const rest = total ? minusUsage(total, known) : null;
  if (!rest) return extra;
  const rounds = input.outcome?.failures.map((f) => f.round) ?? [];
  let failed: Usage | null = null;
  if (rounds.length) {
    if (!input.calls.some((s) => s.refineRole === 'final')) failed = rest;
    else if (input.unstored) {
      const declined = [...input.declined.values()].reduce<Usage | null>(plus, null);
      failed = minusUsage(input.unstored, declined);
      if (failed && (failed.cost_usd ?? 0) > (rest.cost_usd ?? 0) + ROUNDING_USD) failed = rest;
    }
  }
  add(0, failed ? minusUsage(rest, failed) : rest);
  if (failed) add(Math.max(...rounds), failed);
  return extra;
}

/**
 * Whether the last round of a refine turn that did not complete had ended when it stopped,
 * by the engine's rules: round 1 ends with version 1 (merged or copied); a later round with
 * an accepted version, or a version not accepted (over the word limit: once shortened, or
 * once its shortening failed), or, without any, once every agent's review is there (stored
 * or failed) and nobody proposed a change, or no review came back, or every edit failed.
 */
function roundEnded(round: number, own: StreamView[], active: readonly Agent[]): boolean {
  const versions = own.filter((s) => s.refineRole === 'version' && s.status === 'done');
  const meta = (s: StreamView) => (s.refine?.role === 'version' ? s.refine : null);
  if (versions.some((s) => meta(s)?.accepted)) return true;
  if (round <= 1) return false;
  const editFailed = new Set(own.filter((s) => s.refineRole === 'version' && s.status === 'failed').map((s) => s.agent));
  const last = versions.at(-1);
  if (last) {
    const shortened = versions.filter((s) => s.agent === last.agent).length > 1;
    return meta(last)?.reasonCode !== 'over_budget' || shortened || editFailed.has(last.agent);
  }
  const reviews = own.filter((s) => s.refineRole === 'review');
  if (!active.every((agent) => reviews.some((s) => s.agent === agent))) return false;
  const proposals = reviews.flatMap((s) => (s.status === 'done' && s.refine?.role === 'review' ? [s.refine] : []));
  return !proposals.length || proposals.every((r) => r.unchanged) || active.every((agent) => editFailed.has(agent));
}

/**
 * The end of every round of a stored refine turn, as its `refine.round` said it live,
 * rebuilt from the messages: a round's current version is its last accepted one (else the
 * one before), its changes that version's changelog, and its usage what its calls billed
 * (the attempts declined before them included, and what the turn's total has that no
 * message says: `unattributed`) on top of the calls before round 1 (compaction included).
 * A round without a version ended when nobody found anything to change, or when the models
 * failed; in a turn that did not complete, the last round may never have ended
 * (`roundEnded`).
 */
function storedRefineRounds(input: StoredRefineCalls): RefineRoundView[] {
  const { calls, completed, stopReason } = input;
  const billedOf = (s: StreamView) => plus(s.usage, input.declined.get(s.id) ?? null);
  const byRound = new Map<number, Usage | null>([[0, input.compaction]]);
  for (const s of calls) byRound.set(s.round, plus(byRound.get(s.round) ?? null, billedOf(s)));
  const extra = unattributed(input, [...byRound.values()].reduce<Usage | null>(plus, null));
  const billed = (round: number) => plus(byRound.get(round) ?? null, extra.get(round) ?? null);

  const active = AGENTS.filter((agent) => calls.some((s) => s.round === 0 && s.agent === agent && s.refineRole === 'answer' && s.status === 'done'));
  const inRounds = calls.filter((s) => s.round >= 1 && (s.refineRole === 'review' || s.refineRole === 'version'));
  const numbers = [...new Set(inRounds.map((s) => s.round))].sort((a, b) => a - b);
  const rounds: RefineRoundView[] = [];
  let current: Extract<RefineInfo, { role: 'version' }> | null = null;
  let budget = 0;
  let total = billed(0);
  numbers.forEach((round, i) => {
    const own = inRounds.filter((s) => s.round === round);
    const reviews = new Map<Agent, Extract<RefineInfo, { role: 'review' }>>();
    for (const s of own) if (s.refine?.role === 'review') reviews.set(s.agent, s.refine);
    const versions = own.flatMap((s) => (s.refine?.role === 'version' ? [s.refine] : []));
    const last = versions.at(-1) ?? null;
    const unchanged = !last && reviews.size > 0 && [...reviews.values()].every((r) => r.unchanged);
    const later = i < numbers.length - 1;
    if (!later && !completed && !roundEnded(round, own, active)) return; // it never ended
    if (last?.accepted) current = last;
    budget = last?.budgetWords ?? current?.budgetWords ?? budget;
    // As the engine reckons it: the turn's running total after the round, less the one before.
    const spent = billed(round) ?? emptyUsage();
    const usage = spent.cost_usd == null && total?.cost_usd != null ? { ...spent, cost_usd: 0 } : spent;
    total = plus(total, usage);
    const review = (agent: Agent) => reviews.get(agent);
    rounds.push({
      round,
      version: current?.version ?? 0,
      accepted: last?.accepted === true,
      // A round without a version stored no reason: its code alone says it.
      reason: last && !last.accepted ? last.reason : null,
      reasonCode: last ? (last.accepted ? null : last.reasonCode) : unchanged ? 'nothing_to_change' : 'failed_round',
      words: current?.words ?? 0,
      budgetWords: budget,
      changes: last?.accepted ? last.changelog : [],
      proposals: { claude: review('claude')?.changes.length ?? null, chatgpt: review('chatgpt')?.changes.length ?? null },
      scores: { claude: review('claude')?.score ?? null, chatgpt: review('chatgpt')?.score ?? null },
      converged: !later && stopReason === 'converged',
      usage,
      total,
    });
  });
  return rounds;
}

/**
 * Map stored messages (oldest first) into turns.
 *
 * A turn whose question has an outcome (ADR 0007) ended as it says: its status and
 * error, the calls that failed (shown on their cards with their reason), its total
 * usage (every billed call of the turn, taken as is), savings, consensus, final
 * messages and cache flag, exactly as its live terminal event said. An outcome that is
 * still null means the turn never ended: it is incomplete.
 *
 * Turns stored before outcomes existed follow the old rules: they are finished when
 * they stored their answers (a debate, its synthesis); `savings`, `consensus` and
 * `usage` come from any message meta that stores them, else the consensus is derived
 * from the last revision round; and the total usage is the answers' plus the compaction
 * summary's (`question.meta.compaction_usage`) plus the billed calls that stored no
 * message (`unstored_usage` of the last final message, a running total).
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
    const declared = qmeta.mode;
    const streams: StreamView[] = [];
    let savings: Savings | null = null;
    let consensus: Consensus | null = null;
    let usage: Usage | null = null;
    let unstored: Usage | null = null;
    let unstoredId = -Infinity;
    /** Refine turns: the attempts other models declined before each call was served, by stream id. */
    const declined = new Map<string, Usage>();

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
      s.truncated = meta.truncated === true;
      s.finishReason = asText(meta.finish_reason);
      s.unchangedNote = s.unchanged ? asText(meta.unchanged_note) : null;
      s.degraded = meta.degraded === true;
      s.pdfReading = asPdfReadings(meta.pdf_reading);
      if (declared === 'refine') {
        s.refine = asRefineInfo(meta.refine);
        s.refineRole =
          s.refine?.role ?? (m.kind === 'answer' ? 'answer' : m.kind === 'synthesis' ? 'final' : m.round === 1 ? 'version' : null);
        const attempts = asDeclined(meta.declined);
        if (attempts) declined.set(s.id, attempts);
      }
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
      declared === 'solo' || declared === 'duel' || declared === 'debate' || declared === 'refine'
        ? declared
        : inferMode(streams);
    const target: Agent | null = qmeta.target === 'claude' || qmeta.target === 'chatgpt' ? qmeta.target : null;
    // How the turn ended, as its live terminal event said (ADR 0007). Null: it never ended.
    const ended = asOutcome(qmeta.outcome);
    let options = asOptions(qmeta.options);
    // A refine turn's options are the question's meta.refine (so a reloaded turn shows its limits).
    const refine =
      mode === 'refine' ? asRefineOptions(qmeta.refine ?? (isRecord(qmeta.options) ? qmeta.options.refine : undefined)) : null;
    if (refine) options = { ...(options ?? { debate: { ...DEFAULT_SETTINGS.debate }, use_cache: true }), refine };
    const calls =
      mode === 'refine'
        ? withRefineFailures(streams, ended?.failures ?? [], options?.refine?.editor ?? DEFAULT_SETTINGS.refine.editor)
        : ended
          ? withFailures(streams, ended.failures, mode)
          : streams;
    const last = calls.at(-1);
    let status: TurnStatus = 'done';
    let error: ErrorInfo | null = null;
    if (ended) [status, error] = [ended.status, ended.error];
    else if (qmeta.outcome === null || !storedTurnFinished(mode, streams)) [status, error] = ['failed', incomplete()];

    const finalVersion = streams.find((s) => s.refine?.role === 'final')?.refine;
    const stopReason =
      mode === 'refine' ? (ended?.stopReason ?? (finalVersion?.role === 'final' ? finalVersion.stopReason : null)) : null;

    const turn: TurnView = {
      key: `t${turnId}`,
      requestId: null,
      conversationId,
      turnId,
      mode,
      target,
      options,
      question: question?.content ?? '',
      attachments: asAttachments(qmeta.attachments),
      pdfChecks: [],
      createdAt: question?.created_at ?? group[0]!.created_at,
      status,
      phase: last ? (last.kind === 'revision' ? 'revision' : last.kind === 'synthesis' ? 'synthesis' : 'answer') : null,
      round: last?.round ?? 0,
      streams: calls,
      lastSeq: 0,
      // The outcome's total is the whole turn's: nothing is added to it.
      usage: ended?.usage ?? usage,
      savings: ended ? (ended.savings ?? savings) : savings,
      consensus: ended ? ended.consensus : consensus,
      cached: ended ? ended.cached : streams.length > 0 && streams.every((s) => s.cached),
      error,
      finalMessageIds: ended?.finalMessageIds ?? group.filter((m) => m.final && m.kind !== 'question').map((m) => m.id),
      compacted: compaction != null,
      live: false,
      refineRounds:
        mode === 'refine'
          ? storedRefineRounds({ calls, declined, compaction, unstored, outcome: ended, completed: status === 'done', stopReason })
          : [],
      stopReason,
      stoppingRound: null,
      stopRequested: false,
    };
    // A turn that ended has the consensus it had live (none, unless it completed a debate).
    if (mode === 'debate' && !turn.consensus && !ended) turn.consensus = deriveConsensus(turn);
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
