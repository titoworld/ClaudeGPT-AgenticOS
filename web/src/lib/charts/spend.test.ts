import { describe, expect, it } from 'vitest';
import type { AgentSpend, FxRate, MonthSpend, ProviderStatus, Stats } from '../protocol';
import {
  budgetStatus,
  costKpis,
  formatAmount,
  formatCost,
  formatEurTick,
  fxNote,
  monthCards,
  monthLabel,
  rateOf,
  unpricedText,
} from './spend';

// Intl separates the amount and the currency with a no-break space.
const plain = (s: string | null) => s?.replace(/ /g, ' ') ?? null;

const ECB: FxRate = { eur_per_usd: 0.8612, as_of: '2026-09-25', source: 'ecb' };
const HALF: FxRate = { eur_per_usd: 0.5, as_of: null, source: 'manual' };

const spend = (over: Partial<AgentSpend> = {}): AgentSpend => ({
  api_usd: 0,
  equivalent_usd: 0,
  unpriced_calls: 0,
  budget_eur: null,
  budget_used: null,
  plan_eur: null,
  plan_value: null,
  ...over,
});

const month = (claude: Partial<AgentSpend> = {}, chatgpt: Partial<AgentSpend> = {}, fx: FxRate = HALF): MonthSpend => ({
  month: '2026-09',
  fx,
  by_agent: { claude: spend(claude), chatgpt: spend(chatgpt) },
});

const provider = (agent: 'claude' | 'chatgpt', mode: ProviderStatus['mode'], limits: ProviderStatus['limits'] = []): ProviderStatus => ({
  agent,
  mode,
  available: true,
  model: 'm',
  detail: '',
  limits,
});

function stats(over: Partial<Stats> = {}): Stats {
  return {
    days: 30,
    totals: { calls: 0, errors: 0, cost_usd: 0, by_agent: {} as Stats['totals']['by_agent'] },
    savings: { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 100, cost_usd: 0.4 },
    daily: [],
    savings_daily: [],
    latency: {} as Stats['latency'],
    turns: { solo: 0, duel: 0, debate: 0 },
    consensus: { debates: 0, reached: 0, avg_rounds: null },
    costs: {
      fx: HALF,
      by_agent: {
        claude: { api_usd: 2, equivalent_usd: 10, unpriced_calls: 1 },
        chatgpt: { api_usd: 1, equivalent_usd: 0, unpriced_calls: 2 },
      },
    },
    month: month(),
    ...over,
  };
}

describe('euro formatting', () => {
  it('formats totals with cents and tiny amounts with more precision', () => {
    expect(plain(formatAmount(0))).toBe('0 €');
    expect(plain(formatAmount(50))).toBe('50 €');
    expect(plain(formatAmount(12.3))).toBe('12,30 €');
    expect(plain(formatAmount(1234.5))).toBe('1.234,50 €');
    expect(plain(formatAmount(0.713))).toBe('0,71 €');
    expect(plain(formatAmount(0.0123))).toBe('0,0123 €');
    expect(formatAmount(null)).toBe('—');
    expect(formatAmount(Number.NaN)).toBe('—');
  });

  it('keeps chart and table cells in cents so columns line up', () => {
    expect(plain(formatCost(0))).toBe('0 €');
    expect(plain(formatCost(1))).toBe('1,00 €');
    expect(plain(formatCost(0.713))).toBe('0,71 €');
    expect(plain(formatCost(0.0042))).toBe('0,0042 €');
    expect(formatCost(undefined)).toBe('—');
  });

  it('formats axis ticks without trailing zeros', () => {
    expect(plain(formatEurTick(0))).toBe('0 €');
    expect(plain(formatEurTick(0.025))).toBe('0,025 €');
    expect(plain(formatEurTick(1.5))).toBe('1,5 €');
    expect(plain(formatEurTick(2))).toBe('2 €');
    expect(plain(formatEurTick(1500))).toBe('1,5 k €');
  });

  it('describes the exchange rate and its source', () => {
    expect(plain(fxNote(ECB))).toBe('1 USD = 0,8612 € · BCE 25/9');
    expect(plain(fxNote({ ...ECB, as_of: null }))).toBe('1 USD = 0,8612 € · BCE');
    expect(plain(fxNote({ eur_per_usd: 0.86, as_of: null, source: 'manual' }))).toBe('1 USD = 0,86 € · tipus manual');
    expect(fxNote({ ...ECB, eur_per_usd: 0 })).toBeNull();
    expect(fxNote(null)).toBeNull();
    expect(rateOf(ECB)).toBe(0.8612);
    expect(rateOf({ ...ECB, eur_per_usd: Number.NaN })).toBeNull();
  });

  it('counts unpriced calls in Catalan', () => {
    expect(unpricedText(1)).toBe('1 crida sense preu');
    expect(unpricedText(3)).toBe('3 crides sense preu');
  });

  it('names the month', () => {
    expect(monthLabel('2026-09')).toBe('setembre del 2026');
    expect(monthLabel('garbage')).toBe('garbage');
    expect(monthLabel(undefined)).toBe('');
  });
});

