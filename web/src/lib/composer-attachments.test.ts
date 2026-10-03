// The attachments of the composer (docs/PROTOCOL.md "Attachments"): each file is checked as
// the server would check it, prepared (large images downscaled, a thumbnail made) and
// uploaded, with its state on its card: uploading, ready or an error (in Catalan, and at
// the end in Spanish and English). A file the server would refuse is never uploaded; one
// removed is stopped or deleted; the ready ones go with the next question, in order. The
// browser's image and PDF work is a double here (lib/media.test.ts covers it).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  heicMessage,
  pdfPagesMessage,
  svgMessage,
  tooLargeMessage,
  tooManyMessage,
  turnTooLargeMessage,
  unsupportedMessage,
  AttachmentError,
} from './attachments';
import { i18n } from './i18n/index.svelte';
import type { RuntimeSettings } from './protocol';
import { DEFAULT_SETTINGS } from './settings';
import { deferred, FakeApi } from './test-server';

vi.mock('./media', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./media')>()),
  prepareImage: vi.fn(),
  pdfPreview: vi.fn(),
}));

import { ComposerAttachments, type DraftAttachment } from './composer-attachments.svelte';
import { pdfPreview, prepareImage } from './media';
import { toasts } from './toasts.svelte';

const encode = (text: string) => new TextEncoder().encode(text);
const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52, 1, 2, 3]);
const png = (name = 'foto.png') => new File([PNG_BYTES], name, { type: 'image/png' });
const pdf = (name = 'informe.pdf') => new File([encode('%PDF-1.7\n1 0 obj\n')], name, { type: 'application/pdf' });
const text = (name: string, content: string) => new File([encode(content)], name, { type: 'text/plain' });
const thumbnail = () => new Blob([new Uint8Array([82, 73, 70, 70, 1, 2])], { type: 'image/webp' });

/** A file that says it is `size` bytes long (its content stays small). */
function sized<T extends File>(file: T, size: number): T {
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

let server: FakeApi;
let tray: ComposerAttachments;

/** What a card shows of an attachment, as a plain object (for readable failures). */
const plain = (item: DraftAttachment | undefined) =>
  item && {
    name: item.name,
    kind: item.kind,
    status: item.status,
    error: item.error,
    retryable: item.retryable,
    pages: item.pages,
    tokens: item.tokens,
    lines: item.lines,
    thumbnail: item.thumbnail,
  };

beforeEach(() => {
  server = new FakeApi(DEFAULT_SETTINGS as RuntimeSettings);
  vi.stubGlobal('fetch', server.fetch);
  tray = new ComposerAttachments();
  vi.mocked(prepareImage).mockImplementation(async (file) => ({ upload: file, width: 640, height: 480, thumbnail: thumbnail() }));
  vi.mocked(pdfPreview).mockResolvedValue({ thumbnail: thumbnail(), pages: 3 });
});

afterEach(() => {
  tray.clear();
  toasts.items.splice(0);
  vi.unstubAllGlobals();
  vi.mocked(prepareImage).mockReset();
  vi.mocked(pdfPreview).mockReset();
});

describe('a file attached', () => {
  it('an image: uploaded with its thumbnail, then ready', async () => {
    tray.add([png()]);
    const [item] = tray.items;
    expect(plain(item)).toMatchObject({ name: 'foto.png', status: 'uploading', error: null });
    expect(tray.busy).toBe(true);
    await tray.settled();
    expect(tray.busy).toBe(false);
    expect(item!.status).toBe('ready');
    expect(item!.kind).toBe('image');
    expect(server.uploads).toEqual([{ name: 'foto.png', body: PNG_BYTES }]);
    expect(server.thumbnailPuts.map((t) => t.id)).toEqual([item!.attachment!.id]);
    expect(item!.attachment!.has_thumbnail).toBe(true);
    expect(item!.thumbnail).toMatch(/^data:image\/webp;base64,/);
    expect(tray.ready()).toEqual([item!.attachment]);
  });

  it('a large image: its downscaled copy is what is uploaded', async () => {
    const downscaled = new Blob([new Uint8Array([82, 73, 70, 70, 9, 9, 9])], { type: 'image/webp' });
    vi.mocked(prepareImage).mockResolvedValue({ upload: downscaled, width: 2576, height: 1932, thumbnail: thumbnail() });
    tray.add([new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3])], 'foto.jpg', { type: 'image/jpeg' })]);
    await tray.settled();
    expect(vi.mocked(prepareImage).mock.calls[0]![1]).toBe('image/jpeg');
    expect(server.uploads[0]).toEqual({ name: 'foto.jpg', body: new Uint8Array([82, 73, 70, 70, 9, 9, 9]) });
    expect(tray.items[0]!.size).toBe(7);
    // Its card says what was uploaded, as the question's will.
    expect(tray.items[0]!.mime).toBe('image/webp');
  });

  it('a PDF: its first page as the thumbnail, and its pages', async () => {
    vi.mocked(pdfPreview).mockResolvedValue({ thumbnail: thumbnail(), pages: 12 });
    server.describeUpload = () => ({ pages: 12, estimated_tokens: 43_200 });
    tray.add([pdf()]);
    await tray.settled();
    const [item] = tray.items;
    expect(plain(item)).toMatchObject({ kind: 'pdf', status: 'ready', pages: 12, tokens: 43_200 });
    expect(server.thumbnailPuts).toHaveLength(1);
  });

  it('a text file: its first lines for the card, and no thumbnail', async () => {
    tray.add([text('notes.md', '# Notes\n\nPrimera idea\nSegona idea\n')]);
    const [item] = tray.items;
    await tray.settled();
    expect(plain(item)).toMatchObject({ kind: 'text', status: 'ready', lines: '# Notes\n\nPrimera idea\nSegona idea' });
    expect(server.thumbnailPuts).toEqual([]);
    expect(item!.thumbnail).toBeNull();
  });

  it('shows the estimate of the browser until the server gives its own', async () => {
    const upload = deferred<Response | null>();
    server.uploadAnswer = () => upload.promise;
    tray.add([png()]);
    const [item] = tray.items;
    await vi.waitFor(() => expect(item!.tokens).toBe(23 * 18)); // 640 x 480 in 28 px patches
    server.describeUpload = () => ({ estimated_tokens: 999 });
    upload.resolve(null);
    await tray.settled();
    expect(item!.tokens).toBe(999);
  });

  it('keeps an attachment whose thumbnail the server refused (it just shows no thumbnail there)', async () => {
    server.thumbnailAnswer = () => new Response(JSON.stringify({ detail: 'La miniatura ha de ser una imatge PNG o WebP.' }), { status: 415 });
    tray.add([png()]);
    await tray.settled();
    expect(plain(tray.items[0])).toMatchObject({ status: 'ready', error: null });
    expect(tray.items[0]!.attachment!.has_thumbnail).toBe(false);
  });
});

