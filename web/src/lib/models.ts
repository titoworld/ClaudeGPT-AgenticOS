// Model selection helpers: id validation, turn.start overrides, short labels and
// the per-browser persistence of the composer choice.

import { formatK } from './text';
import { AGENTS, MODEL_ID_PATTERN, type Agent, type AgentModels, type ModelInfo } from './protocol';

/** Explicit model per agent for the next turns (absent = the default). */
export type ModelOverrides = Partial<Record<Agent, string>>;

export const MODEL_ID_MAX = 100;

/** Catalan error for a model id typed by the owner, or null when valid. */
export function validateModelId(raw: string): string | null {
  const id = raw.trim();
  if (!id) return "Escriu l'identificador del model.";
  if (id.length > MODEL_ID_MAX) return `Com a màxim ${MODEL_ID_MAX} caràcters.`;
  if (!MODEL_ID_PATTERN.test(id)) {
    return "Identificador no vàlid: sense espais, començant per lletra o xifra (s'admeten . _ - : / @ [ ]).";
  }
  return null;
}

export const isValidModelId = (id: string): boolean => validateModelId(id) === null;

/**
 * The `models` field of turn.start: only the agents that take part in the turn
 * and have an explicit, valid choice. Undefined when nothing is overridden.
 */
export function modelOverridesPayload(
  choices: Partial<Record<Agent, string | null | undefined>>,
  agents: readonly Agent[] = AGENTS,
): ModelOverrides | undefined {
  const out: ModelOverrides = {};
  for (const agent of agents) {
    const id = choices[agent]?.trim();
    if (id && isValidModelId(id)) out[agent] = id;
  }
  return Object.keys(out).length ? out : undefined;
}

/** Stored overrides (localStorage JSON), dropping anything malformed. */
export function parseOverrides(raw: string | null): ModelOverrides {
  if (!raw) return {};
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return {};
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return {};
  const record = data as Record<string, unknown>;
  const out: ModelOverrides = {};
  for (const agent of AGENTS) {
    const id = record[agent];
    if (typeof id === 'string' && isValidModelId(id)) out[agent] = id.trim();
  }
  return out;
}

const DATE_SUFFIX = /(-\d{8}|@\d{8}|-latest)$/;
const CONTEXT_TAG = /\[[^\]]*\]$/;

/** Compact label for tight spots: "claude-opus-5-5-20260101" -> "opus-5-5". */
export function shortModel(id: string): string {
  let s = id.trim();
  s = s.slice(s.lastIndexOf('/') + 1);
  s = s.replace(/^anthropic\./, '').replace(CONTEXT_TAG, '').replace(DATE_SUFFIX, '').replace(/^claude-/, '');
  return s || id.trim();
}

export function findModel(models: AgentModels | null | undefined, id: string | null | undefined): ModelInfo | null {
  if (!models || !id) return null;
  return models.models.find((m) => m.id === id) ?? null;
}

/** Option text in a model list: label, plus the id when it says something else. */
export function modelOptionLabel(m: ModelInfo): string {
  return m.label && m.label !== m.id ? `${m.label} · ${m.id}` : m.id;
}

/** One-line description of a listed model (description and context window). */
export function modelHint(m: ModelInfo | null): string {
  if (!m) return '';
  const parts = [m.description.trim()];
  if (m.context_window) parts.push(`${formatK(m.context_window)} tokens de context`);
  return parts.filter(Boolean).join(' · ');
}