describe('costKpis', () => {
  it('splits real API cost and subscription value per agent, in euros', () => {
    const k = costKpis(stats())!;
    expect(k.api).toEqual({ total: 1.5, byAgent: { claude: 1, chatgpt: 0.5 } });
    expect(k.equivalent).toEqual({ total: 5, byAgent: { claude: 5, chatgpt: 0 } });
    expect(k.saved).toBe(0.2);
    expect(k.unpriced).toEqual({ total: 3, byAgent: { claude: 1, chatgpt: 2 } });
    expect(k.fx).toBe(HALF);
  });

  it('keeps an unknown savings value unknown and ignores negative costs', () => {
    const s = stats({
      savings: { ...stats().savings, cost_usd: null },
      costs: {
        fx: HALF,
        by_agent: {
          claude: { api_usd: -4, equivalent_usd: Number.NaN, unpriced_calls: -1 },
          chatgpt: undefined as never,
        },
      },
    });
    const k = costKpis(s)!;
    expect(k.saved).toBeNull();
    expect(k.api.total).toBe(0);
    expect(k.equivalent.total).toBe(0);
    expect(k.unpriced.total).toBe(0);
  });

  it('falls back to the month rate, and gives up without costs or a rate', () => {
    const s = stats({ costs: { ...stats().costs, fx: undefined as never }, month: month({}, {}, ECB) });
    expect(costKpis(s)!.fx).toBe(ECB);
    expect(costKpis(stats({ costs: undefined as never }))).toBeNull();
    expect(costKpis(stats({ costs: { ...stats().costs, fx: { ...HALF, eur_per_usd: -1 } }, month: undefined as never }))).toBeNull();
  });
});

describe('budget status', () => {
  it('warns from 80 % and is critical from 100 %', () => {
    expect(budgetStatus(0).tone).toBe('ok');
    expect(budgetStatus(0.79)).toEqual({ tone: 'ok', label: 'Dins del pressupost' });
    expect(budgetStatus(0.8)).toEqual({ tone: 'warning', label: 'A prop del pressupost' });
    expect(budgetStatus(0.999).tone).toBe('warning');
    expect(budgetStatus(1)).toEqual({ tone: 'critical', label: 'Pressupost exhaurit' });
    expect(budgetStatus(1.4).tone).toBe('critical');
  });
});

