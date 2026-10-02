// Wire types shared with the backend. Source of truth: docs/PROTOCOL.md.
// Keep in sync with src/agentic_os/orchestrator/events.py and web/ routes.

export type Agent = 'claude' | 'chatgpt';
export const AGENTS: readonly Agent[] = ['claude', 'chatgpt'];
export type TurnMode = 'solo' | 'duel' | 'debate';
export type MessageKind = 'question' | 'answer' | 'revision' | 'synthesis';
export type ProviderMode = 'cli' | 'api' | 'fake';
export type Phase = 'answer' | 'revision' | 'synthesis' | 'compaction';
export type Section = 'text' | 'critique' | 'answer';
export type SavingKind = 'cache' | 'compaction' | 'early_stop' | 'unchanged';

/**
 * Tokens of one or more model calls. `input_tokens` is the input not served from the
 * provider's cache and `output_tokens` includes the reasoning (`reasoning_tokens` is a
 * part of it). A call processed input + cache read + cache write + output tokens
 * (ADR 0008, `processedTokens` in costs.ts).
 */
export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  cost_usd: number | null;
}

export interface UsageLimit {
  window: string; // "5h" | "7d" | ...
  used_percent: number | null;
  resets_at: string | null; // ISO 8601
  status: 'allowed' | 'warning' | 'rejected' | string;
}

export interface ModelInfo {
  id: string; // value sent to the provider (API id, CLI alias...)
  label: string;
  description: string;
  is_default: boolean;
  context_window: number | null;
}

export interface AgentModels {
  mode: ProviderMode;
  default_model: string;
  fast_model: string;
  models: ModelInfo[];
  live: boolean; // false when the provider could not be queried (fallback list)
}

export type ModelCatalog = Record<Agent, AgentModels>;

/** Any id matching this pattern is accepted, so new models work before they are listed. */
export const MODEL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/@[\]-]{0,99}$/;

export interface FxRate {
  eur_per_usd: number;
  as_of: string | null; // YYYY-MM-DD of the ECB rate
  source: 'ecb' | 'manual';
}

