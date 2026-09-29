// What the UI says of an attached PDF's pages (docs/PROTOCOL.md «Adjunts», docs/adr/0009-adjunts.md):
// compact page lists, the warnings of the server's analysis on its card, Claude's check
// of its text for ChatGPT in the turn, and the badge on ChatGPT's messages that says how
// it read it. All in Catalan.
import { describe, expect, it } from 'vitest';
import { checkDetails, pageList, pageRanges, pageRefs, pdfNoteLines, readingGroups } from './pdf-pages';
import type { PdfReading } from './protocol';

/** A no-break space: «pàg.» never ends a line apart from its number. */
const NB = '\u00a0';

describe('page lists', () => {
  it('are compact: runs of three pages or more become a range', () => {
    expect(pageList([2, 3, 4, 9])).toBe('2–4, 9');
    expect(pageList([1, 2, 3, 4, 5])).toBe('1–5');
    expect(pageList([2, 3])).toBe('2, 3');
    expect(pageList([7])).toBe('7');
    expect(pageList([1, 3, 4, 5, 8, 9, 10, 12])).toBe('1, 3–5, 8–10, 12');
    expect(pageList([])).toBe('');
  });

  it('sort the pages and name each once, whatever the order they come in', () => {
    expect(pageList([9, 4, 2, 3, 4])).toBe('2–4, 9');
  });

  it('skip anything that is not a page number', () => {
    expect(pageList([0, -1, 2.5, Number.NaN, 3])).toBe('3');
  });

  it('with the abbreviation, for one page or several, kept with its number', () => {
    expect(pageRefs([2, 3, 4, 9])).toBe(`pàg.${NB}2–4, 9`);
    expect(pageRefs([5])).toBe(`pàg.${NB}5`);
    expect(pageRefs([2, 5])).toBe(`pàg.${NB}2, 5`);
  });

  it('as parts, for a list that wraps only between them', () => {
    expect(pageRanges([9, 2, 3, 4])).toEqual(['2–4', '9']);
    expect(pageRanges([])).toEqual([]);
  });
});

describe("the warnings on a PDF's card", () => {
  it('pages without text, with unreadable text and with text that may be hidden', () => {
    const lines = pdfNoteLines({ no_text: [2, 5], garbled: [3], hidden: [7] });
    expect(lines.map((l) => [l.kind, l.label, l.pages])).toEqual([
      ['no_text', 'Sense text', ['2', '5']],
      ['garbled', 'Text il·legible', ['3']],
      ['hidden', 'Possible text ocult', ['7']],
    ]);
    expect(lines.map((l) => l.description)).toEqual([
      'Pàgines sense text extraïble: escanejades, o amb el text dibuixat com a imatge. Els models que obren el PDF les llegeixen com a imatge.',
      "El text extret d'aquestes pàgines té caràcters trencats (una font sense mapa de caràcters): no es pot llegir tal com és.",
      'Aquestes pàgines poden tenir text que no es veu: invisible, massa petit o fora de la pàgina. Es diu als models que el tractin com a sospitós.',
    ]);
  });

  it('only the kinds a PDF has, with compact page lists', () => {
    expect(pdfNoteLines({ no_text: [1, 2, 3, 4, 9], garbled: [], hidden: [] }).map((l) => l.pages)).toEqual([['1–4', '9']]);
    expect(pdfNoteLines({ no_text: [], garbled: [], hidden: [] })).toEqual([]);
    // Images, text files and PDFs the server could not analyse have none.
    expect(pdfNoteLines(null)).toEqual([]);
  });
});

describe("what the turn says of a finished check (Claude's check for ChatGPT)", () => {
  const checked = { state: 'checked' as const, claudePages: [2, 5], hiddenPages: [5], uncheckedPages: [], reason: null };

  it('the pages ChatGPT read through Claude, and the hidden text it did not get', () => {
    expect(checkDetails(checked)).toEqual(['pàgines 2 i 5 llegides per Claude', `text ocult a la pàg.${NB}5: no s'ha passat`]);
  });

  it('one page, several and runs of pages', () => {
    expect(checkDetails({ ...checked, claudePages: [4], hiddenPages: [] })).toEqual(['pàgina 4 llegida per Claude']);
    expect(checkDetails({ ...checked, claudePages: [2, 3, 4, 9], hiddenPages: [3, 7] })).toEqual([
      'pàgines 2–4 i 9 llegides per Claude',
      `text ocult a les pàg.${NB}3 i 7: no s'ha passat`,
    ]);
    expect(checkDetails({ ...checked, claudePages: [1, 3, 5], hiddenPages: [] })).toEqual(['pàgines 1, 3 i 5 llegides per Claude']);
  });

  it('the pages left unchecked, and why', () => {
    const partial = {
      ...checked,
      claudePages: [],
      hiddenPages: [],
      uncheckedPages: [31, 32, 33, 34, 35, 36, 37, 38, 39, 40],
      reason: "Claude només l'ha pogut contrastar fins a la pàgina 30.",
    };
    expect(checkDetails(partial)).toEqual([
      "pàgines 31–40 sense contrastar: Claude només l'ha pogut contrastar fins a la pàgina 30.",
    ]);
    expect(checkDetails({ ...partial, uncheckedPages: [40], reason: null })).toEqual(['pàgina 40 sense contrastar']);
  });

  it('a check that found nothing to correct says so', () => {
    expect(checkDetails({ ...checked, claudePages: [], hiddenPages: [] })).toEqual(['Claude no hi ha trobat cap diferència']);
  });

  it('nothing while it runs, or when nothing was checked (the reason goes with the headline)', () => {
    expect(checkDetails({ ...checked, state: 'checking', claudePages: [], hiddenPages: [] })).toEqual([]);
    expect(
      checkDetails({ state: 'unchecked', claudePages: [], hiddenPages: [], uncheckedPages: [1, 2], reason: 'La comprovació de Claude ha trigat massa.' }),
    ).toEqual([]);
  });
});

