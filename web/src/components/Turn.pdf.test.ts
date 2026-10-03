// Claude's check of the PDFs for ChatGPT with the subscription, as the owner sees it
// (docs/PROTOCOL.md "Attachments", docs/adr/0009-attachments.md): Codex cannot open a PDF, so it
// reads the text the server extracted, and Claude checks that text while it answers. The
// turn shows each PDF's check live, from the `pdf.check` events, and ChatGPT's messages
// carry a badge that says how it read them, live (stream.completed) and after a reload
// (meta.pdf_reading). Driven through the real app, with the fake server and socket.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  Attachment,
  ConversationSummary,
  Message,
  PdfReading,
  ProviderStatus,
  RuntimeSettings,
  ServerMessage,
  TurnEvent,
  Usage,
} from '../lib/protocol';
import { priced, sequence } from '../lib/test-fixtures';
import { FakeApi, FakeSocket, hello } from '../lib/test-server';
import type { TurnView } from '../lib/turns.svelte';

const SAVED: RuntimeSettings = {
  revision: 1,
  default_mode: 'duel',
  default_target: 'claude',
  debate: { rounds: 1, consensus_threshold: 85, synthesizer: 'claude' },
  refine: { max_rounds: 12, budget_eur: 3, max_words: null, stop_on_convergence: true, convergence_threshold: 90, editor: 'claude' },
  use_cache: false,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'manual', eur_per_usd: 0.9 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
  pdf_in_revisions: 'text',
};

const provider = (agent: ProviderStatus['agent'], mode: ProviderStatus['mode']): ProviderStatus => ({
  agent,
  mode,
  available: true,
  model: agent === 'claude' ? 'claude-opus' : 'gpt-5',
  detail: '',
  limits: [],
});
const subscription = (agent: ProviderStatus['agent']): ProviderStatus => provider(agent, 'cli');
const SUBSCRIPTIONS = [subscription('claude'), subscription('chatgpt')];

const PDF_BYTES = new TextEncoder().encode('%PDF-1.7\n%âãÏÓ\n');
const QUESTION = "Què diu l'informe?";
const CHECK_USAGE: Usage = { ...priced(9000, 700, 0.0123), cache_read_tokens: 2000 };
const ANSWER = priced(1200, 300, 0.004);
const NO_SAVINGS = { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null };

const READING: PdfReading = {
  attachment_id: 7,
  name: 'informe.pdf',
  checked: true,
  claude_pages: [2, 5],
  hidden_pages: [5],
  unchecked_pages: [],
  reason: null,
};

/** What a check event says, over a checked «informe.pdf». */
const check = (partial: Partial<Extract<TurnEvent, { type: 'pdf.check' }>>) => ({
  type: 'pdf.check' as const,
  attachment_id: 7,
  name: 'informe.pdf',
  state: 'checked' as const,
  claude_pages: [2, 5],
  hidden_pages: [5],
  unchecked_pages: [],
  reused: false,
  usage: CHECK_USAGE,
  reason: null,
  ...partial,
});

/** A new app (as after a page load), from the same module graph as the components. */
async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: Turn } = await import('./Turn.svelte');
  return { app, render, cleanup, textOf, flushSync, Turn };
}

let server: FakeApi;
let env: Awaited<ReturnType<typeof load>> | null = null;

beforeAll(async () => {
  await load(); // compiles the components once, outside the tests' time limit
});

beforeEach(() => {
  localStorage.clear();
  history.replaceState(null, '', '#/');
  server = new FakeApi(SAVED);
  FakeSocket.all = [];
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  env?.cleanup();
  env?.app.toLogin(); // stops this app's timers and socket
  env = null;
  vi.unstubAllGlobals();
});

/** The app logged in, with the socket open and the agents `providers` (by default, both on the subscription). */
async function connected(providers: ProviderStatus[] = SUBSCRIPTIONS) {
  server.providers = [...providers];
  env = await load();
  const e = env;
  await e.app.init();
  const socket = FakeSocket.last();
  socket.open();
  const greeting = hello();
  if (greeting.type === 'hello') greeting.providers = [...providers];
  socket.receive(greeting);
  return { e, socket };
}

