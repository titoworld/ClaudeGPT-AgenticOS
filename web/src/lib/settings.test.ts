import { describe, expect, it } from 'vitest';
import type { RuntimeSettings } from './protocol';
import {
  amountText,
  cleanSettings,
  DEFAULT_SETTINGS,
  formatAmount,
  INVALID_AMOUNT,
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
