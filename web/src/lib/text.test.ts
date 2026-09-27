import { describe, expect, it } from 'vitest';
import { dateGroup, formatK, fuzzyFilter, fuzzyScore, groupConversations, normalize, wsErrorText } from './text';
import { DEFAULT_SETTINGS, validateSettings } from './settings';
import type { ConversationSummary } from './protocol';

const now = new Date(2026, 8, 27, 15, 0, 0); // local time

const conv = (id: number, title: string, updated: Date): ConversationSummary => ({
  id,
  title,
  created_at: updated.toISOString(),
  updated_at: updated.toISOString(),
  last_mode: 'debate',
  message_count: 2,
});

describe('dateGroup / groupConversations', () => {
  it('groups by local day', () => {
    expect(dateGroup(new Date(2026, 8, 27, 0, 5).toISOString(), now)).toBe('Avui');
    expect(dateGroup(new Date(2026, 8, 26, 23, 59).toISOString(), now)).toBe('Ahir');
    expect(dateGroup(new Date(2026, 8, 21, 12).toISOString(), now)).toBe('Últims 7 dies');
    expect(dateGroup(new Date(2026, 8, 20, 12).toISOString(), now)).toBe('Anteriors');
  });

  it('keeps group order and item order', () => {
    const groups = groupConversations(
      [
        conv(1, 'a', new Date(2026, 8, 27, 14)),
        conv(2, 'b', new Date(2026, 8, 26, 10)),
        conv(3, 'c', new Date(2026, 7, 1)),
        conv(4, 'd', new Date(2026, 7, 1)),
      ],
      now,
    );
    expect(groups.map((g) => g.label)).toEqual(['Avui', 'Ahir', 'Anteriors']);
    expect(groups[2]?.items.map((c) => c.id)).toEqual([3, 4]);
  });
});

describe('fuzzy search', () => {
  it('ignores accents and case', () => {
    expect(normalize('Revisió Àgil')).toBe('revisio agil');
    expect(fuzzyScore('revisio', 'Revisió del pla')).not.toBeNull();
  });

  it('matches characters in order and rejects others', () => {
    expect(fuzzyScore('tcp', 'Diferència TCP i UDP')).not.toBeNull();
    expect(fuzzyScore('dtu', 'Diferència TCP i UDP')).not.toBeNull();
    expect(fuzzyScore('xyz', 'Diferència TCP i UDP')).toBeNull();
  });

  it('ranks direct and word-start matches first', () => {
    const items = ['Pla de migració', 'Plantilla', 'Aplicació web'];
    expect(fuzzyFilter(items, 'pla', (s) => s)[0]).toBe('Pla de migració');
    expect(fuzzyFilter(items, '', (s) => s)).toEqual(items);
  });
});

describe('formatK', () => {
  it('formats compact token counts in Catalan', () => {
    expect(formatK(950)).toBe('950');
    expect(formatK(3200)).toBe('3,2k');
    expect(formatK(12_000)).toBe('12k');
    expect(formatK(1_250_000)).toBe('1,3M');
  });
});

describe('validateSettings', () => {
  it('accepts the defaults', () => {
    expect(validateSettings(DEFAULT_SETTINGS)).toEqual({});
  });

  it('rejects out-of-range values like the server', () => {
    const errors = validateSettings({
      ...DEFAULT_SETTINGS,
      debate: { rounds: 5, consensus_threshold: 40, synthesizer: 'claude' },
      compaction_threshold_tokens: 1.5,
    });
    expect(Object.keys(errors).sort()).toEqual(['compaction_threshold_tokens', 'consensus_threshold', 'rounds']);
  });
});

describe('wsErrorText', () => {
  it('prefers the server message and falls back per code', () => {
    expect(wsErrorText('busy', 'Massa torns alhora.')).toBe('Massa torns alhora.');
    expect(wsErrorText('busy', '')).toMatch(/^Ja hi ha massa torns en curs/);
    expect(wsErrorText('too_large', '  ')).toBe('El missatge és massa llarg.');
    expect(wsErrorText('nou-codi', '')).toBe('Error del servidor.');
    expect(wsErrorText(undefined, undefined)).toBe('Error del servidor.');
  });
});
