// Texts of PdfChecks, PdfReadingBadge, lib/pdf-pages.ts. English is the source: Spanish and Catalan have the same keys and
// parameters (TypeScript checks it).
//
// A list of pages comes already written (lib/pdf-pages.ts): «2–4, 9», or «2–4 and 9» in a
// sentence. A no-break space ( ) keeps an abbreviation with the first page.

import { plural } from '../index.svelte';

export const en = {
  /** The abbreviation before a list of `count` pages: «p. 2», «pp. 2–4, 9». */
  pages: (count: number): string => plural(count, 'p.', 'pp.'),

  // PdfChecks.svelte: Claude's check of each PDF for ChatGPT, in the turn. The name of the
  // PDF goes between `before` and `after`.
  check: {
    label: 'PDF checks for ChatGPT',
    checking: { before: 'Claude is checking “', after: '” for ChatGPT…' },
    checked: { before: 'ChatGPT reads “', after: '”, checked by Claude' },
    interrupted: { before: 'The turn stopped before Claude finished checking “', after: '”.' },
    /** Then «: <why>» or «.». */
    unchecked: { before: 'ChatGPT reads the text of “', after: '”, unchecked' },
    reused: 'already checked',
    tokens: (tokens: string) => `${tokens} tokens`,
  },

  // lib/pdf-pages.ts checkDetails: what a finished check adds to its headline.
  details: {
    read: (count: number, pages: string) => plural(count, `page ${pages} read by Claude`, `pages ${pages} read by Claude`),
    hidden: (count: number, pages: string) => `hidden text on ${plural(count, 'p.', 'pp.')} ${pages}: not passed on`,
    /** Then «: <why>» when the server says why. */
    unchecked: (count: number, pages: string) => plural(count, `page ${pages} unchecked`, `pages ${pages} unchecked`),
    noDifference: 'Claude found no difference',
  },

  // lib/pdf-pages.ts readingGroups: the badges of ChatGPT's messages (PdfReadingBadge.svelte).
  reading: {
    checked: (count: number) => plural(count, 'PDF checked by Claude', `${count} PDFs checked by Claude`),
    unchecked: (count: number) => plural(count, 'Unchecked PDF', `${count} unchecked PDFs`),
    leadChecked: 'ChatGPT cannot open PDFs: it read the extracted text, checked by Claude.',
    leadUnchecked: 'ChatGPT cannot open PDFs: it read the extracted text, unchecked.',
    rows: { claude: 'Read by Claude', hidden: 'Hidden text, not passed on', unchecked: 'Unchecked' },
  },

  // lib/pdf-pages.ts pdfNoteLines: the warnings on a PDF's card.
  notes: {
    no_text: {
      label: 'No text',
      description:
        'Pages without extractable text: scanned, or with the text drawn as an image. The models that open the PDF read them as images.',
    },
    garbled: {
      label: 'Unreadable text',
      description:
        'The text extracted from these pages has broken characters (a font without a character map): it cannot be read as it is.',
    },
    hidden: {
      label: 'Possible hidden text',
      description:
        'These pages may have text that does not show: invisible, too small or off the page. The models are told to treat it as suspicious.',
    },
  },
};

