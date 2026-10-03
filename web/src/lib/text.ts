// Small UI helpers: conversation grouping, fuzzy matching, compact numbers.

import type { Messages } from './i18n/catalog';
import { i18n, textRecord } from './i18n/index.svelte';
import type { ConversationSummary, ProviderMode, TurnMode } from './protocol';

/** How long ago a conversation was last updated, as the sidebar groups them. */
export type DateGroup = 'today' | 'yesterday' | 'week' | 'older';
export const DATE_GROUPS: readonly DateGroup[] = ['today', 'yesterday', 'week', 'older'];

/** Each group's heading, in the language in force. */
export const DATE_GROUP_LABEL: Record<DateGroup, string> = textRecord(DATE_GROUPS, (group) => i18n.m.app.dateGroups[group]);

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function dateGroup(iso: string, now: Date = new Date()): DateGroup {
  const day = startOfDay(new Date(iso));
  const today = startOfDay(now);
  const diffDays = Math.round((today - day) / 86_400_000);
  if (diffDays <= 0) return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return 'week';
  return 'older';
}

export interface ConversationGroup {
  key: DateGroup;
  /** The group's heading, in the language in force when the groups were made. */
  label: string;
  items: ConversationSummary[];
}

/** Groups conversations (already sorted newest first) by last update. */
export function groupConversations(list: readonly ConversationSummary[], now: Date = new Date()): ConversationGroup[] {
  const map = new Map<DateGroup, ConversationSummary[]>();
  for (const c of list) {
    const g = dateGroup(c.updated_at, now);
    const items = map.get(g);
    if (items) items.push(c);
    else map.set(g, [c]);
  }
  return DATE_GROUPS.filter((g) => map.has(g)).map((key) => ({ key, label: DATE_GROUP_LABEL[key], items: map.get(key)! }));
}

/** Lowercase and strip accents so "revisio" matches "Revisió". */
export function normalize(s: string): string {
  return s.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
}

/**
 * Fuzzy score of `query` against `text` (higher is better, null = no match).
 * Characters must appear in order; contiguous runs and word starts score more.
 */
export function fuzzyScore(query: string, text: string): number | null {
  const q = normalize(query.trim());
  if (!q) return 0;
  const t = normalize(text);
  const direct = t.indexOf(q);
  if (direct >= 0) return 1000 - direct + (direct === 0 || /\W/.test(t[direct - 1] ?? '') ? 200 : 0);
  let score = 0;
  let ti = 0;
  let run = 0;
  for (const ch of q) {
    if (ch === ' ') continue;
    const found = t.indexOf(ch, ti);
    if (found < 0) return null;
    run = found === ti ? run + 1 : 0;
    score += 1 + run * 2 + (found === 0 || /\W/.test(t[found - 1] ?? '') ? 3 : 0);
    ti = found + 1;
  }
  return score;
}

export function fuzzyFilter<T>(items: readonly T[], query: string, key: (item: T) => string): T[] {
  if (!query.trim()) return [...items];
  return items
    .map((item, i) => ({ item, i, score: fuzzyScore(query, key(item)) }))
    .filter((x): x is { item: T; i: number; score: number } => x.score != null)
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .map((x) => x.item);
}

/** 950 -> "950", 3200 -> "3,2k", 1250000 -> "1,3M" (with the decimal mark of the language in force). */
export function formatK(n: number): string {
  const abs = Math.abs(n);
  const format = (value: number, digits: number) => new Intl.NumberFormat(i18n.tag, { maximumFractionDigits: digits }).format(value);
  if (abs < 1000) return format(n, 0);
  if (abs < 1_000_000) return `${format(n / 1000, 1)}k`;
  return `${format(n / 1_000_000, 1)}M`;
}

/** Rough prompt size estimate used across the app: characters / 4. */
export const estimateTokens = (text: string): number => Math.ceil(text.length / 4);

const MODES: readonly TurnMode[] = ['solo', 'duel', 'debate', 'refine'];

/** Each mode's name, in the language in force. */
export const MODE_LABEL: Record<TurnMode, string> = textRecord(MODES, (mode) => i18n.m.common.modes[mode].label);

/** What each mode does, in a sentence (the composer's tooltips, the empty state). */
export const MODE_DESCRIPTION: Record<TurnMode, string> = textRecord(MODES, (mode) => i18n.m.common.modes[mode].description);

export const PROVIDER_MODE_LABEL: Record<ProviderMode, string> = textRecord(['cli', 'api', 'fake'], (mode) => i18n.m.common.providerModes[mode]);

type WsErrorCode = keyof Messages['app']['wsErrors'];
const WS_ERROR_CODES: readonly WsErrorCode[] = ['invalid', 'busy', 'duplicate', 'unavailable', 'too_large', 'internal'];

/** Fallback texts for WebSocket `error` codes (the server message wins when present). */
export const WS_ERROR_TEXT: Record<WsErrorCode, string> = textRecord(WS_ERROR_CODES, (code) => i18n.m.app.wsErrors[code]);

export function wsErrorText(code: string | undefined, message: string | undefined): string {
  const known = code && Object.hasOwn(WS_ERROR_TEXT, code) ? WS_ERROR_TEXT[code as WsErrorCode] : undefined;
  return message?.trim() || known || i18n.m.app.wsErrorFallback;
}

/** Why a provider cut an answer off (`finish_reason`), for «Incomplete answer: …»: its text. */
const TRUNCATION_REASON: Record<string, 'outputLimit' | 'contentFilter' | 'interrupted'> = {
  max_tokens: 'outputLimit',
  max_output_tokens: 'outputLimit',
  content_filter: 'contentFilter',
  incomplete: 'interrupted',
  interrupted: 'interrupted',
};

/**
 * Why an answer is incomplete, in the language in force. The live `stream.completed`
 * only says `truncated`, so a missing reason gets a generic text; a provider code this
 * client does not know is kept (shortened) so it can be looked up.
 */
export function truncationReason(finishReason: string | null | undefined): string {
  const texts = i18n.m.app.truncation;
  const code = finishReason?.trim() ?? '';
  if (Object.hasOwn(TRUNCATION_REASON, code)) return texts[TRUNCATION_REASON[code]!];
  if (!code) return texts.cutOff;
  const chars = Array.from(code);
  return texts.cutOffBecause(chars.length > 40 ? `${chars.slice(0, 40).join('')}…` : code);
}