interface Sent {
  mode?: 'solo' | 'duel';
  /** Who a solo turn asks. */
  target?: 'claude' | 'chatgpt';
  providers?: ProviderStatus[];
}

/**
 * A turn (a duel, by default) sent with the PDFs `pdfs` (already on the server), and its live
 * turn rendered. `receive` hands the socket events of the turn, numbered from the last one sent.
 */
async function liveTurn(pdfs: Attachment[], { mode = 'duel', target = 'chatgpt', providers }: Sent = {}) {
  const { e, socket } = await connected(providers);
  e.app.composer.attachments.restore(pdfs);
  e.app.composer.mode = mode;
  if (mode === 'solo') e.app.composer.target = target;
  expect(e.app.send(QUESTION)).toBe(true);
  const requestId = String(socket.sent.find((m) => m.type === 'turn.start')!.request_id);
  const turn = e.app.turns.get(requestId)!;
  const root = e.render(e.Turn, { turn, plannedRounds: 0 });
  let seq = 0;
  const receive = (...drafts: Parameters<typeof sequence>[1]) => {
    for (const ev of sequence(requestId, drafts)) socket.receive({ ...ev, seq: ++seq } as ServerMessage);
    e.flushSync();
  };
  return { e, root, turn, receive };
}

const liveDuel = (pdfs: Attachment[]) => liveTurn(pdfs);

/** The app again (as after a reload), showing conversation 3 with the stored `messages`. */
async function reloaded(messages: Message[], providers?: ProviderStatus[]) {
  env!.cleanup();
  env!.app.toLogin();
  const summary: ConversationSummary = {
    id: 3, title: 'Informe', created_at: '2026-09-29T10:00:00Z', updated_at: '2026-09-29T10:00:00Z', last_mode: 'duel',
    message_count: messages.length,
  };
  server.conversations = [summary];
  server.messages.set(3, messages);
  const { e } = await connected(providers);
  await e.app.syncRoute({ name: 'chat', id: 3 });
  const [stored] = e.app.viewTurns as TurnView[];
  return { e, root: e.render(e.Turn, { turn: stored!, plannedRounds: 0 }) };
}

function storedPdf(id: number, name: string, pages = 12): Attachment {
  return server.addAttachment(
    { id, name, kind: 'pdf', mime: 'application/pdf', pages, pdf_notes: { no_text: [], garbled: [], hidden: [5] } },
    PDF_BYTES,
  );
}

const checks = (root: HTMLElement) => [...root.querySelectorAll('.pdf-checks .pdf-check')];
const details = (el: Element) => [...el.querySelectorAll('.detail')].map((d) => env!.textOf(d));

// Intl uses a no-break space before "€" in Catalan.
const plain = (s: string) => s.replace(/ | /g, ' ');

