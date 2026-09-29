// RuntimeSettings defaults and validation, mirroring the server ranges (docs/PROTOCOL.md).

import { validateModelId } from './models';
import { AGENTS, type Agent, type ModelPrice, type PdfInRevisions, type RuntimeSettings } from './protocol';

export const LIMITS = {
  rounds: { min: 0, max: 4 },
  consensus_threshold: { min: 50, max: 100 },
  compaction_threshold_tokens: { min: 1000, max: 100_000 },
  eur_per_usd: { min: 0.2, max: 5 },
  /** Monthly budgets and plan prices, in euros. */
  eur: { min: 0, max: 100_000 },
  /** USD per million tokens (storage/models.py MAX_PRICE_PER_MTOK). */
  price: { min: 0, max: 100_000 },
} as const;

export const DEFAULT_EUR_PER_USD = 0.86;

const perAgent = <T>(value: T): Record<Agent, T> => ({ claude: value, chatgpt: value });

export const DEFAULT_SETTINGS: RuntimeSettings = {
  // The built-in settings are revision 0: once the owner has saved settings (always
  // revision 1 or more on the server), a save based on these gets 409 (ADR 0006).
  revision: 0,
  default_mode: 'debate',
  default_target: 'claude',
  debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' },
  use_cache: true,
  compaction_threshold_tokens: 6000,
  models: perAgent(null),
  fast_models: perAgent(null),
  prices: {},
  fx: { mode: 'auto', eur_per_usd: DEFAULT_EUR_PER_USD },
  budgets_eur: perAgent(null),
  plans_eur: perAgent(null),
  pdf_in_revisions: 'text',
};

const isRevision = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

const isPdfInRevisions = (value: unknown): value is PdfInRevisions => value === 'full' || value === 'text';

/**
 * Deep copy of plain settings with every key present (an older server may omit
 * the newer ones). Pass `$state.snapshot(...)` when the source is reactive.
 */
export function normalizeSettings(s: Partial<RuntimeSettings>): RuntimeSettings {
  const d = DEFAULT_SETTINGS;
  const prices: Record<string, ModelPrice> = {};
  for (const [model, p] of Object.entries(s.prices ?? {})) prices[model] = { ...p };
  return {
    revision: isRevision(s.revision) ? s.revision : d.revision,
    default_mode: s.default_mode ?? d.default_mode,
    default_target: s.default_target ?? d.default_target,
    debate: { ...d.debate, ...s.debate },
    use_cache: s.use_cache ?? d.use_cache,
    compaction_threshold_tokens: s.compaction_threshold_tokens ?? d.compaction_threshold_tokens,
    models: { ...d.models, ...s.models },
    fast_models: { ...d.fast_models, ...s.fast_models },
    prices,
    fx: { ...d.fx, ...s.fx },
    budgets_eur: { ...d.budgets_eur, ...s.budgets_eur },
    plans_eur: { ...d.plans_eur, ...s.plans_eur },
    pdf_in_revisions: isPdfInRevisions(s.pdf_in_revisions) ? s.pdf_in_revisions : d.pdf_in_revisions,
  };
}

export type SettingsField =
  | 'rounds'
  | 'consensus_threshold'
  | 'compaction_threshold_tokens'
  | 'eur_per_usd'
  | `models.${Agent}`
  | `fast_models.${Agent}`
  | `budgets_eur.${Agent}`
  | `plans_eur.${Agent}`
  | `price:${string}`;
export type SettingsErrors = Partial<Record<SettingsField, string>>;

const ca = (n: number): string => n.toLocaleString('ca-ES');

const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function checkInt(value: unknown, min: number, max: number): string | null {
  if (!isNumber(value)) return 'Cal un número.';
  if (!Number.isInteger(value)) return 'Ha de ser un nombre enter.';
  if (value < min || value > max) return `Ha d'estar entre ${ca(min)} i ${ca(max)}.`;
  return null;
}

function checkNumber(value: unknown, min: number, max: number): string | null {
  if (!isNumber(value)) return 'Cal un número.';
  if (value < min || value > max) return `Ha d'estar entre ${ca(min)} i ${ca(max)}.`;
  return null;
}

export const INVALID_AMOUNT = 'Escriu un import vàlid, sense separador de milers (p. ex. 1000 o 50,5).';

