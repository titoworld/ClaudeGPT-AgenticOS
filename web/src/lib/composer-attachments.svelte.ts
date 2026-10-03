// The attachments of the composer (docs/PROTOCOL.md «Adjunts»). Each file attached is
// checked as the server would check it, prepared and uploaded, then its thumbnail
// (lib/attachment-work.ts, loaded with the first file), before it is ready; its card
// shows each state, and an error for one refused (in the language of the interface).
//
// A file removed stops its upload, or is deleted on the server if it got there. The
// ready ones go with the next question, in the order they were attached; if the server
// does not take the question they come back with it.

import { api, ApiError, RequestTimeoutError } from './api';
import { knownLines } from './attachment-lines';
import {
  AttachmentError,
  connectionFailedMessage,
  goneMessage,
  thumbnailUrl,
  tooManyMessage,
  type AttachmentStatus,
  type AttachmentView,
} from './attachments';
import { i18n } from './i18n/index.svelte';
import { MAX_ATTACHMENTS, type Attachment, type AttachmentKind, type PdfNotes } from './protocol';
import { toasts } from './toasts.svelte';

type Work = typeof import('./attachment-work');

let work: Promise<Work> | null = null;

/** The work on the files, loaded once, with the first one attached (tried again if it failed). */
function loadWork(): Promise<Work> {
  work ??= import('./attachment-work').catch((err: unknown) => {
    work = null;
    throw err;
  });
  return work;
}

let nextKey = 0;

/** One attachment of the composer: a file being prepared or uploaded, or an uploaded one. */
export class DraftAttachment implements AttachmentView {
  readonly key = `d${++nextKey}`;
  /** The file attached (null for one given back to the composer: it is on the server). */
  readonly file: File | null;
  kind: AttachmentKind | null = $state(null);
  mime: string | null = $state(null);
  /** The server's, once uploaded. */
  attachment: Attachment | null = $state.raw(null);
  thumbnail: string | null = $state(null);
  lines: string | null = $state(null);
  status: AttachmentStatus = $state('uploading');
  error: string | null = $state(null);
  retryable = $state(false);
  /** What is uploaded (a downscaled image is smaller than its file), once known. */
  body: Blob | null = null;
  /** The browser's estimate, until the server gives its own. */
  estimate: number | null = $state(null);
  #pages: number | null = $state(null);
  /** Stops what is being done for it (its upload...). */
  controller: AbortController | null = null;
  removed = false;
  task: Promise<void> = Promise.resolve();

  constructor(file: File | null, attachment: Attachment | null = null) {
    this.file = file;
    if (attachment) {
      this.attachment = attachment;
      this.kind = attachment.kind;
      this.mime = attachment.mime;
      this.status = 'ready';
      this.thumbnail = attachment.has_thumbnail ? thumbnailUrl(attachment.id) : null;
      this.lines = attachment.kind === 'text' ? knownLines(attachment.id) : null;
    }
  }

  get id(): number | null {
    return this.attachment?.id ?? null;
  }

  get name(): string {
    return this.attachment?.name ?? (this.file?.name.trim() || i18n.m.attachments.unnamed);
  }

  get size(): number {
    return this.attachment?.size ?? this.body?.size ?? this.file?.size ?? 0;
  }

  get pages(): number | null {
    return this.attachment?.pages ?? this.#pages;
  }

  set pages(value: number | null) {
    this.#pages = value;
  }

  get tokens(): number | null {
    return this.attachment?.estimated_tokens ?? this.estimate;
  }

  /** The server's analysis of a PDF's pages, once uploaded. */
  get pdfNotes(): PdfNotes | null {
    return this.attachment?.pdf_notes ?? null;
  }

  fail(err: unknown): void {
    const { message, retryable } = failure(err);
    this.status = 'error';
    this.error = message;
    this.retryable = retryable;
  }
}

/** What went wrong with an attachment, and whether trying again can help. */
function failure(err: unknown): { message: string; retryable: boolean } {
  if (err instanceof AttachmentError) return { message: err.message, retryable: false };
  if (err instanceof ApiError) {
    // The file's fault (too big, a type not accepted, not valid): trying again is useless.
    const refused = err.status === 413 || err.status === 415 || err.status === 422;
    const detail = typeof (err.body as { detail?: unknown } | null)?.detail === 'string';
    return {
      message: detail ? err.message : i18n.m.attachments.failed.upload(err.status),
      retryable: !refused,
    };
  }
  if (err instanceof RequestTimeoutError) return { message: err.message, retryable: true };
  return { message: connectionFailedMessage(), retryable: true };
}