describe("the turn shows Claude's check of each PDF for ChatGPT, live", () => {
  it('from checking, while Claude answers, to checked: what ChatGPT reads through Claude and what it cost', async () => {
    const { e, root, receive } = await liveDuel([storedPdf(7, 'informe.pdf')]);
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
      { type: 'phase', phase: 'answer', round: 0 },
      { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'claude-opus' },
      check({ state: 'checking', claude_pages: [], hidden_pages: [], usage: null }),
    );
    // A polite live region holds the list: each change of a check is announced.
    const region = root.querySelector('.pdf-live')!;
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.querySelector('.pdf-checks')).not.toBeNull();
    let [item] = checks(root);
    expect(e.textOf(item!.querySelector('.head'))).toBe('Claude contrasta «informe.pdf» per a ChatGPT…');
    expect(item!.classList.contains('checking')).toBe(true);
    // A spinner, for the eyes only: the words say it is under way.
    const spinner = item!.querySelector('.spinner')!;
    expect(spinner.closest('[aria-hidden="true"]')).not.toBeNull();
    expect(details(item!)).toEqual([]);
    // ChatGPT waits for it.
    expect(e.textOf(root.querySelector('article.card.chatgpt'))).toContain('Pensant…');

    receive(check({}));
    [item] = checks(root);
    expect(item!.classList.contains('checked')).toBe(true);
    expect(item!.querySelector('.spinner')).toBeNull();
    expect(e.textOf(item!.querySelector('.head'))).toBe('ChatGPT llegeix «informe.pdf» contrastat per Claude');
    expect(details(item!).map((d) => plain(d).replace(/ \(.*\)$/, ''))).toEqual([
      'pàgines 2 i 5 llegides per Claude',
      "text ocult a la pàg. 5: no s'ha passat",
      '11.700 tokens',
      '≈ 0,0111 €',
    ]);
    // Tokens and euros like the other usage of the turn: each kind, and the cost's basis.
    const tokens = item!.querySelector('.detail.tokens')!;
    expect(tokens.getAttribute('title')).toBe("9.000 d'entrada · 2.000 llegits de la memòria cau · 700 de sortida");
    expect(e.textOf(tokens.querySelector('.sr-only'))).toBe(`(${tokens.getAttribute('title')})`);
    const cost = item!.querySelector('.detail.cost')!;
    expect(cost.getAttribute('title')).toBe("Valor equivalent a preus d'API — inclòs a la subscripció (0,0123 $)");
    expect(e.textOf(cost.querySelector('.sr-only'))).toBe(`(${cost.getAttribute('title')})`);
  });

  it('a check made in an earlier turn is reused; one that could not be made says why', async () => {
    const { root, receive } = await liveDuel([storedPdf(7, 'informe.pdf'), storedPdf(8, 'annex.pdf', 3)]);
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
      // The events come as the checks end: the list keeps the order of the attachments.
      check({ attachment_id: 8, name: 'annex.pdf', state: 'checking', claude_pages: [], hidden_pages: [], usage: null }),
      check({ reused: true, usage: null }),
    );
    let items = checks(root);
    expect(items.map((i) => env!.textOf(i.querySelector('.head')))).toEqual([
      'ChatGPT llegeix «informe.pdf» contrastat per Claude',
      'Claude contrasta «annex.pdf» per a ChatGPT…',
    ]);
    expect(details(items[0]!)).toEqual([
      'pàgines 2 i 5 llegides per Claude',
      "text ocult a la pàg. 5: no s'ha passat",
      'ja contrastat abans',
    ]);

    receive(
      check({
        attachment_id: 8,
        name: 'annex.pdf',
        state: 'unchecked',
        claude_pages: [],
        hidden_pages: [],
        unchecked_pages: [1, 2, 3],
        usage: priced(0, 0, 0),
        reason: 'La comprovació de Claude ha trigat massa.',
      }),
    );
    items = checks(root);
    expect(items[1]!.classList.contains('unchecked')).toBe(true);
    // The reason is a sentence of its own: after the colon it goes in lowercase.
    expect(env!.textOf(items[1]!.querySelector('.head'))).toBe(
      'ChatGPT llegeix el text de «annex.pdf» sense contrastar: la comprovació de Claude ha trigat massa.',
    );
    // Nothing billed: no cost to show.
    expect(details(items[1]!)).toEqual([]);
  });

  it('a check that ran out of calls says which pages were left unchecked', async () => {
    const { root, receive } = await liveDuel([storedPdf(7, 'informe.pdf', 40)]);
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
      check({
        claude_pages: [1, 2, 3, 4],
        hidden_pages: [],
        unchecked_pages: Array.from({ length: 10 }, (_, i) => 31 + i),
        reason: "Claude només l'ha pogut contrastar fins a la pàgina 30.",
      }),
    );
    const [item] = checks(root);
    expect(details(item!).slice(0, 2)).toEqual([
      'pàgines 1–4 llegides per Claude',
      "pàgines 31–40 sense contrastar: Claude només l'ha pogut contrastar fins a la pàgina 30.",
    ]);
  });

  it('a turn without any check shows none', async () => {
    const { root, receive } = await liveDuel([]);
    receive({ type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false });
    expect(root.querySelector('.pdf-checks')).toBeNull();
  });
});

