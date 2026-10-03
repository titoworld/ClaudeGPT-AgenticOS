// The wire of Claude's check of the PDFs for ChatGPT (docs/PROTOCOL.md «Adjunts», P7b):
// the types of protocol.ts carry what the server sends (orchestrator/events.py
// PdfCheckChanged and PdfReading, pdf_facts.PdfNotes.to_wire) and what the protocol
// documents. The type assertions are checked with the rest of the types (npm run check);
// the field lists, against docs/PROTOCOL.md.
import { describe, expect, expectTypeOf, it } from 'vitest';
import {
  TURN_MODES,
  type Attachment,
  type ClientMessage,
  type MessageMeta,
  type PdfCheckState,
  type PdfNotes,
  type PdfReading,
  type Phase,
  type RefineMeta,
  type RefineOptions,
  type RefineReasonCode,
  type RefineStopReason,
  type RuntimeSettings,
  type Stats,
  type TurnEvent,
  type TurnMode,
  type TurnOutcome,
  type Usage,
} from './protocol';
import { REFINE_REASON_CODES } from './turns.svelte';

type PdfCheckEvent = Extract<TurnEvent, { type: 'pdf.check' }>;
type StreamCompleted = Extract<TurnEvent, { type: 'stream.completed' }>;

/** docs/PROTOCOL.md, read from disk: Vite serves nothing from outside web/. */
async function protocolDoc(): Promise<string> {
  const module: string = 'node:fs'; // not a literal: the web code has no Node types
  const fs = (await import(/* @vite-ignore */ module)) as { readFileSync(path: string, encoding: 'utf8'): string };
  const here: string = import.meta.url;
  return fs.readFileSync(decodeURIComponent(new URL('../../../docs/PROTOCOL.md', here).pathname), 'utf8');
}

/** The field names of a TypeScript block of the protocol, comments left out. */
const fieldsOf = (block: string): string[] =>
  block
    .split('\n')
    .map((line) => line.replace(/\/\/.*$/, ''))
    .flatMap((line) => [...line.matchAll(/(\w+)\??\s*:/g)].map((m) => m[1]!));

const USAGE: Usage = {
  input_tokens: 9000,
  output_tokens: 700,
  cache_read_tokens: 2000,
  cache_write_tokens: 0,
  reasoning_tokens: 0,
  cost_usd: 0.0123,
};

describe('the wire of the PDF check (P7b)', () => {
  it("an attachment's pdf_notes: the warnings of an analysed PDF's pages, else null", async () => {
    expectTypeOf<Attachment['pdf_notes']>().toEqualTypeOf<PdfNotes | null>();
    expectTypeOf<PdfNotes>().toEqualTypeOf<{ no_text: number[]; garbled: number[]; hidden: number[] }>();
    const notes = { no_text: [2, 5], garbled: [3], hidden: [7] } satisfies PdfNotes;
    const block = /\n\s*pdf_notes: \{([\s\S]*?)\} \| null;/.exec(await protocolDoc())?.[1] ?? '';
    expect(fieldsOf(block)).toEqual(Object.keys(notes));
  });

  it('the pdf.check event', async () => {
    expectTypeOf<PdfCheckState>().toEqualTypeOf<'checking' | 'checked' | 'unchecked'>();
    expectTypeOf<PdfCheckEvent['state']>().toEqualTypeOf<PdfCheckState>();
    expectTypeOf<PdfCheckEvent['usage']>().toEqualTypeOf<Usage | null>();
    expectTypeOf<PdfCheckEvent['reason']>().toEqualTypeOf<string | null>();
    expectTypeOf<PdfCheckEvent['claude_pages']>().toEqualTypeOf<number[]>();
    // As PdfCheckChanged.to_wire writes it.
    const wire = {
      type: 'pdf.check',
      request_id: 'r',
      seq: 4,
      attachment_id: 7,
      name: 'informe.pdf',
      state: 'checked',
      claude_pages: [2, 5],
      hidden_pages: [5],
      unchecked_pages: [],
      reused: false,
      usage: USAGE,
      reason: null,
    } satisfies TurnEvent;
    const row = /\n\| `pdf\.check` \| (.*?) \|/.exec(await protocolDoc())?.[1] ?? '';
    const documented = [...row.replace(/\([^)]*\)/g, '').matchAll(/`(\w+)`/g)].map((m) => m[1]!);
    expect(['type', 'request_id', 'seq', ...documented]).toEqual(Object.keys(wire));
  });

  it("how ChatGPT read each PDF: stream.completed and its message's meta", async () => {
    expectTypeOf<StreamCompleted['pdf_reading']>().toEqualTypeOf<PdfReading[] | undefined>();
    expectTypeOf<MessageMeta['pdf_reading']>().toEqualTypeOf<PdfReading[] | undefined>();
    expectTypeOf<PdfReading['reason']>().toEqualTypeOf<string | null>();
    // As PdfReading.to_wire writes it.
    const reading = {
      attachment_id: 7,
      name: 'informe.pdf',
      checked: true,
      claude_pages: [2, 5],
      hidden_pages: [5],
      unchecked_pages: [],
      reason: null,
    } satisfies PdfReading;
    const doc = await protocolDoc();
    const block = /interface PdfReading \{([\s\S]*?)\n\s*\}/.exec(doc)?.[1] ?? '';
    expect(fieldsOf(block)).toEqual(Object.keys(reading));
    const completed = /\n\| `stream\.completed` \| (.*?) \|/.exec(doc)?.[1] ?? '';
    expect(completed).toContain('`pdf_reading`');
  });
});

