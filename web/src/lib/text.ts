// Small UI helpers: conversation grouping, fuzzy matching, compact numbers.

import type { ConversationSummary, ProviderMode, TurnMode } from './protocol';

export type DateGroup = 'Avui' | 'Ahir' | 'Últims 7 dies' | 'Anteriors';
export const DATE_GROUPS: readonly DateGroup[] = ['Avui', 'Ahir', 'Últims 7 dies', 'Anteriors'];

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function dateGroup(iso: string, now: Date = new Date()): DateGroup {
  const day = startOfDay(new Date(iso));
  const today = startOfDay(now);
  const diffDays = Math.round((today - day) / 86_400_000);
  if (diffDays <= 0) return 'Avui';
  if (diffDays === 1) return 'Ahir';
  if (diffDays < 7) return 'Últims 7 dies';
  return 'Anteriors';
}

export interface ConversationGroup {
  label: DateGroup;
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
  return DATE_GROUPS.filter((g) => map.has(g)).map((label) => ({ label, items: map.get(label)! }));
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

const oneDecimal = new Intl.NumberFormat('ca-ES', { maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat('ca-ES', { maximumFractionDigits: 0 });

/** 950 -> "950", 3200 -> "3,2k", 1250000 -> "1,3M". */
export function formatK(n: number): string {
  const abs = Math.abs(n);
  if (abs < 1000) return integer.format(n);
  if (abs < 1_000_000) return `${oneDecimal.format(n / 1000)}k`;
  return `${oneDecimal.format(n / 1_000_000)}M`;
}

/** Rough prompt size estimate used across the app: characters / 4. */
export const estimateTokens = (text: string): number => Math.ceil(text.length / 4);

export const MODE_LABEL: Record<TurnMode, string> = { solo: 'Solo', duel: 'Duel', debate: 'Consell', refine: 'Perfecciona' };

/** What each mode does, in a sentence (the composer's tooltips, the empty state). */
export const MODE_DESCRIPTION: Record<TurnMode, string> = {
  solo: 'Respon una sola IA. El més ràpid i econòmic.',
  duel: 'Claude i ChatGPT responen alhora, costat a costat.',
  debate: 'Responen, es critiquen per rondes i sintetitzen la millor resposta. Si arriben a un consens, paren abans.',
  refine: "Les dues IA milloren un sol document ronda rere ronda fins que l'aturis.",
};

export const PROVIDER_MODE_LABEL: Record<ProviderMode, string> = { cli: 'Subscripció', api: 'API', fake: 'Demo' };

/** Fallback texts for WebSocket `error` codes (the server message wins when present). */
export const WS_ERROR_TEXT: Record<string, string> = {
  invalid: 'El servidor ha rebutjat la petició perquè no és vàlida.',
  busy: "Ja hi ha massa torns en curs. Espera que n'acabi algun i torna-ho a provar.",
  duplicate: "Aquesta petició ja s'havia enviat.",
  unavailable: 'El proveïdor no està disponible ara mateix.',
  too_large: 'El missatge és massa llarg.',
  internal: 'Hi ha hagut un error intern al servidor.',
};

export function wsErrorText(code: string | undefined, message: string | undefined): string {
  return message?.trim() || (code ? WS_ERROR_TEXT[code] : undefined) || 'Error del servidor.';
}

/** Why a provider cut an answer off (`finish_reason`), for «Resposta incompleta: …». */
const TRUNCATION_REASON: Record<string, string> = {
  max_tokens: "s'ha arribat al límit de sortida",
  max_output_tokens: "s'ha arribat al límit de sortida",
  content_filter: 'tallada pel filtre de contingut',
  incomplete: 'interrompuda',
  interrupted: 'interrompuda',
};

/**
 * Why an answer is incomplete, in Catalan. The live `stream.completed` only says
 * `truncated`, so a missing reason gets a generic text; a provider code this
 * client does not know is kept (shortened) so it can be looked up.
 */
export function truncationReason(finishReason: string | null | undefined): string {
  const code = finishReason?.trim() ?? '';
  if (Object.hasOwn(TRUNCATION_REASON, code)) return TRUNCATION_REASON[code]!;
  const generic = "s'ha tallat abans d'acabar";
  if (!code) return generic;
  const chars = Array.from(code);
  return `${generic} (motiu: ${chars.length > 40 ? `${chars.slice(0, 40).join('')}…` : code})`;
}
