// The pages of an attached PDF as the UI names them (docs/PROTOCOL.md «Adjunts»,
// docs/adr/0009-attachments.md): the warnings of the server's analysis on its card, Claude's
// check of its text for ChatGPT in the turn, and the badge on ChatGPT's messages that
// says how it read it. ChatGPT with the subscription (Codex) cannot open a PDF: it reads
// the text the server extracted, and Claude reads for it the pages where that text would
// mislead it. Page lists are compact («pp. 2–4, 9») so a long PDF still fits a card. Every
// text is in the language in force (lib/i18n/areas/pdf.ts).

import { i18n } from './i18n/index.svelte';
import type { PdfNotes, PdfReading } from './protocol';
import type { PdfCheckStatus } from './turns.svelte';

const isPage = (n: number): boolean => Number.isInteger(n) && n >= 1;

/** Runs of consecutive pages: "2–4" from three pages on, else each page. */
function pageParts(pages: readonly number[]): { parts: string[]; count: number } {
  const sorted = [...new Set(pages.filter(isPage))].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let end = i;
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end]! + 1) end++;
    if (end - i >= 2) parts.push(`${sorted[i]}–${sorted[end]}`);
    else for (let k = i; k <= end; k++) parts.push(String(sorted[k]));
    i = end + 1;
  }
  return { parts, count: sorted.length };
}

/** A no-break space: «pp.» never ends a line apart from its first number. */
const NBSP = '\u00a0';

/**
 * Page numbers as a compact list: sorted, each once, runs of three pages or more as a
 * range: "2–4, 9".
 */
export const pageList = (pages: readonly number[]): string => pageParts(pages).parts.join(', ');

/**
 * The parts of that list, each a page or a range: ["2–4", "9"]. A card shows each part
 * unbroken, so a long list wraps only between them (never inside «2–4»).
 */
export const pageRanges = (pages: readonly number[]): string[] => pageParts(pages).parts;

/** The compact list with its abbreviation, for one page or several: «p. 2», «pp. 2–4, 9». */
export function pageRefs(pages: readonly number[]): string {
  const { parts, count } = pageParts(pages);
  return `${i18n.m.pdf.pages(count)}${NBSP}${parts.join(', ')}`;
}

/** The conjunction lists of each language («2–4, 9 and 12»), made when first needed. */
const conjunctions = new Map<string, Intl.ListFormat>();

/** The list in a sentence, its last part after «and»: "2–4 and 9"; and how many pages it names. */
function pagesInProse(pages: readonly number[]): { text: string; count: number } {
  const { parts, count } = pageParts(pages);
  const tag = i18n.tag;
  let list = conjunctions.get(tag);
  if (!list) {
    list = new Intl.ListFormat(tag, { style: 'long', type: 'conjunction' });
    conjunctions.set(tag, list);
  }
  return { text: list.format(parts), count };
}

/** Names that keep their capital letter anywhere in a sentence. */
const NAMES: ReadonlySet<string> = new Set(['Claude', 'ChatGPT', 'Codex']);

/**
 * A sentence of its own, such as the server's reasons («The server could not analyse its
 * pages.»), as it goes after a colon, where English, Spanish and Catalan go on with a
 * lowercase letter: «…unchecked: the server could not analyse its pages.». A name or an
 * acronym keeps its capital («Claude's check took too long.», «PDF…»); a reason on a line of
 * its own keeps it too, so this is only for the text after a colon.
 */
export function afterColon(sentence: string): string {
  const word = /^\p{L}+/u.exec(sentence)?.[0];
  if (!word || NAMES.has(word)) return sentence;
  const rest = word.slice(1);
  if (rest !== rest.toLowerCase()) return sentence; // an acronym or a name such as «OpenAI»
  return sentence.charAt(0).toLowerCase() + sentence.slice(1);
}

// ------------------------------------------------------------ the card

export type PdfNoteKind = keyof PdfNotes;

/** A warning line on a PDF's card: «No text: pp. 2–4, 9». */
export interface PdfNoteLine {
  kind: PdfNoteKind;
  /** «No text». */
  label: string;
  /** The abbreviation before its pages, for how many they are: «p.», «pp.». */
  abbreviation: string;
  /** The pages, as the parts of a compact list (:func:`pageRanges`): ["2–4", "9"]. */
  pages: string[];
  /** What it means, for its title and for screen readers. */
  description: string;
}

