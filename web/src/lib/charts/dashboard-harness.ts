// Development-only harness: mounts the usage dashboard against a mocked API.
// Deterministic data so screenshots are comparable between runs.

import { mount } from 'svelte';
import '../../styles/tokens.css';
import Dashboard from '../../views/Dashboard.svelte';
import type { ProviderStatus, SavingKind, Stats } from '../protocol';
import { addDays, utcDay } from './dates';

const params = new URLSearchParams(location.search);

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

function mockStats(days: number): Stats {
  const r = rng(days * 7919);
  const today = utcDay();
  const daily: Stats['daily'] = [];
  const savingsDaily: Stats['savings_daily'] = [];
  const empty = params.has('empty');
  for (let i = days - 1; i >= 0 && !empty; i--) {
    const date = addDays(today, -i);
    if (r() < 0.22) continue; // idle days
    const load = 0.4 + r() * 1.2 + (i < 5 ? 0.6 : 0);
    daily.push({ date, agent: 'claude', input_tokens: Math.round(9000 * load * (0.6 + r())), output_tokens: Math.round(3800 * load * (0.6 + r())), cache_read_tokens: Math.round(6000 * load * r()) });
    if (r() < 0.85) daily.push({ date, agent: 'chatgpt', input_tokens: Math.round(7600 * load * (0.6 + r())), output_tokens: Math.round(3100 * load * (0.6 + r())), cache_read_tokens: Math.round(4000 * load * r()) });
    for (const kind of ['cache', 'compaction', 'early_stop', 'unchanged'] as SavingKind[]) {
      if (r() < 0.5) savingsDaily.push({ date, kind, tokens: Math.round((kind === 'early_stop' ? 5200 : kind === 'cache' ? 3800 : 2100) * load * r()) });
    }
  }
  const sum = (agent: 'claude' | 'chatgpt', key: 'input_tokens' | 'output_tokens' | 'cache_read_tokens') =>
    daily.filter((d) => d.agent === agent).reduce((a, d) => a + d[key], 0);
  const sav = (kind: SavingKind) => savingsDaily.filter((s) => s.kind === kind).reduce((a, s) => a + s.tokens, 0);
  const api = params.has('api');
  const usage = (agent: 'claude' | 'chatgpt', calls: number) => ({
    input_tokens: sum(agent, 'input_tokens'),
    output_tokens: sum(agent, 'output_tokens'),
    cache_read_tokens: sum(agent, 'cache_read_tokens'),
    cache_write_tokens: 0,
    reasoning_tokens: 0,
    cost_usd: api ? (sum(agent, 'input_tokens') * 3 + sum(agent, 'output_tokens') * 15) / 1e6 : null,
    calls,
  });
  const claude = usage('claude', empty ? 0 : Math.round(days * 5.3));
  const chatgpt = usage('chatgpt', empty ? 0 : Math.round(days * 4.6));
  const savings = { cache: sav('cache'), compaction: sav('compaction'), early_stop: sav('early_stop'), unchanged: sav('unchanged'), total: 0 };
  savings.total = savings.cache + savings.compaction + savings.early_stop + savings.unchanged;
  const debates = empty ? 0 : Math.round(days * 1.4);
  return {
    days,
    totals: { calls: claude.calls + chatgpt.calls, errors: empty ? 0 : 3, cost_usd: (claude.cost_usd ?? 0) + (chatgpt.cost_usd ?? 0), by_agent: { claude, chatgpt } },
    savings,
    daily,
    savings_daily: savingsDaily,
    latency: empty
      ? { claude: { p50_ms: null, p95_ms: null, ttft_p50_ms: null }, chatgpt: { p50_ms: null, p95_ms: null, ttft_p50_ms: null } }
      : { claude: { p50_ms: 6200, p95_ms: 14800, ttft_p50_ms: 1150 }, chatgpt: { p50_ms: 4900, p95_ms: 17300, ttft_p50_ms: 780 } },
    turns: empty ? { solo: 0, duel: 0, debate: 0 } : { solo: Math.round(days * 0.9), duel: Math.round(days * 0.5), debate: debates },
    consensus: { debates, reached: Math.round(debates * 0.64), avg_rounds: debates ? 1.4 : null },
  };
}

function mockProviders(): ProviderStatus[] {
  const inH = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();
  const api = params.has('api');
  return [
    {
      agent: 'claude',
      mode: api ? 'api' : 'cli',
      available: true,
      model: api ? 'claude-sonnet-4-5' : 'claude-opus-4-1 (Max)',
      detail: api ? 'Clau d’API configurada.' : 'Sessió de la subscripció activa.',
      limits: api
        ? []
        : [
            { window: '5h', used_percent: 42, resets_at: inH(2.4), status: 'allowed' },
            { window: '7d', used_percent: 81, resets_at: inH(70), status: 'warning' },
          ],
    },
    {
      agent: 'chatgpt',
      mode: api ? 'api' : 'cli',
      available: !params.has('down'),
      model: 'gpt-5-codex',
      detail: params.has('down') ? 'La CLI no respon.' : 'Sessió de ChatGPT Plus activa.',
      limits: api ? [] : [{ window: '5h', used_percent: 100, resets_at: inH(0.6), status: 'rejected' }, { window: '7d', used_percent: null, resets_at: null, status: 'allowed' }],
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
  if (url.pathname === '/api/providers') return json(mockProviders());
  return json({ detail: 'No trobat' }, 404);
};

const target = document.getElementById('app');
if (!target) throw new Error('Missing #app');
document.body.style.margin = '0';
document.body.style.background = 'var(--bg)';
mount(Dashboard, { target });