/** Optional amount in euros: empty (null) means "not set"; NaN is text that is not an amount. */
function checkEuros(value: unknown): string | null {
  if (value == null || value === '') return null;
  if (typeof value === 'number' && Number.isNaN(value)) return INVALID_AMOUNT;
  if (!isNumber(value) || value < LIMITS.eur.min) return 'Ha de ser un import de 0 € o més.';
  if (value > LIMITS.eur.max) return `Com a màxim ${ca(LIMITS.eur.max)} €.`;
  return null;
}

/** Error for one price row, or null. */
export function validatePrice(model: string, price: ModelPrice): string | null {
  const id = validateModelId(model);
  if (id) return id;
  for (const key of ['input', 'output', 'cache_read', 'cache_write'] as const) {
    const v: unknown = price[key];
    if (!isNumber(v) || v < LIMITS.price.min || v > LIMITS.price.max) {
      return `Els preus han de ser nombres entre ${ca(LIMITS.price.min)} i ${ca(LIMITS.price.max)} (USD per milió de tokens).`;
    }
  }
  return null;
}

export function validateSettings(s: RuntimeSettings): SettingsErrors {
  const errors: SettingsErrors = {};
  const set = (field: SettingsField, error: string | null) => {
    if (error) errors[field] = error;
  };
  set('rounds', checkInt(s.debate.rounds, LIMITS.rounds.min, LIMITS.rounds.max));
  set(
    'consensus_threshold',
    checkInt(s.debate.consensus_threshold, LIMITS.consensus_threshold.min, LIMITS.consensus_threshold.max),
  );
  set(
    'compaction_threshold_tokens',
    checkInt(
      s.compaction_threshold_tokens,
      LIMITS.compaction_threshold_tokens.min,
      LIMITS.compaction_threshold_tokens.max,
    ),
  );
  set('eur_per_usd', checkNumber(s.fx.eur_per_usd, LIMITS.eur_per_usd.min, LIMITS.eur_per_usd.max));
  for (const agent of AGENTS) {
    const model = s.models[agent];
    const fast = s.fast_models[agent];
    set(`models.${agent}`, model == null ? null : validateModelId(model));
    set(`fast_models.${agent}`, fast == null ? null : validateModelId(fast));
    set(`budgets_eur.${agent}`, checkEuros(s.budgets_eur[agent]));
    set(`plans_eur.${agent}`, checkEuros(s.plans_eur[agent]));
  }
  for (const [model, price] of Object.entries(s.prices)) set(`price:${model}`, validatePrice(model, price));
  return errors;
}

// ------------------------------------------------------------ amounts typed as text

const AMOUNT = /^(-?)(\d*)(?:([.,])(\d*))?$/;

/**
 * Euro amount typed by the owner: `null` when empty, NaN when it is not an
 * amount (validation reports it instead of clearing the saved value). Both ','
 * (Catalan) and '.' are decimal separators; "1.000" is rejected because in
 * Catalan it means a thousand, not one.
 */
export function parseAmount(text: string): number | null {
  const s = text.trim();
  if (!s) return null;
  const m = AMOUNT.exec(s);
  if (!m) return Number.NaN;
  const [, sign = '', int = '', sep, frac = ''] = m;
  if (!int && !frac) return Number.NaN;
  if (sep === '.' && frac.length === 3 && /[1-9]/.test(int)) return Number.NaN;
  return Number(`${sign}${int || '0'}.${frac || '0'}`);
}

/** Text of a saved amount, with the Catalan decimal comma ("" when not set). */
export function formatAmount(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '';
  return value.toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: 20 }).replace('.', ',');
}

/**
 * Text an amount input should show for `value`: what the owner typed while it
 * still means that value (NaN included), else the value itself (e.g. the form was
 * reset with the saved settings).
 */
export function amountText(typed: string, value: number | null | undefined): string {
  return Object.is(parseAmount(typed), value ?? null) ? typed : formatAmount(value);
}

/** Empty number inputs bind to null (or undefined); the wire format wants null. */
export function cleanSettings(s: RuntimeSettings): RuntimeSettings {
  const out = normalizeSettings(s);
  for (const agent of AGENTS) {
    out.budgets_eur[agent] = isNumber(out.budgets_eur[agent]) ? out.budgets_eur[agent] : null;
    out.plans_eur[agent] = isNumber(out.plans_eur[agent]) ? out.plans_eur[agent] : null;
    out.models[agent] = out.models[agent]?.trim() || null;
    out.fast_models[agent] = out.fast_models[agent]?.trim() || null;
  }
  return out;
}

export const clamp = (n: number, min: number, max: number): number => Math.min(max, Math.max(min, n));
