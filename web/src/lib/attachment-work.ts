// The browser's work on a file just attached to the composer, loaded with the first
// attachment (lib/composer-attachments.svelte.ts) so the login screen does without it:
//
// 1. its checks, as the server would make them (its type from its content, its limits),
//    so one the server would refuse gets its error at once and is never uploaded;
// 2. its preparation: a large image downscaled, a thumbnail made (an image's, a PDF's
//    first page), a text file's first lines read (lib/media.ts);
// 3. its upload, and then its thumbnail's.

import { api } from './api';
import { rememberLines } from './attachment-lines';
import {
  AttachmentError,
  decodeText,
  detectType,
  emptyMessage,
  estimateTokens,
  firstLines,
  kindLimit,
  pdfPagesMessage,
  tooLargeMessage,
  turnTooLargeMessage,
} from './attachments';
import type { DraftAttachment } from './composer-attachments.svelte';
import { blobToDataUrl, pdfPreview, prepareImage, readHead } from './media';
import { MAX_PDF_PAGES, MAX_TURN_ATTACHMENT_BYTES } from './protocol';

/**
 * Checks, prepares and uploads `item`, then its thumbnail, until it is ready; throws what
 * went wrong (an AttachmentError for a file refused here). `controller` stops it (the
 * owner removed it) and `otherBytes` are those of the question's other attachments.
 */
export async function processDraft(item: DraftAttachment, controller: AbortController, otherBytes: () => number): Promise<void> {
  const file = item.file!;
  const { signal } = controller;
  if (file.size === 0) throw new AttachmentError(422, emptyMessage());
  const type = detectType(await readHead(file), file.name);
  item.kind = type.kind;
  item.mime = type.mime;
  // An image is downscaled first: its limit applies to what is uploaded.
  if (type.kind !== 'image' && file.size > kindLimit(type.kind)) throw new AttachmentError(413, tooLargeMessage(type.kind));
  let body: Blob = file;
  let thumbnail: Promise<Blob | null> = Promise.resolve(null);
  if (type.kind === 'text') {
    const text = decodeText(new Uint8Array(await file.arrayBuffer()), file.name);
    item.lines = firstLines(text);
    item.estimate = estimateTokens('text', { chars: Array.from(text).length });
  } else if (type.kind === 'image') {
    const image = await prepareImage(file, type.mime);
    body = image.upload;
    // An image encoded here (downscaled, or turned upright) is what goes, in the type it was encoded in.
    if (body !== file && body.type) item.mime = body.type;
    item.estimate = estimateTokens('image', image);
    thumbnail = Promise.resolve(image.thumbnail);
  } else {
    thumbnail = pdfPreview(file).then(({ thumbnail: blob, pages }) => {
      if (pages !== null) {
        item.pages = pages;
        item.estimate = estimateTokens('pdf', { pages });
        // Refused as soon as it is known, without waiting for the rest of the upload.
        if (pages > MAX_PDF_PAGES) controller.abort(new AttachmentError(422, pdfPagesMessage(pages)));
      }
      return blob;
    });
  }
  if (item.removed) return;
  if (otherBytes() + body.size > MAX_TURN_ATTACHMENT_BYTES) throw new AttachmentError(422, turnTooLargeMessage());
  item.body = body;
  // The thumbnail shows as soon as it is made.
  const shown = thumbnail
    .then(async (blob) => {
      if (blob && !item.removed) item.thumbnail = await blobToDataUrl(blob);
    })
    .catch(() => {});

  const attachment = await api.uploadAttachment(body, item.name, signal);
  if (item.removed) {
    // Removed while its answer was on the way: never sent, so it goes.
    void api.deleteAttachment(attachment.id).catch(() => {});
    return;
  }
  item.attachment = attachment;
  [item.kind, item.mime] = [attachment.kind, attachment.mime]; // the server's word
  if (attachment.kind === 'text' && item.lines !== null) rememberLines(attachment.id, item.lines);
  const blob = await thumbnail.catch(() => null);
  await shown;
  if (signal.aborted) throw signal.reason;
  if (blob) {
    try {
      await api.uploadThumbnail(attachment.id, blob, signal);
      item.attachment = { ...attachment, has_thumbnail: true };
    } catch (err) {
      // Without its thumbnail it still goes: its card shows an icon elsewhere.
      if (signal.aborted) throw err;
    }
  }
  item.status = 'ready';
}
