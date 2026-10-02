// Attaching files in the composer (docs/PROTOCOL.md «Adjunts»): the attach button and its
// file picker, dropping files on the composer and pasting images; the cards and their
// states; a send that waits for the uploads and then sends their ids with the question
// (turn.start `attachments`); and a question the server did not take coming back with its
// attachments. The browser's image and PDF work is a double (lib/media.test.ts covers it).
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACCEPT } from '../lib/attachments';
import type { RuntimeSettings } from '../lib/protocol';
import { deferred, FakeApi, FakeSocket } from '../lib/test-server';

vi.mock('../lib/media', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/media')>()),
  prepareImage: vi.fn(async (file: Blob) => ({
    upload: file,
    width: 640,
    height: 480,
    thumbnail: new Blob([new Uint8Array([82, 73, 70, 70])], { type: 'image/webp' }),
  })),
  pdfPreview: vi.fn(async () => ({ thumbnail: null, pages: 2 })),
}));

const SAVED: RuntimeSettings = {
  revision: 3,
  default_mode: 'solo',
  default_target: 'claude',
  debate: { rounds: 1, consensus_threshold: 70, synthesizer: 'claude' },
  refine: { max_rounds: 12, budget_eur: 3, max_words: null, stop_on_convergence: true, convergence_threshold: 90, editor: 'claude' },
  use_cache: false,
  compaction_threshold_tokens: 6000,
  models: { claude: null, chatgpt: null },
  fast_models: { claude: null, chatgpt: null },
  prices: {},
  fx: { mode: 'auto', eur_per_usd: 0.86 },
  budgets_eur: { claude: null, chatgpt: null },
  plans_eur: { claude: null, chatgpt: null },
  pdf_in_revisions: 'text',
};

const NO_USAGE = { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, reasoning_tokens: 0, cost_usd: null };
const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
const png = (name = 'foto.png') => new File([PNG_BYTES], name, { type: 'image/png' });
const QUESTION = 'Què hi ha en aquesta imatge?';

/** A new app (as after a page load) and the composer, from the same module graph. */
async function load() {
  vi.resetModules();
  const { app } = await import('../lib/app.svelte');
  const { toasts } = await import('../lib/toasts.svelte');
  const { render, cleanup, textOf } = await import('../lib/test-render');
  const { flushSync } = await import('svelte');
  const { default: Composer } = await import('./Composer.svelte');
  return { app, toasts, render, cleanup, textOf, flushSync, Composer };
}

let server: FakeApi;
let env: Awaited<ReturnType<typeof load>> | null = null;

/** Logged in, settings loaded, socket open, the composer mounted. */
async function ready() {
  env = await load();
  const e = env;
  await e.app.init();
  const socket = FakeSocket.last();
  socket.open();
  const root = e.render(e.Composer, {});
  e.flushSync();
  const textarea = root.querySelector('textarea')!;
  return { e, socket, root, textarea };
}

function type(textarea: HTMLTextAreaElement, value: string): void {
  textarea.value = value;
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  env!.flushSync();
}

function pressEnter(textarea: HTMLTextAreaElement): void {
  textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  env!.flushSync();
}

/** The owner picks `files` in the file picker. */
function choose(root: HTMLElement, files: File[]): void {
  const input = root.querySelector<HTMLInputElement>('input[type=file]')!;
  Object.defineProperty(input, 'files', { value: files, configurable: true });
  input.dispatchEvent(new Event('change', { bubbles: true }));
  env!.flushSync();
}

/** A drag event carrying `files` (jsdom has no DataTransfer). */
function drag(type: string, files: File[], types = ['Files']): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: { types, files, dropEffect: 'none' } });
  return event;
}

function paste(textarea: HTMLTextAreaElement, files: File[], text = ''): Event {
  const event = new Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'clipboardData', {
    value: { files, types: text ? ['text/plain', 'Files'] : ['Files'], getData: (t: string) => (t === 'text/plain' ? text : '') },
  });
  textarea.dispatchEvent(event);
  env!.flushSync();
  return event;
}

const cards = (root: HTMLElement) => [...root.querySelectorAll('.attachments .att')];
const sendButton = (root: HTMLElement) => root.querySelector<HTMLButtonElement>('button.send')!;
const turnStarts = (socket: FakeSocket) => socket.sent.filter((m) => m.type === 'turn.start');

async function settle(): Promise<void> {
  await env!.app.composer.attachments.settled();
  env!.flushSync();
}

beforeAll(async () => {
  await load(); // compiles the components once, outside the tests' time limit
});

beforeEach(() => {
  server = new FakeApi(SAVED);
  FakeSocket.all = [];
  vi.stubGlobal('fetch', server.fetch);
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  env?.cleanup();
  env?.app.toLogin(); // stops this app's timers and socket
  env?.app.composer.attachments.clear();
  env = null;
  vi.unstubAllGlobals();
});

