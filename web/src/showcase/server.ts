// The showcase's server: the REST API (FakeApi of lib/test-server.ts, with /api/stats on
// top) and a WebSocket that greets, answers pings and replays the turns the page shows
// live. Both agents run on their subscriptions (Claude Code and Codex), with the models
// and prices of pricing.py. As the real server does, it writes the agents' status and the
// models' descriptions in the language of each request (Accept-Language, or the socket's
// ?lang=), else in English.

import { addDays, utcDay } from '../lib/charts/dates';
import { asLocale, type Locale } from '../lib/i18n/index.svelte';
import type {
  Agent,
  AgentSpend,
  FxRate,
  ModelCatalog,
  MonthSpend,
  Pricing,
  ProviderStatus,
  RuntimeSettings,
  SavingKind,
  ServerMessage,
  Stats,
  TurnEvent,
} from '../lib/protocol';
import { FakeApi, FakeSocket } from '../lib/test-server';
import { MODEL } from './conversations';

const TODAY = utcDay();

/** The last weekday before today: the date of the ECB's latest rate. */
function lastEcbDay(): string {
  let day = addDays(TODAY, -1);
  while ([0, 6].includes(new Date(`${day}T00:00:00Z`).getUTCDay())) day = addDays(day, -1);
  return day;
}

export const FX: FxRate = { eur_per_usd: 0.8612, as_of: lastEcbDay(), source: 'ecb' };

export const SETTINGS: RuntimeSettings = {
  revision: 12,
  default_mode: 'debate',
  default_target: 'claude',
  debate: { rounds: 3, consensus_threshold: 85, synthesizer: 'claude' },
  refine: { max_rounds: 12, budget_eur: 3, max_words: null, stop_on_convergence: true, convergence_threshold: 90, editor: 'claude' },
  use_cache: true,
  compaction_threshold_tokens: 6000,
  models: { claude: MODEL.claude, chatgpt: MODEL.chatgpt },
  fast_models: { claude: 'haiku', chatgpt: 'gpt-6-luna' },
  prices: {},
  fx: { mode: 'auto', eur_per_usd: FX.eur_per_usd },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: 90, chatgpt: 23 },
  pdf_in_revisions: 'text',
};

/** The server's texts of the agents' status and of the models, in each language. */
interface ServerTexts {
  claude: string;
  chatgpt: string;
  /** Before what an alias says of its family, as the server writes it. */
  newest: string;
  /** Before the model an alias stands for («Now: claude-opus-5-5»). */
  now: string;
  models: Record<'opus' | 'sonnet' | 'haiku' | 'fable' | 'gpt-6-astra' | 'gpt-6-sol' | 'gpt-6-luna', string>;
}

const SERVER_TEXTS: Record<Locale, ServerTexts> = {
  en: {
    claude: 'Subscription active (max)',
    chatgpt: 'ChatGPT subscription active (Plus)',
    newest: 'Always the newest version.',
    now: 'Now',
    models: {
      opus: 'Deep reasoning and long tasks.',
      sonnet: 'A balance of quality and speed.',
      haiku: 'The fastest and cheapest; good for summaries.',
      fable: 'The most capable; may not be included in every plan.',
      'gpt-6-astra': 'The most capable, for the most demanding work.',
      'gpt-6-sol': 'Balanced, for everyday work.',
      'gpt-6-luna': 'Fast and cheap, for simple tasks.',
    },
  },
  es: {
    claude: 'Suscripción activa (max)',
    chatgpt: 'Suscripción de ChatGPT activa (Plus)',
    newest: 'Siempre la versión más reciente.',
    now: 'Ahora',
    models: {
      opus: 'Razonamiento profundo y tareas largas.',
      sonnet: 'Equilibrio entre calidad y velocidad.',
      haiku: 'El más rápido y económico; bueno para los resúmenes.',
      fable: 'El más capaz; puede no estar incluido en todos los planes.',
      'gpt-6-astra': 'El más capaz, para el trabajo más exigente.',
      'gpt-6-sol': 'Equilibrado, para el trabajo de cada día.',
      'gpt-6-luna': 'Rápido y económico, para tareas sencillas.',
    },
  },
  ca: {
    claude: 'Subscripció activa (max)',
    chatgpt: 'Subscripció ChatGPT activa (Plus)',
    newest: 'Sempre la versió més nova.',
    now: 'Ara',
    models: {
      opus: 'Raonament profund i tasques llargues.',
      sonnet: 'Equilibri entre qualitat i velocitat.',
      haiku: 'El més ràpid i econòmic; bo per als resums.',
      fable: 'El més capaç; pot no estar inclòs en tots els plans.',
      'gpt-6-astra': 'El més capaç, per a la feina més exigent.',
      'gpt-6-sol': 'Equilibrat, per a la feina de cada dia.',
      'gpt-6-luna': 'Ràpid i econòmic, per a tasques senzilles.',
    },
  },
};

