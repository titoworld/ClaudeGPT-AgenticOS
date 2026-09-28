import { describe, expect, it } from 'vitest';
import {
  approxEur,
  budgetLevel,
  inferCostBasis,
  formatMoney,
  fxText,
  monthName,
  savingsEur,
  spendLine,
  streamCost,
  turnCost,
  turnCostTitle,
} from './costs';
import type { AgentSpend, FxRate } from './protocol';
import { usage } from './test-fixtures';
import { createLiveTurn, type StreamView, type TurnView } from './turns.svelte';

// Intl uses a no-break space before "€" and "%" in Catalan.
const plain = (s: string | null | undefined) => s?.replace(/ | /g, ' ');

const fx: FxRate = { eur_per_usd: 0.86, as_of: '2026-09-25', source: 'ecb' };

function stream(partial: Partial<StreamView>): StreamView {
  return {
    id: 's',
    agent: 'claude',
    kind: 'answer',
    round: 0,
    model: 'm',
    text: '',
    critique: '',
    status: 'done',
    error: null,
    messageId: 1,
    usage: null,
    latencyMs: null,
    ttftMs: null,
    agreement: null,
    unchanged: false,
    cached: false,
    costBasis: null,
    truncated: false,
    finishReason: null,
    unchangedNote: null,
    degraded: false,
    ...partial,
  };
}

const priced = (cost: number | null) => ({ ...usage(100, 50), cost_usd: cost });

describe('euro formatting', () => {
  it('shows per-answer costs as "≈ x €" with adaptive precision', () => {
    expect(plain(approxEur(0.0143, 0.86))).toBe('≈ 0,0123 €');
    expect(plain(approxEur(1, 0.86))).toBe('≈ 0,86 €');
    expect(plain(approxEur(20, 0.86))).toBe('≈ 17,20 €');
    expect(approxEur(null, 0.86)).toBeNull();
    expect(approxEur(undefined, 0.86)).toBeNull();
  });

  it('formats totals and limits compactly', () => {
    expect(plain(formatMoney(50))).toBe('50 €');
    expect(plain(formatMoney(12.3))).toBe('12,30 €');
    expect(plain(formatMoney(1234.5))).toBe('1.234,50 €');
    expect(plain(formatMoney(0))).toBe('0 €');
    expect(plain(formatMoney(0.0456))).toBe('0,0456 €');
  });

  it('describes the exchange rate', () => {
    expect(plain(fxText(fx))).toBe('1 $ = 0,86 € · BCE, 25 de set.');
    expect(plain(fxText({ eur_per_usd: 0.912345, as_of: null, source: 'manual' }))).toBe('1 $ = 0,9123 € · manual');
  });

  it('names the month in Catalan', () => {
    expect(monthName('2026-09')).toBe('setembre del 2026');
    expect(monthName('bad')).toBe('bad');
  });
});