describe('Composer: attaching files', () => {
  it('the attach button opens a file picker for several files of the accepted types', async () => {
    const { root } = await ready();
    const input = root.querySelector<HTMLInputElement>('input[type=file]')!;
    expect(input.multiple).toBe(true);
    expect(input.accept).toBe(ACCEPT);
    const click = vi.spyOn(input, 'click').mockImplementation(() => {});
    const attach = root.querySelector<HTMLButtonElement>('button[aria-label="Adjunta fitxers"]')!;
    attach.click();
    expect(click).toHaveBeenCalledOnce();
  });

  it('the chosen files show their cards: uploading, then ready', async () => {
    const { e, root } = await ready();
    choose(root, [png('a.png'), png('b.png')]);
    expect(cards(root).map((c) => e.textOf(c.querySelector('.name')))).toEqual(['a.png', 'b.png']);
    expect(e.textOf(cards(root)[0])).toContain('Pujant…');
    await settle();
    expect(e.textOf(cards(root)[0])).not.toContain('Pujant…');
    expect(server.uploads.map((u) => u.name)).toEqual(['a.png', 'b.png']);
  });

  it('files dropped on the composer are attached; while dragging it says where to drop them', async () => {
    const { e, root } = await ready();
    const form = root.querySelector('form')!;
    const enter = drag('dragenter', [png()]);
    form.dispatchEvent(enter);
    const over = drag('dragover', [png()]);
    form.dispatchEvent(over);
    e.flushSync();
    expect(over.defaultPrevented).toBe(true); // the browser allows the drop
    expect(e.textOf(root.querySelector('.drop-overlay'))).toBe('Deixa anar els fitxers per adjuntar-los');
    const drop = drag('drop', [png('deixat.png')]);
    form.dispatchEvent(drop);
    e.flushSync();
    expect(drop.defaultPrevented).toBe(true); // the browser does not open the file
    expect(root.querySelector('.drop-overlay')).toBeNull();
    expect(cards(root).map((c) => e.textOf(c.querySelector('.name')))).toEqual(['deixat.png']);

    // Dragging text is not about attachments.
    const text = drag('dragover', [], ['text/plain']);
    form.dispatchEvent(text);
    e.flushSync();
    expect(text.defaultPrevented).toBe(false);
    expect(root.querySelector('.drop-overlay')).toBeNull();
  });

  it('a pasted image is attached; pasted text stays text', async () => {
    const { e, root, textarea } = await ready();
    expect(paste(textarea, [png('image.png')]).defaultPrevented).toBe(true);
    expect(cards(root)).toHaveLength(1);
    // Cells copied from a spreadsheet bring an image of them too: the text wins.
    expect(paste(textarea, [png('cells.png')], 'A1\tB1').defaultPrevented).toBe(false);
    expect(cards(root)).toHaveLength(1);
    // Only images come from a paste.
    expect(paste(textarea, [new File(['%PDF-1.7'], 'a.pdf', { type: 'application/pdf' })]).defaultPrevented).toBe(false);
    expect(cards(root)).toHaveLength(1);
    await settle();
    expect(e.app.composer.attachments.items[0]!.status).toBe('ready');
  });

  it("an uploaded PDF shows the warnings of the server's analysis of its pages", async () => {
    const { e, root } = await ready();
    server.describeUpload = (name) =>
      name === 'escanejat.pdf' ? { pages: 9, pdf_notes: { no_text: [1, 2, 3, 9], garbled: [], hidden: [4] } } : {};
    choose(root, [new File(['%PDF-1.7\n%âãÏÓ\n'], 'escanejat.pdf', { type: 'application/pdf' }), png()]);
    // Nothing is known of its pages while it uploads.
    expect(root.querySelector('.pdf-note')).toBeNull();
    await settle();
    const [pdf, image] = cards(root);
    expect([...pdf!.querySelectorAll('.pdf-note .note-text')].map((n) => e.textOf(n))).toEqual([
      'Sense text: pàg. 1–3, 9',
      'Possible text ocult: pàg. 4',
    ]);
    expect(image!.querySelector('.pdf-note')).toBeNull();
  });

  it('the remove button takes the card away and deletes the upload', async () => {
    const { e, root } = await ready();
    choose(root, [png()]);
    await settle();
    const id = e.app.composer.attachments.items[0]!.attachment!.id;
    root.querySelector<HTMLButtonElement>('button.remove')!.click();
    e.flushSync();
    expect(cards(root)).toEqual([]);
    await vi.waitFor(() => expect(server.calls).toContain(`DELETE /api/attachments/${id}`));
  });
});

