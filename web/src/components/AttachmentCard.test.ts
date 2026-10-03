// An attachment's card, in the composer and in a question (like claude.ai): its thumbnail,
// the first lines of a text file or an icon, its name, type, size, pages and estimated
// tokens, and its state (uploading, ready or an error). A ready one opens its preview; in
// the composer it can be removed, and retried after a failed connection. A PDF also shows
// the warnings of the server's analysis of its pages (P7b). In Catalan, and at the end in
// Spanish and English.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { AttachmentView } from '../lib/attachments';
import { i18n } from '../lib/i18n/index.svelte';
import type { RuntimeSettings } from '../lib/protocol';
import { DEFAULT_SETTINGS } from '../lib/settings';
import { cleanup, render, textOf } from '../lib/test-render';
import { FakeApi } from '../lib/test-server';
import AttachmentCard from './AttachmentCard.svelte';

const view = (partial: Partial<AttachmentView>): AttachmentView => ({
  key: 'a1',
  id: 3,
  name: 'foto.png',
  kind: 'image',
  mime: 'image/png',
  size: 120_000,
  pages: null,
  tokens: 414,
  thumbnail: null,
  lines: null,
  status: 'ready',
  error: null,
  retryable: false,
  pdfNotes: null,
  ...partial,
});

let server: FakeApi;

beforeEach(() => {
  server = new FakeApi(DEFAULT_SETTINGS as RuntimeSettings);
  vi.stubGlobal('fetch', server.fetch);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('AttachmentCard', () => {
  it('an image with its thumbnail: name, type, size and tokens', () => {
    const root = render(AttachmentCard, { view: view({ thumbnail: '/api/attachments/3/thumbnail' }) });
    const img = root.querySelector('img');
    expect(img?.getAttribute('src')).toBe('/api/attachments/3/thumbnail');
    expect(img?.getAttribute('alt')).toBe('');
    const text = textOf(root);
    for (const part of ['foto.png', 'PNG', '120 kB', '≈ 414 tokens']) expect(text).toContain(part);
  });

  it('a PDF without a thumbnail: an icon, its pages and its tokens', () => {
    const root = render(AttachmentCard, {
      view: view({ name: 'informe.pdf', kind: 'pdf', mime: 'application/pdf', size: 1_234_567, pages: 12, tokens: 43_200 }),
    });
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('.thumb svg.icon')).not.toBeNull();
    const text = textOf(root);
    for (const part of ['informe.pdf', 'PDF', '12 pàgines', '1,2 MB', '≈ 43,2k tokens']) expect(text).toContain(part);
  });

  it('falls back to the icon when its thumbnail does not load', () => {
    const root = render(AttachmentCard, { view: view({ thumbnail: '/api/attachments/3/thumbnail' }) });
    root.querySelector('img')!.dispatchEvent(new Event('error'));
    flushSync();
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('.thumb svg.icon')).not.toBeNull();
  });

  it('a text file shows its first lines as plain text, with hidden characters revealed', () => {
    const root = render(AttachmentCard, {
      view: view({ name: 'notes.md', kind: 'text', mime: 'text/plain', lines: '<b>Títol</b>\nlínia ‮amagada' }),
    });
    const lines = root.querySelector('.lines');
    expect(lines?.querySelector('b')).toBeNull();
    expect(textOf(lines)).toBe('<b>Títol</b> línia ⟨U+202E⟩amagada');
    expect(textOf(root)).toContain('MD');
  });

  it('a text file of a stored question gets its first lines from the server (only the start)', async () => {
    server.addAttachment({ id: 9, name: 'main.py', kind: 'text', mime: 'text/plain' }, new TextEncoder().encode('import os\nprint(os.getcwd())\n'));
    const root = render(AttachmentCard, { view: view({ id: 9, name: 'main.py', kind: 'text', mime: 'text/plain' }) });
    await vi.waitFor(() => expect(textOf(root.querySelector('.lines'))).toBe('import os print(os.getcwd())'));
    expect(server.ranges).toEqual(['bytes=0-4095']);
  });

  it('uploading: says so, cannot be opened yet, can be removed', () => {
    const onopen = vi.fn();
    const onremove = vi.fn();
    const root = render(AttachmentCard, { view: view({ id: null, status: 'uploading', tokens: null }), onopen, onremove });
    expect(textOf(root)).toContain('Pujant…');
    expect(root.querySelector('button.body')).toBeNull();
    const remove = root.querySelector<HTMLButtonElement>('button.remove')!;
    expect(remove.getAttribute('aria-label')).toBe('Treu foto.png');
    remove.click();
    expect(onremove).toHaveBeenCalledOnce();
    expect(onopen).not.toHaveBeenCalled();
  });

  it('an error: its reason, and a retry only when it can help', () => {
    const onretry = vi.fn();
    const failed = view({ id: null, status: 'error', error: "No s'ha pogut pujar el fitxer: la connexió ha fallat.", retryable: true, tokens: null });
    const root = render(AttachmentCard, { view: failed, onretry, onremove: vi.fn() });
    expect(textOf(root.querySelector('[role=alert]'))).toBe("No s'ha pogut pujar el fitxer: la connexió ha fallat.");
    const retry = [...root.querySelectorAll('button')].find((b) => textOf(b) === 'Torna-ho a provar')!;
    retry.click();
    expect(onretry).toHaveBeenCalledOnce();

    const refused = render(AttachmentCard, {
      view: view({ status: 'error', error: 'El fitxer és massa gran: un PDF pot tenir com a molt 20 MB.', retryable: false }),
      onretry,
    });
    expect([...refused.querySelectorAll('button')].map((b) => textOf(b))).not.toContain('Torna-ho a provar');
  });

  it('a ready one opens its preview', () => {
    const onopen = vi.fn();
    const root = render(AttachmentCard, { view: view({}), onopen });
    const open = root.querySelector<HTMLButtonElement>('button.body')!;
    expect(open.title).toBe('Obre la vista prèvia');
    open.click();
    expect(onopen).toHaveBeenCalledOnce();
  });
});