describe('monthCards', () => {
  it('shows the API budget against its limit', () => {
    const [claude] = monthCards([provider('claude', 'api')], month({ api_usd: 84, budget_eur: 50, budget_used: 0.84 }));
    expect(claude!.plan).toBeNull();
    expect(claude!.budget).toMatchObject({
      kind: 'budget',
      label: 'Pressupost d’API',
      amountEur: 42,
      limitEur: 50,
      ratio: 0.84,
      tone: 'warning',
      status: { icon: 'warning', label: 'A prop del pressupost' },
      action: null,
    });
    expect(plain(claude!.budget!.headText)).toBe('84 %');
    expect(plain(claude!.budget!.amountText)).toBe('42 € de 50 €');
    expect(plain(claude!.budget!.valueText)).toBe('42 € de 50 € (84 %): a prop del pressupost');
  });

  it('computes the budget share when the server did not, and flags an exceeded budget', () => {
    const [claude] = monthCards([provider('claude', 'api')], month({ api_usd: 30, budget_eur: 10 }));
    expect(claude!.budget).toMatchObject({ ratio: 1.5, tone: 'critical', status: { icon: 'critical' } });
    expect(plain(claude!.budget!.headText)).toBe('150 %');
  });

  it('invites to set a budget when there is none', () => {
    const [claude] = monthCards([provider('claude', 'api')], month({ api_usd: 3 }));
    expect(claude!.budget).toMatchObject({
      label: 'Despesa d’API',
      ratio: null,
      limitEur: null,
      status: null,
      tone: 'unknown',
      caption: 'Sense pressupost mensual.',
      action: 'Defineix-lo a Configuració',
    });
    expect(plain(claude!.budget!.headText)).toBe('1,50 €');
  });

  it('compares the subscription value with the plan price', () => {
    const cards = monthCards(
      [provider('claude', 'cli'), provider('chatgpt', 'cli')],
      month({ equivalent_usd: 68.4, plan_eur: 20, plan_value: 1.71 }, { equivalent_usd: 20, plan_eur: 20 }),
    );
    const [claude, chatgpt] = cards;
    expect(claude!.budget).toBeNull();
    expect(claude!.plan).toMatchObject({
      kind: 'plan',
      label: 'Valor de la subscripció',
      ratio: 1.71,
      tone: 'ok',
      status: { icon: 'good', label: 'Ja surt a compte' },
    });
    expect(plain(claude!.plan!.amountText)).toBe('34,20 € de 20 €');
    // Below the plan price: a plain caption, never a warning.
    expect(chatgpt!.plan).toMatchObject({ ratio: 0.5, tone: 'ok', status: null, caption: 'Equivalent a preus d’API' });
  });

  it('invites to set the plan price when there is none', () => {
    const [claude] = monthCards([provider('claude', 'cli')], month({ equivalent_usd: 4 }));
    expect(claude!.plan).toMatchObject({ ratio: null, status: null, action: 'Indica el preu del pla' });
    expect(plain(claude!.plan!.headText)).toBe('2 €');
  });

  it('shows rows for whatever happened this month, besides the current mode', () => {
    // Now on the subscription, but it paid for API calls earlier this month.
    const [claude] = monthCards([provider('claude', 'cli')], month({ api_usd: 2, equivalent_usd: 1 }));
    expect(claude!.budget).not.toBeNull();
    expect(claude!.plan).not.toBeNull();
    // Demo mode: no rows unless the owner set a budget or plan price.
    const [fake] = monthCards([provider('claude', 'fake')], month({ equivalent_usd: 1 }));
    expect(fake!.budget).toBeNull();
    expect(fake!.plan).toBeNull();
  });

  it('keeps the provider windows and the unpriced calls', () => {
    const [claude] = monthCards(
      [provider('claude', 'cli', [{ window: '5h', used_percent: 40, resets_at: null, status: 'allowed' }])],
      month({ equivalent_usd: 2, unpriced_calls: 3 }),
    );
    expect(claude!.provider!.limits[0]).toMatchObject({ window: 'Finestra de 5 hores', usedPercent: 40 });
    expect(claude!.unpriced).toBe(3);
  });

  it('works without providers (money rows from the spend alone) and without spend', () => {
    const cards = monthCards(null, month({ api_usd: 1 }, { equivalent_usd: 1 }));
    expect(cards.map((c) => c.agent)).toEqual(['claude', 'chatgpt']);
    expect(cards[0]!.provider).toBeNull();
    expect(cards[0]!.budget).not.toBeNull();
    expect(cards[1]!.plan).not.toBeNull();

    const noSpend = monthCards([provider('chatgpt', 'api'), provider('claude', 'cli')], undefined);
    expect(noSpend.map((c) => [c.agent, c.budget, c.plan])).toEqual([
      ['claude', null, null],
      ['chatgpt', null, null],
    ]);
    expect(monthCards(null, null)).toEqual([]);
    // An invalid rate hides the money rather than showing it at 0.
    expect(monthCards([provider('claude', 'api')], month({ api_usd: 5 }, {}, { ...HALF, eur_per_usd: 0 }))[0]!.budget).toBeNull();
  });
});
