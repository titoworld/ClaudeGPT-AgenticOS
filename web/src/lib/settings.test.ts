import { describe, expect, it } from 'vitest';
import type { RuntimeSettings } from './protocol';
import {
  amountText,
  cleanSettings,
  DEFAULT_SETTINGS,
  formatAmount,
  INVALID_AMOUNT,
  LIMITS,
  normalizeSettings,
  parseAmount,
  validatePrice,
  validateSettings,
} from './settings';

const valid = (): RuntimeSettings => normalizeSettings(DEFAULT_SETTINGS);

describe('normalizeSettings', () => {
  it('fills the keys an older server does not send', () => {
    const old = {
      default_mode: 'solo',
      default_target: 'chatgpt',
      debate: { rounds: 1, consensus_threshold: 90, synthesizer: 'chatgpt' },
      use_cache: false,
      compaction_threshold_tokens: 4000,
    } as Partial<RuntimeSettings>;
    const s = normalizeSettings(old);
    expect(s.default_mode).toBe('solo');
    expect(s.debate.rounds).toBe(1);
    expect(s.models).toEqual({ claude: null, chatgpt: null });
    expect(s.fx).toEqual({ mode: 'auto', eur_per_usd: 0.86 });
    expect(s.prices).toEqual({});
    expect(s.budgets_eur).toEqual({ claude: null, chatgpt: null });
  });

  it('keeps the revision, and gives 0 for a missing or invalid one', () => {
    expect(normalizeSettings({ ...DEFAULT_SETTINGS, revision: 12 }).revision).toBe(12);
    expect(normalizeSettings({ default_mode: 'solo' }).revision).toBe(0);
    for (const revision of [-1, 1.5, Number.NaN, '3']) {
      expect(normalizeSettings({ revision } as unknown as Partial<RuntimeSettings>).revision).toBe(0);
    }
  });

  it('deep-copies nested objects', () => {
    const src = valid();
    src.prices['x-1'] = { input: 1, output: 2, cache_read: 0.1, cache_write: 1.25 };
    const copy = normalizeSettings(src);
    copy.prices['x-1']!.input = 9;
    copy.models.claude = 'opus';
    expect(src.prices['x-1']!.input).toBe(1);
    expect(src.models.claude).toBeNull();
  });
});

describe('validateSettings: new fields', () => {
  it('accepts the defaults', () => {
    expect(validateSettings(valid())).toEqual({});
  });

  it('checks the exchange rate range', () => {
    const s = valid();
    s.fx.eur_per_usd = 0.1;
    expect(validateSettings(s).eur_per_usd).toBe("Ha d'estar entre 0,2 i 5.");
    s.fx.eur_per_usd = Number.NaN;
    expect(validateSettings(s).eur_per_usd).toBe('Cal un número.');
    s.fx.eur_per_usd = 0.9123;
    expect(validateSettings(s).eur_per_usd).toBeUndefined();
  });

  it('checks model ids with MODEL_ID_PATTERN', () => {
    const s = valid();
    s.models.claude = 'opus';
    s.fast_models.chatgpt = 'gpt 6';
    const errors = validateSettings(s);
    expect(errors['models.claude']).toBeUndefined();
    expect(errors['fast_models.chatgpt']).toMatch(/^Identificador no vàlid/);
  });

  it('accepts empty budgets and plans and rejects negative amounts', () => {
    const s = valid();
    s.budgets_eur.claude = 50;
    s.plans_eur.chatgpt = 0;
    expect(validateSettings(s)).toEqual({});
    s.budgets_eur.chatgpt = -1;
    s.plans_eur.claude = 1e9;
    const errors = validateSettings(s);
    expect(errors['budgets_eur.chatgpt']).toBe('Ha de ser un import de 0 € o més.');
    expect(errors['plans_eur.claude']).toMatch(/^Com a màxim/);
  });

  it('validates price rows', () => {
    const ok = { input: 2, output: 10, cache_read: 0.2, cache_write: 2.5 };
    expect(validatePrice('gpt-6-sol', ok)).toBeNull();
    expect(validatePrice('bad id', ok)).toMatch(/^Identificador no vàlid/);
    expect(validatePrice('m', { ...ok, output: -1 })).toMatch(/^Els preus han de ser nombres/);
    expect(validatePrice('m', { ...ok, cache_read: Number.NaN })).toMatch(/^Els preus/);
    // Up to storage/models.py MAX_PRICE_PER_MTOK, like the server (F8).
    expect(validatePrice('m', { ...ok, input: 20_000 })).toBeNull();
    expect(validatePrice('m', { ...ok, input: 100_000 })).toBeNull();
    expect(validatePrice('m', { ...ok, input: 100_001 })).toMatch(/entre 0 i 100\.000 /);
    const s = valid();
    s.prices['m'] = { ...ok, input: -2 };
    expect(Object.keys(validateSettings(s))).toEqual(['price:m']);
  });
});