// A live region inserted together with its content is not announced, and a PDF's only event
// may be its end: a check an earlier turn stored, a PDF the server could not analyse, a turn
// without Claude. So the region is in the page from the moment the turn is sent.
describe('screen readers hear every check of a live turn, its first event too', () => {
  const lone = [
    ['a check an earlier turn stored', check({ reused: true, usage: null }), 'ChatGPT llegeix «informe.pdf» contrastat per Claude'],
    [
      'a PDF the server could not analyse',
      check({
        state: 'unchecked', claude_pages: [], hidden_pages: [], unchecked_pages: [1, 2, 3], usage: priced(0, 0, 0),
        reason: "El servidor no n'ha pogut analitzar les pàgines.",
      }),
      "ChatGPT llegeix el text de «informe.pdf» sense contrastar: el servidor no n'ha pogut analitzar les pàgines.",
    ],
    [
      'a turn without Claude',
      check({
        state: 'unchecked', claude_pages: [], hidden_pages: [], unchecked_pages: [1, 2, 3], usage: null,
        reason: 'Claude no està disponible per contrastar-lo.',
      }),
      // A reason that starts with a name keeps its capital.
      'ChatGPT llegeix el text de «informe.pdf» sense contrastar: Claude no està disponible per contrastar-lo.',
    ],
  ] as const;

  for (const [label, end, headline] of lone) {
    it(`${label}: its only event goes into a region that was already in the page`, async () => {
      const { e, root, receive } = await liveDuel([storedPdf(7, 'informe.pdf', 3)]);
      const region = root.querySelector('.pdf-live')!;
      expect(region.getAttribute('aria-live')).toBe('polite');
      receive({ type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false });
      expect(root.querySelector('.pdf-live')).toBe(region);
      expect(e.textOf(region)).toBe('');

      receive(end);
      // The same region, still in the page: what came is a change inside it.
      expect(root.querySelector('.pdf-live')).toBe(region);
      expect(region.isConnected).toBe(true);
      expect(e.textOf(region.querySelector('.head'))).toBe(headline);
    });
  }

  it('takes no room while it is empty', async () => {
    const { root, receive } = await liveDuel([storedPdf(7, 'informe.pdf', 3)]);
    const region = root.querySelector('.pdf-live')!;
    // Out of the layout (no gap in the turn), yet in the page for screen readers.
    expect(region.classList.contains('sr-only')).toBe(true);
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
      check({ reused: true, usage: null }),
    );
    expect(region.classList.contains('sr-only')).toBe(false);
  });

  it('goes once the turn ends without any check', async () => {
    const { root, receive } = await liveDuel([]);
    expect(root.querySelector('.pdf-live')).not.toBeNull();
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 13, mode: 'duel', new_conversation: false },
      { type: 'turn.cancelled', usage: priced(0, 0, 0) },
    );
    expect(root.querySelector('.pdf-live')).toBeNull();
  });
});