export class ComposerAttachments {
  items: DraftAttachment[] = $state([]);

  /** Some attachment is still being prepared or uploaded. */
  get busy(): boolean {
    return this.items.some((item) => item.status === 'uploading');
  }

  /** Some attachment failed: it must be removed (or retried) before the question is sent. */
  get failed(): boolean {
    return this.items.some((item) => item.status === 'error');
  }

  /** The estimated tokens of the attachments that go with the question. */
  get tokens(): number {
    return this.items.reduce((sum, item) => sum + (item.status === 'error' ? 0 : (item.tokens ?? 0)), 0);
  }

  /** The uploaded attachments, in the order they were attached. */
  ready(): Attachment[] {
    return this.items.flatMap((item) => (item.status === 'ready' && item.attachment ? [item.attachment] : []));
  }

  /** Attaches `files` (at most MAX_ATTACHMENTS in all: the rest are left out, and it says so). */
  add(files: Iterable<File>): void {
    let refused = false;
    for (const file of files) {
      if (this.items.length >= MAX_ATTACHMENTS) {
        refused = true;
        continue;
      }
      const item = new DraftAttachment(file);
      this.items.push(item);
      item.task = this.#process(item);
    }
    if (refused) toasts.push(tooManyMessage(), 'error');
  }

  /** Uploads again an attachment whose upload failed for a reason a retry can fix. */
  retry(key: string): void {
    const item = this.items.find((i) => i.key === key);
    if (!item?.file || item.status !== 'error' || !item.retryable) return;
    item.status = 'uploading';
    item.error = null;
    item.retryable = false;
    item.task = this.#process(item);
  }

  /** Takes an attachment out: its upload stops, or it is deleted on the server. */
  remove(key: string): void {
    const item = this.items.find((i) => i.key === key);
    if (!item) return;
    this.#drop(item);
    this.items = this.items.filter((i) => i !== item);
    // Never sent, so the server can delete it (it would also do so by itself after a day).
    if (item.attachment) void api.deleteAttachment(item.attachment.id).catch(() => {});
  }

  /** Resolves once no attachment is being prepared or uploaded. */
  async settled(): Promise<void> {
    for (;;) {
      const tasks = this.items.filter((item) => item.status === 'uploading').map((item) => item.task);
      if (!tasks.length) return;
      await Promise.allSettled(tasks);
    }
  }

  /** The attachments of a question just sent: they leave the composer (not the server). */
  take(): Attachment[] {
    const sent = this.ready();
    this.clear();
    return sent;
  }

  /** Attachments of a question the server did not take, given back (into an empty composer). */
  restore(attachments: readonly Attachment[]): void {
    if (this.items.length || !attachments.length) return;
    // Plain copies: a live turn's are reactive state.
    this.items = attachments.map((a) => new DraftAttachment(null, $state.snapshot(a) as Attachment));
  }

  /**
   * The server does not have attachment `id` any more (an upload never sent is deleted
   * after a day): its card says so, and it must be removed before sending.
   */
  markGone(id: number): void {
    this.items.find((item) => item.attachment?.id === id)?.fail(new AttachmentError(404, goneMessage()));
  }

  /** Forgets every attachment (the session ended, or they were sent): nothing is deleted. */
  clear(): void {
    for (const item of this.items) this.#drop(item);
    this.items = [];
  }

  #drop(item: DraftAttachment): void {
    item.removed = true;
    item.controller?.abort();
  }

  /** Bytes of what goes with the question, `item` left out. */
  #bytes(item: DraftAttachment): number {
    return this.items.reduce((sum, other) => {
      if (other === item || other.status === 'error') return sum;
      return sum + (other.body?.size ?? other.attachment?.size ?? 0);
    }, 0);
  }

  async #process(item: DraftAttachment): Promise<void> {
    const controller = new AbortController();
    item.controller = controller;
    try {
      const { processDraft } = await loadWork();
      await processDraft(item, controller, () => this.#bytes(item));
    } catch (err) {
      if (item.removed) return;
      // A reason given when it was stopped (a PDF with too many pages) says it best.
      item.fail(controller.signal.reason instanceof AttachmentError ? controller.signal.reason : err);
    }
  }
}