export const es: typeof en = {
  pages: (count: number) => plural(count, 'pág.', 'págs.'),

  check: {
    label: 'Contraste de los PDF para ChatGPT',
    checking: { before: 'Claude contrasta «', after: '» para ChatGPT…' },
    checked: { before: 'ChatGPT lee «', after: '» contrastado por Claude' },
    interrupted: { before: 'El turno se ha detenido antes de que Claude acabara de contrastar «', after: '».' },
    unchecked: { before: 'ChatGPT lee el texto de «', after: '» sin contrastar' },
    reused: 'ya contrastado antes',
    tokens: (tokens: string) => `${tokens} tokens`,
  },

  details: {
    read: (count: number, pages: string) => plural(count, `página ${pages} leída por Claude`, `páginas ${pages} leídas por Claude`),
    hidden: (count: number, pages: string) => `texto oculto en ${plural(count, 'la pág.', 'las págs.')} ${pages}: no se ha pasado`,
    unchecked: (count: number, pages: string) => plural(count, `página ${pages} sin contrastar`, `páginas ${pages} sin contrastar`),
    noDifference: 'Claude no ha encontrado ninguna diferencia',
  },

  reading: {
    checked: (count: number) => plural(count, 'PDF contrastado por Claude', `${count} PDF contrastados por Claude`),
    unchecked: (count: number) => plural(count, 'PDF sin contrastar', `${count} PDF sin contrastar`),
    leadChecked: 'ChatGPT no puede abrir los PDF: ha leído el texto extraído, contrastado por Claude.',
    leadUnchecked: 'ChatGPT no puede abrir los PDF: ha leído el texto extraído, sin contrastar.',
    rows: { claude: 'Leídas por Claude', hidden: 'Texto oculto, no pasado', unchecked: 'Sin contrastar' },
  },

  notes: {
    no_text: {
      label: 'Sin texto',
      description:
        'Páginas sin texto extraíble: escaneadas, o con el texto dibujado como imagen. Los modelos que abren el PDF las leen como imagen.',
    },
    garbled: {
      label: 'Texto ilegible',
      description:
        'El texto extraído de estas páginas tiene caracteres rotos (una fuente sin mapa de caracteres): no se puede leer tal como está.',
    },
    hidden: {
      label: 'Posible texto oculto',
      description:
        'Estas páginas pueden tener texto que no se ve: invisible, demasiado pequeño o fuera de la página. Se pide a los modelos que lo traten como sospechoso.',
    },
  },
};

export const ca: typeof en = {
  pages: (_count: number) => 'pàg.',

  check: {
    label: 'Contrast dels PDF per a ChatGPT',
    checking: { before: 'Claude contrasta «', after: '» per a ChatGPT…' },
    checked: { before: 'ChatGPT llegeix «', after: '» contrastat per Claude' },
    interrupted: { before: "El torn s'ha aturat abans que Claude acabés de contrastar «", after: '».' },
    unchecked: { before: 'ChatGPT llegeix el text de «', after: '» sense contrastar' },
    reused: 'ja contrastat abans',
    tokens: (tokens: string) => `${tokens} tokens`,
  },

  details: {
    read: (count: number, pages: string) => plural(count, `pàgina ${pages} llegida per Claude`, `pàgines ${pages} llegides per Claude`),
    hidden: (count: number, pages: string) => `text ocult a ${plural(count, 'la', 'les')} pàg. ${pages}: no s'ha passat`,
    unchecked: (count: number, pages: string) => plural(count, `pàgina ${pages} sense contrastar`, `pàgines ${pages} sense contrastar`),
    noDifference: 'Claude no hi ha trobat cap diferència',
  },

  reading: {
    checked: (count: number) => plural(count, 'PDF contrastat per Claude', `${count} PDF contrastats per Claude`),
    unchecked: (count: number) => plural(count, 'PDF sense contrastar', `${count} PDF sense contrastar`),
    leadChecked: "ChatGPT no pot obrir els PDF: n'ha llegit el text extret, contrastat per Claude.",
    leadUnchecked: "ChatGPT no pot obrir els PDF: n'ha llegit el text extret, sense contrastar.",
    rows: { claude: 'Llegides per Claude', hidden: 'Text ocult, no passat', unchecked: 'Sense contrastar' },
  },

  notes: {
    no_text: {
      label: 'Sense text',
      description:
        'Pàgines sense text extraïble: escanejades, o amb el text dibuixat com a imatge. Els models que obren el PDF les llegeixen com a imatge.',
    },
    garbled: {
      label: 'Text il·legible',
      description:
        "El text extret d'aquestes pàgines té caràcters trencats (una font sense mapa de caràcters): no es pot llegir tal com és.",
    },
    hidden: {
      label: 'Possible text ocult',
      description:
        'Aquestes pàgines poden tenir text que no es veu: invisible, massa petit o fora de la pàgina. Es diu als models que el tractin com a sospitós.',
    },
  },
};