describe('Composer: sending a question with attachments', () => {
  it('waits for the uploads, then sends their ids with the question', async () => {
    const { e, socket, root, textarea } = await ready();
    const upload = deferred<Response | null>();
    server.uploadAnswer = () => upload.promise;
    choose(root, [png()]);
    type(textarea, QUESTION);
    pressEnter(textarea);
    expect(turnStarts(socket)).toEqual([]);
    // On a line of its own, which narrow composers show too.
    const note = root.querySelector('.upload-note');
    expect(note?.getAttribute('role')).toBe('status');
    expect(e.textOf(note)).toBe("S'enviarà quan s'acabin de pujar els adjunts…");
    expect(sendButton(root).getAttribute('aria-label')).toBe("Deixa d'esperar els adjunts");

    upload.resolve(null);
    await vi.waitFor(() => expect(turnStarts(socket)).toHaveLength(1));
    e.flushSync();
    expect(e.textOf(root.querySelector('.upload-note'))).toBe('');
    const [attachment] = [...server.attachments.values()].map((s) => s.attachment);
    expect(turnStarts(socket)[0]).toMatchObject({ text: QUESTION, attachments: [attachment!.id] });
    expect(e.app.composer.draft).toBe('');
    expect(cards(root)).toEqual([]);
    const live = e.app.turns.get(String(turnStarts(socket)[0]!.request_id))!;
    expect(live.attachments.map((a) => a.id)).toEqual([attachment!.id]);
  });

  it('a second press stops waiting', async () => {
    const { e, socket, root, textarea } = await ready();
    const upload = deferred<Response | null>();
    server.uploadAnswer = () => upload.promise;
    choose(root, [png()]);
    type(textarea, QUESTION);
    sendButton(root).click();
    e.flushSync();
    sendButton(root).click();
    e.flushSync();
    expect(sendButton(root).getAttribute('aria-label')).toBe('Envia');
    upload.resolve(null);
    await settle();
    expect(turnStarts(socket)).toEqual([]);
    expect(e.app.composer.draft).toBe(QUESTION);
  });

  it('a question without attachments sends none', async () => {
    const { socket, textarea } = await ready();
    type(textarea, QUESTION);
    pressEnter(textarea);
    expect(turnStarts(socket)).toHaveLength(1);
    expect(turnStarts(socket)[0]).not.toHaveProperty('attachments');
  });

  it('an attachment with an error must be removed (or retried) before sending', async () => {
    const { e, socket, root, textarea } = await ready();
    choose(root, [new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' })]);
    await settle();
    type(textarea, QUESTION);
    expect(sendButton(root).disabled).toBe(true);
    expect(sendButton(root).title).toBe("Treu els adjunts que han fallat per enviar la pregunta");
    pressEnter(textarea);
    expect(turnStarts(socket)).toEqual([]);
    root.querySelector<HTMLButtonElement>('button.remove')!.click();
    e.flushSync();
    expect(sendButton(root).disabled).toBe(false);
  });

  it('a question the server did not take comes back with its attachments', async () => {
    const { e, socket, root, textarea } = await ready();
    choose(root, [png('a.png'), png('b.png')]);
    await settle();
    type(textarea, QUESTION);
    pressEnter(textarea);
    const [start] = turnStarts(socket);
    expect(start!.attachments).toHaveLength(2);
    expect(cards(root)).toEqual([]);
    socket.receive({
      type: 'turn.failed',
      request_id: String(start!.request_id),
      seq: 1,
      error: { kind: 'not_found', message: 'La conversa no existeix.' },
      usage: NO_USAGE,
    });
    e.flushSync();
    expect(e.app.composer.draft).toBe(QUESTION);
    expect(e.app.composer.attachments.ready().map((a) => a.id)).toEqual(start!.attachments);
    expect(cards(root).map((c) => e.textOf(c.querySelector('.name')))).toEqual(['a.png', 'b.png']);
  });

  it('an attachment the server no longer has (unsent for a day) comes back marked', async () => {
    const { e, socket, root, textarea } = await ready();
    choose(root, [png('a.png'), png('b.png')]);
    await settle();
    type(textarea, QUESTION);
    pressEnter(textarea);
    const [start] = turnStarts(socket);
    const gone = (start!.attachments as number[])[1]!;
    socket.receive({
      type: 'turn.failed',
      request_id: String(start!.request_id),
      seq: 1,
      error: { kind: 'invalid', message: `L'adjunt ${gone} no existeix.` },
      usage: NO_USAGE,
    });
    e.flushSync();
    expect(e.app.composer.draft).toBe(QUESTION);
    expect(e.textOf(cards(root)[1]!.querySelector('[role=alert]'))).toBe(
      "Aquest adjunt ja no és al servidor: treu-lo i torna'l a adjuntar.",
    );
    expect(sendButton(root).disabled).toBe(true);
  });

  it('the token hint adds the estimate of the attachments', async () => {
    const { e, root, textarea } = await ready();
    server.describeUpload = () => ({ estimated_tokens: 1000 });
    choose(root, [png()]);
    await settle();
    type(textarea, 'x'.repeat(400)); // 100 tokens
    expect(e.textOf(root.querySelector('.kbd-hint'))).toBe('≈ 1,1k tokens');
  });

  it('logging out forgets the attachments', async () => {
    const { e, root } = await ready();
    choose(root, [png()]);
    await settle();
    await e.app.logout();
    expect(e.app.composer.attachments.items).toEqual([]);
  });
});
