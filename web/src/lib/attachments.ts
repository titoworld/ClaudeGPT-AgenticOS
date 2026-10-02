// Files attached to a question (docs/PROTOCOL.md «Adjunts», docs/adr/0009-adjunts.md).
//
// - What the server would accept, checked in the browser with the server's own rules
//   and Catalan messages (src/agentic_os/attachments.py): the type comes from the
//   content, never from the name or the browser's type, so a file the server would
//   refuse is never uploaded (a large refused upload would only show a network error:
//   the server answers before it has read it all).
// - Which images are downscaled before the upload, and how they are encoded.
// - What an attachment's card says: type, size, pages, estimated tokens (and a PDF's
//   page warnings: lib/pdf-pages.ts).

import {
  DOWNSCALE_EDGE,
  MAX_ATTACHMENTS,
  MAX_IMAGE_BYTES,
  MAX_IMAGE_SIDE,
  MAX_PDF_BYTES,
  MAX_PDF_PAGES,
  MAX_TEXT_BYTES,
  MAX_TURN_ATTACHMENT_BYTES,
  TEXT_EXTENSIONS,
  type Attachment,
  type AttachmentKind,
  type PdfNotes,
} from './protocol';

/** A file that is refused: `status` is what the server answers for it (413, 415, 422). */
export class AttachmentError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'AttachmentError';
    this.status = status;
  }
}

/** A number as the server writes it in its messages: `9.000`. */
const grouped = (n: number): string => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.');

/** A size limit as the messages write it: `20 MB`, `200 kB`. */
function limitText(bytes: number): string {
  if (bytes >= 1_000_000 && bytes % 1_000_000 === 0) return `${bytes / 1_000_000} MB`;
  if (bytes >= 1000 && bytes % 1000 === 0) return `${bytes / 1000} kB`;
  return `${grouped(bytes)} bytes`;
}

// The server's messages (src/agentic_os/attachments.py and the engine's).
export const UNSUPPORTED_MESSAGE =
  "Aquest tipus de fitxer no s'admet. Pots adjuntar imatges (PNG, JPEG, GIF o WebP), PDF i fitxers de text (UTF-8).";
export const SVG_MESSAGE =
  "Les imatges SVG no s'admeten, perquè poden portar codi. Converteix-la a PNG i torna-la a adjuntar.";
export const HEIC_MESSAGE =
  "Les imatges HEIC no s'admeten. Converteix-la a JPEG (o fes-ne una captura) i torna-la a adjuntar.";
export const EMPTY_MESSAGE = 'El fitxer és buit.';
export const TOO_MANY_MESSAGE = `Un missatge pot portar com a màxim ${MAX_ATTACHMENTS} adjunts.`;
export const TURN_TOO_LARGE_MESSAGE = `Els adjunts d'un missatge no poden sumar més de ${limitText(MAX_TURN_ATTACHMENT_BYTES)}.`;
// The browser's own.
export const UNREADABLE_IMAGE_MESSAGE = "No s'ha pogut llegir la imatge: el fitxer no és vàlid.";
export const CONNECTION_FAILED_MESSAGE = "No s'ha pogut pujar el fitxer: la connexió ha fallat.";
export const GONE_MESSAGE = "Aquest adjunt ja no és al servidor: treu-lo i torna'l a adjuntar.";

/**
 * The attachment a turn failed for because the server does not have it (anymore): an
 * upload never sent is deleted after a day. docs/PROTOCOL.md gives the message of that
 * `turn.failed`: «L'adjunt 12 no existeix.».
 */
export function missingAttachment(message: string): number | null {
  const found = /^L'adjunt (\d+) no existeix\.$/.exec(message.trim());
  return found ? Number(found[1]) : null;
}

const KIND_LIMIT: Record<AttachmentKind, number> = { image: MAX_IMAGE_BYTES, pdf: MAX_PDF_BYTES, text: MAX_TEXT_BYTES };
const KIND_NAME: Record<AttachmentKind, string> = { image: 'una imatge', pdf: 'un PDF', text: 'un fitxer de text' };

/** Most bytes a file of `kind` may have. */
export const kindLimit = (kind: AttachmentKind): number => KIND_LIMIT[kind];

export const tooLargeMessage = (kind: AttachmentKind): string =>
  `El fitxer és massa gran: ${KIND_NAME[kind]} pot tenir com a molt ${limitText(KIND_LIMIT[kind])}.`;

export const imageSideMessage = (width: number, height: number): string =>
  `La imatge fa ${grouped(width)} x ${grouped(height)} píxels: com a molt ${grouped(MAX_IMAGE_SIDE)} per costat.`;

export const pdfPagesMessage = (pages: number): string =>
  `El PDF té ${grouped(pages)} pàgines: com a molt ${MAX_PDF_PAGES}.`;

// ------------------------------------------------------------ type of a file

export interface FileType {
  kind: AttachmentKind;
  mime: string;
}