describe("AttachmentCard: the warnings of a PDF's pages (the server's analysis)", () => {
  const pdf = (partial: Partial<AttachmentView>) =>
    view({ name: 'informe.pdf', kind: 'pdf', mime: 'application/pdf', size: 1_234_567, pages: 12, tokens: 43_200, ...partial });
  const notes = (root: HTMLElement) => [...root.querySelectorAll('.pdf-note')];

  it('a line per kind under its meta, each with what it means for its title and for screen readers', () => {
    const root = render(AttachmentCard, { view: pdf({ pdfNotes: { no_text: [2, 5], garbled: [3], hidden: [7] } }) });
    const lines = notes(root);
    expect(lines.map((n) => textOf(n.querySelector('.note-text')))).toEqual([
      'Sense text: pàg. 2, 5',
      'Text il·legible: pàg. 3',
      'Possible text ocult: pàg. 7',
    ]);
    const scan =
      'Pàgines sense text extraïble: escanejades, o amb el text dibuixat com a imatge. Els models que obren el PDF les llegeixen com a imatge.';
    expect(lines[0]!.getAttribute('title')).toBe(scan);
    expect(textOf(lines[0]!.querySelector('.sr-only'))).toBe(`(${scan})`);
    expect(lines.every((n) => n.getAttribute('title') && textOf(n.querySelector('.sr-only')))).toBe(true);
    // Only the text that may be hidden is a warning (its colour); the icons are decoration.
    expect(lines.map((n) => n.classList.contains('warning'))).toEqual([false, false, true]);
    expect(lines.every((n) => n.querySelector('svg')?.getAttribute('aria-hidden') === 'true')).toBe(true);
    // Under the meta: after the pages, the size and the tokens.
    const info = root.querySelector('.info')!;
    expect(textOf(info)).toMatch(/12 pàgines · 1,2 MB ≈ 43,2k tokens Sense text: pàg\. 2, 5/);
  });

  it('page lists are compact', () => {
    const root = render(AttachmentCard, { view: pdf({ pdfNotes: { no_text: [2, 3, 4, 9], garbled: [], hidden: [] } }) });
    expect(notes(root).map((n) => textOf(n.querySelector('.note-text')))).toEqual(['Sense text: pàg. 2–4, 9']);
  });

  it('none for a PDF without warnings or not analysed, nor for other files', () => {
    for (const partial of [
      { pdfNotes: { no_text: [], garbled: [], hidden: [] } },
      { pdfNotes: null },
    ] as Partial<AttachmentView>[]) {
      expect(notes(render(AttachmentCard, { view: pdf(partial) }))).toEqual([]);
    }
    expect(notes(render(AttachmentCard, { view: view({}) }))).toEqual([]);
  });

  it("in a question's card they are part of the button that opens its preview", () => {
    const root = render(AttachmentCard, { view: pdf({ pdfNotes: { no_text: [], garbled: [], hidden: [7] } }), onopen: vi.fn() });
    expect(textOf(root.querySelector('button.body .pdf-note .note-text'))).toBe('Possible text ocult: pàg. 7');
  });
});