const inHours = (hours: number): string => new Date(Date.now() + hours * 3_600_000).toISOString();

const LIMITS: Record<Agent, ProviderStatus['limits']> = {
  claude: [
    { window: '5h', used_percent: 27, resets_at: inHours(2.6), status: 'allowed' },
    { window: '7d', used_percent: 44, resets_at: inHours(4 * 24 + 3), status: 'allowed' },
  ],
  chatgpt: [
    { window: '5h', used_percent: 18, resets_at: inHours(3.9), status: 'allowed' },
    { window: '7d', used_percent: 52, resets_at: inHours(2 * 24 + 7), status: 'allowed' },
  ],
};

/** The agents' status, in `lang`. */
export function providers(lang: Locale): ProviderStatus[] {
  const t = SERVER_TEXTS[lang];
  return [
    { agent: 'claude', mode: 'cli', available: true, model: MODEL.claude, detail: t.claude, limits: LIMITS.claude },
    { agent: 'chatgpt', mode: 'cli', available: true, model: MODEL.chatgpt, detail: t.chatgpt, limits: LIMITS.chatgpt },
  ];
}

const model = (id: string, label: string, description: string, isDefault = false, context: number | null = null) => ({
  id,
  label,
  description,
  is_default: isDefault,
  context_window: context,
});

/** The models of each agent, described in `lang`. */
export function catalog(lang: Locale): ModelCatalog {
  const { models: d, newest, now } = SERVER_TEXTS[lang];
  const alias = (family: string, current: string) => `${newest} ${family} ${now}: ${current}`;
  return {
    claude: {
      mode: 'cli',
      default_model: MODEL.claude,
      fast_model: 'haiku',
      live: true,
      models: [
        model(MODEL.claude, MODEL.claude, d.opus, true),
        model('opus', 'Claude Opus', alias(d.opus, MODEL.claude)),
        model('sonnet', 'Claude Sonnet', alias(d.sonnet, 'claude-sonnet-5')),
        model('haiku', 'Claude Haiku', alias(d.haiku, 'claude-haiku-4-5')),
        model('fable', 'Claude Fable', alias(d.fable, 'claude-fable-5-1')),
      ],
    },
    chatgpt: {
      mode: 'cli',
      default_model: MODEL.chatgpt,
      fast_model: 'gpt-6-luna',
      live: true,
      models: [
        model('gpt-6-astra', 'gpt-6-astra', d['gpt-6-astra'], true, 400_000),
        model('gpt-6-sol', 'gpt-6-sol', d['gpt-6-sol'], false, 400_000),
        model('gpt-6-luna', 'gpt-6-luna', d['gpt-6-luna'], false, 400_000),
      ],
    },
  };
}

/** USD per million tokens (input, output, cache read, cache write), as pricing.py has them. */
const PRICES: Record<string, [number, number, number, number]> = {
  'claude-fable-5-1': [10, 50, 0.25, 12.5],
  'claude-opus-5-5': [4, 20, 0.2, 5],
  'claude-sonnet-5': [2, 10, 0.2, 2.5],
  'claude-haiku-4': [1, 5, 0.1, 1.25],
  'gpt-6-astra': [10, 50, 1, 12.5],
  'gpt-6-sol': [2, 10, 0.2, 2.5],
  'gpt-6-luna': [0.1, 0.5, 0.01, 0.125],
};

