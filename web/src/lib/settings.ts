// RuntimeSettings validation, mirroring the server ranges (docs/PROTOCOL.md).

import type { RuntimeSettings } from './protocol';

export const LIMITS = {
  rounds: { min: 0, max: 4 },
  consensus_threshold: { min: 50, max: 100 },
  compaction_threshold_tokens: { min: 1000, max: 100_000 },
} as const;

export const DEFAULT_SETTINGS: RuntimeSettings = {
  default_mode: 'debate',
  default_target: 'claude',
  debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' },
  use_cache: true,
  compaction_threshold_tokens: 6000,
};

export type SettingsField = 'rounds' | 'consensus_threshold' | 'compaction_threshold_tokens';
export type SettingsErrors = Partial<Record<SettingsField, string>>;

function checkInt(value: unknown, min: number, max: number): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Cal un número.';
  if (!Number.isInteger(value)) return 'Ha de ser un nombre enter.';
  if (value < min || value > max) return `Ha d'estar entre ${min.toLocaleString('ca-ES')} i ${max.toLocaleString('ca-ES')}.`;
  return null;
}

export function validateSettings(s: RuntimeSettings): SettingsErrors {
  const errors: SettingsErrors = {};
  const rounds = checkInt(s.debate.rounds, LIMITS.rounds.min, LIMITS.rounds.max);
  if (rounds) errors.rounds = rounds;
  const threshold = checkInt(
    s.debate.consensus_threshold,
    LIMITS.consensus_threshold.min,
    LIMITS.consensus_threshold.max,
  );
  if (threshold) errors.consensus_threshold = threshold;
  const compaction = checkInt(
    s.compaction_threshold_tokens,
    LIMITS.compaction_threshold_tokens.min,
    LIMITS.compaction_threshold_tokens.max,
  );
  if (compaction) errors.compaction_threshold_tokens = compaction;
  return errors;
}

export const clamp = (n: number, min: number, max: number): number => Math.min(max, Math.max(min, n));