/** Bytes enough to tell every accepted type apart (and HEIC, for its message). */
export const SNIFF_BYTES = 16;

const TEXT = new Set(TEXT_EXTENSIONS);
const HEIF_BRANDS = new Set(['heic', 'heix', 'hevc', 'hevx', 'heim', 'heis', 'mif1', 'msf1', 'avif']);

const ascii = (head: Uint8Array, start: number, end: number): string =>
  String.fromCharCode(...head.subarray(start, end));

const startsWith = (head: Uint8Array, signature: readonly number[]): boolean =>
  head.length >= signature.length && signature.every((byte, i) => head[i] === byte);

/** The lowercase extension of a file name, as the server reads it (`""` without one). */
export function extension(name: string): string {
  const clean = name.trim();
  const dot = clean.lastIndexOf('.');
  return dot > 0 ? clean.slice(dot + 1).toLowerCase() : '';
}

/** The type of a file from its first bytes: PNG, JPEG, GIF, WebP or PDF; null otherwise. */
export function sniff(head: Uint8Array): FileType | null {
  if (startsWith(head, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return { kind: 'image', mime: 'image/png' };
  if (startsWith(head, [0xff, 0xd8, 0xff])) return { kind: 'image', mime: 'image/jpeg' };
  const start = ascii(head, 0, 6);
  if (start === 'GIF87a' || start === 'GIF89a') return { kind: 'image', mime: 'image/gif' };
  if (ascii(head, 0, 4) === 'RIFF' && ascii(head, 8, 12) === 'WEBP') return { kind: 'image', mime: 'image/webp' };
  if (start.startsWith('%PDF-')) return { kind: 'pdf', mime: 'application/pdf' };
  return null;
}

function unsupported(head: Uint8Array, suffix: string): AttachmentError {
  if (suffix === 'svg' || suffix === 'svgz') return new AttachmentError(415, SVG_MESSAGE);
  const heif = ascii(head, 4, 8) === 'ftyp' && HEIF_BRANDS.has(ascii(head, 8, 12));
  if (suffix === 'heic' || suffix === 'heif' || suffix === 'avif' || heif) return new AttachmentError(415, HEIC_MESSAGE);
  return new AttachmentError(415, UNSUPPORTED_MESSAGE);
}

/**
 * The type of a file from its first bytes (SNIFF_BYTES, or all of a shorter file) and,
 * for text only, its name's extension. Throws AttachmentError (415) for a type the server
 * does not accept, SVG and HEIC with their own message. A text candidate still needs its
 * whole content checked (`decodeText`).
 */
export function detectType(head: Uint8Array, name: string): FileType {
  const found = sniff(head);
  if (found) return found;
  const suffix = extension(name);
  if (TEXT.has(suffix) && !head.includes(0)) return { kind: 'text', mime: 'text/plain' };
  throw unsupported(head, suffix);
}

/** The content of a text file: valid UTF-8 without NUL, without a byte order mark. */
export function decodeText(bytes: Uint8Array, name: string): string {
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw unsupported(bytes.subarray(0, SNIFF_BYTES), extension(name));
  }
  if (text.includes('\0')) throw unsupported(bytes.subarray(0, SNIFF_BYTES), extension(name));
  return text;
}

/** The first lines of a text for its card: blank lines at the start and ends of lines trimmed. */
export function firstLines(text: string, maxLines = 8, maxChars = 600): string {
  const lines = text.replace(/\r\n?/g, '\n').split('\n').map((line) => line.trimEnd());
  const start = lines.findIndex((line) => line !== '');
  if (start < 0) return '';
  const shown = lines.slice(start, start + maxLines).join('\n').trimEnd();
  const chars = Array.from(shown);
  return chars.length > maxChars ? chars.slice(0, maxChars).join('') : shown;
}

/**
 * What the file picker offers: the images (by type, so phones offer the camera and the
 * photos), PDFs and the text files the server accepts.
 */
export const ACCEPT = [
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'application/pdf',
  ...['png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', ...TEXT_EXTENSIONS].map((ext) => `.${ext}`),
].join(',');

// ------------------------------------------------------------ images

/** `width` x `height` with its long edge fitted into `edge`, never enlarged (as the server fits it). */
export function fitWithin(width: number, height: number, edge: number): { width: number; height: number } {
  const long = Math.max(width, height);
  if (long <= edge) return { width, height };
  return { width: Math.max(1, Math.floor((width * edge) / long)), height: Math.max(1, Math.floor((height * edge) / long)) };
}

/**
 * The size an image must be encoded at before its upload, or null to upload it as it is:
 * larger than the models use (DOWNSCALE_EDGE), or heavier than the limit. A GIF always
 * goes as it is (re-encoding it would drop its animation; the models see its first frame).
 */
export function downscaleTarget(
  mime: string,
  width: number,
  height: number,
  size: number,
): { width: number; height: number } | null {
  if (mime === 'image/gif') return null;
  if (Math.max(width, height) > DOWNSCALE_EDGE) return fitWithin(width, height, DOWNSCALE_EDGE);
  return size > MAX_IMAGE_BYTES ? { width, height } : null;
}

export interface EncodeFormat {
  type: string;
  quality?: number;
}

/**
 * Formats to try, in order, for an image downscaled in a canvas: WebP (quality 0.9);
 * where the browser cannot encode WebP (it gives a PNG), a JPEG for a photo, and for the
 * rest a PNG (it keeps transparency) or, if that is too heavy, a JPEG.
 */
export function encodeFormats(sourceMime: string): EncodeFormat[] {
  const webp: EncodeFormat = { type: 'image/webp', quality: 0.9 };
  const jpeg: EncodeFormat = { type: 'image/jpeg', quality: 0.9 };
  return sourceMime === 'image/jpeg' ? [webp, jpeg] : [webp, { type: 'image/png' }, jpeg];
}

// ------------------------------------------------------------ tokens

const PATCH = 28;
const MAX_IMAGE_TOKENS = 4784;
const PDF_PAGE_TOKENS = 3600;

/**
 * Approximate input tokens of an attachment for one call, as the server estimates them:
 * an image costs one token per 28 x 28 px patch once fitted into DOWNSCALE_EDGE (at most
 * 4.784), a PDF 3.600 per page, a text a token per 4 characters.
 */
export function estimateTokens(
  kind: AttachmentKind,
  { width, height, pages, chars }: { width?: number | null; height?: number | null; pages?: number | null; chars?: number },
): number {
  if (kind === 'image') {
    if (!width || !height) return 0;
    const fitted = fitWithin(width, height, DOWNSCALE_EDGE);
    return Math.min(MAX_IMAGE_TOKENS, Math.ceil(fitted.width / PATCH) * Math.ceil(fitted.height / PATCH));
  }
  if (kind === 'pdf') return (pages ?? 0) * PDF_PAGE_TOKENS;
  return Math.ceil((chars ?? 0) / 4);
}

// ------------------------------------------------------------ what a card says

const oneDecimal = new Intl.NumberFormat('ca-ES', { maximumFractionDigits: 1 });

/** A file size in decimal units, as the limits are written: `340 kB`, `1,2 MB`. */
export function formatBytes(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 999_950) return `${oneDecimal.format(bytes / 1000)} kB`;
  return `${oneDecimal.format(bytes / 1_000_000)} MB`;
}

