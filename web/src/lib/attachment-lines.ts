// The first lines of attached text files, for their cards: known when the file was
// attached in this tab, otherwise asked of the server (only the start of the file).

import { api } from './api';
import { firstLines } from './attachments';

/** Bytes asked of the server: more than the card shows. */
export const LINES_BYTES = 4096;
/** First lines kept (a few hundred characters each). */
const KEPT = 200;

const known = new Map<number, string>();
const pending = new Map<number, Promise<string | null>>();

export function rememberLines(id: number, lines: string): void {
  known.delete(id);
  known.set(id, lines);
  if (known.size > KEPT) known.delete(known.keys().next().value!);
}

export const knownLines = (id: number): string | null => known.get(id) ?? null;

/** The first lines of text attachment `id`; null when the server cannot give them. */
export function loadLines(id: number): Promise<string | null> {
  const have = known.get(id);
  if (have !== undefined) return Promise.resolve(have);
  let request = pending.get(id);
  if (!request) {
    request = api
      .attachmentText(id, { maxBytes: LINES_BYTES })
      .then(
        (text) => {
          const lines = firstLines(text);
          rememberLines(id, lines);
          return lines;
        },
        () => null,
      )
      .finally(() => pending.delete(id));
    pending.set(id, request);
  }
  return request;
}

/** The session ended: nothing of it stays in memory. */
export function forgetLines(): void {
  known.clear();
  pending.clear();
}