describe('AttachmentCard in Spanish and English', () => {
  const pdf = (partial: Partial<AttachmentView>) =>
    view({ name: 'informe.pdf', kind: 'pdf', mime: 'application/pdf', size: 1_234_567, pages: 12, tokens: 43_200, ...partial });
  const noteTexts = (root: HTMLElement) => [...root.querySelectorAll('.pdf-note .note-text')].map((n) => textOf(n));

  afterEach(() => i18n.set('ca'));

  it('in Spanish: its pages, size and tokens, its states and its buttons', () => {
    i18n.set('es');
    const ready = render(AttachmentCard, { view: pdf({ pdfNotes: { no_text: [2, 5], garbled: [3], hidden: [] } }), onopen: vi.fn() });
    for (const part of ['informe.pdf', 'PDF', '12 páginas', '1,2 MB', '≈ 43,2k tokens']) expect(textOf(ready)).toContain(part);
    expect(ready.querySelector<HTMLButtonElement>('button.body')!.title).toBe('Abrir la vista previa');
    expect(noteTexts(ready).map((text) => text.slice(text.indexOf(':')))).toEqual([': págs. 2, 5', ': pág. 3']);

    const uploading = render(AttachmentCard, { view: view({ id: null, status: 'uploading', tokens: null }), onremove: vi.fn() });
    expect(textOf(uploading)).toContain('Subiendo…');
    const remove = uploading.querySelector<HTMLButtonElement>('button.remove')!;
    expect([remove.getAttribute('aria-label'), remove.title]).toEqual(['Quitar foto.png', 'Quitar el adjunto']);

    const failed = view({ id: null, status: 'error', error: 'No se ha podido subir el archivo: la conexión ha fallado.', retryable: true });
    const retry = render(AttachmentCard, { view: failed, onretry: vi.fn() }).querySelector('button.retry');
    expect(textOf(retry)).toBe('Reintentar');
  });

  it('in English, and it follows a change of language', () => {
    i18n.set('en');
    const root = render(AttachmentCard, { view: pdf({ pdfNotes: { no_text: [2, 3, 4], garbled: [8], hidden: [] } }) });
    for (const part of ['12 pages', '1.2 MB', '≈ 43.2k tokens']) expect(textOf(root)).toContain(part);
    expect(noteTexts(root).map((text) => text.slice(text.indexOf(':')))).toEqual([': pp. 2–4', ': p. 8']);
    i18n.set('es');
    flushSync();
    for (const part of ['12 páginas', '1,2 MB', '≈ 43,2k tokens']) expect(textOf(root)).toContain(part);
  });
});
