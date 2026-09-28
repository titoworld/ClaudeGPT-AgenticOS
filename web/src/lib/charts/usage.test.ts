import { describe, expect, it } from 'vitest';
import { LIMIT_WARN_PERCENT } from '../limits';
import type { ProviderStatus, Stats, Usage } from '../protocol';
import {
  AGENT_SERIES,
  dailyCost,
  dailySavings,
  dailyTokens,
  formatCompact,
  isEmpty,
  kpis,
  latencyData,
  limitTone,
  plural,
  providerCards,
  resetLabel,
  SAVING_SERIES,
  turnsData,
  windowLabel,
} from './usage';

const usage = (input: number, output: number, cost: number | null = null, cacheRead = 0, cacheWrite = 0): Usage & { calls: number } => ({
  input_tokens: input,
  output_tokens: output,
  cache_read_tokens: cacheRead,
  cache_write_tokens: cacheWrite,
  reasoning_tokens: 0,
  cost_usd: cost,
  calls: 3,
});

const FX = { eur_per_usd: 0.8, as_of: '2026-09-25', source: 'ecb' as const };
const noSpend = { api_usd: 0, equivalent_usd: 0, unpriced_calls: 0, budget_eur: null, budget_used: null, plan_eur: null, plan_value: null };

function stats(overrides: Partial<Stats> = {}): Stats {
  return {
    days: 7,
    totals: { calls: 6, errors: 1, cost_usd: 0, by_agent: { claude: usage(1000, 500, null, 200, 300), chatgpt: usage(700, 300, null, 50) } },
    savings: { cache: 400, compaction: 100, early_stop: 300, unchanged: 200, total: 1000, cost_usd: 0.5 },
    daily: [
      { date: '2026-09-27', agent: 'claude', input_tokens: 600, output_tokens: 200, cache_read_tokens: 100, cache_write_tokens: 40, cost_usd: 0.25 },
      { date: '2026-09-27', agent: 'chatgpt', input_tokens: 700, output_tokens: 300, cache_read_tokens: 50, cache_write_tokens: 0, cost_usd: 0.1 },
      { date: '2026-09-25', agent: 'claude', input_tokens: 400, output_tokens: 300, cache_read_tokens: 100, cache_write_tokens: 0, cost_usd: 0.05 },
      { date: '2026-08-01', agent: 'claude', input_tokens: 9, output_tokens: 9, cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 9 }, // outside range
    ],
    savings_daily: [
      { date: '2026-09-27', kind: 'cache', tokens: 400 },
      { date: '2026-09-26', kind: 'early_stop', tokens: 300 },
      { date: '2026-09-26', kind: 'early_stop', tokens: 50 },
    ],
    latency: {
      claude: { p50_ms: 6200, p95_ms: 14800, ttft_p50_ms: 1150 },
      chatgpt: { p50_ms: null, p95_ms: null, ttft_p50_ms: null },
    },
    turns: { solo: 2, duel: 1, debate: 4 },
    consensus: { debates: 4, reached: 3, avg_rounds: 1.5 },
    costs: {
      fx: FX,
      by_agent: {
        claude: { api_usd: 0, equivalent_usd: 0.3, unpriced_calls: 0 },
        chatgpt: { api_usd: 0.1, equivalent_usd: 0, unpriced_calls: 2 },
      },
    },
    month: { month: '2026-09', fx: FX, by_agent: { claude: noSpend, chatgpt: noSpend } },
    ...overrides,
  };
}