type RefineRoundEvent = Extract<TurnEvent, { type: 'refine.round' }>;
type TurnCompleted = Extract<TurnEvent, { type: 'turn.completed' }>;

/** The backticked field names of a row of the protocol's table of server events. */
async function eventRow(type: string): Promise<string[]> {
  const escaped = type.replace('.', '\\.');
  const row = new RegExp(`\\n\\| \`${escaped}\` \\| (.*?) \\|`).exec(await protocolDoc())?.[1] ?? '';
  // The optional ones are named after «opcional:»; what a field holds goes in brackets.
  return [...row.replace(/\([^)]*\)/g, '').matchAll(/`(\w+)`/g)].map((m) => m[1]!);
}

describe('the wire of the refine mode («Perfecciona», ADR 0010)', () => {
  it('a fourth turn mode, which the stats count too', async () => {
    expectTypeOf<TurnMode>().toEqualTypeOf<'solo' | 'duel' | 'debate' | 'refine'>();
    expectTypeOf<Stats['turns']>().toEqualTypeOf<Record<TurnMode, number>>();
    // Never the default mode: a refine turn runs until the owner stops it.
    expectTypeOf<RuntimeSettings['default_mode']>().toEqualTypeOf<'solo' | 'duel' | 'debate'>();
    const doc = await protocolDoc();
    const modes = /type TurnMode = ([^;]+);/.exec(doc)?.[1] ?? '';
    expect([...modes.matchAll(/"(\w+)"/g)].map((m) => m[1])).toEqual([...TURN_MODES]);
    const counted = /\n {2}turns: \{([^}]*)\}/.exec(doc)?.[1] ?? '';
    expect(fieldsOf(counted)).toEqual([...TURN_MODES]);
  });

  it('its options, in the settings and in turn.start', async () => {
    expectTypeOf<RuntimeSettings['refine']>().toEqualTypeOf<RefineOptions>();
    expectTypeOf<RefineOptions['max_words']>().toEqualTypeOf<number | null>();
    // As storage/models.py refine_to_wire writes them.
    const options = {
      max_rounds: 12,
      budget_eur: 3,
      max_words: null,
      stop_on_convergence: true,
      convergence_threshold: 90,
      editor: 'claude',
    } satisfies RefineOptions;
    const start = {
      type: 'turn.start',
      request_id: 'r',
      text: 'Un pla',
      mode: 'refine',
      conversation_id: null,
      options: { debate: { rounds: 2, consensus_threshold: 85, synthesizer: 'claude' }, refine: options, use_cache: true },
    } satisfies ClientMessage;
    expect(start.options.refine).toBe(options);
    const block = /interface RefineOptions \{([\s\S]*?)\n\}/.exec(await protocolDoc())?.[1] ?? '';
    expect(fieldsOf(block)).toEqual(Object.keys(options));
  });

  it('turn.stop, and the turn.stopping that answers it', async () => {
    const stop = { type: 'turn.stop', request_id: 'r' } satisfies ClientMessage;
    expect(stop.type).toBe('turn.stop');
    // As TurnStopping.to_wire writes it.
    const stopping = { type: 'turn.stopping', request_id: 'r', seq: 9, round: 3 } satisfies TurnEvent;
    expect(['type', 'request_id', 'seq', ...(await eventRow('turn.stopping'))]).toEqual(Object.keys(stopping));
    expect(await protocolDoc()).toContain('{"type": "turn.stop", "request_id": "uuid"}');
  });

  it('the phases of a round, and refine.round at its end', async () => {
    expectTypeOf<Phase>().toEqualTypeOf<'answer' | 'revision' | 'synthesis' | 'compaction' | 'review' | 'edit'>();
    expectTypeOf<RefineRoundEvent['proposals']>().toEqualTypeOf<Record<'claude' | 'chatgpt', number | null>>();
    expectTypeOf<RefineRoundEvent['reason']>().toEqualTypeOf<string | null>();
    expectTypeOf<RefineRoundEvent['reason_code']>().toEqualTypeOf<RefineReasonCode | null>();
    // As RefineRound.to_wire writes it.
    const round = {
      type: 'refine.round',
      request_id: 'r',
      seq: 30,
      round: 2,
      version: 2,
      accepted: true,
      reason: null,
      reason_code: null,
      words: 410,
      budget_words: 456,
      changes: [{ kind: 'defect', text: 'El termini de la fase 2' }],
      proposals: { claude: 1, chatgpt: 0 },
      scores: { claude: 70, chatgpt: null },
      converged: false,
      usage: USAGE,
      total: USAGE,
    } satisfies TurnEvent;
    expect(['type', 'request_id', 'seq', ...(await eventRow('refine.round'))]).toEqual(Object.keys(round));
    const phase = /\n\| `phase` \| (.*?) \|/.exec(await protocolDoc())?.[1] ?? '';
    expect(phase).toContain('`review`');
    expect(phase).toContain('`edit`');
  });

  it("why it stopped: turn.completed and the turn's outcome", async () => {
    expectTypeOf<RefineStopReason>().toEqualTypeOf<'owner' | 'converged' | 'unchanged' | 'max_rounds' | 'budget' | 'failed'>();
    expectTypeOf<TurnCompleted['stop_reason']>().toEqualTypeOf<RefineStopReason | undefined>();
    expectTypeOf<TurnOutcome['stop_reason']>().toEqualTypeOf<RefineStopReason | undefined>();
    const doc = await protocolDoc();
    expect(await eventRow('turn.completed')).toContain('stop_reason');
    const reasons = /stop_reason\?: ([^;]+);/.exec(doc)?.[1] ?? '';
    expect([...reasons.matchAll(/"(\w+)"/g)].map((m) => m[1])).toEqual(['owner', 'converged', 'unchanged', 'max_rounds', 'budget', 'failed']);
  });

  it("each message's meta.refine, as its stream.completed brings it", async () => {
    expectTypeOf<Extract<TurnEvent, { type: 'stream.completed' }>['refine']>().toEqualTypeOf<RefineMeta | undefined>();
    // As the engine stores them (docs/PROTOCOL.md «Metadades de missatge»).
    const review = { role: 'review', score: 70, unchanged: false, changes: [{ kind: 'defect', text: 'x' }] } satisfies RefineMeta;
    const version = {
      role: 'version',
      version: 1,
      words: 380,
      budget_words: 456,
      accepted: true,
      reason: null,
      reason_code: null,
      changelog: [{ kind: 'merge', text: 'y' }],
      copied_from: 71,
    } satisfies RefineMeta;
    // A message stored before the codes has its reason alone.
    expectTypeOf<Extract<RefineMeta, { role: 'version' }>['reason_code']>().toEqualTypeOf<RefineReasonCode | null | undefined>();
    const final = { role: 'final', version: 1, words: 380, budget_words: 456, stop_reason: 'owner' } satisfies RefineMeta;
    const block = /type RefineMeta =([\s\S]*?)```/.exec(await protocolDoc())?.[1] ?? '';
    const variants = block.split(/\n\s*\| \{/).slice(1).map(fieldsOf);
    expect(variants).toEqual([Object.keys(review), Object.keys(version), Object.keys(final)]);
    const meta: MessageMeta = { refine: review };
    expect(meta.refine).toBe(review);
  });
});

describe('the reasons of refine.round (P8)', () => {
  it('has a code for each reason, which the client knows', () => {
    expectTypeOf<RefineReasonCode>().toEqualTypeOf<'over_budget' | 'incomplete' | 'identical' | 'nothing_to_change' | 'failed_round'>();
    expect(REFINE_REASON_CODES).toEqual(['over_budget', 'incomplete', 'identical', 'nothing_to_change', 'failed_round']);
  });

  it('every code the client knows is one the protocol gives', async () => {
    const row = (await protocolDoc()).split('\n').find((line) => line.startsWith('| `refine.round` |')) ?? '';
    const quoted = [...row.matchAll(/`(\w+)`/g)].map((m) => m[1]);
    expect(REFINE_REASON_CODES.filter((code) => !quoted.includes(code))).toEqual([]);
  });
});
