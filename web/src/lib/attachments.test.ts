// What the browser checks before it uploads an attachment (docs/PROTOCOL.md «Adjunts»):
// the type from the content as the server sniffs it, its messages (in Catalan, and in
// English and Spanish at the end), the limits, the downscale of large images and the
// token estimate shown on each card.
import { afterEach, describe, expect, it } from 'vitest';
import {
  ACCEPT,
  AttachmentError,
  attachmentView,
  contentUrl,
  decodeText,
  detectType,
  downscaleTarget,
  emptyMessage,
  encodeFormats,
  estimateTokens,
  extension,
  firstLines,
  fitWithin,
  formatBytes,
  goneMessage,
  heicMessage,
  imageSideMessage,
  kindLabel,
  missingAttachment,
  pagesLabel,
  pdfPagesMessage,
  svgMessage,
  thumbnailUrl,
  tooLargeMessage,
  tooManyMessage,
  turnTooLargeMessage,
  typeLabel,
  unsupportedMessage,
} from './attachments';
import { i18n } from './i18n/index.svelte';
import {
  DOWNSCALE_EDGE,
  MAX_ATTACHMENTS,
  MAX_IMAGE_BYTES,
  MAX_IMAGE_SIDE,
  MAX_PDF_BYTES,
  MAX_PDF_PAGES,
  MAX_TEXT_BYTES,
  MAX_THUMBNAIL_BYTES,
  MAX_THUMBNAIL_SIDE,
  MAX_TURN_ATTACHMENT_BYTES,
  TEXT_EXTENSIONS,
  type Attachment,
} from './protocol';

const bytes = (...parts: (string | number[])[]): Uint8Array =>
  new Uint8Array(parts.flatMap((p) => (typeof p === 'string' ? [...p].map((c) => c.charCodeAt(0)) : p)));

const PNG = bytes([0x89], 'PNG', [0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13], 'IHDR');
const JPEG = bytes([0xff, 0xd8, 0xff, 0xe0, 0, 16], 'JFIF', [0, 1, 1, 0, 0, 1]);
const GIF87 = bytes('GIF87a', [1, 0, 1, 0, 0, 0, 0, 0, 0, 0]);
const GIF89 = bytes('GIF89a', [1, 0, 1, 0, 0, 0, 0, 0, 0, 0]);
const WEBP = bytes('RIFF', [36, 0, 0, 0], 'WEBPVP8 ');
const PDF = bytes('%PDF-1.7\n%', [0xe2, 0xe3, 0xcf, 0xd3], '\n');
const HEIC = bytes([0, 0, 0, 0x18], 'ftypheic', [0, 0, 0, 0]);
const TEXT = bytes('Hola, què tal?\n');

function refused(run: () => unknown): AttachmentError {
  try {
    run();
  } catch (err) {
    expect(err).toBeInstanceOf(AttachmentError);
    return err as AttachmentError;
  }
  throw new Error('it was not refused');
}

