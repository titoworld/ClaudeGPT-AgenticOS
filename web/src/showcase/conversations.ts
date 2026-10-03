// Example conversations of the showcase (index.html): what the server stores of them and,
// for the turns the page shows live, the events it sends, with the shapes the engine
// writes (orchestrator/engine.py). The texts are in content/*.md.

import type {
  Agent,
  Attachment,
  ConversationSummary,
  Message,
  MessageMeta,
  PdfReading,
  RefineChange,
  RefineMeta,
  RefineOptions,
  Savings,
  TurnEvent,
  TurnOutcome,
  Usage,
} from '../lib/protocol';
import { addUsage, emptyUsage } from '../lib/turns.svelte';
import consell from './content/consell.md?raw';
import copies from './content/copies.md?raw';
import pressupost from './content/pressupost.md?raw';

export const MODEL: Record<Agent, string> = { claude: 'claude-opus-5-5', chatgpt: 'gpt-6-astra' };

/** USD per million tokens (input, output, cache read), as pricing.py has them. */
const PRICE: Record<Agent, [number, number, number]> = { claude: [4, 20, 0.2], chatgpt: [10, 50, 1] };

/** The sections of a content file, by the name of their `<!-- @name -->` marker. */
function sections(raw: string): Record<string, string> {
  const parts = raw.split(/^<!-- @([\w-]+) -->\n/m);
  const out: Record<string, string> = {};
  for (let i = 1; i < parts.length; i += 2) out[parts[i]!] = parts[i + 1]!.trim();
  return out;
}

function text(all: Record<string, string>, name: string): string {
  const value = all[name];
  if (value === undefined) throw new Error(`Missing section ${name}`);
  return value;
}

/** What a subscription call would have cost at API prices (the `equivalent` basis). */
function call(agent: Agent, input: number, output: number, cacheRead = 0, reasoning = 0): Usage {
  const [i, o, c] = PRICE[agent];
  const cost = (input * i + output * o + cacheRead * c) / 1e6;
  return {
    input_tokens: input,
    output_tokens: output,
    cache_read_tokens: cacheRead,
    cache_write_tokens: 0,
    reasoning_tokens: reasoning,
    cost_usd: Math.round(cost * 1e6) / 1e6,
  };
}

const total = (parts: Usage[]): Usage => parts.reduce(addUsage, emptyUsage());

const NO_SAVINGS: Savings = { cache: 0, compaction: 0, early_stop: 0, unchanged: 0, total: 0, cost_usd: null };

/** Words as the engine counts them (`domain.words`). */
const words = (value: string): number => value.split(/\s+/).filter(Boolean).length;

/** A time `minutes` before the page was opened, as the server writes timestamps. */
export const ago = (minutes: number): string => new Date(Date.now() - minutes * 60_000).toISOString();

interface Call {
  usage: Usage;
  latency: number;
  ttft: number;
}

/** The meta of a stored answer, revision or synthesis. */
function meta(agent: Agent, c: Call, extra: MessageMeta = {}): MessageMeta {
  return {
    model: MODEL[agent],
    usage: c.usage,
    latency_ms: c.latency,
    ttft_ms: c.ttft,
    cached: false,
    cost_basis: 'equivalent',
    ...extra,
  };
}

function stored(partial: Omit<Message, 'agent' | 'round' | 'final' | 'meta'> & Partial<Message>): Message {
  return { agent: null, round: 0, final: false, meta: {}, ...partial };
}

// --------------------------------------------------------------------- the council

const C = sections(consell);
export const DEBATE_ID = 41;
const DQ = 410;