describe('daily series', () => {
  it('fills every day of the range with the tokens each agent processed (A7)', () => {
    const d = dailyTokens(stats(), '2026-09-27');
    expect(d).toHaveLength(7);
    expect(d[0]!.key).toBe('2026-09-21');
    // Input, cache reads, cache writes and output.
    expect(d.at(-1)).toMatchObject({ key: '2026-09-27', label: '27 set.', values: { claude: 940, chatgpt: 1050 } });
    expect(d.find((x) => x.key === '2026-09-25')!.values).toEqual({ claude: 800, chatgpt: 0 });
    expect(d.find((x) => x.key === '2026-09-26')!.values).toEqual({ claude: 0, chatgpt: 0 });
    // Out-of-range rows are ignored rather than stretching the axis.
    expect(d.some((x) => x.key === '2026-08-01')).toBe(false);
  });

  it('sums savings per day and kind', () => {
    const d = dailySavings(stats(), '2026-09-27');
    expect(d).toHaveLength(7);
    expect(d.find((x) => x.key === '2026-09-26')!.values).toEqual({ cache: 0, compaction: 0, early_stop: 350, unchanged: 0 });
    expect(d.at(-1)!.values.cache).toBe(400);
  });

  it('converts the daily cost of each agent to euros', () => {
    const d = dailyCost(stats(), 0.8, '2026-09-27');
    expect(d).toHaveLength(7);
    expect(d.at(-1)!.values.claude).toBeCloseTo(0.2);
    expect(d.at(-1)!.values.chatgpt).toBeCloseTo(0.08);
    expect(d.find((x) => x.key === '2026-09-25')!.values.claude).toBeCloseTo(0.04);
    expect(d.find((x) => x.key === '2026-09-25')!.values.chatgpt).toBe(0);
    expect(d.some((x) => x.key === '2026-08-01')).toBe(false);
  });

  it('draws no cost without a valid rate or with missing / negative costs', () => {
    expect(isEmpty(dailyCost(stats(), Number.NaN, '2026-09-27'))).toBe(true);
    expect(isEmpty(dailyCost(stats(), 0, '2026-09-27'))).toBe(true);
    const garbage = stats({
      daily: [
        { date: '2026-09-27', agent: 'claude', input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: -3 },
        { date: '2026-09-27', agent: 'chatgpt', input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0 } as Stats['daily'][number],
      ],
    });
    expect(isEmpty(dailyCost(garbage, 0.9, '2026-09-27'))).toBe(true);
  });

  it('survives missing arrays and unknown agents or kinds', () => {
    const s = stats({ daily: undefined as unknown as Stats['daily'], savings_daily: [{ date: '2026-09-27', kind: 'magic' as never, tokens: 5 }] });
    expect(isEmpty(dailyTokens(s, '2026-09-27'))).toBe(true);
    expect(isEmpty(dailyCost(s, 0.9, '2026-09-27'))).toBe(true);
    expect(isEmpty(dailySavings(s, '2026-09-27'))).toBe(true);
  });
});

describe('categorical series', () => {
  it('keeps unknown latencies as null (drawn as "—")', () => {
    const d = latencyData(stats());
    expect(d.map((x) => x.key)).toEqual(['p50', 'p95']);
    expect(d[0]!.values).toEqual({ claude: 6200, chatgpt: null });
    expect(isEmpty(latencyData(stats({ latency: { claude: { p50_ms: null, p95_ms: null, ttft_p50_ms: null }, chatgpt: { p50_ms: null, p95_ms: null, ttft_p50_ms: null } } })))).toBe(true);
  });

  it('lists turns per mode in a fixed order', () => {
    expect(turnsData(stats()).map((x) => [x.label, x.values.count])).toEqual([
      ['Solo', 2],
      ['Duel', 1],
      ['Debat', 4],
    ]);
  });

  it('uses the validated palette tokens for series colours', () => {
    expect(AGENT_SERIES.map((s) => s.color)).toEqual(['var(--claude)', 'var(--chatgpt)']);
    expect(SAVING_SERIES.map((s) => s.color)).toEqual([
      'var(--saving-cache)',
      'var(--saving-compaction)',
      'var(--saving-early-stop)',
      'var(--saving-unchanged)',
    ]);
  });
});