const NOTE_KINDS: readonly PdfNoteKind[] = ['no_text', 'garbled', 'hidden'];

/** The warning lines of a PDF's card, one per kind of warning it has (none without `notes`). */
export function pdfNoteLines(notes: PdfNotes | null): PdfNoteLine[] {
  if (!notes) return [];
  const texts = i18n.m.pdf;
  return NOTE_KINDS.filter((kind) => pageParts(notes[kind] ?? []).count > 0).map((kind) => ({
    kind,
    label: texts.notes[kind].label,
    abbreviation: texts.pages(pageParts(notes[kind]).count),
    pages: pageRanges(notes[kind]),
    description: texts.notes[kind].description,
  }));
}

// ------------------------------------------------------------ the turn

/** What the turn knows of a PDF's check, as `checkDetails` reads it. */
export interface CheckFacts {
  state: PdfCheckStatus;
  claudePages: readonly number[];
  hiddenPages: readonly number[];
  uncheckedPages: readonly number[];
  reason: string | null;
}

/**
 * What a finished check with pages checked adds to its headline («ChatGPT reads
 * “report.pdf”, checked by Claude»): the pages ChatGPT reads through Claude, the hidden
 * text it does not get, the pages left unchecked and why, or that Claude found nothing to
 * correct. Nothing while it runs, nor for a PDF nobody checked through it: its headline
 * says why.
 */
export function checkDetails(check: CheckFacts): string[] {
  if (check.state !== 'checked') return [];
  const texts = i18n.m.pdf.details;
  const details: string[] = [];
  const read = pagesInProse(check.claudePages);
  if (read.count) details.push(texts.read(read.count, read.text));
  const hidden = pagesInProse(check.hiddenPages);
  if (hidden.count) details.push(texts.hidden(hidden.count, hidden.text));
  const unchecked = pagesInProse(check.uncheckedPages);
  if (unchecked.count) {
    const pages = texts.unchecked(unchecked.count, unchecked.text);
    details.push(check.reason ? `${pages}: ${afterColon(check.reason)}` : pages);
  }
  if (!details.length) details.push(texts.noDifference);
  return details;
}

// ------------------------------------------------------------ ChatGPT's messages

/** A row of a PDF in the badge's tooltip: what its pages are, and which. */
export interface ReadingRow {
  label: string;
  pages: string;
}

export interface ReadingDetail {
  attachmentId: number;
  name: string;
  rows: ReadingRow[];
  /** Why pages were left unchecked, or that Claude found nothing to correct. */
  note: string | null;
}

/** A badge of a ChatGPT message: its PDFs that Claude checked, or those nobody did. */
export interface ReadingGroup {
  checked: boolean;
  /** «PDF checked by Claude», «Unchecked PDF» (with the number, for several). */
  label: string;
  /** The first line of its tooltip. */
  lead: string;
  pdfs: ReadingDetail[];
}

function readingDetail(reading: PdfReading): ReadingDetail {
  const texts = i18n.m.pdf;
  const rows: ReadingRow[] = [];
  const add = (label: string, pages: readonly number[]) => {
    if (pageParts(pages).count) rows.push({ label, pages: pageRefs(pages) });
  };
  add(texts.reading.rows.claude, reading.claude_pages);
  add(texts.reading.rows.hidden, reading.hidden_pages);
  add(texts.reading.rows.unchecked, reading.unchecked_pages);
  const note = reading.reason ?? (reading.checked && !rows.length ? `${texts.details.noDifference}.` : null);
  return { attachmentId: reading.attachment_id, name: reading.name, rows, note };
}

/**
 * The badges of a ChatGPT message from how it read the question's PDFs (`meta.pdf_reading`):
 * one for those Claude checked and one for those nobody did, so neither label is ever
 * wrong about a PDF; each with a tooltip that lists, per PDF, the pages read by Claude,
 * the hidden ones and the unchecked ones.
 */
export function readingGroups(readings: readonly PdfReading[]): ReadingGroup[] {
  const texts = i18n.m.pdf.reading;
  const groups: ReadingGroup[] = [];
  for (const checked of [true, false]) {
    const pdfs = readings.filter((r) => r.checked === checked);
    if (!pdfs.length) continue;
    groups.push({
      checked,
      label: checked ? texts.checked(pdfs.length) : texts.unchecked(pdfs.length),
      lead: checked ? texts.leadChecked : texts.leadUnchecked,
      pdfs: pdfs.map(readingDetail),
    });
  }
  return groups;
}