const DEBATE_CALLS = {
  claude0: { usage: call('claude', 1180, 640, 0, 120), latency: 17_840, ttft: 1_420 },
  chatgpt0: { usage: call('chatgpt', 1150, 590, 0, 210), latency: 21_930, ttft: 2_080 },
  claude1: { usage: call('claude', 1850, 720, 1100, 140), latency: 19_210, ttft: 1_310 },
  chatgpt1: { usage: call('chatgpt', 1900, 610, 1024, 190), latency: 18_660, ttft: 1_870 },
  claude2: { usage: call('claude', 1400, 60, 2300), latency: 3_480, ttft: 1_150 },
  chatgpt2: { usage: call('chatgpt', 1350, 55, 2048), latency: 4_120, ttft: 1_640 },
  synthesis: { usage: call('claude', 2600, 880, 1500, 160), latency: 23_570, ttft: 1_390 },
} satisfies Record<string, Call>;

const DEBATE_SAVINGS: Savings = { cache: 0, compaction: 0, early_stop: 6400, unchanged: 1260, total: 7660, cost_usd: 0.1121 };
const DEBATE_CONSENSUS = { reached: true, round: 2, scores: { claude: 92, chatgpt: 90 } };
const DEBATE_OUTCOME: TurnOutcome = {
  status: 'completed',
  failures: [],
  usage: total(Object.values(DEBATE_CALLS).map((c) => c.usage)),
  savings: DEBATE_SAVINGS,
  consensus: DEBATE_CONSENSUS,
  final_message_ids: [DQ + 7],
  cached: false,
};

