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
  model?: string;
  usage?: Usage;
  latency_ms?: number;
  ttft_ms?: number | null;
  cached?: boolean;
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
  daily: { date: string; agent: Agent; input_tokens: number; output_tokens: number; cache_read_tokens: number }[];
  savings_daily: { date: string; kind: SavingKind; tokens: number }[];
  latency: Record<Agent, { p50_ms: number | null; p95_ms: number | null; ttft_p50_ms: number | null }>;
  turns: Record<TurnMode, number>;
  consensus: { debates: number; reached: number; avg_rounds: number | null };
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
      active_turns: { request_id: string; conversation_id: number; last_seq: number }[];
    }
  | { type: 'turn.unknown'; request_id: string }
  | { type: 'pong'; t: number }
  | { type: 'error'; message: string; request_id?: string };

/** WebSocket close codes used by the server. */
export const WS_CLOSE_UNAUTHORIZED = 4401;
export const WS_CLOSE_FORBIDDEN_ORIGIN = 4403;