describe('cleanSettings', () => {
  it('sends null for empty inputs and trimmed model ids', () => {
    const s = valid();
    (s.budgets_eur as Record<string, unknown>).claude = undefined;
    (s.plans_eur as Record<string, unknown>).chatgpt = '';
    s.plans_eur.claude = 20;
    s.models.claude = '  opus ';
    s.fast_models.chatgpt = '   ';
    const out = cleanSettings(s);
    expect(out.budgets_eur).toEqual({ claude: null, chatgpt: null });
    expect(out.plans_eur).toEqual({ claude: 20, chatgpt: null });
    expect(out.models.claude).toBe('opus');
    expect(out.fast_models.chatgpt).toBeNull();
  });
});

describe('amounts typed in the budget and plan inputs (F2)', () => {
  it('reads the Catalan decimal comma and the point', () => {
    expect(parseAmount('50,5')).toBe(50.5);
    expect(parseAmount(' 22,99 ')).toBe(22.99);
    expect(parseAmount('50.5')).toBe(50.5);
    expect(parseAmount('0.125')).toBe(0.125);
    expect(parseAmount('1000')).toBe(1000);
    expect(parseAmount('12,')).toBe(12);
    expect(parseAmount(',5')).toBe(0.5);
    expect(parseAmount('-3')).toBe(-3);
  });

  it('empty means "not set"; anything else that is not an amount is NaN, never null', () => {
    expect(parseAmount('')).toBeNull();
    expect(parseAmount('   ')).toBeNull();
    for (const text of ['abc', '50 €', '1.000', '12.500', '1.000,50', '1,000.5', ',', '-', '5e3', '50,5,1']) {
      expect(parseAmount(text), text).toBeNaN();
    }
  });

  it('an amount that cannot be read is an error and keeps the saved value from being cleared', () => {
    const s = valid();
    s.budgets_eur.claude = 50;
    s.plans_eur.chatgpt = 23;
    // The owner edits both inputs: one with a decimal comma, one with a thousands point.
    s.budgets_eur.claude = parseAmount('50,5');
    s.plans_eur.chatgpt = parseAmount('1.000');
    const errors = validateSettings(s);
    expect(errors['budgets_eur.claude']).toBeUndefined();
    expect(errors['plans_eur.chatgpt']).toBe(INVALID_AMOUNT);
    // The drawer does not save while there are errors; once fixed, the value is sent.
    s.plans_eur.chatgpt = parseAmount('22,99');
    expect(validateSettings(s)).toEqual({});
    expect(cleanSettings(s).budgets_eur.claude).toBe(50.5);
    expect(cleanSettings(s).plans_eur.chatgpt).toBe(22.99);
  });

  it('shows saved amounts with a decimal comma and keeps what is being typed', () => {
    expect(formatAmount(50.5)).toBe('50,5');
    expect(formatAmount(1000)).toBe('1000');
    expect(formatAmount(null)).toBe('');
    for (const v of [0, 0.1, 22.99, 100_000, 1e-7]) expect(parseAmount(formatAmount(v))).toBe(v);

    expect(amountText('', 50)).toBe('50');
    expect(amountText('50,', 50)).toBe('50,');
    expect(amountText('50,50', 50.5)).toBe('50,50');
    expect(amountText('abc', Number.NaN)).toBe('abc');
    expect(amountText('', null)).toBe('');
    // The form was reset with other saved values.
    expect(amountText('50,5', 20)).toBe('20');
    expect(amountText('abc', null)).toBe('');
  });
});

describe('pdf_in_revisions: what the debate revisions get of an attached PDF', () => {
  it('is «only the text» by default and when an older server does not send it', () => {
    expect(DEFAULT_SETTINGS.pdf_in_revisions).toBe('text');
    expect(normalizeSettings({ default_mode: 'solo' }).pdf_in_revisions).toBe('text');
  });

  it('keeps «full» and «text», and gives the default for anything else', () => {
    expect(normalizeSettings({ ...DEFAULT_SETTINGS, pdf_in_revisions: 'full' }).pdf_in_revisions).toBe('full');
    expect(normalizeSettings({ ...DEFAULT_SETTINGS, pdf_in_revisions: 'text' }).pdf_in_revisions).toBe('text');
    for (const value of ['FULL', '', null, 1]) {
      expect(normalizeSettings({ ...DEFAULT_SETTINGS, pdf_in_revisions: value } as unknown as Partial<RuntimeSettings>).pdf_in_revisions).toBe('text');
    }
    expect(cleanSettings({ ...DEFAULT_SETTINGS, pdf_in_revisions: 'full' }).pdf_in_revisions).toBe('full');
  });
});

/** docs/PROTOCOL.md, read from disk: Vite serves nothing from outside web/. */
async function protocolDoc(): Promise<string> {
  const module: string = 'node:fs'; // not a literal: the web code has no Node types
  const fs = (await import(/* @vite-ignore */ module)) as { readFileSync(path: string, encoding: 'utf8'): string };
  const here: string = import.meta.url;
  return fs.readFileSync(decodeURIComponent(new URL('../../../docs/PROTOCOL.md', here).pathname), 'utf8');
}

