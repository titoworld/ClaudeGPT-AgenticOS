// The preview of an attachment, in the app (docs/adr/0009-attachments.md): an image in a
// lightbox, a PDF page by page with PDF.js, a text file as plain text; each with a
// download button. PDF.js is a double here (lib/pdf.test.ts covers it).
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { Attachment, RuntimeSettings } from '../lib/protocol';
import { DEFAULT_SETTINGS } from '../lib/settings';
import { cleanup, render, textOf } from '../lib/test-render';
import { deferred, FakeApi, polyfillDialog } from '../lib/test-server';

vi.mock('../lib/pdf', () => ({ openPdf: vi.fn() }));

import { openPdf, type PdfDocument } from '../lib/pdf';
import { viewer } from '../lib/viewer.svelte';
import AttachmentViewer from './AttachmentViewer.svelte';

const attachment = (partial: Partial<Attachment>): Attachment => ({
  id: 3,
  name: 'foto.png',
  kind: 'image',
  mime: 'image/png',
  size: 120_000,
  pages: null,
  width: 640,
  height: 480,
  sha256: 'a'.repeat(64),
  created_at: '2026-09-28T10:00:00Z',
  has_thumbnail: true,
  text_available: false,
  estimated_tokens: 414,
  pdf_notes: null,
  ...partial,
});

const PDF = attachment({ id: 5, name: 'informe.pdf', kind: 'pdf', mime: 'application/pdf', pages: 3, width: null, height: null });
const TEXT = attachment({ id: 9, name: 'notes.md', kind: 'text', mime: 'text/plain', width: null, height: null });

let server: FakeApi;

/** A PDF document double: records the pages it draws. */
function fakeDocument(pages = 3) {
  const doc = {
    pages,
    render: vi.fn(async (_page: number, _canvas: HTMLCanvasElement, _width: number) => true),
    destroy: vi.fn(async () => {}),
  };
  return doc as typeof doc & PdfDocument;
}

function open(root: HTMLElement, a: Attachment): HTMLDialogElement {
  viewer.open(a);
  flushSync();
  const dialog = root.querySelector('dialog')!;
  expect(dialog.open).toBe(true);
  return dialog;
}

const button = (root: HTMLElement, label: string) =>
  root.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;

beforeAll(polyfillDialog);

beforeEach(() => {
  server = new FakeApi(DEFAULT_SETTINGS as RuntimeSettings);
  vi.stubGlobal('fetch', server.fetch);
});

afterEach(() => {
  viewer.close();
  flushSync();
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(openPdf).mockReset();
});

describe('AttachmentViewer', () => {
  it('shows an image in a lightbox, with its name and a download button', () => {
    const root = render(AttachmentViewer, {});
    const dialog = open(root, attachment({}));
    const img = dialog.querySelector('img')!;
    expect(img.getAttribute('src')).toBe('/api/attachments/3/content');
    expect(img.getAttribute('alt')).toBe('foto.png');
    expect(textOf(dialog.querySelector('h2'))).toBe('foto.png');
    const download = dialog.querySelector<HTMLAnchorElement>('a[download]')!;
    expect(download.getAttribute('href')).toBe('/api/attachments/3/content');
    expect(download.getAttribute('download')).toBe('foto.png');
    expect(textOf(download)).toBe('Descarrega');

    button(root, 'Tanca la vista prèvia').click();
    flushSync();
    expect(dialog.open).toBe(false);
    expect(viewer.current).toBeNull();
  });

  it('shows a PDF page by page', async () => {
    const doc = fakeDocument(3);
    vi.mocked(openPdf).mockResolvedValue(doc);
    const root = render(AttachmentViewer, {});
    const dialog = open(root, PDF);
    // PDF.js loads only now (its own chunk).
    await vi.waitFor(() => expect(vi.mocked(openPdf)).toHaveBeenCalledWith({ url: '/api/attachments/5/content' }));
    await vi.waitFor(() => expect(doc.render).toHaveBeenCalled());
    expect(doc.render.mock.calls[0]![0]).toBe(1);
    expect(doc.render.mock.calls[0]![1]).toBe(dialog.querySelector('canvas'));
    expect(doc.render.mock.calls[0]![2]).toBeGreaterThan(0);
    expect(textOf(dialog.querySelector('.pager'))).toContain('Pàgina 1 de 3');
    expect(button(root, 'Pàgina anterior').disabled).toBe(true);

    button(root, 'Pàgina següent').click();
    flushSync();
    await vi.waitFor(() => expect(doc.render.mock.calls.at(-1)![0]).toBe(2));
    expect(textOf(dialog.querySelector('.pager'))).toContain('Pàgina 2 de 3');

    // The arrow keys turn the pages too.
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    flushSync();
    await vi.waitFor(() => expect(doc.render.mock.calls.at(-1)![0]).toBe(3));
    expect(button(root, 'Pàgina següent').disabled).toBe(true);
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    flushSync();
    await vi.waitFor(() => expect(doc.render.mock.calls.at(-1)![0]).toBe(2));

    expect(dialog.querySelector('a[download]')?.getAttribute('href')).toBe('/api/attachments/5/content');
    viewer.close();
    flushSync();
    await vi.waitFor(() => expect(doc.destroy).toHaveBeenCalled());
  });

  it('a PDF that cannot be shown says so, and can still be downloaded', async () => {
    vi.mocked(openPdf).mockRejectedValue(new Error('Invalid PDF structure.'));
    const root = render(AttachmentViewer, {});
    const dialog = open(root, PDF);
    await vi.waitFor(() =>
      expect(textOf(dialog.querySelector('[role=alert]'))).toBe("No s'ha pogut mostrar el PDF. Pots descarregar-lo."),
    );
    expect(dialog.querySelector('a[download]')).not.toBeNull();
  });

  it('a PDF opened while another one loads shows the last one', async () => {
    const first = deferred<PdfDocument>();
    const firstDoc = fakeDocument(7);
    const secondDoc = fakeDocument(2);
    vi.mocked(openPdf).mockReturnValueOnce(first.promise).mockResolvedValueOnce(secondDoc);
    const root = render(AttachmentViewer, {});
    open(root, PDF);
    viewer.open({ ...PDF, id: 6, name: 'altre.pdf' });
    flushSync();
    await vi.waitFor(() => expect(secondDoc.render).toHaveBeenCalled());
    first.resolve(firstDoc);
    await vi.waitFor(() => expect(firstDoc.destroy).toHaveBeenCalled());
    expect(firstDoc.render).not.toHaveBeenCalled();
    expect(textOf(root.querySelector('.pager'))).toContain('Pàgina 1 de 2');
  });

  it('shows a text file as plain text, never as HTML', async () => {
    server.addAttachment(TEXT, new TextEncoder().encode('Hola <b>món</b>\n<script>alert(1)</script>\n‮amagat'));
    const root = render(AttachmentViewer, {});
    const dialog = open(root, TEXT);
    await vi.waitFor(() => expect(dialog.querySelector('pre')).not.toBeNull());
    const pre = dialog.querySelector('pre')!;
    expect(pre.querySelector('b, script')).toBeNull();
    expect(pre.textContent).toBe('Hola <b>món</b>\n<script>alert(1)</script>\n⟨U+202E⟩amagat');
    expect(server.ranges).toEqual([null]); // the whole file
  });

  it('a text file that cannot be loaded says so', async () => {
    const root = render(AttachmentViewer, {});
    const dialog = open(root, TEXT); // the server does not have it: 404
    await vi.waitFor(() => expect(textOf(dialog.querySelector('[role=alert]'))).toContain("No s'ha pogut carregar el fitxer."));
  });
});
