// Turn view model: one shape for live turns (built from WebSocket events) and
// stored turns (mapped from REST messages), so both render identically.
//
// `applyTurnEvent` is deterministic and has no side effects beyond the turn it
// receives. It mutates that turn in place on purpose: inside a Svelte $state
// proxy, `stream.text += delta` only re-renders the text that changed, instead
// of rebuilding the whole conversation on every token.

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
  Savings,
  TurnEvent,
  TurnFailure,
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
}

/**
 * Where a PDF's check stands in a live turn: as its latest `pdf.check` says, or "interrupted"
 * when the turn ended while Claude was still checking it. The server sends no end for such a
 * check (it stops with the turn), and nobody read the PDF through it.
 */
export type PdfCheckStatus = PdfCheckState | 'interrupted';

/**
 * Claude's check of a PDF of the question for ChatGPT with the subscription, as the turn's
 * `pdf.check` events tell it (docs/adr/0009-adjunts.md). Pages as in PdfReading.
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
  /** Why pages remain unchecked (Catalan); null when none does. */
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
  };
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
      s.truncated = ev.truncated === true;
      s.finishReason = asText(ev.finish_reason);
      s.unchangedNote = s.unchanged ? asText(ev.unchanged_note) : null;
      s.pdfReading = asPdfReadings(ev.pdf_reading);
      break;
    }
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
      stopOpenStreams(turn, 'done');
      stopOpenChecks(turn);
      if (ev.cached) for (const s of turn.streams) s.cached = true;
      break;
    case 'turn.failed':
      turn.status = 'failed';
      turn.error = ev.error;
      // A turn that fails or is cancelled may have billed calls too: its total (N10).
      turn.usage = ev.usage ?? turn.usage;
      stopOpenStreams(turn, 'interrupted');
      stopOpenChecks(turn);
      break;
    case 'turn.cancelled':
      turn.status = 'cancelled';
      turn.usage = ev.usage ?? turn.usage;
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
  if (degraded) return `No s'ha pogut fer la síntesi: es mostra l'última resposta de ${who}.`;
  const chosen = turn.options?.debate.synthesizer;
  const missing = failed.find((agent) => agent !== shown.agent) ?? (chosen !== shown.agent ? chosen : undefined);
  if (!missing) return null;
  const lead = `${AGENT_LABEL[missing]} no ha pogut fer la síntesi`;
  if (shown.status === 'done') return `${lead}; l'ha feta ${who}.`;
  if (shown.status === 'streaming') return `${lead}; ara la fa ${who}.`;
  return `${lead}; ho ha intentat ${who}.`;
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
const incomplete = (): ErrorInfo => ({ kind: INCOMPLETE_KIND, message: 'Aquest torn no es va completar.' });

/**
 * Whether stored messages hold a finished turn, for turns stored without an outcome:
 * every finished debate stores a synthesis (real, degraded or replayed from the cache);
 * solo and duel turns store their answers.
 */
function storedTurnFinished(mode: TurnMode, streams: StreamView[]): boolean {
  if (!streams.length) return false;
  return mode !== 'debate' || streams.some((s) => s.kind === 'synthesis');
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
      s.truncated = meta.truncated === true;
      s.finishReason = asText(meta.finish_reason);
      s.unchangedNote = s.unchanged ? asText(meta.unchanged_note) : null;
      s.degraded = meta.degraded === true;
      s.pdfReading = asPdfReadings(meta.pdf_reading);
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
    // How the turn ended, as its live terminal event said (ADR 0007). Null: it never ended.
    const ended = asOutcome(qmeta.outcome);
    const calls = ended ? withFailures(streams, ended.failures, mode) : streams;
    const last = calls.at(-1);
    let status: TurnStatus = 'done';
    let error: ErrorInfo | null = null;
    if (ended) [status, error] = [ended.status, ended.error];
    else if (qmeta.outcome === null || !storedTurnFinished(mode, streams)) [status, error] = ['failed', incomplete()];

    const turn: TurnView = {
      key: `t${turnId}`,
      requestId: null,
      conversationId,
      turnId,
      mode,
      target,
      options: asOptions(qmeta.options),
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