describe('the type of a file comes from its content, as the server decides it', () => {
  it.each([
    ['foto.png', PNG, 'image', 'image/png'],
    ['foto.txt', PNG, 'image', 'image/png'], // the content wins over the name
    ['sense-extensio', JPEG, 'image', 'image/jpeg'],
    ['a.gif', GIF87, 'image', 'image/gif'],
    ['a.gif', GIF89, 'image', 'image/gif'],
    ['a.webp', WEBP, 'image', 'image/webp'],
    ['informe.PDF', PDF, 'pdf', 'application/pdf'],
    ['notes.md', TEXT, 'text', 'text/plain'],
    ['Main.PY', TEXT, 'text', 'text/plain'],
    ['index.html', TEXT, 'text', 'text/plain'], // never HTML
    ['  dades.csv ', TEXT, 'text', 'text/plain'],
  ])('%s', (name, head, kind, mime) => {
    expect(detectType(head, name)).toEqual({ kind, mime });
  });

  it('refuses SVG and HEIC with their own message, anything else with the general one (415)', () => {
    expect(refused(() => detectType(bytes('<svg xmlns="http://www.w3.org/2000/svg">'), 'logo.svg'))).toMatchObject({
      status: 415,
      message: svgMessage(),
    });
    expect(refused(() => detectType(HEIC, 'IMG_0001.HEIC')).message).toBe(heicMessage());
    expect(refused(() => detectType(HEIC, 'foto.jpg')).message).toBe(heicMessage()); // a HEIF brand in the content
    for (const [name, head] of [
      ['arxiu.zip', bytes('PK', [3, 4, 20, 0])],
      ['dades.txt', bytes('ab', [0], 'cd')], // a NUL: not text
      ['.txt', TEXT], // no name before the extension
      ['sense-extensio', TEXT],
      ['presentacio.pptx', TEXT],
    ] as const) {
      expect(refused(() => detectType(head, name)), name).toMatchObject({ status: 415, message: unsupportedMessage() });
    }
  });

  it('uses the server messages', () => {
    expect(unsupportedMessage()).toBe(
      "Aquest tipus de fitxer no s'admet. Pots adjuntar imatges (PNG, JPEG, GIF o WebP), PDF i fitxers de text (UTF-8).",
    );
    expect(svgMessage()).toBe(
      "Les imatges SVG no s'admeten, perquè poden portar codi. Converteix-la a PNG i torna-la a adjuntar.",
    );
    expect(heicMessage()).toBe(
      "Les imatges HEIC no s'admeten. Converteix-la a JPEG (o fes-ne una captura) i torna-la a adjuntar.",
    );
    expect(emptyMessage()).toBe('El fitxer és buit.');
    expect(tooLargeMessage('image')).toBe('El fitxer és massa gran: una imatge pot tenir com a molt 7 MB.');
    expect(tooLargeMessage('pdf')).toBe('El fitxer és massa gran: un PDF pot tenir com a molt 20 MB.');
    expect(tooLargeMessage('text')).toBe('El fitxer és massa gran: un fitxer de text pot tenir com a molt 200 kB.');
    expect(imageSideMessage(9000, 1200)).toBe('La imatge fa 9.000 x 1.200 píxels: com a molt 8.000 per costat.');
    expect(pdfPagesMessage(150)).toBe('El PDF té 150 pàgines: com a molt 100.');
    expect(tooManyMessage()).toBe('Un missatge pot portar com a màxim 5 adjunts.');
    expect(turnTooLargeMessage()).toBe("Els adjunts d'un missatge no poden sumar més de 20 MB.");
  });

  it('knows which attachment a turn failed for because the server no longer has it', () => {
    // The server names it, whatever the language of its message.
    expect(missingAttachment({ kind: 'invalid', message: 'Attachment 12 does not exist.', attachment_id: 12 })).toBe(12);
    // Errors stored before it did: their Catalan message.
    expect(missingAttachment({ kind: 'invalid', message: "L'adjunt 12 no existeix." })).toBe(12);
    expect(missingAttachment({ kind: 'invalid', message: "L'adjunt no existeix." })).toBeNull();
    expect(missingAttachment({ kind: 'not_found', message: 'La conversa no existeix.' })).toBeNull();
    expect(goneMessage()).toBe("Aquest adjunt ja no és al servidor: treu-lo i torna'l a adjuntar.");
  });

  it('reads the extension as the server does', () => {
    expect(extension('informe.final.PDF')).toBe('pdf');
    expect(extension('.bashrc')).toBe('');
    expect(extension('README')).toBe('');
    expect(extension(' notes.TXT  ')).toBe('txt');
  });
});

describe('the content of a text file', () => {
  it('is valid UTF-8 without NUL, without the byte order mark', () => {
    expect(decodeText(new TextEncoder().encode('Pa amb tomàquet\n'), 'a.txt')).toBe('Pa amb tomàquet\n');
    expect(decodeText(new TextEncoder().encode('﻿Hola'), 'a.md')).toBe('Hola');
  });

  it('is refused otherwise (415)', () => {
    expect(refused(() => decodeText(bytes([0x41, 0xff, 0xfe, 0x42]), 'a.txt'))).toMatchObject({
      status: 415,
      message: unsupportedMessage(),
    });
    expect(refused(() => decodeText(bytes('a', [0], 'b'), 'a.txt')).message).toBe(unsupportedMessage());
  });

  it('gives the first lines for its card', () => {
    const text = Array.from({ length: 30 }, (_, i) => `línia ${i + 1}   `).join('\r\n');
    expect(firstLines(text, 3)).toBe('línia 1\nlínia 2\nlínia 3');
    expect(firstLines('a'.repeat(1000), 5, 40)).toBe('a'.repeat(40));
    expect(firstLines('\n\n  \nprimera\nsegona', 2)).toBe('primera\nsegona');
  });
});

