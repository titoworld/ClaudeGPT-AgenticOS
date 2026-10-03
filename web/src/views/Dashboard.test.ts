// The usage dashboard counts every token the calls processed, with the provider's cache in
// view (audit A7, ADR 0008): cache reads and writes are billed tokens too, and the share of
// savings compares tokens of the same kind.
import { flushSync } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { i18n, LOCALE_KEY } from '../lib/i18n/index.svelte';
import type { Stats } from '../lib/protocol';
import { cleanup, render, textOf } from '../lib/test-render';

const data = vi.hoisted(() => ({ stats: null as unknown }));

vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status = 500;
  }
  return { ApiError, api: { stats: async () => data.stats, providers: async () => [] } };
});
vi.mock('../lib/app.svelte', () => ({ app: { settingsOpen: false } }));

import Dashboard from './Dashboard.svelte';

const TODAY = new Date().toISOString().slice(0, 10);
const FX = { eur_per_usd: 0.86, as_of: null, source: 'manual' as const };
const NO_SPEND = { api_usd: 0, equivalent_usd: 0, unpriced_calls: 0, budget_eur: null, budget_used: null, plan_eur: null, plan_value: null };

/** A period where the providers' cache did most of the work (the audit's numbers). */
function cacheHeavy(): Stats {
  const claude = { input_tokens: 12, output_tokens: 400, cache_read_tokens: 40_000, cache_write_tokens: 80_000, reasoning_tokens: 0 };
  const chatgpt = { input_tokens: 6, output_tokens: 200, cache_read_tokens: 20_000, cache_write_tokens: 40_000, reasoning_tokens: 0 };
  return {
    days: 30,
    totals: {
      calls: 6,
      errors: 0,
      cost_usd: 0.79509,
      by_agent: { claude: { ...claude, cost_usd: 0.53006, calls: 4 }, chatgpt: { ...chatgpt, cost_usd: 0.26503, calls: 2 } },
    },
    savings: { cache: 30_103, compaction: 0, early_stop: 0, unchanged: 0, total: 30_103, cost_usd: 0.13 },
    daily: [
      { date: TODAY, agent: 'claude', ...claude, cost_usd: 0.53006 },
      { date: TODAY, agent: 'chatgpt', ...chatgpt, cost_usd: 0.26503 },
    ],
    savings_daily: [{ date: TODAY, kind: 'cache', tokens: 30_103 }],
    latency: {
      claude: { p50_ms: null, p95_ms: null, ttft_p50_ms: null },
      chatgpt: { p50_ms: null, p95_ms: null, ttft_p50_ms: null },
    },
    turns: { solo: 1, duel: 1, debate: 0, refine: 0 },
    consensus: { debates: 0, reached: 0, avg_rounds: null },
    costs: { fx: FX, by_agent: { claude: { api_usd: 0.53006, equivalent_usd: 0, unpriced_calls: 0 }, chatgpt: { api_usd: 0.26503, equivalent_usd: 0, unpriced_calls: 0 } } },
    month: { month: TODAY.slice(0, 7), fx: FX, by_agent: { claude: NO_SPEND, chatgpt: NO_SPEND } },
  };
}