const IMAGE_TYPES: Record<string, string> = {
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'image/gif': 'GIF',
  'image/webp': 'WebP',
};

/** The short type on a card: PNG, PDF, PY... */
export function typeLabel(kind: AttachmentKind | null, mime: string | null, name: string): string {
  if (kind === 'image') return IMAGE_TYPES[mime ?? ''] ?? 'Imatge';
  if (kind === 'pdf') return 'PDF';
  if (kind === 'text') return extension(name).toUpperCase() || 'TXT';
  return 'Fitxer';
}

export function kindLabel(kind: AttachmentKind): string {
  return kind === 'image' ? 'Imatge' : kind === 'pdf' ? 'PDF' : 'Fitxer de text';
}

export const pagesLabel = (pages: number): string => (pages === 1 ? '1 pàgina' : `${grouped(pages)} pàgines`);

export const contentUrl = (id: number): string => `/api/attachments/${id}/content`;
export const thumbnailUrl = (id: number): string => `/api/attachments/${id}/thumbnail`;

export type AttachmentStatus = 'uploading' | 'ready' | 'error';

/** What an attachment's card shows: one in the composer, or one of a question. */
export interface AttachmentView {
  key: string;
  /** Once uploaded. */
  id: number | null;
  name: string;
  /** Null until the file's type is known. */
  kind: AttachmentKind | null;
  mime: string | null;
  size: number;
  pages: number | null;
  tokens: number | null;
  /** URL of its thumbnail (the server's, or one made in this browser), if any. */
  thumbnail: string | null;
  /** A text file: its first lines, when known here (else the card asks the server). */
  lines: string | null;
  status: AttachmentStatus;
  /** Why it failed (Catalan). */
  error: string | null;
  /** Its upload failed for a reason a retry can fix (the connection...). */
  retryable: boolean;
  /** A PDF the server analysed: the warnings of its pages (lib/pdf-pages.ts says them). */
  pdfNotes: PdfNotes | null;
}

/** The card of an uploaded attachment (a question's, or one given back to the composer). */
export function attachmentView(a: Attachment, lines: string | null = null): AttachmentView {
  return {
    key: `a${a.id}`,
    id: a.id,
    name: a.name,
    kind: a.kind,
    mime: a.mime,
    size: a.size,
    pages: a.pages,
    tokens: a.estimated_tokens,
    thumbnail: a.has_thumbnail ? thumbnailUrl(a.id) : null,
    lines,
    status: 'ready',
    error: null,
    retryable: false,
    pdfNotes: a.pdf_notes ?? null,
  };
}
