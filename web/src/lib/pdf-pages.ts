// The pages of an attached PDF as the UI names them (docs/PROTOCOL.md «Adjunts»,
// docs/adr/0009-attachments.md): the warnings of the server's analysis on its card, Claude's
// check of its text for ChatGPT in the turn, and the badge on ChatGPT's messages that
// says how it read it. ChatGPT with the subscription (Codex) cannot open a PDF: it reads
// the text the server extracted, and Claude reads for it the pages where that text would
// mislead it. Page lists are compact («pàg. 2–4, 9») so a long PDF still fits a card.

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

/** A no-break space: «pàg.» never ends a line apart from its first number. */
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

/** The compact list with its abbreviation, for one page or several: «pàg. 2–4, 9». */
export const pageRefs = (pages: readonly number[]): string => `pàg.${NBSP}${pageList(pages)}`;

/** The list in a sentence, the last part after «i»: "2–4 i 9"; and how many pages it names. */
function pagesInProse(pages: readonly number[]): { text: string; count: number } {
  const { parts, count } = pageParts(pages);
  const text = parts.length < 2 ? parts.join('') : `${parts.slice(0, -1).join(', ')} i ${parts.at(-1)}`;
  return { text, count };
}

/** Names that keep their capital letter anywhere in a sentence. */
const NAMES: ReadonlySet<string> = new Set(['Claude', 'ChatGPT', 'Codex']);

/**
 * A sentence of its own, such as the server's reasons («La comprovació de Claude ha trigat
 * massa.»), as it goes after a colon, where Catalan starts with a lowercase letter: «…sense
 * contrastar: la comprovació de Claude ha trigat massa.». A name or an acronym keeps its
 * capital («Claude no l'ha volgut contrastar.», «PDF…»); a reason on a line of its own keeps
 * it too, so this is only for the text after a colon.
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

/** A warning line on a PDF's card: «Sense text: pàg. 2–4, 9». */
export interface PdfNoteLine {
  kind: PdfNoteKind;
  /** «Sense text». */
  label: string;
  /** The pages, as the parts of a compact list (:func:`pageRanges`): ["2–4", "9"]. */
  pages: string[];
  /** What it means, for its title and for screen readers. */
  description: string;
}

const NOTES: readonly { kind: PdfNoteKind; label: string; description: string }[] = [
  {
    kind: 'no_text',
    label: 'Sense text',
    description:
      'Pàgines sense text extraïble: escanejades, o amb el text dibuixat com a imatge. Els models que obren el PDF les llegeixen com a imatge.',
  },
  {
    kind: 'garbled',
    label: 'Text il·legible',
    description:
      "El text extret d'aquestes pàgines té caràcters trencats (una font sense mapa de caràcters): no es pot llegir tal com és.",
  },
  {
    kind: 'hidden',
    label: 'Possible text ocult',
    description:
      'Aquestes pàgines poden tenir text que no es veu: invisible, massa petit o fora de la pàgina. Es diu als models que el tractin com a sospitós.',
  },
];

/** The warning lines of a PDF's card, one per kind of warning it has (none without `notes`). */
export function pdfNoteLines(notes: PdfNotes | null): PdfNoteLine[] {
  if (!notes) return [];
  return NOTES.filter(({ kind }) => pageParts(notes[kind] ?? []).count > 0).map(({ kind, label, description }) => ({
    kind,
    label,
    pages: pageRanges(notes[kind]),
    description,
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
 * What a finished check with pages checked adds to its headline («ChatGPT llegeix
 * «informe.pdf» contrastat per Claude»): the pages ChatGPT reads through Claude, the
 * hidden text it does not get, the pages left unchecked and why, or that Claude found
 * nothing to correct. Nothing while it runs, nor for a PDF nobody checked through it: its
 * headline says why.
 */
export function checkDetails(check: CheckFacts): string[] {
  if (check.state !== 'checked') return [];
  const details: string[] = [];
  const read = pagesInProse(check.claudePages);
  if (read.count === 1) details.push(`pàgina ${read.text} llegida per Claude`);
  else if (read.count > 1) details.push(`pàgines ${read.text} llegides per Claude`);
  const hidden = pagesInProse(check.hiddenPages);
  if (hidden.count) {
    details.push(`text ocult a ${hidden.count === 1 ? 'la' : 'les'} pàg.${NBSP}${hidden.text}: no s'ha passat`);
  }
  const unchecked = pagesInProse(check.uncheckedPages);
  if (unchecked.count) {
    const pages = unchecked.count === 1 ? `pàgina ${unchecked.text}` : `pàgines ${unchecked.text}`;
    details.push(check.reason ? `${pages} sense contrastar: ${afterColon(check.reason)}` : `${pages} sense contrastar`);
  }
  if (!details.length) details.push('Claude no hi ha trobat cap diferència');
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
  /** «PDF contrastat per Claude», «PDF sense contrastar» (with the number, for several). */
  label: string;
  /** The first line of its tooltip. */
  lead: string;
  pdfs: ReadingDetail[];
}

function readingDetail(reading: PdfReading): ReadingDetail {
  const rows: ReadingRow[] = [];
  const add = (label: string, pages: readonly number[]) => {
    if (pageParts(pages).count) rows.push({ label, pages: pageRefs(pages) });
  };
  add('Llegides per Claude', reading.claude_pages);
  add('Text ocult, no passat', reading.hidden_pages);
  add('Sense contrastar', reading.unchecked_pages);
  const note = reading.reason ?? (reading.checked && !rows.length ? 'Claude no hi ha trobat cap diferència.' : null);
  return { attachmentId: reading.attachment_id, name: reading.name, rows, note };
}

/**
 * The badges of a ChatGPT message from how it read the question's PDFs (`meta.pdf_reading`):
 * one for those Claude checked and one for those nobody did, so neither label is ever
 * wrong about a PDF; each with a tooltip that lists, per PDF, the pages read by Claude,
 * the hidden ones and the unchecked ones.
 */
export function readingGroups(readings: readonly PdfReading[]): ReadingGroup[] {
  const groups: ReadingGroup[] = [];
  for (const checked of [true, false]) {
    const pdfs = readings.filter((r) => r.checked === checked);
    if (!pdfs.length) continue;
    const several = pdfs.length > 1 ? `${pdfs.length} ` : '';
    groups.push({
      checked,
      label: checked
        ? `${several}PDF ${several ? 'contrastats' : 'contrastat'} per Claude`
        : `${several}PDF sense contrastar`,
      lead: `ChatGPT no pot obrir els PDF: n'ha llegit el text extret, ${checked ? 'contrastat per Claude' : 'sense contrastar'}.`,
      pdfs: pdfs.map(readingDetail),
    });
  }
  return groups;
}
