// The wire of Claude's check of the PDFs for ChatGPT (docs/PROTOCOL.md «Adjunts», P7b):
// the types of protocol.ts carry what the server sends (orchestrator/events.py
// PdfCheckChanged and PdfReading, pdf_facts.PdfNotes.to_wire) and what the protocol
// documents. The type assertions are checked with the rest of the types (npm run check);
// the field lists, against docs/PROTOCOL.md.
import { describe, expect, expectTypeOf, it } from 'vitest';
import type { Attachment, MessageMeta, PdfCheckState, PdfNotes, PdfReading, TurnEvent, Usage } from './protocol';

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
