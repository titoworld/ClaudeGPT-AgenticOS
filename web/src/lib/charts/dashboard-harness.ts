// Development-only harness: mounts the usage dashboard against a mocked API.
// Deterministic data so screenshots are comparable between runs: one 90-day
// history (fixed seed) that every range, and the month panel, slice from.

import { mount } from 'svelte';
import '../../styles/tokens.css';
import Dashboard from '../../views/Dashboard.svelte';
import { processedTokens } from '../costs';
import type { Agent, AgentSpend, FxRate, MonthSpend, ProviderMode, ProviderStatus, SavingKind, Stats } from '../protocol';
import { addDays, utcDay } from './dates';

const params = new URLSearchParams(location.search);
const empty = params.has('empty');
/** ?api: both agents on API keys; ?mixed: Claude subscription + ChatGPT API; default: both subscriptions. */
const MODE: Record<Agent, ProviderMode> = {
  claude: params.has('api') ? 'api' : 'cli',
  chatgpt: params.has('api') || params.has('mixed') ? 'api' : 'cli',
};
/** USD per million tokens (input, output, cache read, cache write). */
const PRICE: Record<Agent, [number, number, number, number]> = { claude: [3, 15, 0.3, 3.75], chatgpt: [1.25, 10, 0.125, 0] };
const SCALE = 12;
const HISTORY = 90;

function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function lastEcbDay(today: string): string {
  let d = addDays(today, -1);
  while ([0, 6].includes(new Date(`${d}T00:00:00Z`).getUTCDay())) d = addDays(d, -1);
  return d;
}

const today = utcDay();
const fx: FxRate = params.has('manual')
  ? { eur_per_usd: 0.86, as_of: null, source: 'manual' }
  : { eur_per_usd: 0.8612, as_of: lastEcbDay(today), source: 'ecb' };

type DailyRow = Stats['daily'][number];
type SavingRow = Stats['savings_daily'][number];

function history(): { daily: DailyRow[]; savings: SavingRow[] } {
  const r = rng(90 * 7919);
  const daily: DailyRow[] = [];
  const savings: SavingRow[] = [];
  const row = (date: string, agent: Agent, load: number, base: [number, number, number, number]): DailyRow => {
    const [i, o, c, w] = base.map((b, k) => Math.round(b * SCALE * load * (k >= 2 ? r() : 0.6 + r()))) as [number, number, number, number];
    const [pi, po, pc, pw] = PRICE[agent];
    return {
      date,
      agent,
      input_tokens: i,
      output_tokens: o,
      cache_read_tokens: c,
      cache_write_tokens: w,
      cost_usd: (i * pi + o * po + c * pc + w * pw) / 1e6,
    };
  };
  for (let i = HISTORY - 1; i >= 0 && !empty; i--) {
    const date = addDays(today, -i);
    if (r() < 0.22) continue; // idle days
    const load = 0.4 + r() * 1.2 + (i < 5 ? 0.6 : 0);
    daily.push(row(date, 'claude', load, [9000, 3800, 6000, 1500]));
    if (r() < 0.85) daily.push(row(date, 'chatgpt', load, [7600, 3100, 4000, 0]));
    for (const kind of ['cache', 'compaction', 'early_stop', 'unchanged'] as SavingKind[]) {
      if (r() < 0.5) {
        const base = kind === 'early_stop' ? 5200 : kind === 'cache' ? 3800 : 2100;
        savings.push({ date, kind, tokens: Math.round(base * SCALE * load * r()) });
      }
    }
  }
  return { daily, savings };
}

const HISTORY_DATA = history();

const sumBy = <T>(rows: T[], f: (r: T) => number) => rows.reduce((a, r) => a + f(r), 0);

function agentCosts(rows: DailyRow[], agent: Agent, unpriced: number) {
  const usd = sumBy(
    rows.filter((d) => d.agent === agent),
    (d) => d.cost_usd,
  );
  return MODE[agent] === 'api'
    ? { api_usd: usd, equivalent_usd: 0, unpriced_calls: unpriced }
    : { api_usd: 0, equivalent_usd: usd, unpriced_calls: unpriced };
}

/** Unpriced calls only in the default (subscription) scenario, on ChatGPT. */
const unpricedFor = (agent: Agent, n: number) => (agent === 'chatgpt' && !params.has('api') && !params.has('mixed') && !empty ? n : 0);

function mockMonth(): MonthSpend {
  const month = today.slice(0, 7);
  const rows = HISTORY_DATA.daily.filter((d) => d.date.startsWith(month));
  const noLimits = params.has('noplan');
  // Budgets / plan prices tuned so every state shows: API warning and exceeded,
  // one subscription that already pays off and one that does not yet.
  const budgetRatio: Record<Agent, number> = { claude: 0.86, chatgpt: 1.12 };
  const plan: Record<Agent, number> = { claude: 18, chatgpt: 23 };
  const spend = (agent: Agent): AgentSpend => {
    const c = agentCosts(rows, agent, unpricedFor(agent, 2));
    const apiEur = c.api_usd * fx.eur_per_usd;
    const budget = noLimits || MODE[agent] !== 'api' ? null : Math.max(5, Math.round(apiEur / budgetRatio[agent]));
    const planEur = noLimits || MODE[agent] !== 'cli' ? null : plan[agent];
    return {
      ...c,
      budget_eur: budget,
      budget_used: budget ? apiEur / budget : null,
      plan_eur: planEur,
      plan_value: planEur ? (c.equivalent_usd * fx.eur_per_usd) / planEur : null,
    };
  };
  return { month, fx, by_agent: { claude: spend('claude'), chatgpt: spend('chatgpt') } };
}