describe('what the server would refuse is refused here, without uploading it', () => {
  it.each([
    ['logo.svg', () => text('logo.svg', '<svg xmlns="http://www.w3.org/2000/svg"/>'), svgMessage()],
    ['foto.heic', () => new File([new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112, 104, 101, 105, 99, 0, 0, 0, 0])], 'foto.heic'), heicMessage()],
    ['arxiu.zip', () => new File([new Uint8Array([80, 75, 3, 4, 20, 0])], 'arxiu.zip'), unsupportedMessage()],
    ['dades.txt (not UTF-8)', () => new File([new Uint8Array([65, 0xff, 0xfe, 66])], 'dades.txt'), unsupportedMessage()],
    ['buit.txt', () => new File([], 'buit.txt'), 'El fitxer és buit.'],
    ['llarg.txt', () => sized(text('llarg.txt', 'hola'), 200_001), tooLargeMessage('text')],
    ['gran.pdf', () => sized(pdf('gran.pdf'), 20_000_001), tooLargeMessage('pdf')],
  ])('%s', async (_name, make, message) => {
    tray.add([make()]);
    await tray.settled();
    expect(plain(tray.items[0])).toMatchObject({ status: 'error', error: message, retryable: false });
    expect(tray.failed).toBe(true);
    expect(server.uploads).toEqual([]);
  });

  it('an image the browser cannot read or the server would refuse', async () => {
    vi.mocked(prepareImage).mockRejectedValue(new AttachmentError(422, "No s'ha pogut llegir la imatge: el fitxer no és vàlid."));
    tray.add([png()]);
    await tray.settled();
    expect(plain(tray.items[0])).toMatchObject({ status: 'error', error: "No s'ha pogut llegir la imatge: el fitxer no és vàlid." });
    expect(server.uploads).toEqual([]);
  });

  it('at most 5 attachments per message', async () => {
    tray.add(Array.from({ length: 7 }, (_, i) => png(`foto-${i + 1}.png`)));
    expect(tray.items.map((i) => i.name)).toEqual(['foto-1.png', 'foto-2.png', 'foto-3.png', 'foto-4.png', 'foto-5.png']);
    expect(toasts.items.map((t) => t.text)).toEqual([tooManyMessage()]);
    await tray.settled();
    tray.add([png('foto-6.png')]);
    expect(tray.items).toHaveLength(5);
    expect(server.uploads).toHaveLength(5);
  });

  it('at most 20 MB together', async () => {
    tray.add([sized(pdf('a.pdf'), 12_000_000)]);
    await tray.settled();
    tray.add([sized(pdf('b.pdf'), 8_000_001)]);
    await tray.settled();
    expect(plain(tray.items[1])).toMatchObject({ status: 'error', error: turnTooLargeMessage() });
    expect(server.uploads.map((u) => u.name)).toEqual(['a.pdf']);
    // Removing one makes room again.
    tray.remove(tray.items[0]!.key);
    tray.add([sized(pdf('c.pdf'), 8_000_001)]);
    await tray.settled();
    expect(tray.items.map((i) => i.status)).toEqual(['error', 'ready']);
  });

  it('a PDF with more pages than allowed, as soon as they are known', async () => {
    const upload = deferred<Response | null>();
    server.uploadAnswer = () => upload.promise;
    vi.mocked(pdfPreview).mockResolvedValue({ thumbnail: thumbnail(), pages: 150 });
    tray.add([pdf()]);
    await tray.settled();
    expect(plain(tray.items[0])).toMatchObject({ status: 'error', error: pdfPagesMessage(150), retryable: false });
    expect(server.aborted).toEqual(['PUT /api/attachments']);
  });
});

