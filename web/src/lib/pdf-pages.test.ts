// What the UI says of an attached PDF's pages (docs/PROTOCOL.md "Attachments", docs/adr/0009-attachments.md):
// compact page lists, the warnings of the server's analysis on its card, Claude's check
// of its text for ChatGPT in the turn, and the badge on ChatGPT's messages that says how
// it read it. In Catalan, and at the end in English and Spanish.
import { afterEach, describe, expect, it } from 'vitest';
import { i18n, type Locale } from './i18n/index.svelte';
import { afterColon, checkDetails, pageList, pageRanges, pageRefs, pdfNoteLines, readingGroups } from './pdf-pages';
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

  it('a reason after the colon starts with a lowercase letter, unless it starts with a name', () => {
    const failed = {
      ...checked,
      claudePages: [1, 2, 3],
      hiddenPages: [],
      uncheckedPages: [4, 5, 6, 7, 8, 9, 10, 11, 12],
      reason: 'La comprovació de Claude ha fallat: Error inesperat del proveïdor.',
    };
    expect(checkDetails(failed)).toEqual([
      'pàgines 1–3 llegides per Claude',
      'pàgines 4–12 sense contrastar: la comprovació de Claude ha fallat: Error inesperat del proveïdor.',
    ]);
    expect(checkDetails({ ...failed, reason: "Claude no l'ha volgut contrastar." })[1]).toBe(
      "pàgines 4–12 sense contrastar: Claude no l'ha volgut contrastar.",
    );
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

  it('a reason on a line of its own keeps its capital letter', () => {
    const [group] = readingGroups([
      reading({ checked: false, claude_pages: [], hidden_pages: [], unchecked_pages: [1], reason: 'La comprovació de Claude ha trigat massa.' }),
    ]);
    expect(group!.pdfs[0]!.note).toBe('La comprovació de Claude ha trigat massa.');
  });
});

describe('a sentence after a colon (Catalan typography)', () => {
  it("starts with a lowercase letter: the server's reasons are sentences of their own", () => {
    expect(afterColon('La comprovació de Claude ha trigat massa.')).toBe('la comprovació de Claude ha trigat massa.');
    expect(afterColon("El servidor no n'ha pogut analitzar les pàgines.")).toBe("el servidor no n'ha pogut analitzar les pàgines.");
    expect(afterColon("L'anàlisi ha fallat.")).toBe("l'anàlisi ha fallat.");
    expect(afterColon('És massa llarg.')).toBe('és massa llarg.');
  });

  it('keeps the capital of a name or an acronym', () => {
    expect(afterColon("Claude no l'ha volgut contrastar.")).toBe("Claude no l'ha volgut contrastar.");
    expect(afterColon("Claude només l'ha pogut contrastar fins a la pàgina 30.")).toBe(
      "Claude només l'ha pogut contrastar fins a la pàgina 30.",
    );
    expect(afterColon('ChatGPT no pot obrir el PDF.')).toBe('ChatGPT no pot obrir el PDF.');
    expect(afterColon('PDF malmès.')).toBe('PDF malmès.');
  });

  it('leaves alone what does not start with a letter', () => {
    expect(afterColon('')).toBe('');
    expect(afterColon('3 pàgines sense text.')).toBe('3 pàgines sense text.');
    expect(afterColon('«Informe» no existeix.')).toBe('«Informe» no existeix.');
  });
});