describe('kpis', () => {
  it('computes processed tokens, savings share, turns and consensus', () => {
    const k = kpis(stats());
    expect(k.processed).toEqual({
      total: 3050,
      byAgent: { claude: 2000, chatgpt: 1050 },
      calls: 6,
      errors: 1,
      input: 1700,
      cacheRead: 250,
      cacheWrite: 300,
      output: 800,
    });
    expect(k.saved.total).toBe(1000);
    // The same kind of tokens on both sides: saved / (processed + saved).
    expect(k.saved.ratio).toBeCloseTo(1000 / 4050);
    expect(k.turns.total).toBe(7);
    expect(k.consensus).toEqual({ debates: 4, reached: 3, rate: 0.75, avgRounds: 1.5 });
    expect(k.latency.chatgpt).toEqual({ p50: null, p95: null, ttft: null });
  });

  it('handles an empty period without dividing by zero', () => {
    const empty = stats({
      totals: { calls: 0, errors: 0, cost_usd: 0, by_agent: {} as Stats['totals']['by_agent'] },
      savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null },
      consensus: { debates: 0, reached: 0, avg_rounds: null },
    });
    const k = kpis(empty);
    expect(k.processed.total).toBe(0);
    expect(k.saved.ratio).toBeNull();
    expect(k.consensus.rate).toBeNull();
  });

  it('falls back to the sum of kinds when the total is missing', () => {
    const s = stats({ savings: { cache: 1, compaction: 2, early_stop: 3, unchanged: 4, total: 0, cost_usd: null } });
    expect(kpis(s).saved.total).toBe(10);
  });

  it('counts the cache in the tokens a call processed (A7: 30.103, not 103)', () => {
    const one = { input_tokens: 3, output_tokens: 100, cache_read_tokens: 10_000, cache_write_tokens: 20_000, reasoning_tokens: 50 };
    const s = stats({
      totals: { calls: 1, errors: 0, cost_usd: 0.13, by_agent: { claude: { ...one, cost_usd: 0.13, calls: 1 }, chatgpt: usage(0, 0) } },
      daily: [{ date: '2026-09-27', agent: 'claude', ...one, cost_usd: 0.13 }],
    });
    expect(kpis(s).processed.total).toBe(30_103);
    expect(dailyTokens(s, '2026-09-27').at(-1)!.values.claude).toBe(30_103);
  });

  it('measures the savings ratio against processed tokens, cache included (A7)', () => {
    // The audit's case: a compaction that saved 3.252 tokens in a period that processed 180.618.
    const s = stats({
      totals: {
        calls: 6,
        errors: 0,
        cost_usd: 0,
        by_agent: { claude: usage(15, 500, null, 50_000, 100_000), chatgpt: usage(3, 100, null, 10_000, 20_000) },
      },
      savings: { cache: 0, compaction: 3252, early_stop: 0, unchanged: 0, total: 3252, cost_usd: null },
    });
    const k = kpis(s);
    expect(k.processed.total).toBe(180_618);
    expect(k.saved.ratio).toBeCloseTo(3252 / (180_618 + 3252), 6);
    expect(k.saved.ratio).toBeLessThan(0.02);
  });

  it('counts daily rows without cache writes (older servers) as having none', () => {
    const row = { date: '2026-09-27', agent: 'claude', input_tokens: 10, output_tokens: 5, cache_read_tokens: 20, cost_usd: 0 };
    const d = dailyTokens(stats({ daily: [row as Stats['daily'][number]] }), '2026-09-27');
    expect(d.at(-1)!.values.claude).toBe(35);
  });
});