describe('answer cost', () => {
  it('uses the cost basis for the tooltip', () => {
    const api = streamCost(stream({ usage: priced(0.0143), costBasis: 'api' }), 0.86);
    expect(plain(api?.text)).toBe('≈ 0,0123 €');
    expect(api?.title).toMatch(/^Cost real de l'API \(0,0143 \$\)$/);
    const eq = streamCost(stream({ usage: priced(0.0143), costBasis: 'equivalent' }), 0.86);
    expect(eq?.title).toMatch(/^Valor equivalent a preus d'API — inclòs a la subscripció/);
    const unknown = streamCost(stream({ usage: priced(0.5) }), 0.86);
    expect(unknown?.title).toMatch(/^Cost estimat a preus d'API/);
  });

  it('infers the basis of live answers like the server does', () => {
    expect(inferCostBasis('api', priced(null))).toBe('api');
    expect(inferCostBasis('cli', priced(null))).toBe('equivalent');
    expect(inferCostBasis('fake', priced(0.01))).toBe('equivalent');
    expect(inferCostBasis('fake', priced(null))).toBeNull();
    expect(inferCostBasis(undefined, priced(0.01))).toBeNull();
  });

  it('is hidden when unknown or for a free cached answer', () => {
    expect(streamCost(stream({ usage: priced(null) }), 0.86)).toBeNull();
    expect(streamCost(stream({ usage: null }), 0.86)).toBeNull();
    expect(streamCost(stream({ usage: priced(0), cached: true }), 0.86)).toBeNull();
    expect(streamCost(stream({ usage: priced(0) }), 0.86)).not.toBeNull();
  });
});

describe('turn cost and savings', () => {
  function turnWith(streams: StreamView[], totalUsd?: number | null): TurnView {
    const t = createLiveTurn({ requestId: 'r', question: 'q', mode: 'duel' });
    t.streams = streams;
    if (totalUsd !== undefined) t.usage = priced(totalUsd);
    return t;
  }

  it('splits real API cost from value included in subscriptions', () => {
    const t = turnWith([
      stream({ id: 'a', usage: priced(0.01), costBasis: 'api' }),
      stream({ id: 'b', agent: 'chatgpt', usage: priced(0.03), costBasis: 'equivalent' }),
      stream({ id: 'c', usage: priced(null) }),
    ]);
    const cost = turnCost(t);
    expect(cost.totalUsd).toBeCloseTo(0.04);
    expect(cost.apiUsd).toBeCloseTo(0.01);
    expect(cost.equivalentUsd).toBeCloseTo(0.03);
    expect(plain(turnCostTitle(cost, 1))).toBe(
      "Cost del torn a preus d'API · cost real d'API: 0,01 € · valor inclòs a la subscripció: 0,03 €",
    );
  });

  it('prefers the server total (it includes internal calls)', () => {
    const t = turnWith([stream({ usage: priced(0.01), costBasis: 'api' })], 0.05);
    expect(turnCost(t).totalUsd).toBe(0.05);
    expect(turnCost(turnWith([stream({ usage: priced(null) })])).totalUsd).toBeNull();
  });

  it('converts the value of the saved tokens', () => {
    const base = { cache: 0, compaction: 0, early_stop: 0, unchanged: 10, total: 10 };
    expect(savingsEur({ ...base, cost_usd: 0.1 }, 0.86)).toBeCloseTo(0.086);
    expect(savingsEur({ ...base, cost_usd: null }, 0.86)).toBeNull();
    expect(savingsEur({ ...base, cost_usd: 0 }, 0.86)).toBeNull();
    expect(savingsEur(null, 0.86)).toBeNull();
  });
});

describe('budget bar', () => {
  it('warns from 80 % and is critical from 100 %', () => {
    expect(budgetLevel(null)).toBe('ok');
    expect(budgetLevel(0)).toBe('ok');
    expect(budgetLevel(0.799)).toBe('ok');
    expect(budgetLevel(0.8)).toBe('warn');
    expect(budgetLevel(0.99)).toBe('warn');
    expect(budgetLevel(1)).toBe('bad');
    expect(budgetLevel(1.7)).toBe('bad');
    expect(budgetLevel(Number.NaN)).toBe('ok');
  });
});

describe('spendLine (sidebar month line)', () => {
  const spend = (partial: Partial<AgentSpend>): AgentSpend => ({
    api_usd: 0,
    equivalent_usd: 0,
    unpriced_calls: 0,
    budget_eur: null,
    budget_used: null,
    plan_eur: null,
    plan_value: null,
    ...partial,
  });
  const one: FxRate = { eur_per_usd: 1, as_of: null, source: 'manual' };

  it('API mode with a budget: amount, share and level', () => {
    const line = spendLine('api', spend({ api_usd: 12.3, budget_eur: 50, budget_used: 0.246 }), one, '2026-09');
    expect(line?.label).toBe('Gastat');
    expect(plain(`${line?.amount} (${line?.percent})`)).toBe('12,30 € de 50 € (25 %)');
    expect(plain(line?.title)).toBe("Setembre del 2026: despesa d'API de 12,30 € sobre un pressupost de 50 € (25 %).");
    expect(line?.ratio).toBe(0.246);
    expect(line?.level).toBe('ok');
    expect(line?.kind).toBe('budget');

    const warn = spendLine('api', spend({ api_usd: 41, budget_eur: 50, budget_used: 0.82 }), one, '2026-09');
    expect(warn?.level).toBe('warn');
    const over = spendLine('api', spend({ api_usd: 55, budget_eur: 50, budget_used: 1.1 }), one, '2026-09');
    expect(over?.level).toBe('bad');
    expect(plain(`${over?.amount} (${over?.percent})`)).toBe('55 € de 50 € (110 %)');
  });

  it('computes the share when the server does not send it', () => {
    const line = spendLine('api', spend({ api_usd: 40, budget_eur: 50 }), one, '2026-09');
    expect(line?.ratio).toBeCloseTo(0.8);
    expect(line?.level).toBe('warn');
  });

  it('API mode without a budget shows the plain amount', () => {
    const line = spendLine('api', spend({ api_usd: 2, unpriced_calls: 3 }), { ...one, eur_per_usd: 0.86 }, '2026-09');
    expect(plain(line?.amount)).toBe('1,72 €');
    expect(line?.ratio).toBeNull();
    expect(line?.percent).toBeNull();
    expect(line?.title).toContain('sense pressupost');
    expect(line?.title).toContain('3 crides amb models sense preu conegut no hi compten.');
  });

  it('subscription mode compares the value obtained with the plan price', () => {
    const line = spendLine('cli', spend({ equivalent_usd: 34.2, plan_eur: 100, plan_value: 0.342 }), one, '2026-09');
    expect(line?.label).toBe('Valor aprofitat');
    expect(plain(`${line?.label} ${line?.amount} (${line?.percent})`)).toBe('Valor aprofitat 34,20 € · pla 100 € (34 %)');
    expect(line?.kind).toBe('plan');
    // Getting more than the plan is good news, never a warning.
    const lots = spendLine('cli', spend({ equivalent_usd: 250, plan_eur: 100, plan_value: 2.5 }), one, '2026-09');
    expect(lots?.level).toBe('ok');

    const noPlan = spendLine('cli', spend({ equivalent_usd: 34.2 }), one, '2026-09');
    expect(plain(noPlan?.amount)).toBe('34,20 €');
    expect(noPlan?.ratio).toBeNull();
  });

  it('shows nothing for the demo provider', () => {
    expect(spendLine('fake', spend({}), one, '2026-09')).toBeNull();
  });
});