function mockStats(days: number): Stats {
  const from = addDays(today, -(days - 1));
  const daily = HISTORY_DATA.daily.filter((d) => d.date >= from);
  const savingsDaily = HISTORY_DATA.savings.filter((s) => s.date >= from);
  const sum = (agent: Agent, key: 'input_tokens' | 'output_tokens' | 'cache_read_tokens' | 'cache_write_tokens' | 'cost_usd') =>
    sumBy(
      daily.filter((d) => d.agent === agent),
      (d) => d[key],
    );
  const usage = (agent: Agent, calls: number) => ({
    input_tokens: sum(agent, 'input_tokens'),
    output_tokens: sum(agent, 'output_tokens'),
    cache_read_tokens: sum(agent, 'cache_read_tokens'),
    cache_write_tokens: sum(agent, 'cache_write_tokens'),
    reasoning_tokens: 0,
    cost_usd: empty ? null : sum(agent, 'cost_usd'),
    calls,
  });
  const claude = usage('claude', empty ? 0 : Math.round(days * 5.3));
  const chatgpt = usage('chatgpt', empty ? 0 : Math.round(days * 4.6));
  const sav = (kind: SavingKind) =>
    sumBy(
      savingsDaily.filter((s) => s.kind === kind),
      (s) => s.tokens,
    );
  const byKind = { cache: sav('cache'), compaction: sav('compaction'), early_stop: sav('early_stop'), unchanged: sav('unchanged') };
  const savedTotal = byKind.cache + byKind.compaction + byKind.early_stop + byKind.unchanged;
  const costTotal = (claude.cost_usd ?? 0) + (chatgpt.cost_usd ?? 0);
  const processed = processedTokens(claude) + processedTokens(chatgpt);
  const debates = empty ? 0 : Math.round(days * 1.4);
  const unpriced = Math.max(1, Math.round(days / 10));
  return {
    days,
    totals: { calls: claude.calls + chatgpt.calls, errors: empty ? 0 : 3, cost_usd: costTotal, by_agent: { claude, chatgpt } },
    savings: { ...byKind, total: savedTotal, cost_usd: processed > 0 ? (savedTotal * costTotal) / processed : null },
    daily,
    savings_daily: savingsDaily,
    latency: empty
      ? { claude: { p50_ms: null, p95_ms: null, ttft_p50_ms: null }, chatgpt: { p50_ms: null, p95_ms: null, ttft_p50_ms: null } }
      : { claude: { p50_ms: 6200, p95_ms: 14800, ttft_p50_ms: 1150 }, chatgpt: { p50_ms: 4900, p95_ms: 17300, ttft_p50_ms: 780 } },
    turns: empty ? { solo: 0, duel: 0, debate: 0 } : { solo: Math.round(days * 0.9), duel: Math.round(days * 0.5), debate: debates },
    consensus: { debates, reached: Math.round(debates * 0.64), avg_rounds: debates ? 1.4 : null },
    costs: {
      fx,
      by_agent: {
        claude: agentCosts(daily, 'claude', unpricedFor('claude', unpriced)),
        chatgpt: agentCosts(daily, 'chatgpt', unpricedFor('chatgpt', unpriced)),
      },
    },
    month: mockMonth(),
  };
}

function mockProviders(): ProviderStatus[] {
  const inH = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();
  const down = params.has('down');
  return [
    {
      agent: 'claude',
      mode: MODE.claude,
      available: true,
      model: MODE.claude === 'api' ? 'claude-sonnet-4-5' : 'claude-sonnet-4-5 (Pro)',
      detail: MODE.claude === 'api' ? 'Clau d’API configurada.' : 'Sessió de la subscripció activa.',
      limits:
        MODE.claude === 'api'
          ? []
          : [
              { window: '5h', used_percent: 42, resets_at: inH(2.4), status: 'allowed' },
              { window: '7d', used_percent: 81, resets_at: inH(70), status: 'warning' },
            ],
    },
    {
      agent: 'chatgpt',
      mode: MODE.chatgpt,
      available: !down,
      model: MODE.chatgpt === 'api' ? 'gpt-5' : 'gpt-5-codex',
      detail: down ? 'La CLI no respon.' : MODE.chatgpt === 'api' ? 'Clau d’API configurada.' : 'Sessió de ChatGPT Plus activa.',
      limits:
        MODE.chatgpt === 'api'
          ? []
          : [
              { window: '5h', used_percent: 100, resets_at: inH(0.6), status: 'rejected' },
              { window: '7d', used_percent: null, resets_at: null, status: 'allowed' },
            ],
    },
  ];
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

window.fetch = async (input: RequestInfo | URL) => {
  const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.origin);
  await sleep(params.has('slow') ? 1500 : 120);
  if (params.has('error')) return json({ detail: 'El servidor no respon.' }, 503);
  if (url.pathname === '/api/stats') return json(mockStats(Number(url.searchParams.get('days') ?? 30)));
  if (url.pathname === '/api/spend') return json(mockMonth());
  if (url.pathname === '/api/providers') {
    return params.has('noproviders') ? json({ detail: 'No s’ha pogut consultar.' }, 503) : json(mockProviders());
  }
  return json({ detail: 'No trobat' }, 404);
};

const target = document.getElementById('app');
if (!target) throw new Error('Missing #app');
document.body.style.margin = '0';
document.body.style.background = 'var(--bg)';
mount(Dashboard, { target });