describe('images larger than the models use are downscaled in the browser', () => {
  it('fits the long edge into 2.576 px, never enlarging', () => {
    expect(fitWithin(4000, 3000, DOWNSCALE_EDGE)).toEqual({ width: 2576, height: 1932 });
    expect(fitWithin(3000, 4000, DOWNSCALE_EDGE)).toEqual({ width: 1932, height: 2576 });
    expect(fitWithin(1000, 800, DOWNSCALE_EDGE)).toEqual({ width: 1000, height: 800 });
    expect(fitWithin(20_000, 1, DOWNSCALE_EDGE)).toEqual({ width: 2576, height: 1 });
  });

  it('downscales a large or heavy PNG, JPEG or WebP; a GIF goes as it is', () => {
    expect(downscaleTarget('image/jpeg', 4000, 3000, 2_000_000)).toEqual({ width: 2576, height: 1932 });
    expect(downscaleTarget('image/webp', 2577, 10, 1000)).toEqual({ width: 2576, height: 9 });
    // Within the edge but heavier than the limit: encoded again at its size.
    expect(downscaleTarget('image/png', 2000, 2000, MAX_IMAGE_BYTES + 1)).toEqual({ width: 2000, height: 2000 });
    expect(downscaleTarget('image/png', 2576, 2000, MAX_IMAGE_BYTES)).toBeNull();
    expect(downscaleTarget('image/gif', 5000, 5000, 3_000_000)).toBeNull();
  });

  it('encodes as WebP (quality 0.9), else as the browser can without losing much', () => {
    expect(encodeFormats('image/jpeg')).toEqual([
      { type: 'image/webp', quality: 0.9 },
      { type: 'image/jpeg', quality: 0.9 },
    ]);
    expect(encodeFormats('image/png')).toEqual([
      { type: 'image/webp', quality: 0.9 },
      { type: 'image/png' },
      { type: 'image/jpeg', quality: 0.9 },
    ]);
  });
});

describe('the estimated tokens of an attachment (as the server estimates them)', () => {
  it('images: one token per 28 px patch of the downscaled image, at most 4.784', () => {
    expect(estimateTokens('image', { width: 1000, height: 1000 })).toBe(36 * 36);
    expect(estimateTokens('image', { width: 100, height: 50 })).toBe(4 * 2);
    expect(estimateTokens('image', { width: 4000, height: 3000 })).toBe(4784);
    expect(estimateTokens('image', { width: 2576, height: 1000 })).toBe(92 * 36);
    expect(estimateTokens('image', {})).toBe(0);
  });

  it('PDFs: 3.600 per page; text: characters / 4', () => {
    expect(estimateTokens('pdf', { pages: 12 })).toBe(43_200);
    expect(estimateTokens('pdf', {})).toBe(0);
    expect(estimateTokens('text', { chars: 1001 })).toBe(251);
  });
});