// The server sends no end for a check that a cancelled or failed turn stopped: the turn's end
// settles it, so the turn never looks as if Claude still worked on it (and billed it).
describe('a turn that ends while Claude checks its PDF', () => {
  const ends = [
    [{ type: 'turn.cancelled', usage: CHECK_USAGE }, "Aquest torn s'ha aturat."],
    [
      { type: 'turn.failed', error: { kind: 'internal', message: 'Error intern.' }, usage: CHECK_USAGE },
      'El torn ha fallat. Error intern.',
    ],
  ] as const;

  for (const [end, banner] of ends) {
    it(`${end.type}: the check stopped with it, and ChatGPT read nothing through it`, async () => {
      const { e, root, receive } = await liveDuel([storedPdf(7, 'informe.pdf')]);
      receive(
        { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
        { type: 'phase', phase: 'answer', round: 0 },
        { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'claude-opus' },
        check({ state: 'checking', claude_pages: [], hidden_pages: [], usage: null }),
        end,
      );
      expect(e.textOf(root.querySelector('.banner'))).toBe(banner);
      const [item] = checks(root);
      expect(item!.classList.contains('checking')).toBe(false);
      expect(item!.classList.contains('interrupted')).toBe(true);
      expect(item!.querySelector('.spinner')).toBeNull();
      expect(e.textOf(item!.querySelector('.head'))).toBe(
        "El torn s'ha aturat abans que Claude acabés de contrastar «informe.pdf».",
      );
      expect(e.textOf(item)).not.toContain('ChatGPT llegeix');
      // What the check billed is in the turn's total, not on its own.
      expect(details(item!)).toEqual([]);
    });
  }
});

// Claude's check calls are in the turn's total and on no answer. In a solo turn for ChatGPT with
// the subscription, the reloaded turn knows only that ChatGPT read the PDF through a check, not
// what kind of cost Claude's calls were: the total says the same live and after a reload (P5),
// and never calls Claude's API spend a value included in the subscription.
describe("the turn's total when Claude by API checked the PDF for ChatGPT with the subscription", () => {
  const CHECK = priced(40_000, 800, 0.05);
  const SOLO_ANSWER = priced(1200, 300, 0.01);
  const TOTAL = priced(41_200, 1100, 0.06);
  const PROVIDERS = [provider('claude', 'api'), provider('chatgpt', 'cli')];
  const reading: PdfReading = { ...READING, claude_pages: [2], hidden_pages: [] };

  function storedSolo(pdf: Attachment): Message[] {
    const at = '2026-09-29T10:00:00Z';
    return [
      {
        id: 11, turn_id: 12, kind: 'question', content: QUESTION, agent: null, round: 0, final: true, created_at: at,
        meta: {
          mode: 'solo',
          target: 'chatgpt',
          attachments: [pdf],
          outcome: {
            status: 'completed', failures: [], usage: TOTAL, savings: NO_SAVINGS, consensus: null, final_message_ids: [14],
            cached: false,
          },
        },
      },
      {
        id: 14, turn_id: 12, kind: 'answer', content: 'Resposta de ChatGPT', agent: 'chatgpt', round: 0, final: true, created_at: at,
        meta: { model: 'gpt-5', usage: SOLO_ANSWER, cost_basis: 'equivalent', pdf_reading: [reading] },
      },
    ];
  }

  it('puts the check among the other calls, live and after a reload', async () => {
    const pdf = storedPdf(7, 'informe.pdf');
    const { root, receive } = await liveTurn([pdf], { mode: 'solo', target: 'chatgpt', providers: PROVIDERS });
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'solo', new_conversation: false },
      check({ state: 'checking', claude_pages: [], hidden_pages: [], usage: null }),
      check({ claude_pages: [2], hidden_pages: [], usage: CHECK }),
      { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
      {
        type: 'stream.completed', stream_id: 'g', message_id: 14, usage: SOLO_ANSWER, latency_ms: 900, ttft_ms: 100,
        agreement: null, unchanged: false, cost_basis: 'equivalent', pdf_reading: [reading],
      },
      {
        type: 'turn.completed', conversation_id: 3, turn_id: 12, final_message_ids: [14], usage: TOTAL, savings: NO_SAVINGS,
        consensus: null, cached: false,
      },
    );
    // The check's own cost says what it is: Claude's mode tells it, live.
    expect(root.querySelector('.pdf-check .detail.cost')!.getAttribute('title')).toBe("Cost real de l'API (0,05 $)");
    const live = root.querySelector('.totals .cost')!.getAttribute('title')!;
    expect(plain(live)).toBe(
      "Cost del torn a preus d'API · valor inclòs a la subscripció: 0,009 € · altres crides: 0,045 €",
    );

    const { root: again } = await reloaded(storedSolo(pdf), PROVIDERS);
    expect(again.querySelector('.totals .cost')!.getAttribute('title')).toBe(live);
  });
});

// ---------------------------------------------------------------- the badge