describe('the refine defaults («Perfecciona», ADR 0010)', () => {
  it('has the ranges and defaults the protocol gives, as the server validates them', async () => {
    // tests/test_docs.py checks that these numbers are the server's.
    const doc = await protocolDoc();
    const range = (field: string) => {
      const m = new RegExp(`${field}: [^;]+;\\s+// ([\\d,.]+)–([\\d,.]+)`).exec(doc);
      return m ? [m[1]!, m[2]!].map((n) => Number(n.replaceAll('.', '').replace(',', '.'))) : null;
    };
    expect(range('max_rounds')).toEqual([LIMITS.refine_rounds.min, LIMITS.refine_rounds.max]);
    expect(range('budget_eur')).toEqual([LIMITS.refine_budget_eur.min, LIMITS.refine_budget_eur.max]);
    expect(range('convergence_threshold')).toEqual([LIMITS.refine_threshold.min, LIMITS.refine_threshold.max]);
    const words = /de cada versió: ([\d.]+)–([\d.]+)/.exec(doc);
    expect(words && [words[1], words[2]].map((n) => Number(n!.replaceAll('.', '')))).toEqual([
      LIMITS.refine_words.min,
      LIMITS.refine_words.max,
    ]);
    expect(DEFAULT_SETTINGS.refine).toEqual({
      max_rounds: 12,
      budget_eur: 3,
      max_words: null,
      stop_on_convergence: true,
      convergence_threshold: 90,
      editor: 'claude',
    });
    for (const [field, value] of [['max_rounds', 12], ['budget_eur', 3], ['convergence_threshold', 90]] as const) {
      expect(new RegExp(`${field}: [^;]+;\\s+// [^\\n]*per defecte (\\d+)`).exec(doc)?.[1]).toBe(String(value));
    }
  });

  it('fills them when an older server does not send them, and keeps the ones it sends', () => {
    expect(normalizeSettings({ default_mode: 'solo' }).refine).toEqual(DEFAULT_SETTINGS.refine);
    const saved = normalizeSettings({ ...DEFAULT_SETTINGS, refine: { ...DEFAULT_SETTINGS.refine, max_rounds: 20, max_words: 800 } });
    expect(saved.refine).toMatchObject({ max_rounds: 20, max_words: 800, budget_eur: 3 });
    // A deep copy, as the rest.
    saved.refine.max_rounds = 4;
    expect(DEFAULT_SETTINGS.refine.max_rounds).toBe(12);
  });

  it('never takes refine as the default mode: a refine turn runs until the owner stops it', () => {
    for (const mode of ['refine', 'consell', null, 3]) {
      const s = normalizeSettings({ ...DEFAULT_SETTINGS, default_mode: mode } as unknown as Partial<RuntimeSettings>);
      expect(s.default_mode).toBe('debate');
    }
    expect(normalizeSettings({ ...DEFAULT_SETTINGS, default_mode: 'duel' }).default_mode).toBe('duel');
  });

  it('validates every refine field with its range', () => {
    const s = valid();
    s.refine = { ...s.refine, max_rounds: 1, budget_eur: 0.05, max_words: 99, convergence_threshold: 101 };
    expect(validateSettings(s)).toEqual({
      'refine.max_rounds': "Ha d'estar entre 2 i 50.",
      'refine.budget_eur': "Ha d'estar entre 0,1 i 100.",
      'refine.max_words': "Ha d'estar entre 100 i 20.000.",
      'refine.convergence_threshold': "Ha d'estar entre 50 i 100.",
    });
    s.refine = { ...s.refine, max_rounds: 50, budget_eur: 100, max_words: 20_000, convergence_threshold: 50 };
    expect(validateSettings(s)).toEqual({});
    s.refine = { ...s.refine, max_rounds: 2, budget_eur: 0.1, max_words: null };
    expect(validateSettings(s)).toEqual({});
  });

  it('reports an amount it cannot read, an empty one, and a word limit that is not a whole number', () => {
    const s = valid();
    s.refine = { ...s.refine, budget_eur: parseAmount('1.000') as number, max_words: Number.NaN };
    expect(validateSettings(s)).toMatchObject({
      'refine.budget_eur': INVALID_AMOUNT,
      'refine.max_words': 'Cal un número.',
    });
    s.refine = { ...s.refine, budget_eur: parseAmount('') as unknown as number, max_words: 150.5 };
    expect(validateSettings(s)).toMatchObject({
      'refine.budget_eur': 'Cal un import.',
      'refine.max_words': 'Ha de ser un nombre enter.',
    });
    s.refine = { ...s.refine, budget_eur: parseAmount('2,5') as number, max_words: 400 };
    expect(validateSettings(s)).toEqual({});
    expect(cleanSettings(s).refine).toMatchObject({ budget_eur: 2.5, max_words: 400 });
  });
});