describe('what a card says', () => {
  it('sizes in decimal units, with the Catalan decimal comma', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(999)).toBe('999 B');
    expect(formatBytes(1000)).toBe('1 kB');
    expect(formatBytes(1500)).toBe('1,5 kB');
    expect(formatBytes(200_000)).toBe('200 kB');
    expect(formatBytes(1_234_567)).toBe('1,2 MB');
    expect(formatBytes(20_000_000)).toBe('20 MB');
  });

  it('the type and the pages', () => {
    expect(typeLabel('image', 'image/png', 'a.png')).toBe('PNG');
    expect(typeLabel('image', 'image/jpeg', 'a.jpg')).toBe('JPEG');
    expect(typeLabel('image', 'image/gif', 'a.gif')).toBe('GIF');
    expect(typeLabel('image', 'image/webp', 'a.png')).toBe('WebP');
    expect(typeLabel('pdf', 'application/pdf', 'a.pdf')).toBe('PDF');
    expect(typeLabel('text', 'text/plain', 'main.py')).toBe('PY');
    expect(typeLabel('text', 'text/plain', 'dades.JSON')).toBe('JSON');
    expect(typeLabel(null, null, 'a.bin')).toBe('Fitxer');
    expect(kindLabel('image')).toBe('Imatge');
    expect(kindLabel('pdf')).toBe('PDF');
    expect(kindLabel('text')).toBe('Fitxer de text');
    expect(pagesLabel(1)).toBe('1 pàgina');
    expect(pagesLabel(12)).toBe('12 pàgines');
  });

  it('a stored attachment: its thumbnail from the server, when it has one', () => {
    const attachment: Attachment = {
      id: 12,
      name: 'informe.pdf',
      kind: 'pdf',
      mime: 'application/pdf',
      size: 1_200_000,
      pages: 12,
      width: null,
      height: null,
      sha256: 'a'.repeat(64),
      created_at: '2026-09-28T10:00:00Z',
      has_thumbnail: true,
      text_available: true,
      estimated_tokens: 43_200,
      pdf_notes: { no_text: [2, 5], garbled: [], hidden: [7] },
    };
    expect(contentUrl(12)).toBe('/api/attachments/12/content');
    expect(thumbnailUrl(12)).toBe('/api/attachments/12/thumbnail');
    expect(attachmentView(attachment)).toMatchObject({
      id: 12,
      name: 'informe.pdf',
      kind: 'pdf',
      size: 1_200_000,
      pages: 12,
      tokens: 43_200,
      thumbnail: '/api/attachments/12/thumbnail',
      lines: null,
      status: 'ready',
      error: null,
      pdfNotes: { no_text: [2, 5], garbled: [], hidden: [7] },
    });
    expect(attachmentView({ ...attachment, has_thumbnail: false }).thumbnail).toBeNull();
    expect(attachmentView({ ...attachment, pdf_notes: null }).pdfNotes).toBeNull();
  });

  it('the file picker offers what the server takes', () => {
    const accepted = ACCEPT.split(',');
    for (const type of ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'application/pdf', '.pdf', '.txt', '.md', '.py']) {
      expect(accepted).toContain(type);
    }
    expect(accepted).toHaveLength(new Set(accepted).size);
    expect(ACCEPT).not.toContain('svg');
    for (const ext of TEXT_EXTENSIONS) expect(accepted).toContain(`.${ext}`);
  });
});

/** docs/PROTOCOL.md, read from disk: Vite serves nothing from outside web/. */
async function protocolDoc(): Promise<string> {
  const module: string = 'node:fs'; // not a literal: the web code has no Node types
  const fs = (await import(/* @vite-ignore */ module)) as { readFileSync(path: string, encoding: 'utf8'): string };
  const here: string = import.meta.url;
  return fs.readFileSync(decodeURIComponent(new URL('../../../docs/PROTOCOL.md', here).pathname), 'utf8');
}