/** The stored messages of the duel of `liveTurnEvents`, as the server keeps them. */
function storedDuel(pdf: Attachment): Message[] {
  const at = '2026-09-29T10:00:00Z';
  return [
    {
      id: 11, turn_id: 12, kind: 'question', content: QUESTION, agent: null, round: 0, final: true, created_at: at,
      meta: {
        mode: 'duel',
        attachments: [pdf],
        outcome: {
          status: 'completed', failures: [], usage: priced(13_700, 1300, 0.0203), savings: NO_SAVINGS, consensus: null,
          final_message_ids: [13, 14], cached: false,
        },
      },
    },
    {
      id: 13, turn_id: 12, kind: 'answer', content: 'Resposta de Claude', agent: 'claude', round: 0, final: true, created_at: at,
      meta: { model: 'claude-opus', usage: ANSWER, cost_basis: 'equivalent' },
    },
    {
      id: 14, turn_id: 12, kind: 'answer', content: 'Resposta de ChatGPT', agent: 'chatgpt', round: 0, final: true, created_at: at,
      meta: { model: 'gpt-5', usage: ANSWER, cost_basis: 'equivalent', pdf_reading: [READING] },
    },
  ];
}

const liveTurnEvents: Parameters<typeof sequence>[1] = [
  { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
  { type: 'phase', phase: 'answer', round: 0 },
  { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'claude-opus' },
  check({ state: 'checking', claude_pages: [], hidden_pages: [], usage: null }),
  { type: 'stream.delta', stream_id: 'c', section: 'text', text: 'Resposta de Claude' },
  check({}),
  { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
  { type: 'stream.delta', stream_id: 'g', section: 'text', text: 'Resposta de ChatGPT' },
  {
    type: 'stream.completed', stream_id: 'c', message_id: 13, usage: ANSWER, latency_ms: 900, ttft_ms: 100,
    agreement: null, unchanged: false, cost_basis: 'equivalent',
  },
  {
    type: 'stream.completed', stream_id: 'g', message_id: 14, usage: ANSWER, latency_ms: 900, ttft_ms: 100,
    agreement: null, unchanged: false, cost_basis: 'equivalent', pdf_reading: [READING],
  },
  {
    type: 'turn.completed', conversation_id: 3, turn_id: 12, final_message_ids: [13, 14], usage: priced(13_700, 1300, 0.0203),
    savings: NO_SAVINGS, consensus: null, cached: false,
  },
];

/** The badge of an agent's card: its label, its tooltip and how they are tied. */
function badge(root: HTMLElement, agent: 'claude' | 'chatgpt') {
  const card = root.querySelector(`article.card.${agent}`)!;
  const button = card.querySelector<HTMLButtonElement>('.pdf-reading button');
  if (!button) return null;
  const tip = document.getElementById(button.getAttribute('aria-describedby') ?? '');
  expect(card.contains(tip)).toBe(true);
  return { button, label: env!.textOf(button), tip: env!.textOf(tip), role: tip?.getAttribute('role') };
}

describe("the badge on ChatGPT's messages says how it read the PDFs", () => {
  it('live, from stream.completed, and the same after a reload (meta.pdf_reading)', async () => {
    const pdf = storedPdf(7, 'informe.pdf');
    const { e, root, receive } = await liveDuel([pdf]);
    receive(...liveTurnEvents);
    const live = badge(root, 'chatgpt')!;
    expect(live.label).toBe('PDF contrastat per Claude');
    expect(live.role).toBe('tooltip');
    expect(live.tip).toBe(
      "ChatGPT no pot obrir els PDF: n'ha llegit el text extret, contrastat per Claude. informe.pdf " +
        'Llegides per Claude pàg. 2, 5 Text ocult, no passat pàg. 5',
    );
    // A tap shows it too (phones have no hover).
    expect(live.button.getAttribute('aria-expanded')).toBe('false');
    live.button.click();
    e.flushSync();
    expect(live.button.getAttribute('aria-expanded')).toBe('true');
    // Claude reads the PDF itself: no badge.
    expect(badge(root, 'claude')).toBeNull();
    // The check stays in the finished turn.
    expect(checks(root)).toHaveLength(1);

    // A reload: the conversation comes from the server.
    const { e: again, root: shown } = await reloaded(storedDuel(pdf));
    const after = badge(shown, 'chatgpt')!;
    expect([after.label, after.tip, after.role]).toEqual([live.label, live.tip, live.role]);
    expect(badge(shown, 'claude')).toBeNull();
    // What the check did live is not stored: the badge says it.
    expect(shown.querySelector('.pdf-checks')).toBeNull();
    expect(shown.querySelector('.pdf-live')).toBeNull();
    // The question's card shows the warnings of the PDF's pages.
    expect(again.textOf(shown.querySelector('.question .pdf-note .note-text'))).toBe('Possible text ocult: pàg. 5');
  });

  it("an unchecked PDF's badge says so, with its pages and why", async () => {
    const { root, receive } = await liveDuel([storedPdf(7, 'informe.pdf', 3)]);
    const unchecked: PdfReading = {
      ...READING,
      checked: false,
      claude_pages: [],
      hidden_pages: [],
      unchecked_pages: [1, 2, 3],
      reason: 'Claude no està disponible per contrastar-lo.',
    };
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'solo', new_conversation: false },
      { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
      {
        type: 'stream.completed', stream_id: 'g', message_id: 14, usage: ANSWER, latency_ms: 900, ttft_ms: 100,
        agreement: null, unchanged: false, pdf_reading: [unchecked],
      },
    );
    const shown = badge(root, 'chatgpt')!;
    expect(shown.label).toBe('PDF sense contrastar');
    expect(shown.button.classList.contains('warn')).toBe(true);
    expect(shown.tip).toBe(
      "ChatGPT no pot obrir els PDF: n'ha llegit el text extret, sense contrastar. informe.pdf " +
        'Sense contrastar pàg. 1–3 Claude no està disponible per contrastar-lo.',
    );
  });
});

describe("the badge on a debate's revisions and synthesis by ChatGPT", () => {
  it('each ChatGPT message of the turn carries it, live and after a reload', async () => {
    const { root, receive } = await liveDuel([storedPdf(7, 'informe.pdf')]);
    const done = (streamId: string, messageId: number, extra: object = {}) =>
      ({
        type: 'stream.completed', stream_id: streamId, message_id: messageId, usage: ANSWER, latency_ms: 900, ttft_ms: 100,
        agreement: null, unchanged: false, ...extra,
      }) as const;
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'debate', new_conversation: false },
      { type: 'stream.started', stream_id: 'c0', agent: 'claude', kind: 'answer', round: 0, model: 'claude-opus' },
      check({}),
      { type: 'stream.started', stream_id: 'g0', agent: 'chatgpt', kind: 'answer', round: 0, model: 'gpt-5' },
      done('c0', 13),
      done('g0', 14, { pdf_reading: [READING] }),
      { type: 'phase', phase: 'revision', round: 1 },
      { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'claude-opus' },
      { type: 'stream.started', stream_id: 'g1', agent: 'chatgpt', kind: 'revision', round: 1, model: 'gpt-5' },
      { type: 'stream.delta', stream_id: 'g1', section: 'critique', text: '- Bé' },
      done('c1', 15, { agreement: 90 }),
      done('g1', 16, { agreement: 90, pdf_reading: [READING] }),
      { type: 'phase', phase: 'synthesis', round: 1 },
      { type: 'stream.started', stream_id: 's', agent: 'chatgpt', kind: 'synthesis', round: 1, model: 'gpt-5' },
      { type: 'stream.delta', stream_id: 's', section: 'text', text: 'Síntesi' },
      done('s', 17, { pdf_reading: [READING] }),
    );
    const revision = root.querySelector('section[aria-label="Revisió de ChatGPT"]')!;
    expect(env!.textOf(revision.querySelector('.pdf-reading button'))).toBe('PDF contrastat per Claude');
    expect(root.querySelector('section[aria-label="Revisió de Claude"] .pdf-reading')).toBeNull();
    const synthesis = root.querySelector('article.card.synthesis')!;
    expect(env!.textOf(synthesis.querySelector('.pdf-reading button'))).toBe('PDF contrastat per Claude');
  });
});

