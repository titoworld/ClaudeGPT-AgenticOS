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

export interface Pricing {
  fx: FxRate;
  prices: (ModelPrice & { model: string; source: 'default' | 'custom' })[];
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

export interface RuntimeSettings {
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
}

export interface ConversationSummary {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  last_mode: TurnMode | null;
  message_count: number;
}

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
  model?: string;
  usage?: Usage;
  latency_ms?: number;
  ttft_ms?: number | null;
  cached?: boolean;
  cost_basis?: 'api' | 'equivalent';
  savings?: Savings;
  consensus?: Consensus;
  degraded?: boolean;
  critique?: string;
  agreement?: number | null;
  unchanged?: boolean;
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
    })
  | (TurnEventBase & { type: 'stream.failed'; stream_id: string; error: ErrorInfo })
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
  | (TurnEventBase & { type: 'turn.failed'; error: ErrorInfo })
  | (TurnEventBase & { type: 'turn.cancelled' });

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
