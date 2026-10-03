// Images and PDFs prepared in the browser before their upload (docs/PROTOCOL.md «Adjunts»):
//
// - An image larger than the models use (DOWNSCALE_EDGE) is downscaled in a canvas and
//   encoded as WebP (quality 0.9): the same for the models, far less to upload. A GIF
//   goes as it is. Where the browser cannot encode WebP, see `encodeFormats`.
// - A photo stored turned (an EXIF orientation, as phones take them) is shown upright,
//   but the models get its pixels as stored, without the metadata: it is re-encoded the
//   same way at its size, upright (lib/image-header.ts).
// - Every image and PDF gets a small thumbnail (a PNG or WebP within the server's limits),
//   made once, here, and uploaded with it; a PDF's is its first page, drawn by PDF.js,
//   which is loaded only then (lib/load-pdf.ts).

import {
  AttachmentError,
  downscaleTarget,
  encodeFormats,
  fitWithin,
  imageSideMessage,
  SNIFF_BYTES,
  tooLargeMessage,
  unreadableImageMessage,
  type EncodeFormat,
} from './attachments';
import { storedUpright, type Size } from './image-header';
import { loadPdf } from './load-pdf';
import { MAX_IMAGE_BYTES, MAX_IMAGE_SIDE, MAX_THUMBNAIL_BYTES } from './protocol';

/** The long edge of a thumbnail. */
export const THUMBNAIL_EDGE = 256;
/** Long edges tried, in order, while a thumbnail is heavier than the server takes. */
const THUMBNAIL_EDGES = [THUMBNAIL_EDGE, 192, 128];
const THUMBNAIL_FORMATS: readonly EncodeFormat[] = [{ type: 'image/webp', quality: 0.8 }, { type: 'image/png' }];
/** A PDF not drawn by then gets no thumbnail (a card with an icon), and PDF.js stops. */
export const PDF_PREVIEW_TIMEOUT_MS = 20_000;

/** The first bytes of a file (its type is read from them). */
export async function readHead(file: Blob, bytes = SNIFF_BYTES): Promise<Uint8Array> {
  return new Uint8Array(await file.slice(0, bytes).arrayBuffer());
}

export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error('The file could not be read.'));
    reader.readAsDataURL(blob);
  });
}

type Source = CanvasImageSource & { readonly width: number; readonly height: number };

/** `source` drawn at `width` x `height` (on `background`, where a format has no transparency). */
function draw(source: Source, width: number, height: number, background?: string): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) throw new AttachmentError(422, unreadableImageMessage());
  if (background) {
    context.fillStyle = background;
    context.fillRect(0, 0, width, height);
  }
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = 'high';
  context.drawImage(source, 0, 0, width, height);
  return canvas;
}

/** The canvas encoded as `format`; null when this browser cannot encode it (it gives a PNG then). */
function encode(canvas: HTMLCanvasElement, format: EncodeFormat): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob && blob.type === format.type ? blob : null), format.type, format.quality);
  });
}

/** The image at `size`, in the first format this browser encodes within the upload limit. */
async function downscale(image: Source, size: { width: number; height: number }, mime: string): Promise<Blob> {
  let plain: HTMLCanvasElement | null = null;
  for (const format of encodeFormats(mime)) {
    // A JPEG has no transparency: what was transparent turns white, not black.
    const canvas =
      format.type === 'image/jpeg' ? draw(image, size.width, size.height, '#fff') : (plain ??= draw(image, size.width, size.height));
    const blob = await encode(canvas, format);
    if (blob && blob.size <= MAX_IMAGE_BYTES) return blob;
  }
  throw new AttachmentError(413, tooLargeMessage('image'));
}

/** A thumbnail of `image`: THUMBNAIL_EDGE or smaller, within the server's limits; null if none fits. */
async function thumbnailOf(image: Source): Promise<Blob | null> {
  for (const edge of THUMBNAIL_EDGES) {
    const size = fitWithin(image.width, image.height, edge);
    const canvas = draw(image, size.width, size.height);
    for (const format of THUMBNAIL_FORMATS) {
      const blob = await encode(canvas, format);
      if (blob && blob.size <= MAX_THUMBNAIL_BYTES) return blob;
    }
  }
  return null;
}

export interface PreparedImage {
  /** What is uploaded: the file itself, or a copy encoded here (downscaled, or turned upright). */
  upload: Blob;
  /** Of what is uploaded, upright (as its EXIF orientation says). */
  width: number;
  height: number;
  thumbnail: Blob | null;
}

/**
 * The size `file` is encoded at before its upload, or null to upload it as it is;
 * `decoded` is its size as the browser shows it (upright). A large or heavy image is
 * downscaled (`downscaleTarget`); one stored otherwise than it is shown is turned upright
 * at its size. A GIF always goes as it is.
 */
async function encodeTarget(file: Blob, mime: string, decoded: Size): Promise<Size | null> {
  const target = downscaleTarget(mime, decoded.width, decoded.height, file.size);
  if (target !== null || mime === 'image/gif') return target;
  // Within MAX_IMAGE_BYTES: all of it is read, as the server reads it.
  const data = new Uint8Array(await file.arrayBuffer());
  return storedUpright(data, mime, decoded) ? null : { width: decoded.width, height: decoded.height };
}

/**
 * An image ready to upload, with its thumbnail. Throws AttachmentError for one the
 * browser cannot read (422), a GIF the server would refuse (413, 422), or one still too
 * heavy once downscaled (413).
 */
export async function prepareImage(file: Blob, mime: string): Promise<PreparedImage> {
  if (mime === 'image/gif' && file.size > MAX_IMAGE_BYTES) throw new AttachmentError(413, tooLargeMessage('image'));
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch {
    throw new AttachmentError(422, unreadableImageMessage());
  }
  try {
    const { width, height } = bitmap;
    if (mime === 'image/gif' && Math.max(width, height) > MAX_IMAGE_SIDE) {
      throw new AttachmentError(422, imageSideMessage(width, height));
    }
    const target = await encodeTarget(file, mime, { width, height });
    const upload = target ? await downscale(bitmap, target, mime) : file;
    const thumbnail = await thumbnailOf(bitmap).catch(() => null);
    return { upload, ...(target ?? { width, height }), thumbnail };
  } finally {
    bitmap.close();
  }
}

export interface PdfPreview {
  thumbnail: Blob | null;
  pages: number | null;
}

/** A PDF's first page as its thumbnail, and its pages; nothing when PDF.js cannot draw it in time. */
export async function pdfPreview(file: Blob): Promise<PdfPreview> {
  const none: PdfPreview = { thumbnail: null, pages: null };
  const stop = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<PdfPreview>((resolve) => {
    timer = setTimeout(() => {
      stop.abort();
      resolve(none);
    }, PDF_PREVIEW_TIMEOUT_MS);
  });
  const work = (async (): Promise<PdfPreview> => {
    const data = new Uint8Array(await file.arrayBuffer());
    const { renderFirstPage } = await loadPdf();
    const { canvas, pages } = await renderFirstPage(data, THUMBNAIL_EDGE, stop.signal);
    return { thumbnail: await thumbnailOf(canvas), pages };
  })().catch(() => none);
  try {
    return await Promise.race([work, late]);
  } finally {
    clearTimeout(timer);
  }
}