/** USD per million tokens, as vendors publish them. */
export interface ModelPrice {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

/** A row of the effective price table: the default prices with the owner's over them. */
export interface PriceRow extends ModelPrice {
  model: string;
  source: 'default' | 'custom';
  /** Family id the server matches models on (pricing.normalize_model; see models.ts normalizeModel). */
  key: string;
  /** The default price a custom row replaces (same key); null otherwise. */
  default: ModelPrice | null;
}

export interface Pricing {
  fx: FxRate;
  prices: PriceRow[];
}

export interface AgentSpend {
  api_usd: number; // real cost of api-mode calls
  equivalent_usd: number; // value of cli (subscription) calls at API prices
  unpriced_calls: number;
  budget_eur: number | null;
  budget_used: number | null; // ratio 0..1+ of the monthly API budget
  plan_eur: number | null;
  plan_value: number | null; // ratio 0..1+ of the subscription price
}

export interface MonthSpend {
  month: string; // YYYY-MM (UTC)
  fx: FxRate;
  by_agent: Record<Agent, AgentSpend>;
}

export interface ProviderStatus {
  agent: Agent;
  mode: ProviderMode;
  available: boolean;
  model: string;
  detail: string;
  limits: UsageLimit[];
}

export interface DebateOptions {
  rounds: number; // 0-4
  consensus_threshold: number; // 50-100
  synthesizer: Agent;
}

export interface TurnOptions {
  debate: DebateOptions;
  use_cache: boolean;
}

/**
 * What the revisions of a debate get of an attached PDF: "text" its extracted text (far
 * fewer tokens), "full" the document. The answers and the synthesis always get it whole.
 */
export type PdfInRevisions = 'full' | 'text';

export interface RuntimeSettings {
  /**
   * Version of the stored settings, +1 on every save. PUT /api/settings carries the
   * revision the edit was based on: 409 (SettingsConflict) if they changed since.
   */
  revision: number;
  default_mode: TurnMode;
  default_target: Agent;
  debate: DebateOptions;
  use_cache: boolean;
  compaction_threshold_tokens: number;
  models: Record<Agent, string | null>; // null = provider default
  fast_models: Record<Agent, string | null>;
  prices: Record<string, ModelPrice>; // owner overrides / new models
  fx: { mode: 'auto' | 'manual'; eur_per_usd: number };
  budgets_eur: Record<Agent, number | null>; // monthly API budget
  plans_eur: Record<Agent, number | null>; // monthly subscription price
  pdf_in_revisions: PdfInRevisions;
}

/** Body of the 409 answer to PUT /api/settings: the settings are the current ones. */
export interface SettingsConflict {
  detail: string;
  settings: RuntimeSettings;
}

export interface ConversationSummary {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  last_mode: TurnMode | null;
  message_count: number;
}

/**
 * GET /api/conversations?q=…: the titles that contain the text, ignoring case and
 * accents (literally: `%` and `_` are plain characters), paged with `limit` and
 * `before` like the whole list. The text is trimmed: a blank one is no search, and
 * one longer than this many characters gets 422.
 */
export const CONVERSATION_QUERY_MAX_LENGTH = 200;

// ---------------------------------------------------------------- attachments

export type AttachmentKind = 'image' | 'pdf' | 'text';

/**
 * The warnings of the server's analysis of an attached PDF's pages (docs/PROTOCOL.md
 * «Adjunts»), by kind: page numbers, from 1. Warnings, not verdicts.
 */
export interface PdfNotes {
  /** Pages without text: scans, or text drawn as an image. */
  no_text: number[];
  /** Pages whose extracted text is unreadable (a font without a character map). */
  garbled: number[];
  /** Pages that may hold text that is not visible: invisible, tiny or off the page. */
  hidden: number[];
}

/** A file attached to a question (docs/PROTOCOL.md «Adjunts»). */
export interface Attachment {
  id: number;
  /** Display name, cleaned by the server (no path, no control or invisible characters). */
  name: string;
  kind: AttachmentKind;
  /** image/png|jpeg|gif|webp, application/pdf or text/plain: sniffed from the content. */
  mime: string;
  size: number; // bytes
  pages: number | null; // PDF
  width: number | null; // images, in pixels
  height: number | null;
  sha256: string;
  created_at: string;
  /** The browser uploaded its thumbnail (GET /api/attachments/{id}/thumbnail). */
  has_thumbnail: boolean;
  /** Text files: always; PDFs: the server could extract their text. */
  text_available: boolean;
  /** Approximate input tokens of each call that gets it. */
  estimated_tokens: number;
  /**
   * An analysed PDF: the warnings of its pages. Null for images, text files and PDFs the
   * server could not analyse (questions stored before the analysis do not have the key).
   */
  pdf_notes: PdfNotes | null;
}

/**
 * Where Claude's check of a PDF for ChatGPT with the subscription is (docs/adr/0009-adjunts.md):
 * running, done with at least one page checked, or done with none (it failed, took too
 * long, the PDF was not analysed or there is no Claude).
 */
export type PdfCheckState = 'checking' | 'checked' | 'unchecked';

/**
 * How ChatGPT, when it cannot open PDFs (the subscription: Codex), read one PDF of the
 * question: the text the server extracted, with the pages Claude's check read for it.
 */
export interface PdfReading {
  attachment_id: number;
  name: string;
  /** Claude checked at least one page of it. */
  checked: boolean;
  /** Pages ChatGPT read, all or in part, as Claude read them. */
  claude_pages: number[];
  /** Pages with text that is not visible: ChatGPT did not get it. */
  hidden_pages: number[];
  /** Pages nobody checked: ChatGPT read their extracted text as it is. */
  unchecked_pages: number[];
  /** Why pages remain unchecked (Catalan); null when none does. */
  reason: string | null;
}

/** Attachments of one message. */
export const MAX_ATTACHMENTS = 5;
/** Raw bytes of all the attachments of one message. */
export const MAX_TURN_ATTACHMENT_BYTES = 20_000_000;
export const MAX_IMAGE_BYTES = 7_000_000;
/** Pixels per side of an image. */
export const MAX_IMAGE_SIDE = 8000;
/**
 * The models see an image with its long edge at most this long: the browser downscales
 * a larger one before uploading it (same fidelity for the models, far less upload).
 */
export const DOWNSCALE_EDGE = 2576;
export const MAX_PDF_BYTES = 20_000_000;
export const MAX_PDF_PAGES = 100;
export const MAX_TEXT_BYTES = 200_000;
/** A thumbnail: a PNG or WebP of at most these bytes and pixels per side. */
export const MAX_THUMBNAIL_BYTES = 100_000;
export const MAX_THUMBNAIL_SIDE = 512;
/** Extensions of the text files accepted (their content must be UTF-8 without NUL). */
export const TEXT_EXTENSIONS: readonly string[] = [
  'txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'yaml', 'yml', 'xml', 'html', 'htm', 'log', 'ini', 'toml', 'cfg',
  'py', 'js', 'ts', 'jsx', 'tsx', 'svelte', 'css', 'scss', 'sql', 'sh', 'bash', 'rs', 'go', 'java', 'kt', 'c', 'h',
  'cpp', 'hpp', 'cs', 'rb', 'php', 'swift', 'lua', 'r', 'pl',
];

export interface Message {
  id: number;
  turn_id: number;
  kind: MessageKind;
  content: string;
  agent: Agent | null;
  round: number;
  final: boolean;
  meta: MessageMeta;
  created_at: string;
}

export interface MessageMeta {
  mode?: TurnMode;
  target?: Agent;
  options?: TurnOptions;
  models?: Partial<Record<Agent, string>>;
  /** Question only: its attachments, in order, as they were when the turn started. */
  attachments?: Attachment[];
  /**
   * Question only: how the turn ended (ADR 0007). `null` from the moment the question is
   * stored until the engine writes it at the end of the turn, so a turn that never ended
   * (a crash, a restart) keeps `null`; absent on turns stored before it existed.
   */
  outcome?: TurnOutcome | null;
  model?: string;
  usage?: Usage;
  latency_ms?: number;
  ttft_ms?: number | null;
  cached?: boolean;
  cost_basis?: 'api' | 'equivalent';
  compaction_usage?: Usage; // question only: the turn's compaction summary call
  unstored_usage?: Usage; // last final message: billed calls stored on no message (failed/empty)
  /**
   * An answer served after a fallback (Claude API): the billed attempts other models
   * declined before it, each with its model and cost. They count in the turn's total,
   * never in this message's `usage` (tokens of different models are never summed).
   */
  declined?: { model: string; usage: Usage }[];
  savings?: Savings;
  consensus?: Consensus;
  degraded?: boolean;
  critique?: string;
  agreement?: number | null;
  unchanged?: boolean;
  /** Revision kept unchanged: the short note the model wrote after UNCHANGED, if any. */
  unchanged_note?: string;
  /** Present only when the answer was cut off: a usable partial answer, never a complete one. */
  truncated?: true;
  /**
   * Why the answer stopped: "max_tokens", "content_filter", "incomplete",
   * "interrupted" or a provider-specific string. Set when relevant (a truncated answer).
   */
  finish_reason?: string;
  /**
   * ChatGPT's messages (answers, revisions, synthesis) of a question with PDFs when it
   * cannot open them: how it read each one, in the order of the attachments.
   */
  pdf_reading?: PdfReading[];
  [key: string]: unknown;
}

export interface ConversationDetail extends ConversationSummary {
  summary: string | null;
  messages: Message[];
}

export interface Savings {
  cache: number;
  compaction: number;
  early_stop: number;
  unchanged: number;
  total: number;
  cost_usd: number | null; // approximate value of the saved tokens
}

export interface Stats {
  days: number;
  totals: {
    calls: number;
    errors: number;
    cost_usd: number;
    by_agent: Record<Agent, Usage & { calls: number }>;
  };
  savings: Savings;
  daily: {
    date: string; // UTC calendar day
    agent: Agent;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    cost_usd: number;
  }[];
  savings_daily: { date: string; kind: SavingKind; tokens: number }[];
  latency: Record<Agent, { p50_ms: number | null; p95_ms: number | null; ttft_p50_ms: number | null }>;
  turns: Record<TurnMode, number>;
  consensus: { debates: number; reached: number; avg_rounds: number | null };
  costs: {
    fx: FxRate;
    by_agent: Record<Agent, { api_usd: number; equivalent_usd: number; unpriced_calls: number }>;
  };
  month: MonthSpend;
}

export interface AuthState {
  authenticated: boolean;
  setup_required: boolean;
}

// ---------------------------------------------------------------- WebSocket

export interface ErrorInfo {
  kind: string;
  message: string;
}

export interface Consensus {
  reached: boolean;
  round: number;
  scores: Partial<Record<Agent, number>>;
}

/** A model call that failed during a turn (a `stream.failed`): `kind` and `message` are its error's. */
export interface TurnFailure {
  agent: Agent;
  kind: string;
  message: string;
  round: number;
}

/**
 * How a turn ended, written once on its question (`meta.outcome`) when it ends (ADR 0007):
 * what the terminal event said, so a reloaded turn shows what the live one did.
 */
export interface TurnOutcome {
  status: 'completed' | 'failed' | 'cancelled';
  /** Only when `status` is "failed": the error of `turn.failed`. */
  error?: ErrorInfo;
  /** The stream failures of the turn, in the order they happened (possibly none). */
  failures: TurnFailure[];
  /**
   * The turn's total: every billed call (compaction, failed calls, declined attempts and
   * calls stored on no message included), the `usage` of the terminal event.
   */
  usage: Usage;
  savings: Savings;
  consensus: Consensus | null;
  final_message_ids: number[];
  cached: boolean;
}

export type ClientMessage =
  | {
      type: 'turn.start';
      request_id: string;
      text: string;
      mode: TurnMode;
      target?: Agent;
      conversation_id: number | null;
      options?: TurnOptions;
      models?: Partial<Record<Agent, string>>;
      /** Ids of uploaded attachments, in order: at most MAX_ATTACHMENTS, each once. */
      attachments?: number[];
    }
  | { type: 'turn.cancel'; request_id: string }
  | { type: 'turn.subscribe'; request_id: string; after_seq: number }
  | { type: 'ping'; t: number };

interface TurnEventBase {
  request_id: string;
  seq: number;
}

export type TurnEvent =
  | (TurnEventBase & {
      type: 'turn.started';
      conversation_id: number;
      turn_id: number;
      mode: TurnMode;
      new_conversation: boolean;
    })
  | (TurnEventBase & { type: 'phase'; phase: Phase; round: number })
  | (TurnEventBase & {
      /**
       * Claude's check of a PDF of the question for ChatGPT with the subscription: "checking"
       * when Claude starts checking it, then "checked" or "unchecked". A PDF no call checks
       * gets only its end: a check an earlier turn stored, a PDF the server could not analyse,
       * a turn without Claude, or the time running out before its check started. A turn that
       * is cancelled or fails while Claude checks a PDF sends no end for it. Its pages as in
       * PdfReading.
       */
      type: 'pdf.check';
      attachment_id: number;
      name: string;
      state: PdfCheckState;
      claude_pages: number[];
      hidden_pages: number[];
      unchecked_pages: number[];
      /** An earlier turn's check, stored: no call was made for it in this turn. */
      reused: boolean;
      /** What this turn's calls for the PDF billed, with the cost (null while checking, or reused). */
      usage: Usage | null;
      /** Why pages remain unchecked (Catalan); null when none does. */
      reason: string | null;
    })
  | (TurnEventBase & {
      type: 'stream.started';
      stream_id: string;
      agent: Agent;
      kind: Exclude<MessageKind, 'question'>;
      round: number;
      model: string;
    })
  | (TurnEventBase & { type: 'stream.delta'; stream_id: string; section: Section; text: string })
  | (TurnEventBase & {
      type: 'stream.completed';
      stream_id: string;
      message_id: number;
      usage: Usage;
      latency_ms: number;
      ttft_ms: number | null;
      agreement: number | null;
      unchanged: boolean;
      cost_basis?: 'api' | 'equivalent' | null;
      /** True when the answer was cut off (omitted when false). */
      truncated?: boolean;
      /** Why a truncated answer stopped (e.g. "max_tokens"), as the stored `meta.finish_reason`. */
      finish_reason?: string;
      /** Unchanged revision: the model's short note, as the stored `meta.unchanged_note`. */
      unchanged_note?: string;
      /** ChatGPT when it cannot open PDFs: how it read each one, as the stored `meta.pdf_reading`. */
      pdf_reading?: PdfReading[];
    })
  | (TurnEventBase & {
      type: 'stream.failed';
      stream_id: string;
      error: ErrorInfo;
      /** What the failed call was billed, when known (a refusal, an empty reply...). */
      usage?: Usage;
    })
  | (TurnEventBase & {
      type: 'turn.completed';
      conversation_id: number;
      turn_id: number;
      final_message_ids: number[];
      usage: Usage;
      savings: Savings;
      consensus: Consensus | null;
      cached: boolean;
    })
  | (TurnEventBase & {
      type: 'turn.failed';
      error: ErrorInfo;
      /** The turn's total so far (zero when nothing was billed), as the stored `outcome.usage`. */
      usage: Usage;
    })
  | (TurnEventBase & {
      type: 'turn.cancelled';
      /** The turn's total so far (zero when nothing was billed), as the stored `outcome.usage`. */
      usage: Usage;
    });

export type ServerMessage =
  | TurnEvent
  | {
      type: 'hello';
      version: string;
      providers: ProviderStatus[];
      fx: FxRate;
      active_turns: { request_id: string; conversation_id: number | null; last_seq: number }[];
    }
  | { type: 'turn.unknown'; request_id: string }
  | { type: 'pong'; t: number }
  | { type: 'error'; code?: string; message: string; request_id?: string };

/** WebSocket close codes used by the server. */
export const WS_CLOSE_UNAUTHORIZED = 4401;
export const WS_CLOSE_FORBIDDEN_ORIGIN = 4403;

// ---------------------------------------------------------------- sessions

/**
 * Request header (value "1") of the REST requests the app makes by itself, not because
 * the owner did something: refreshes after `hello` or a reconnection, periodic
 * refreshes, retries. The server checks the session without counting them as activity,
 * like a WebSocket ping, so an unused tab does not keep the session alive. Every
 * POST /api/auth/logout carries it too: a logout that fails must not extend the session.
 */
export const BACKGROUND_HEADER = 'X-AOS-Background';