export const PRICING: Pricing = {
  fx: FX,
  prices: Object.entries(PRICES).map(([id, [input, output, cacheRead, cacheWrite]]) => ({
    model: id,
    input,
    output,
    cache_read: cacheRead,
    cache_write: cacheWrite,
    source: 'default' as const,
    key: id,
    default: null,
  })),
};

// ------------------------------------------------------------------ usage history

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

type DailyRow = Stats['daily'][number];
type SavingRow = Stats['savings_daily'][number];

/** Tokens of an ordinary day (input, output, cache read) and the model's prices. */
const DAY_LOAD: Record<Agent, { tokens: [number, number, number]; price: [number, number, number, number] }> = {
  claude: { tokens: [380_000, 88_000, 610_000], price: PRICES[MODEL.claude]! },
  chatgpt: { tokens: [150_000, 36_000, 210_000], price: PRICES[MODEL.chatgpt]! },
};

const HISTORY = 90;

function history(): { daily: DailyRow[]; savings: SavingRow[] } {
  const r = rng(20_261_002);
  const daily: DailyRow[] = [];
  const savings: SavingRow[] = [];
  for (let i = HISTORY - 1; i >= 0; i--) {
    const date = addDays(TODAY, -i);
    const weekend = [0, 6].includes(new Date(`${date}T00:00:00Z`).getUTCDay());
    if (r() < (weekend ? 0.55 : 0.08)) continue;
    const load = (weekend ? 0.35 : 0.7) + r() * 0.8;
    for (const agent of ['claude', 'chatgpt'] as const) {
      const { tokens, price } = DAY_LOAD[agent];
      const [input, output, cacheRead] = tokens.map((t) => Math.round(t * load * (0.75 + r() * 0.5))) as [number, number, number];
      daily.push({
        date,
        agent,
        input_tokens: input,
        output_tokens: output,
        cache_read_tokens: cacheRead,
        cache_write_tokens: 0,
        cost_usd: (input * price[0] + output * price[1] + cacheRead * price[2]) / 1e6,
      });
    }
    const base: Record<SavingKind, number> = { cache: 95_000, compaction: 150_000, early_stop: 230_000, unchanged: 64_000 };
    for (const kind of Object.keys(base) as SavingKind[]) {
      if (r() < 0.75) savings.push({ date, kind, tokens: Math.round(base[kind] * load * (0.5 + r())) });
    }
  }
  return { daily, savings };
}

const HISTORY_DATA = history();

const sumOf = <T>(rows: readonly T[], f: (row: T) => number): number => rows.reduce((a, row) => a + f(row), 0);

function month(): MonthSpend {
  const rows = HISTORY_DATA.daily.filter((d) => d.date.startsWith(TODAY.slice(0, 7)));
  const spend = (agent: Agent): AgentSpend => {
    const usd = sumOf(
      rows.filter((d) => d.agent === agent),
      (d) => d.cost_usd,
    );
    const plan = SETTINGS.plans_eur[agent];
    return {
      api_usd: 0,
      equivalent_usd: usd,
      unpriced_calls: 0,
      budget_eur: null,
      budget_used: null,
      plan_eur: plan,
      plan_value: plan ? (usd * FX.eur_per_usd) / plan : null,
    };
  };
  return { month: TODAY.slice(0, 7), fx: FX, by_agent: { claude: spend('claude'), chatgpt: spend('chatgpt') } };
}