describe("the badge on ChatGPT's messages (meta.pdf_reading)", () => {
  const reading = (partial: Partial<PdfReading>): PdfReading => ({
    attachment_id: 7,
    name: 'informe.pdf',
    checked: true,
    claude_pages: [2, 5],
    hidden_pages: [5],
    unchecked_pages: [],
    reason: null,
    ...partial,
  });

  it('a PDF checked by Claude: the pages it read, the hidden ones and the unchecked ones', () => {
    const [group, ...rest] = readingGroups([reading({ unchecked_pages: [11, 12], reason: "Claude només l'ha pogut contrastar fins a la pàgina 10." })]);
    expect(rest).toEqual([]);
    expect(group).toEqual({
      checked: true,
      label: 'PDF contrastat per Claude',
      lead: "ChatGPT no pot obrir els PDF: n'ha llegit el text extret, contrastat per Claude.",
      pdfs: [
        {
          attachmentId: 7,
          name: 'informe.pdf',
          rows: [
            { label: 'Llegides per Claude', pages: `pàg.${NB}2, 5` },
            { label: 'Text ocult, no passat', pages: `pàg.${NB}5` },
            { label: 'Sense contrastar', pages: `pàg.${NB}11, 12` },
          ],
          note: "Claude només l'ha pogut contrastar fins a la pàgina 10.",
        },
      ],
    });
  });

  it('a PDF nobody checked: its pages and why', () => {
    const [group] = readingGroups([
      reading({ checked: false, claude_pages: [], hidden_pages: [], unchecked_pages: [1, 2, 3, 4], reason: 'Claude no està disponible per contrastar-lo.' }),
    ]);
    expect(group).toMatchObject({
      checked: false,
      label: 'PDF sense contrastar',
      lead: "ChatGPT no pot obrir els PDF: n'ha llegit el text extret, sense contrastar.",
      pdfs: [{ rows: [{ label: 'Sense contrastar', pages: `pàg.${NB}1–4` }], note: 'Claude no està disponible per contrastar-lo.' }],
    });
  });

  it('a checked PDF where Claude found nothing to correct', () => {
    const [group] = readingGroups([reading({ claude_pages: [], hidden_pages: [] })]);
    expect(group!.pdfs).toEqual([{ attachmentId: 7, name: 'informe.pdf', rows: [], note: 'Claude no hi ha trobat cap diferència.' }]);
  });

  it('several PDFs: the checked ones and the unchecked ones apart, each named', () => {
    const groups = readingGroups([
      reading({ attachment_id: 1, name: 'a.pdf' }),
      reading({ attachment_id: 2, name: 'b.pdf', checked: false, claude_pages: [], hidden_pages: [], unchecked_pages: [1], reason: 'La comprovació de Claude ha trigat massa.' }),
      reading({ attachment_id: 3, name: 'c.pdf', claude_pages: [1], hidden_pages: [] }),
    ]);
    expect(groups.map((g) => [g.label, g.pdfs.map((p) => p.name)])).toEqual([
      ['2 PDF contrastats per Claude', ['a.pdf', 'c.pdf']],
      ['PDF sense contrastar', ['b.pdf']],
    ]);
    const unchecked = readingGroups([
      reading({ attachment_id: 1, checked: false, claude_pages: [], hidden_pages: [], unchecked_pages: [1] }),
      reading({ attachment_id: 2, checked: false, claude_pages: [], hidden_pages: [], unchecked_pages: [1] }),
    ]);
    expect(unchecked.map((g) => g.label)).toEqual(['2 PDF sense contrastar']);
  });

  it('none for a message without readings', () => {
    expect(readingGroups([])).toEqual([]);
  });
});