describe('the limits are the ones the protocol documents', () => {
  it('per message, per type and for thumbnails', async () => {
    const doc = await protocolDoc();
    const numbers = (pattern: RegExp): number[] => {
      const match = pattern.exec(doc);
      expect(match, String(pattern)).not.toBeNull();
      return match!.slice(1).map((n) => Number(n.replaceAll('.', '')));
    };
    expect(numbers(/com a molt (\d+) adjunts per missatge, i (\d+) MB entre tots/)).toEqual([
      MAX_ATTACHMENTS,
      MAX_TURN_ATTACHMENT_BYTES / 1_000_000,
    ]);
    expect(numbers(/imatge: (\d+) MB i ([\d.]+) píxels per costat/)).toEqual([MAX_IMAGE_BYTES / 1_000_000, MAX_IMAGE_SIDE]);
    expect(numbers(/més de ([\d.]+) píxels al costat llarg/)).toEqual([DOWNSCALE_EDGE]);
    expect(numbers(/PDF: (\d+) MB i (\d+) pàgines/)).toEqual([MAX_PDF_BYTES / 1_000_000, MAX_PDF_PAGES]);
    expect(numbers(/text: (\d+) kB/)).toEqual([MAX_TEXT_BYTES / 1000]);
    expect(numbers(/com a molt (\d+) kB i (\d+) píxels per costat/)).toEqual([MAX_THUMBNAIL_BYTES / 1000, MAX_THUMBNAIL_SIDE]);
    expect([MAX_ATTACHMENTS, MAX_TURN_ATTACHMENT_BYTES, MAX_IMAGE_BYTES, DOWNSCALE_EDGE]).toEqual([5, 20_000_000, 7_000_000, 2576]);
  });

  it('the extensions of text files', async () => {
    const doc = await protocolDoc();
    const list = /amb una d'aquestes extensions: (.+?)\. Sempre/.exec(doc)?.[1] ?? '';
    const documented = [...list.matchAll(/`([a-z]+)`/g)].map((m) => m[1]);
    expect(documented.length).toBeGreaterThan(30);
    expect(new Set(documented)).toEqual(new Set(TEXT_EXTENSIONS));
  });
});

describe('in English and Spanish', () => {
  afterEach(() => i18n.set('ca'));

  it('the refusals, with the numbers as each language writes them', () => {
    i18n.set('en');
    expect(tooLargeMessage('image')).toBe('The file is too large: an image can be at most 7 MB.');
    expect(tooLargeMessage('text')).toBe('The file is too large: a text file can be at most 200 kB.');
    expect(imageSideMessage(9000, 1200)).toBe('The image is 9,000 x 1,200 pixels: at most 8,000 per side.');
    expect(pdfPagesMessage(1500)).toBe('The PDF has 1,500 pages: at most 100.');
    expect(tooManyMessage()).toBe('A message can carry at most 5 attachments.');
    expect(turnTooLargeMessage()).toBe('The attachments of a message cannot add up to more than 20 MB.');
    expect(refused(() => detectType(HEIC, 'foto.heic')).message).toBe(
      'HEIC images are not accepted. Convert it to JPEG (or take a screenshot of it) and attach it again.',
    );

    i18n.set('es');
    expect(tooLargeMessage('pdf')).toBe('El archivo es demasiado grande: un PDF puede ocupar como máximo 20 MB.');
    expect(tooLargeMessage('image')).toBe('El archivo es demasiado grande: una imagen puede ocupar como máximo 7 MB.');
    expect(imageSideMessage(12_000, 1200)).toBe('La imagen mide 12.000 x 1200 píxeles: como máximo 8000 por lado.');
    expect(emptyMessage()).toBe('El archivo está vacío.');
    expect(goneMessage()).toBe('Este adjunto ya no está en el servidor: quítalo y vuelve a adjuntarlo.');
    expect(refused(() => detectType(bytes('PK'), 'arxiu.zip')).message).toBe(
      'Este tipo de archivo no se admite. Puedes adjuntar imágenes (PNG, JPEG, GIF o WebP), PDF y archivos de texto (UTF-8).',
    );
  });

  it('what a card says: sizes with the decimal mark of the language, types and pages', () => {
    i18n.set('en');
    expect([formatBytes(1500), formatBytes(1_234_567)]).toEqual(['1.5 kB', '1.2 MB']);
    expect([typeLabel(null, null, 'a.bin'), typeLabel('image', 'image/avif', 'a.avif'), kindLabel('text')]).toEqual([
      'File',
      'Image',
      'Text file',
    ]);
    expect([pagesLabel(1), pagesLabel(12)]).toEqual(['1 page', '12 pages']);

    i18n.set('es');
    expect([formatBytes(1500), formatBytes(1_234_567)]).toEqual(['1,5 kB', '1,2 MB']);
    expect([typeLabel(null, null, 'a.bin'), kindLabel('image'), kindLabel('text')]).toEqual(['Archivo', 'Imagen', 'Archivo de texto']);
    expect([pagesLabel(1), pagesLabel(12)]).toEqual(['1 página', '12 páginas']);
  });
});