describe('in English and Spanish', () => {
  afterEach(() => i18n.set('ca'));

  const checked = { state: 'checked' as const, claudePages: [2, 5], hiddenPages: [5], uncheckedPages: [], reason: null };
  const reading = (partial: Partial<PdfReading>): PdfReading => ({
    attachment_id: 7,
    name: 'report.pdf',
    checked: true,
    claude_pages: [2, 5],
    hidden_pages: [5],
    unchecked_pages: [],
    reason: null,
    ...partial,
  });

  it('a list of pages in a sentence follows the grammar of each language', () => {
    const read = (locale: Locale) => {
      i18n.set(locale);
      return [[2], [2, 5], [2, 3, 4, 9, 12]].map((pages) => checkDetails({ ...checked, claudePages: pages, hiddenPages: [] })[0]);
    };
    expect(read('en')).toEqual(['page 2 read by Claude', 'pages 2 and 5 read by Claude', 'pages 2–4, 9 and 12 read by Claude']);
    expect(read('es')).toEqual(['página 2 leída por Claude', 'páginas 2 y 5 leídas por Claude', 'páginas 2–4, 9 y 12 leídas por Claude']);
    expect(read('ca')).toEqual(['pàgina 2 llegida per Claude', 'pàgines 2 i 5 llegides per Claude', 'pàgines 2–4, 9 i 12 llegides per Claude']);
  });

  it('the abbreviation of a compact list says one page or several', () => {
    i18n.set('en');
    expect([pageRefs([5]), pageRefs([2, 3, 4, 9])]).toEqual([`p.${NB}5`, `pp.${NB}2–4, 9`]);
    i18n.set('es');
    expect([pageRefs([5]), pageRefs([2, 5])]).toEqual([`pág.${NB}5`, `págs.${NB}2, 5`]);
  });

  it("what a finished check adds to its headline, the server's reason after a colon", () => {
    i18n.set('en');
    expect(checkDetails(checked)).toEqual(['pages 2 and 5 read by Claude', `hidden text on p.${NB}5: not passed on`]);
    const late = { ...checked, claudePages: [30], hiddenPages: [], uncheckedPages: [31, 32, 33] };
    expect(checkDetails({ ...late, reason: 'The server could not analyse its pages.' })).toEqual([
      'page 30 read by Claude',
      'pages 31–33 unchecked: the server could not analyse its pages.',
    ]);
    expect(checkDetails({ ...late, reason: "Claude's check took too long." })[1]).toBe("pages 31–33 unchecked: Claude's check took too long.");
    expect(checkDetails({ ...checked, claudePages: [], hiddenPages: [] })).toEqual(['Claude found no difference']);
    i18n.set('es');
    expect(checkDetails({ ...checked, hiddenPages: [5, 7] })).toEqual([
      'páginas 2 y 5 leídas por Claude',
      `texto oculto en las págs.${NB}5 y 7: no se ha pasado`,
    ]);
    expect(checkDetails({ ...checked, claudePages: [], hiddenPages: [], uncheckedPages: [4], reason: 'La comprobación de Claude ha tardado demasiado.' })).toEqual([
      'página 4 sin contrastar: la comprobación de Claude ha tardado demasiado.',
    ]);
  });

  it("the badges of ChatGPT's messages", () => {
    const readings = [
      reading({}),
      reading({ attachment_id: 8, name: 'annex.pdf', claude_pages: [], hidden_pages: [] }),
      reading({ attachment_id: 9, name: 'scan.pdf', checked: false, claude_pages: [], hidden_pages: [], unchecked_pages: [1, 2, 3] }),
    ];
    i18n.set('en');
    const [checkedGroup, unchecked] = readingGroups(readings);
    expect(checkedGroup).toMatchObject({
      label: '2 PDFs checked by Claude',
      lead: 'ChatGPT cannot open PDFs: it read the extracted text, checked by Claude.',
      pdfs: [
        { rows: [{ label: 'Read by Claude', pages: `pp.${NB}2, 5` }, { label: 'Hidden text, not passed on', pages: `p.${NB}5` }], note: null },
        { rows: [], note: 'Claude found no difference.' },
      ],
    });
    expect(unchecked).toMatchObject({ label: 'Unchecked PDF', pdfs: [{ rows: [{ label: 'Unchecked', pages: `pp.${NB}1–3` }] }] });
    i18n.set('es');
    const [one, other] = readingGroups([readings[0]!, readings[2]!, { ...readings[2]!, attachment_id: 10 }]);
    expect(one).toMatchObject({
      label: 'PDF contrastado por Claude',
      lead: 'ChatGPT no puede abrir los PDF: ha leído el texto extraído, contrastado por Claude.',
      pdfs: [{ rows: [{ label: 'Leídas por Claude', pages: `págs.${NB}2, 5` }, { label: 'Texto oculto, no pasado', pages: `pág.${NB}5` }] }],
    });
    expect(other).toMatchObject({
      label: '2 PDF sin contrastar',
      lead: 'ChatGPT no puede abrir los PDF: ha leído el texto extraído, sin contrastar.',
      pdfs: [{ rows: [{ label: 'Sin contrastar', pages: `págs.${NB}1–3` }] }, { rows: [{ label: 'Sin contrastar' }] }],
    });
  });

  it("the warnings on a PDF's card, with the abbreviation of their pages", () => {
    i18n.set('es');
    const lines = pdfNoteLines({ no_text: [2], garbled: [3, 4], hidden: [] });
    expect(lines.map((l) => [l.label, l.abbreviation, l.pages])).toEqual([
      ['Sin texto', 'pág.', ['2']],
      ['Texto ilegible', 'págs.', ['3', '4']],
    ]);
    expect(lines[0]!.description).toMatch(/^Páginas sin texto extraíble/);
    i18n.set('en');
    expect(pdfNoteLines({ no_text: [], garbled: [], hidden: [1, 2, 3] }).map((l) => [l.label, l.abbreviation, l.pages])).toEqual([
      ['Possible hidden text', 'pp.', ['1–3']],
    ]);
    i18n.set('ca');
    expect(pdfNoteLines({ no_text: [1, 2], garbled: [], hidden: [] }).map((l) => l.abbreviation)).toEqual(['pàg.']);
  });

  it('a sentence after a colon starts with a lowercase letter, unless it starts with a name', () => {
    expect(afterColon('The server could not analyse its pages.')).toBe('the server could not analyse its pages.');
    expect(afterColon("Claude's check took too long.")).toBe("Claude's check took too long.");
    expect(afterColon('El servidor no ha podido analizar sus páginas.')).toBe('el servidor no ha podido analizar sus páginas.');
    expect(afterColon('OpenAI no responde.')).toBe('OpenAI no responde.');
  });
});