describe('in Spanish', () => {
  // The language of a page load: the owner's choice, kept in this browser.
  beforeEach(() => localStorage.setItem('aos.lang', 'es'));

  it("the check of a PDF, live, and the details of a finished one", async () => {
    const { e, root, receive } = await liveDuel([storedPdf(7, 'informe.pdf')]);
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
      { type: 'phase', phase: 'answer', round: 0 },
      { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: 'claude-opus' },
      check({ state: 'checking', claude_pages: [], hidden_pages: [], usage: null }),
    );
    expect(root.querySelector('.pdf-checks')?.getAttribute('aria-label')).toBe('Contraste de los PDF para ChatGPT');
    let [item] = checks(root);
    expect(e.textOf(item!.querySelector('.head'))).toBe('Claude contrasta «informe.pdf» para ChatGPT…');
    expect(e.textOf(root.querySelector('article.card.chatgpt'))).toContain('Pensando…');

    receive(check({}));
    [item] = checks(root);
    expect(e.textOf(item!.querySelector('.head'))).toBe('ChatGPT lee «informe.pdf» contrastado por Claude');
    expect(details(item!).map((d) => plain(d).replace(/ \(.*\)$/, ''))).toEqual([
      'páginas 2 y 5 leídas por Claude',
      'texto oculto en la pág. 5: no se ha pasado',
      '11.700 tokens',
      '≈ 0,0111 €',
    ]);
    expect(item!.querySelector('.detail.tokens')!.getAttribute('title')).toBe('9000 de entrada · 2000 leídos de la caché · 700 de salida');
    expect(item!.querySelector('.detail.cost')!.getAttribute('title')).toBe(
      'Valor equivalente a precios de API — incluido en la suscripción (0,0123 $)',
    );
  });

  it('a check that could not be made says why, in the words of the server', async () => {
    const { e, root, receive } = await liveDuel([storedPdf(7, 'informe.pdf')]);
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
      check({ state: 'unchecked', claude_pages: [], hidden_pages: [], unchecked_pages: [1, 2, 3], usage: null, reason: 'La comprobación de Claude ha tardado demasiado.' }),
    );
    expect(e.textOf(checks(root)[0]!.querySelector('.head'))).toBe(
      'ChatGPT lee el texto de «informe.pdf» sin contrastar: la comprobación de Claude ha tardado demasiado.',
    );
  });

  it("the badge on ChatGPT's message", async () => {
    const { root, receive } = await liveDuel([storedPdf(7, 'informe.pdf')]);
    receive(...liveTurnEvents);
    const shown = badge(root, 'chatgpt')!;
    expect(shown.label).toBe('PDF contrastado por Claude');
    expect(shown.tip).toBe(
      'ChatGPT no puede abrir los PDF: ha leído el texto extraído, contrastado por Claude. informe.pdf ' +
        'Leídas por Claude págs. 2, 5 Texto oculto, no pasado pág. 5',
    );
  });
});

describe('in English', () => {
  beforeEach(() => localStorage.setItem('aos.lang', 'en'));

  it('a check made in an earlier turn is reused', async () => {
    const { root, receive } = await liveDuel([storedPdf(7, 'informe.pdf'), storedPdf(8, 'annex.pdf', 3)]);
    receive(
      { type: 'turn.started', conversation_id: 3, turn_id: 12, mode: 'duel', new_conversation: false },
      check({ attachment_id: 8, name: 'annex.pdf', state: 'checking', claude_pages: [], hidden_pages: [], usage: null }),
      check({ reused: true, usage: null }),
    );
    const items = checks(root);
    expect(items.map((i) => env!.textOf(i.querySelector('.head')))).toEqual([
      'ChatGPT reads “informe.pdf”, checked by Claude',
      'Claude is checking “annex.pdf” for ChatGPT…',
    ]);
    expect(details(items[0]!)).toEqual(['pages 2 and 5 read by Claude', 'hidden text on p. 5: not passed on', 'already checked']);
  });
});
