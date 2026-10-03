import { afterEach, describe, expect, it } from 'vitest';
import { i18n } from './i18n/index.svelte';
import { DATE_GROUP_LABEL, dateGroup, formatK, fuzzyFilter, fuzzyScore, groupConversations, normalize, truncationReason, wsErrorText } from './text';
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
    expect(dateGroup(new Date(2026, 8, 27, 0, 5).toISOString(), now)).toBe('today');
    expect(dateGroup(new Date(2026, 8, 26, 23, 59).toISOString(), now)).toBe('yesterday');
    expect(dateGroup(new Date(2026, 8, 21, 12).toISOString(), now)).toBe('week');
    expect(dateGroup(new Date(2026, 8, 20, 12).toISOString(), now)).toBe('older');
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
    expect(groups.map((g) => g.key)).toEqual(['today', 'yesterday', 'older']);
    expect(groups[2]?.items.map((c) => c.id)).toEqual([3, 4]);
  });
});

describe('in English and Spanish', () => {
  afterEach(() => i18n.set('ca'));

  const list = [conv(1, 'a', new Date(2026, 8, 27, 14)), conv(2, 'b', new Date(2026, 8, 22, 10)), conv(3, 'c', new Date(2026, 7, 1))];

  it('the date groups have their headings in the language in force, under the same keys', () => {
    i18n.set('en');
    expect(groupConversations(list, now).map((g) => g.label)).toEqual(['Today', 'Last 7 days', 'Older']);
    expect(DATE_GROUP_LABEL.yesterday).toBe('Yesterday');
    i18n.set('es');
    expect(groupConversations(list, now).map((g) => g.label)).toEqual(['Hoy', 'Últimos 7 días', 'Anteriores']);
    expect(groupConversations(list, now).map((g) => g.key)).toEqual(['today', 'week', 'older']);
    expect(DATE_GROUP_LABEL.yesterday).toBe('Ayer');
  });

  it('wsErrorText falls back per code, then to a generic text', () => {
    i18n.set('en');
    expect(wsErrorText('busy', 'Too many turns at once.')).toBe('Too many turns at once.');
    expect(wsErrorText('too_large', '')).toBe('The message is too long.');
    expect(wsErrorText('new-code', '')).toBe('Server error.');
    expect(wsErrorText('constructor', undefined)).toBe('Server error.');
    i18n.set('es');
    expect(wsErrorText('duplicate', ' ')).toBe('Esta petición ya se había enviado.');
    expect(wsErrorText(undefined, undefined)).toBe('Error del servidor.');
  });

  it('truncationReason explains why an answer was cut off', () => {
    i18n.set('en');
    expect(truncationReason('max_tokens')).toBe('the output limit was reached');
    expect(truncationReason(null)).toBe('it was cut off before the end');
    expect(truncationReason('pause_turn')).toBe('it was cut off before the end (reason: pause_turn)');
    i18n.set('es');
    expect(truncationReason('content_filter')).toBe('cortada por el filtro de contenido');
    expect(truncationReason('pause_turn')).toBe('se ha cortado antes de acabar (motivo: pause_turn)');
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

describe('truncationReason', () => {
  it('explains in Catalan why an answer was cut off', () => {
    expect(truncationReason('max_tokens')).toBe("s'ha arribat al límit de sortida");
    expect(truncationReason('max_output_tokens')).toBe("s'ha arribat al límit de sortida");
    expect(truncationReason('content_filter')).toBe('tallada pel filtre de contingut');
    expect(truncationReason('interrupted')).toBe('interrompuda');
    expect(truncationReason('incomplete')).toBe('interrompuda');
  });

  it('falls back to a generic reason, keeping an unknown provider code', () => {
    // The live stream.completed only says `truncated`: the reason arrives with the stored meta.
    expect(truncationReason(null)).toBe("s'ha tallat abans d'acabar");
    expect(truncationReason('  ')).toBe("s'ha tallat abans d'acabar");
    expect(truncationReason('pause_turn')).toBe("s'ha tallat abans d'acabar (motiu: pause_turn)");
    expect(truncationReason('x'.repeat(200))).toBe(`s'ha tallat abans d'acabar (motiu: ${'x'.repeat(40)}…)`);
  });
});