beforeEach(() => {
  data.stats = cacheHeavy();
  // Charts measure their width (bind:clientWidth); jsdom has no ResizeObserver.
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** The dashboard once its statistics have loaded. */
async function loaded(): Promise<HTMLElement> {
  const el = render(Dashboard, {});
  await vi.waitFor(() => {
    flushSync();
    expect(el.querySelector('.kpis')).not.toBeNull();
  });
  return el;
}

// Intl uses a no-break space before "%" and "k" in Catalan.
const plain = (s: string) => s.replace(/ | /g, ' ');

function tile(el: HTMLElement, title: string): Element {
  const found = [...el.querySelectorAll('.kpis .tile')].find((t) => textOf(t.querySelector('h3')) === title);
  expect(found, `tile «${title}»`).toBeDefined();
  return found!;
}

describe('Dashboard: tokens processed, cache included (A7)', () => {
  it('counts input, cache reads, cache writes and output, and shows each part', async () => {
    const el = await loaded();
    const processed = tile(el, 'Tokens processats');
    expect(plain(textOf(processed.querySelector('.value')))).toBe('180,6 k');
    const parts = [...processed.querySelectorAll('ul[aria-label="Tokens processats per tipus"] li')].map((li) => plain(textOf(li)));
    expect(parts).toEqual(['Entrada 18', 'Lectura de memòria cau 60 k', 'Escriptura a memòria cau 120 k', 'Sortida 600']);
    expect(plain(textOf(processed))).toContain('Claude 120,4 k');
    expect(plain(textOf(processed))).toContain('ChatGPT 60,2 k');
  });

  it('compares the savings with the processed tokens', async () => {
    const el = await loaded();
    // 30.103 saved of 30.103 + 180.618: the cache is on both sides.
    expect(plain(textOf(tile(el, 'Tokens estalviats')))).toContain('14 %');
  });

  it('draws the processed tokens of each day', async () => {
    const el = await loaded();
    const card = [...el.querySelectorAll('figure.card')].find((f) => textOf(f.querySelector('h3')) === 'Tokens per dia')!;
    expect(card).toBeDefined();
    (card.querySelector('button.toggle') as HTMLButtonElement).click();
    flushSync();
    const row = [...card.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('th, td')].map((c) => plain(textOf(c))));
    expect(row).toHaveLength(1);
    expect(row[0]!.slice(1)).toEqual(['120.412', '60.206', '180.618']);
  });

  it("says what the providers' cache read and wrote in the period", async () => {
    const el = await loaded();
    const note = [...el.querySelectorAll('.techniques li')].find((li) => textOf(li.querySelector('h4')) === 'Memòria cau del proveïdor')!;
    expect(plain(textOf(note))).toContain('60 k');
    expect(plain(textOf(note))).toContain('120 k');
  });
});

describe('Dashboard: the turns of each mode', () => {
  it('counts the «Perfecciona» turns with the others', async () => {
    data.stats = { ...cacheHeavy(), turns: { solo: 1, duel: 1, debate: 0, refine: 2 } };
    const el = await loaded();
    const turns = tile(el, 'Torns');
    expect(textOf(turns.querySelector('.value'))).toBe('4');
    expect(textOf(turns.querySelector('.sub'))).toBe('Solo 1 · Duel 1 · Consell 0 · Perfecciona 2');
  });

  it('reads 0 from a server that does not count them yet', async () => {
    data.stats = { ...cacheHeavy(), turns: { solo: 1, duel: 1, debate: 0 } as Stats['turns'] };
    const el = await loaded();
    expect(textOf(tile(el, 'Torns').querySelector('.sub'))).toBe('Solo 1 · Duel 1 · Consell 0 · Perfecciona 0');
  });
});

describe('Dashboard in English and Spanish (ADR 0011)', () => {
  afterEach(() => {
    i18n.set('ca');
    localStorage.removeItem(LOCALE_KEY);
    // The charts' width, given by the test that draws them.
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
  });

  /** The chart card titled `title`. */
  const card = (el: HTMLElement, title: string) =>
    [...el.querySelectorAll('figure.card')].find((f) => textOf(f.querySelector('h3')) === title) ?? null;

  it('shows its KPIs in English, and repaints them in another language', async () => {
    i18n.set('en');
    const el = await loaded();
    expect(textOf(el.querySelector('h2'))).toBe('Usage and savings');
    const processed = tile(el, 'Tokens processed');
    expect(textOf(processed.querySelector('.value'))).toMatch(/^180\.6k$/i);
    const parts = [...processed.querySelectorAll('ul[aria-label="Tokens processed by kind"] li span')].map((s) => textOf(s));
    expect(parts).toEqual(['Input', 'Cache reads', 'Cache writes', 'Output']);
    expect(textOf(processed.querySelector('.sub'))).toBe('6 calls · 0 errors');
    expect(textOf(tile(el, 'Turns').querySelector('.sub'))).toBe('Solo 1 · Duel 1 · Council 0 · Refine 0');
    expect(textOf(tile(el, 'Tokens saved'))).toContain('14% fewer tokens than would have been processed');
    expect(textOf(el.querySelector('.money'))).toContain('Amounts in euros · 1 USD = €0.86 · manual rate');

    i18n.set('es');
    flushSync();
    expect(textOf(el.querySelector('h2'))).toBe('Uso y ahorro');
    expect(textOf(tile(el, 'Turnos').querySelector('.sub'))).toBe('Solo 1 · Duelo 1 · Consejo 0 · Perfecciona 0');
  });

  it('writes the days of a chart, on its axis and in its table, in Spanish', async () => {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 640 });
    i18n.set('es');
    const el = await loaded();
    const tokens = card(el, 'Tokens por día')!;
    expect(tokens).not.toBeNull();
    const axis = [...tokens.querySelectorAll('svg text')].map((t) => t.textContent ?? '').filter((t) => /^\d{1,2} \D+$/.test(t));
    expect(axis.length).toBeGreaterThan(1);
    for (const day of axis) expect(day).toMatch(/^\d{1,2} (ene|feb|mar|abr|may|jun|jul|ago|sept|oct|nov|dic)$/);

    tokens.querySelector<HTMLButtonElement>('button.toggle')!.click();
    flushSync();
    expect(textOf(tokens.querySelector('button.toggle'))).toBe('Ver gráfico');
    expect([...tokens.querySelectorAll('thead th')].map((th) => textOf(th))).toEqual(['Día', 'Claude', 'ChatGPT', 'Total']);
    const row = [...tokens.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('th, td')].map((c) => textOf(c)));
    expect(row).toHaveLength(1);
    expect(row[0]![0]).toMatch(/^(lun|mar|mié|jue|vie|sáb|dom), \d{1,2} de [a-z]+$/);
    expect(row[0]!.slice(1)).toEqual(['120.412', '60.206', '180.618']);
  });

  it('writes the days of a chart on its axis in English', async () => {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 640 });
    i18n.set('en');
    const el = await loaded();
    const savings = card(el, 'Savings per day')!;
    const axis = [...savings.querySelectorAll('svg text')].map((t) => t.textContent ?? '').filter((t) => /^\d{1,2} \D+$/.test(t));
    expect(axis.length).toBeGreaterThan(1);
    for (const day of axis) expect(day).toMatch(/^\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)$/);
    expect(savings.querySelector('rect.hit')?.getAttribute('aria-label')).toMatch(
      /^\w{3} \d{1,2} \w+: Cache 0, Compaction 0, Stop by consensus 0, No changes 0, total 0$/,
    );
  });
});