function stats(days: number): Stats {
  const from = addDays(TODAY, -(days - 1));
  const daily = HISTORY_DATA.daily.filter((d) => d.date >= from);
  const savingsDaily = HISTORY_DATA.savings.filter((s) => s.date >= from);
  const active = new Set(daily.map((d) => d.date)).size;
  const usage = (agent: Agent, calls: number) => {
    const rows = daily.filter((d) => d.agent === agent);
    return {
      input_tokens: sumOf(rows, (d) => d.input_tokens),
      output_tokens: sumOf(rows, (d) => d.output_tokens),
      cache_read_tokens: sumOf(rows, (d) => d.cache_read_tokens),
      cache_write_tokens: 0,
      reasoning_tokens: Math.round(sumOf(rows, (d) => d.output_tokens) * 0.18),
      cost_usd: sumOf(rows, (d) => d.cost_usd),
      calls,
    };
  };
  const claude = usage('claude', active * 31);
  const chatgpt = usage('chatgpt', active * 24);
  const saved = (kind: SavingKind) =>
    sumOf(
      savingsDaily.filter((s) => s.kind === kind),
      (s) => s.tokens,
    );
  const byKind = { cache: saved('cache'), compaction: saved('compaction'), early_stop: saved('early_stop'), unchanged: saved('unchanged') };
  const savedTotal = byKind.cache + byKind.compaction + byKind.early_stop + byKind.unchanged;
  const debates = Math.round(active * 2.6);
  return {
    days,
    totals: { calls: claude.calls + chatgpt.calls, errors: Math.round(active / 9), cost_usd: claude.cost_usd + chatgpt.cost_usd, by_agent: { claude, chatgpt } },
    savings: { ...byKind, total: savedTotal, cost_usd: savedTotal * 7.8e-6 },
    daily,
    savings_daily: savingsDaily,
    latency: {
      claude: { p50_ms: 14_200, p95_ms: 31_800, ttft_p50_ms: 1_340 },
      chatgpt: { p50_ms: 17_900, p95_ms: 42_600, ttft_p50_ms: 1_960 },
    },
    turns: { solo: Math.round(active * 3.1), duel: Math.round(active * 1.4), debate: debates, refine: Math.round(active * 0.6) },
    consensus: { debates, reached: Math.round(debates * 0.81), avg_rounds: 1.7 },
    costs: {
      fx: FX,
      by_agent: {
        claude: { api_usd: 0, equivalent_usd: claude.cost_usd, unpriced_calls: 0 },
        chatgpt: { api_usd: 0, equivalent_usd: chatgpt.cost_usd, unpriced_calls: 0 },
      },
    },
    month: month(),
  };
}

const json = (body: unknown): Response =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

/** The language of a request, as the server reads it: its Accept-Language, else English. */
function requestLocale(input: RequestInfo | URL, init?: RequestInit): Locale {
  const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
  return asLocale(headers.get('Accept-Language')) ?? 'en';
}

/** The REST API: FakeApi, plus the dashboard's statistics. */
export function showcaseApi(): { api: FakeApi; fetch: typeof window.fetch } {
  const api = new FakeApi(SETTINGS);
  api.pricing = PRICING;
  api.spend = month();
  const fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), location.origin);
    // A server a few milliseconds away: the app paints its loading states as it does live.
    await new Promise((resolve) => setTimeout(resolve, 40));
    if (url.pathname === '/api/stats') return json(stats(Number(url.searchParams.get('days') ?? 30)));
    const lang = requestLocale(input, init);
    api.providers = providers(lang);
    api.catalog = catalog(lang);
    return api.fetch(input, init);
  };
  return { api, fetch };
}

// ---------------------------------------------------------------------- the socket

/** Turns the server is running (or ran a moment ago), replayed to a `turn.subscribe`. */
export const live = new Map<string, { conversationId: number; events: TurnEvent[] }>();

/** A WebSocket to the showcase's server: it opens at once and says hello, in its ?lang=. */
export class ShowcaseSocket extends FakeSocket {
  constructor(url: string) {
    super(url);
    const lang = asLocale(new URL(url, location.href).searchParams.get('lang')) ?? 'en';
    setTimeout(() => {
      this.open();
      this.receive({
        type: 'hello',
        version: '0.2.0',
        providers: providers(lang),
        fx: FX,
        active_turns: [...live].map(([requestId, turn]) => ({
          request_id: requestId,
          conversation_id: turn.conversationId,
          last_seq: turn.events.at(-1)?.seq ?? 0,
        })),
      });
    }, 20);
  }

  override send(data: string): void {
    super.send(data);
    const msg = JSON.parse(data) as { type: string; t?: number; request_id?: string; after_seq?: number };
    if (msg.type === 'ping') setTimeout(() => this.receive({ type: 'pong', t: msg.t ?? 0 }), 23);
    if (msg.type === 'turn.subscribe') {
      for (const ev of live.get(msg.request_id ?? '')?.events ?? []) {
        if (ev.seq > (msg.after_seq ?? 0)) this.receive(ev as ServerMessage);
      }
    }
  }
}