describe('when the upload fails', () => {
  it.each([
    [413, 'El fitxer és massa gran: un PDF pot tenir com a molt 20 MB.'],
    [415, unsupportedMessage()],
    [422, 'El PDF està xifrat o protegit amb contrasenya. Treu-ne la protecció i torna\'l a adjuntar.'],
  ])('the server refused it (%i): its reason, and no retry', async (status, detail) => {
    server.uploadAnswer = () => new Response(JSON.stringify({ detail }), { status });
    tray.add([pdf()]);
    await tray.settled();
    expect(plain(tray.items[0])).toMatchObject({ status: 'error', error: detail, retryable: false });
  });

  it('the connection failed: it can be retried', async () => {
    let fail = true;
    server.uploadAnswer = () => {
      if (fail) throw new TypeError('Failed to fetch');
      return null;
    };
    tray.add([png()]);
    await tray.settled();
    const [item] = tray.items;
    expect(plain(item)).toMatchObject({ status: 'error', error: "No s'ha pogut pujar el fitxer: la connexió ha fallat.", retryable: true });
    fail = false;
    tray.retry(item!.key);
    expect(item!.status).toBe('uploading');
    await tray.settled();
    expect(plain(item)).toMatchObject({ status: 'ready', error: null });
    expect(tray.failed).toBe(false);
  });

  it('the server could not store it (507): its reason, and a retry', async () => {
    server.uploadAnswer = () =>
      new Response(JSON.stringify({ detail: 'El servidor no té prou espai al disc per desar el fitxer.' }), { status: 507 });
    tray.add([png()]);
    await tray.settled();
    expect(plain(tray.items[0])).toMatchObject({
      status: 'error',
      error: 'El servidor no té prou espai al disc per desar el fitxer.',
      retryable: true,
    });
  });
});

describe('removing an attachment', () => {
  it('uploaded: deleted on the server', async () => {
    tray.add([png()]);
    await tray.settled();
    const { key, attachment } = tray.items[0]!;
    tray.remove(key);
    expect(tray.items).toEqual([]);
    await vi.waitFor(() => expect(server.calls).toContain(`DELETE /api/attachments/${attachment!.id}`));
  });

  it('still uploading: its upload stops, and nothing is left behind', async () => {
    const upload = deferred<Response | null>();
    server.uploadAnswer = () => upload.promise;
    tray.add([png()]);
    await vi.waitFor(() => expect(server.uploads).toHaveLength(1));
    tray.remove(tray.items[0]!.key);
    expect(tray.items).toEqual([]);
    expect(tray.busy).toBe(false);
    await tray.settled();
    expect(server.aborted).toEqual(['PUT /api/attachments']);
    expect(toasts.items).toEqual([]);
  });
});