describe('subscription limits', () => {
  const providers: ProviderStatus[] = [
    {
      agent: 'chatgpt',
      mode: 'cli',
      available: false,
      model: 'gpt-5-codex',
      detail: 'La CLI no respon.',
      limits: [{ window: '5h', used_percent: 130, resets_at: '2026-09-27T20:00:00Z', status: 'rejected' }],
    },
    {
      agent: 'claude',
      mode: 'api',
      available: true,
      model: 'claude-sonnet',
      detail: '',
      limits: [{ window: '7d', used_percent: null, resets_at: 'garbage', status: 'mystery' }],
    },
  ];

  it('orders cards by agent and normalises limits', () => {
    const cards = providerCards(providers);
    expect(cards.map((c) => c.agent)).toEqual(['claude', 'chatgpt']);
    expect(cards[0]).toMatchObject({ name: 'Claude', mode: 'api', modeLabel: 'Clau d’API', available: true });
    expect(cards[0]!.limits[0]).toMatchObject({ window: 'Finestra de 7 dies', usedPercent: null, tone: 'unknown', resetsAt: null });
    expect(cards[1]!.limits[0]).toMatchObject({ window: 'Finestra de 5 hores', usedPercent: 100, tone: 'critical', statusLabel: 'Límit exhaurit' });
    expect(providerCards(null)).toEqual([]);
  });

  it('labels windows and statuses in Catalan', () => {
    expect(windowLabel('5h')).toBe('Finestra de 5 hores');
    expect(windowLabel('1h')).toBe('Finestra de 1 hora');
    expect(windowLabel('7d')).toBe('Finestra de 7 dies');
    expect(windowLabel('1w')).toBe('Finestra de 1 setmana');
    expect(windowLabel('monthly')).toBe('Finestra monthly');
    expect(limitTone('allowed', 10)).toEqual({ tone: 'ok', label: 'Dins del límit' });
    expect(limitTone('warning', 90).tone).toBe('warning');
    expect(limitTone('rejected', null).tone).toBe('critical');
    expect(limitTone('???', 100).tone).toBe('critical');
  });

  it('warns about a nearly full window at the sidebar threshold, whatever the status says (F7)', () => {
    // claude_cli reports "allowed" for every window but the enforced one until it is full.
    const cards = providerCards([
      {
        agent: 'claude', mode: 'cli', available: true, model: 'opus', detail: '',
        limits: [
          { window: '7d', used_percent: 95, resets_at: null, status: 'allowed' },
          { window: '5h', used_percent: 79, resets_at: null, status: 'allowed' },
        ],
      } as ProviderStatus,
    ]);
    expect(cards[0]!.limits.map((l) => [l.tone, l.statusLabel])).toEqual([
      ['warning', 'A prop del límit'],
      ['ok', 'Dins del límit'],
    ]);
    expect(limitTone('allowed', LIMIT_WARN_PERCENT).tone).toBe('warning');
    expect(limitTone('???', LIMIT_WARN_PERCENT).tone).toBe('warning');
    expect(limitTone('???', 50).tone).toBe('unknown');
  });

  it('describes when a window resets', () => {
    const now = new Date('2026-09-27T18:00:00Z');
    expect(resetLabel(null, now)).toBeNull();
    expect(resetLabel('nope', now)).toBeNull();
    expect(resetLabel('2026-09-27T17:00:00Z', now)).toBe('Es restableix en breu');
    expect(resetLabel('2026-09-27T18:30:00Z', now)).toMatch(/^Es restableix d’aquí a 30 minuts \(\d{2}:\d{2}\)$/);
    expect(resetLabel('2026-09-27T20:00:00Z', now)).toMatch(/d’aquí a 2 hores/);
    expect(resetLabel('2026-09-30T18:00:00Z', now)).toMatch(/d’aquí a 3 dies \(.+ a les \d{2}:\d{2}\)$/);
  });
});

describe('formatters', () => {
  it('formats axis ticks compactly', () => {
    // Intl separates number and unit with a no-break space.
    const plain = (v: number) => formatCompact(v).replace(/\u00a0/g, ' ');
    expect(plain(0)).toBe('0');
    expect(plain(500)).toBe('500');
    expect(plain(5000)).toBe('5 k');
    expect(plain(1_200_000)).toBe('1,2 M');
  });

  it('pluralises', () => {
    expect(plural(1, 'crida', 'crides')).toBe('1 crida');
    expect(plural(3, 'crida', 'crides', '3')).toBe('3 crides');
    expect(plural(0, 'error', 'errors')).toBe('0 errors');
  });
});