export function debateMessages(): Message[] {
  const d = DEBATE_CALLS;
  const turn = { turn_id: DQ };
  return [
    stored({
      ...turn, id: DQ, kind: 'question', content: text(C, 'question'), final: true, created_at: ago(19),
      meta: {
        mode: 'debate', target: 'claude',
        options: { debate: { rounds: 3, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: true },
        outcome: DEBATE_OUTCOME,
      },
    }),
    stored({ ...turn, id: DQ + 1, kind: 'answer', agent: 'claude', content: text(C, 'claude-answer'), created_at: ago(18), meta: meta('claude', d.claude0) }),
    stored({ ...turn, id: DQ + 2, kind: 'answer', agent: 'chatgpt', content: text(C, 'chatgpt-answer'), created_at: ago(18), meta: meta('chatgpt', d.chatgpt0) }),
    stored({
      ...turn, id: DQ + 3, kind: 'revision', agent: 'claude', round: 1, content: text(C, 'claude-revision-1'), created_at: ago(17),
      meta: meta('claude', d.claude1, { critique: text(C, 'claude-critique-1'), agreement: 74, unchanged: false }),
    }),
    stored({
      ...turn, id: DQ + 4, kind: 'revision', agent: 'chatgpt', round: 1, content: text(C, 'chatgpt-revision-1'), created_at: ago(17),
      meta: meta('chatgpt', d.chatgpt1, { critique: text(C, 'chatgpt-critique-1'), agreement: 81, unchanged: false }),
    }),
    stored({
      ...turn, id: DQ + 5, kind: 'revision', agent: 'claude', round: 2, content: text(C, 'claude-revision-1'), created_at: ago(16),
      meta: meta('claude', d.claude2, { critique: text(C, 'claude-critique-2'), agreement: 92, unchanged: true }),
    }),
    stored({
      ...turn, id: DQ + 6, kind: 'revision', agent: 'chatgpt', round: 2, content: text(C, 'chatgpt-revision-1'), created_at: ago(16),
      meta: meta('chatgpt', d.chatgpt2, { critique: text(C, 'chatgpt-critique-2'), agreement: 90, unchanged: true }),
    }),
    stored({
      ...turn, id: DQ + 7, kind: 'synthesis', agent: 'claude', round: 2, content: text(C, 'synthesis'), final: true, created_at: ago(15),
      meta: meta('claude', d.synthesis, { consensus: DEBATE_CONSENSUS, savings: DEBATE_SAVINGS }),
    }),
  ];
}

// ------------------------------------------------- a duel over a PDF (ChatGPT on Codex)

const P = sections(pressupost);
export const PDF_ID = 39;
const PQ = 390;
export const PDF_REQUEST = 'req-pressupost';

/** The attached quote: its file is pressupost-cuina.pdf, its thumbnail pressupost-cuina.webp. */
export const PDF_ATTACHMENT: Attachment = {
  id: 12,
  name: 'pressupost-cuina.pdf',
  kind: 'pdf',
  mime: 'application/pdf',
  size: 0, // the size of the file, set when it is loaded
  pages: 6,
  width: null,
  height: null,
  sha256: '9d7ee04d8d39277a1215cadc65d27978bbb0ac10234a36117f1c62325ec164bf',
  created_at: ago(70),
  has_thumbnail: true,
  text_available: true,
  estimated_tokens: 21_600,
  // Page 6 is the floor plan, a drawing without text: Claude reads it for ChatGPT.
  pdf_notes: { no_text: [6], garbled: [], hidden: [] },
};

const PDF_READING: PdfReading = {
  attachment_id: PDF_ATTACHMENT.id,
  name: PDF_ATTACHMENT.name,
  checked: true,
  claude_pages: [6],
  hidden_pages: [],
  unchecked_pages: [],
  reason: null,
};

const PDF_CALLS = {
  claude: { usage: call('claude', 23_420, 910, 0, 180), latency: 26_310, ttft: 2_240 },
  check: { usage: call('claude', 3_960, 260, 0, 40), latency: 8_120, ttft: 1_510 },
  chatgpt: { usage: call('chatgpt', 2_940, 760, 0, 260), latency: 24_870, ttft: 9_430 },
} satisfies Record<string, Call>;

const PDF_TOTAL = total(Object.values(PDF_CALLS).map((c) => c.usage));

export function pdfMessages(size: number): Message[] {
  const turn = { turn_id: PQ };
  return [
    stored({
      ...turn, id: PQ, kind: 'question', content: text(P, 'question'), final: true, created_at: ago(64),
      meta: {
        mode: 'duel', target: 'claude',
        options: { debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: true },
        attachments: [{ ...PDF_ATTACHMENT, size }],
        outcome: {
          status: 'completed', failures: [], usage: PDF_TOTAL, savings: NO_SAVINGS, consensus: null,
          final_message_ids: [PQ + 1, PQ + 2], cached: false,
        },
      },
    }),
    stored({
      ...turn, id: PQ + 1, kind: 'answer', agent: 'claude', content: text(P, 'claude-answer'), final: true, created_at: ago(63),
      meta: meta('claude', PDF_CALLS.claude, { savings: NO_SAVINGS }),
    }),
    stored({
      ...turn, id: PQ + 2, kind: 'answer', agent: 'chatgpt', content: text(P, 'chatgpt-answer'), final: true, created_at: ago(63),
      meta: meta('chatgpt', PDF_CALLS.chatgpt, { savings: NO_SAVINGS, pdf_reading: [PDF_READING] }),
    }),
  ];
}

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
/** An event before it is numbered. */
type Draft = DistributiveOmit<TurnEvent, 'seq' | 'request_id'>;

/** Numbers drafts as the events of `requestId`. */
function sequence(requestId: string, drafts: Draft[]): TurnEvent[] {
  return drafts.map((d, i) => ({ ...d, request_id: requestId, seq: i + 1 }) as TurnEvent);
}

/** Text streamed in pieces of a few words, as a model writes it. */
function deltas(streamId: string, section: 'text' | 'critique' | 'answer', value: string): Draft[] {
  const pieces = value.match(/\S+\s*/g) ?? [];
  const out: Draft[] = [];
  for (let i = 0; i < pieces.length; i += 6) {
    out.push({ type: 'stream.delta', stream_id: streamId, section, text: pieces.slice(i, i + 6).join('') });
  }
  return out;
}

function completed(streamId: string, messageId: number, c: Call, extra: Partial<Extract<Draft, { type: 'stream.completed' }>> = {}): Draft {
  return {
    type: 'stream.completed', stream_id: streamId, message_id: messageId, usage: c.usage, latency_ms: c.latency,
    ttft_ms: c.ttft, agreement: null, unchanged: false, cost_basis: 'equivalent', ...extra,
  };
}

/** The duel as this tab saw it: the turn ended a moment ago, with Claude's check of the PDF. */
export function pdfEvents(): TurnEvent[] {
  const check = {
    type: 'pdf.check' as const, attachment_id: PDF_ATTACHMENT.id, name: PDF_ATTACHMENT.name,
    claude_pages: [] as number[], hidden_pages: [] as number[], unchecked_pages: [] as number[], reused: false, reason: null,
  };
  return sequence(PDF_REQUEST, [
    { type: 'turn.started', conversation_id: PDF_ID, turn_id: PQ, mode: 'duel', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { ...check, state: 'checking', usage: null },
    { type: 'stream.started', stream_id: 'c', agent: 'claude', kind: 'answer', round: 0, model: MODEL.claude },
    { ...check, state: 'checked', claude_pages: [6], usage: PDF_CALLS.check.usage },
    { type: 'stream.started', stream_id: 'g', agent: 'chatgpt', kind: 'answer', round: 0, model: MODEL.chatgpt },
    ...deltas('c', 'text', text(P, 'claude-answer')),
    completed('c', PQ + 1, PDF_CALLS.claude),
    ...deltas('g', 'text', text(P, 'chatgpt-answer')),
    completed('g', PQ + 2, PDF_CALLS.chatgpt, { pdf_reading: [PDF_READING] }),
    {
      type: 'turn.completed', conversation_id: PDF_ID, turn_id: PQ, final_message_ids: [PQ + 1, PQ + 2], usage: PDF_TOTAL,
      savings: NO_SAVINGS, consensus: null, cached: false,
    },
  ]);
}

// ---------------------------------------------- «Perfecciona», live in its fourth round

const R = sections(copies);
export const REFINE_ID = 40;
const RQ = 400;
export const REFINE_REQUEST = 'req-copies';

const REFINE_OPTIONS: RefineOptions = {
  max_rounds: 12, budget_eur: 3, max_words: null, stop_on_convergence: true, convergence_threshold: 90, editor: 'claude',
};

const change = (kind: string, value: string): RefineChange => ({ kind, text: value });

const MERGE = [
  change('merge', "L'estructura per seccions i la regla 3-2-1 vénen de la resposta de Claude."),
  change('merge', 'La taula de retenció i les comprovacions vénen de la resposta de ChatGPT.'),
];
const REVIEWS: Record<2 | 3, Record<Agent, { score: number; changes: RefineChange[] }>> = {
  2: {
    claude: {
      score: 74,
      changes: [
        change('defect', "Objectius: un RPO de 24 hores contradiu el registre de transaccions cada 15 minuts."),
        change('simplification', 'Fora el paràgraf del RAID: el RAID no és una còpia de seguretat.'),
      ],
    },
    chatgpt: { score: 71, changes: [change('defect', "Retenció: «les últimes» no diu quantes còpies mensuals es guarden.")] },
  },
  3: {
    claude: {
      score: 86,
      changes: [change('requirement', "L'encàrrec demana com es comprova la restauració: la prova mensual no té responsable ni dia.")],
    },
    chatgpt: {
      score: 83,
      changes: [
        change('simplification', 'La taula de retenció té tres files: una frase diu el mateix en menys espai.'),
        change('clarity', '«Es restaura la base de dades» no diu què es comprova ni on s\'anota.'),
      ],
    },
  },
};
const CHANGELOG: Record<2 | 3, RefineChange[]> = {
  2: [
    change('defect', "L'RPO de la base de dades és de 15 minuts, com el registre de transaccions."),
    change('defect', 'Les còpies mensuals es guarden 12 mesos.'),
    change('simplification', 'Fora el paràgraf del RAID.'),
  ],
  3: [
    change('requirement', 'Hi ha un responsable i un suplent, i la prova mensual té dia: el primer dilluns.'),
    change('clarity', 'La prova diu què es comprova i on se n\'anota el resultat.'),
    change('simplification', 'La retenció passa de taula a una frase.'),
  ],
};
const ROUND_FOUR_CLAUDE = { score: 93, changes: [change('clarity', "L'alerta diària no diu qui la rep ni qui l'atén.")] };

const REFINE_CALLS = {
  claude0: { usage: call('claude', 1240, 690, 0, 130), latency: 16_920, ttft: 1_380 },
  chatgpt0: { usage: call('chatgpt', 1210, 610, 0, 180), latency: 19_740, ttft: 2_150 },
  merge: { usage: call('claude', 2950, 1180, 0, 210), latency: 31_400, ttft: 1_560 },
  review2c: { usage: call('claude', 2210, 260, 1300, 90), latency: 9_840, ttft: 1_280 },
  review2g: { usage: call('chatgpt', 2190, 210, 1024, 110), latency: 11_270, ttft: 1_930 },
  edit2: { usage: call('claude', 2780, 1090, 1500, 160), latency: 28_650, ttft: 1_470 },
  review3c: { usage: call('claude', 2160, 240, 1400, 80), latency: 9_120, ttft: 1_250 },
  review3g: { usage: call('chatgpt', 2140, 300, 1024, 120), latency: 12_480, ttft: 2_010 },
  edit3: { usage: call('claude', 2860, 1160, 1500, 170), latency: 29_930, ttft: 1_520 },
  review4c: { usage: call('claude', 2230, 150, 1400, 60), latency: 7_640, ttft: 1_220 },
} satisfies Record<string, Call>;

const BUDGET_WORDS = Math.max(300, Math.ceil(Math.round(1.2 * words(text(R, 'v1')) * 1e6) / 1e6));

function versionMeta(version: number, body: string, changelog: RefineChange[]): RefineMeta {
  return { role: 'version', version, words: words(body), budget_words: BUDGET_WORDS, accepted: true, reason: null, changelog };
}

function reviewMeta(review: { score: number; changes: RefineChange[] }): RefineMeta {
  return { role: 'review', score: review.score, unchanged: review.changes.length === 0, changes: review.changes };
}

const lines = (changes: RefineChange[]): string => changes.map((c) => `- [${c.kind}] ${c.text}`).join('\n');

/** Ids of the stored messages of the refine turn, in the order the engine stores them. */
const RM = { claude0: RQ + 1, chatgpt0: RQ + 2, merge: RQ + 3, review2c: RQ + 4, review2g: RQ + 5, edit2: RQ + 6, review3c: RQ + 7, review3g: RQ + 8, edit3: RQ + 9, review4c: RQ + 10 };

export function refineMessages(): Message[] {
  const turn = { turn_id: RQ };
  const k = REFINE_CALLS;
  const review = (id: number, agent: Agent, round: number, r: { score: number; changes: RefineChange[] }, c: Call, minutes: number) =>
    stored({ ...turn, id, kind: 'revision', agent, round, content: lines(r.changes), created_at: ago(minutes), meta: meta(agent, c, { refine: reviewMeta(r) }) });
  const version = (id: number, round: number, body: string, changelog: RefineChange[], c: Call, minutes: number) =>
    stored({ ...turn, id, kind: 'revision', agent: 'claude', round, content: body, created_at: ago(minutes), meta: meta('claude', c, { refine: versionMeta(round, body, changelog) }) });
  return [
    stored({
      ...turn, id: RQ, kind: 'question', content: text(R, 'question'), final: true, created_at: ago(9),
      meta: {
        mode: 'refine', target: 'claude',
        options: { debate: { rounds: 3, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: true },
        refine: REFINE_OPTIONS,
        outcome: null, // the turn is running
      },
    }),
    stored({ ...turn, id: RM.claude0, kind: 'answer', agent: 'claude', content: text(R, 'claude-answer'), created_at: ago(8), meta: meta('claude', k.claude0) }),
    stored({ ...turn, id: RM.chatgpt0, kind: 'answer', agent: 'chatgpt', content: text(R, 'chatgpt-answer'), created_at: ago(8), meta: meta('chatgpt', k.chatgpt0) }),
    version(RM.merge, 1, text(R, 'v1'), MERGE, k.merge, 7),
    review(RM.review2c, 'claude', 2, REVIEWS[2].claude, k.review2c, 6),
    review(RM.review2g, 'chatgpt', 2, REVIEWS[2].chatgpt, k.review2g, 6),
    version(RM.edit2, 2, text(R, 'v2'), CHANGELOG[2], k.edit2, 5),
    review(RM.review3c, 'claude', 3, REVIEWS[3].claude, k.review3c, 4),
    review(RM.review3g, 'chatgpt', 3, REVIEWS[3].chatgpt, k.review3g, 4),
    version(RM.edit3, 3, text(R, 'v3'), CHANGELOG[3], k.edit3, 2),
    review(RM.review4c, 'claude', 4, ROUND_FOUR_CLAUDE, k.review4c, 1),
  ];
}

/** The refine turn so far: three versions, and the fourth round's reviews under way (Claude's done). */
export function refineEvents(): TurnEvent[] {
  const k = REFINE_CALLS;
  const sum = (...names: (keyof typeof REFINE_CALLS)[]) => total(names.map((n) => k[n].usage));
  const round = (n: 1 | 2 | 3, body: string, changes: RefineChange[], usage: Usage, so_far: Usage): Draft => ({
    type: 'refine.round', round: n, version: n, accepted: true, reason: null, reason_code: null, words: words(body), budget_words: BUDGET_WORDS,
    changes,
    proposals: n === 1 ? { claude: null, chatgpt: null } : { claude: REVIEWS[n].claude.changes.length, chatgpt: REVIEWS[n].chatgpt.changes.length },
    scores: n === 1 ? { claude: null, chatgpt: null } : { claude: REVIEWS[n].claude.score, chatgpt: REVIEWS[n].chatgpt.score },
    converged: false, usage, total: so_far,
  });
  const reviews = (n: 2 | 3, c: 'review2c' | 'review3c', g: 'review2g' | 'review3g'): Draft[] => [
    { type: 'phase', phase: 'review', round: n },
    { type: 'stream.started', stream_id: `r${n}c`, agent: 'claude', kind: 'revision', round: n, model: MODEL.claude },
    { type: 'stream.started', stream_id: `r${n}g`, agent: 'chatgpt', kind: 'revision', round: n, model: MODEL.chatgpt },
    ...deltas(`r${n}c`, 'critique', lines(REVIEWS[n].claude.changes)),
    ...deltas(`r${n}g`, 'critique', lines(REVIEWS[n].chatgpt.changes)),
    completed(`r${n}c`, RM[c], k[c], { refine: reviewMeta(REVIEWS[n].claude) }),
    completed(`r${n}g`, RM[g], k[g], { refine: reviewMeta(REVIEWS[n].chatgpt) }),
  ];
  const edit = (n: 1 | 2 | 3, body: string, changelog: RefineChange[], c: 'merge' | 'edit2' | 'edit3'): Draft[] => [
    { type: 'phase', phase: 'edit', round: n },
    { type: 'stream.started', stream_id: `e${n}`, agent: 'claude', kind: 'revision', round: n, model: MODEL.claude },
    ...deltas(`e${n}`, 'answer', body),
    ...deltas(`e${n}`, 'critique', lines(changelog)),
    completed(`e${n}`, RM[c], k[c], { refine: versionMeta(n, body, changelog) }),
  ];
  return sequence(REFINE_REQUEST, [
    { type: 'turn.started', conversation_id: REFINE_ID, turn_id: RQ, mode: 'refine', new_conversation: false },
    { type: 'phase', phase: 'answer', round: 0 },
    { type: 'stream.started', stream_id: 'a0c', agent: 'claude', kind: 'answer', round: 0, model: MODEL.claude },
    { type: 'stream.started', stream_id: 'a0g', agent: 'chatgpt', kind: 'answer', round: 0, model: MODEL.chatgpt },
    ...deltas('a0c', 'text', text(R, 'claude-answer')),
    ...deltas('a0g', 'text', text(R, 'chatgpt-answer')),
    completed('a0c', RM.claude0, k.claude0),
    completed('a0g', RM.chatgpt0, k.chatgpt0),
    ...edit(1, text(R, 'v1'), MERGE, 'merge'),
    round(1, text(R, 'v1'), MERGE, k.merge.usage, sum('claude0', 'chatgpt0', 'merge')),
    ...reviews(2, 'review2c', 'review2g'),
    ...edit(2, text(R, 'v2'), CHANGELOG[2], 'edit2'),
    round(2, text(R, 'v2'), CHANGELOG[2], sum('review2c', 'review2g', 'edit2'), sum('claude0', 'chatgpt0', 'merge', 'review2c', 'review2g', 'edit2')),
    ...reviews(3, 'review3c', 'review3g'),
    ...edit(3, text(R, 'v3'), CHANGELOG[3], 'edit3'),
    round(
      3, text(R, 'v3'), CHANGELOG[3], sum('review3c', 'review3g', 'edit3'),
      sum('claude0', 'chatgpt0', 'merge', 'review2c', 'review2g', 'edit2', 'review3c', 'review3g', 'edit3'),
    ),
    { type: 'phase', phase: 'review', round: 4 },
    { type: 'stream.started', stream_id: 'r4c', agent: 'claude', kind: 'revision', round: 4, model: MODEL.claude },
    { type: 'stream.started', stream_id: 'r4g', agent: 'chatgpt', kind: 'revision', round: 4, model: MODEL.chatgpt },
    ...deltas('r4c', 'critique', lines(ROUND_FOUR_CLAUDE.changes)),
    completed('r4c', RM.review4c, k.review4c, { refine: reviewMeta(ROUND_FOUR_CLAUDE) }),
    // ChatGPT is still thinking about its review: it has written nothing yet.
  ]);
}

// ------------------------------------------------------------------- the sidebar list

const summary = (id: number, title: string, minutes: number, mode: ConversationSummary['last_mode'], count: number): ConversationSummary => ({
  id, title, created_at: ago(minutes + 3), updated_at: ago(minutes), last_mode: mode, message_count: count,
});

const DAY = 24 * 60;

export function conversationList(): ConversationSummary[] {
  return [
    summary(REFINE_ID, 'Política de còpies de seguretat del servidor', 1, 'refine', 11),
    summary(DEBATE_ID, "Migració de PostgreSQL 14 a 17 amb el mínim temps d'aturada", 15, 'debate', 8),
    summary(PDF_ID, 'Revisa el pressupost de la reforma de la cuina', 63, 'duel', 3),
    summary(38, 'Pla de proves per a la passarel·la de pagaments', 190, 'debate', 8),
    summary(37, 'Correu per renegociar el contracte amb el proveïdor', DAY + 140, 'solo', 2),
    summary(36, 'Svelte 5 o React 19 per a un tauler intern?', DAY + 420, 'duel', 3),
    summary(35, 'Explica la factura de la llum de setembre', 3 * DAY + 60, 'solo', 2),
    summary(34, "Revisa l'script de desplegament amb rsync", 4 * DAY + 200, 'debate', 8),
    summary(33, "Noms per a l'associació de veïns", 12 * DAY, 'duel', 3),
    summary(32, 'Resum de «Thinking in Systems» en deu idees', 20 * DAY, 'solo', 2),
  ];
}