describe('the attachments of a question', () => {
  it('go in the order they were attached, and leave the composer once sent (they are not deleted)', async () => {
    tray.add([png('a.png'), text('b.txt', 'hola')]);
    await tray.settled();
    const ids = tray.ready().map((a) => a.id);
    expect(tray.ready().map((a) => a.name)).toEqual(['a.png', 'b.txt']);
    expect(tray.take().map((a) => a.id)).toEqual(ids);
    expect(tray.items).toEqual([]);
    expect(server.calls.filter((c) => c.startsWith('DELETE'))).toEqual([]);
  });

  it('come back if the server did not take the question, with their thumbnails', async () => {
    tray.add([png('a.png'), text('b.txt', 'primera línia\nsegona')]);
    await tray.settled();
    const sent = tray.take();
    tray.restore(sent);
    expect(tray.items.map((i) => [i.name, i.status])).toEqual([
      ['a.png', 'ready'],
      ['b.txt', 'ready'],
    ]);
    expect(tray.items[0]!.thumbnail).toBe(`/api/attachments/${sent[0]!.id}/thumbnail`);
    expect(tray.items[1]!.lines).toBe('primera línia\nsegona');
    expect(tray.ready()).toEqual(sent);
  });

  it('one the server no longer has is marked, to be removed before sending again', async () => {
    tray.add([png('a.png'), png('b.png')]);
    await tray.settled();
    const sent = tray.take();
    tray.restore(sent);
    tray.markGone(sent[1]!.id);
    expect(plain(tray.items[1])).toMatchObject({
      status: 'error',
      error: "Aquest adjunt ja no és al servidor: treu-lo i torna'l a adjuntar.",
      retryable: false,
    });
    expect(tray.failed).toBe(true);
    expect(tray.ready().map((a) => a.id)).toEqual([sent[0]!.id]);
    tray.markGone(999); // not one of them: nothing changes
    expect(plain(tray.items[0])).toMatchObject({ status: 'ready' });
  });

  it('are all forgotten when the session ends (nothing is deleted)', async () => {
    const upload = deferred<Response | null>();
    server.uploadAnswer = () => upload.promise;
    tray.add([png()]);
    tray.clear();
    expect(tray.items).toEqual([]);
    await tray.settled();
    expect(server.calls.filter((c) => c.startsWith('DELETE'))).toEqual([]);
  });

  it('add up their estimated tokens', async () => {
    server.describeUpload = (name) => ({ estimated_tokens: name === 'a.png' ? 1000 : 250 });
    tray.add([png('a.png'), text('b.txt', 'x'.repeat(1000))]);
    await tray.settled();
    expect(tray.tokens).toBe(1250);
  });
});

describe('in Spanish and English', () => {
  afterEach(() => i18n.set('ca'));

  it('in Spanish: a file too large, the connection that failed and too many files', async () => {
    i18n.set('es');
    server.uploadAnswer = () => {
      throw new TypeError('Failed to fetch');
    };
    tray.add([sized(pdf('gran.pdf'), 20_000_001), png(), new File([], '')]);
    await tray.settled();
    expect(tray.items.map(plain)).toMatchObject([
      { name: 'gran.pdf', error: 'El archivo es demasiado grande: un PDF puede ocupar como máximo 20 MB.', retryable: false },
      { name: 'foto.png', error: 'No se ha podido subir el archivo: la conexión ha fallado.', retryable: true },
      { name: 'archivo', error: 'El archivo está vacío.', retryable: false },
    ]);
    tray.add([png('a.png'), png('b.png'), png('c.png')]);
    expect(toasts.items.map((t) => t.text)).toEqual(['Un mensaje puede llevar como máximo 5 adjuntos.']);
  });

  it('in English: an upload the server refused without a reason, and an attachment it no longer has', async () => {
    i18n.set('en');
    server.uploadAnswer = () => new Response('', { status: 500 });
    tray.add([png()]);
    await tray.settled();
    expect(plain(tray.items[0])).toMatchObject({ error: 'The file could not be uploaded (error 500).', retryable: true });

    server.uploadAnswer = () => null;
    tray.clear();
    tray.add([png()]);
    await tray.settled();
    tray.markGone(tray.items[0]!.attachment!.id);
    expect(plain(tray.items[0])).toMatchObject({
      status: 'error',
      error: 'This attachment is no longer on the server: remove it and attach it again.',
    });
  });
});
