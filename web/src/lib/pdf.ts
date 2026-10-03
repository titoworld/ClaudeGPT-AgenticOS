// PDF.js, for the thumbnails and the previews of attached PDFs (docs/adr/0009-attachments.md).
// Loaded only when a PDF is attached or previewed: nothing imports this module
// statically (lib/bundle.test.ts), so it and PDF.js are a chunk of their own.
//
// Set up for the app's Content-Security-Policy (server/middleware.py, vite.config.ts):
// - the worker is a file of the app, on the same origin, so PDF.js starts it directly
//   and never wraps it in a blob: worker;
// - no WebAssembly (`useWasm`), since compiling it would need 'wasm-unsafe-eval': the
//   decoders of scanned pages (JBIG2, JPEG 2000) run in their JavaScript versions. PDF.js
//   6 never evaluates code (it has no eval or new Function left, so no `isEvalSupported`
//   option either): lib/pdf.test.ts checks the pinned build;
// - glyphs are drawn as paths (`disableFontFace`): no FontFace for font-src, and the
//   fonts of a PDF never reach the browser's font engine;
// - every file it fetches by name (standard fonts, CMaps, decoders) is an asset of the
//   app: vite.config.ts keeps their names, under assets/pdfjs-<version>/.
//
// It is PDF.js's "legacy" build: the other one needs JavaScript newer than many current
// browsers have (Map.prototype.getOrInsertComputed...), and this one brings it along.

import {
  getDocument,
  GlobalWorkerOptions,
  VerbosityLevel,
  type PDFDocumentProxy,
  type RenderTask,
} from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';

GlobalWorkerOptions.workerSrc = workerUrl;

/** The files PDF.js fetches by name, imported so that the build ships them (path -> URL). */
export const PDFJS_ASSETS = {
  standard_fonts: import.meta.glob<string>('/node_modules/pdfjs-dist/standard_fonts/*', {
    query: '?url&no-inline',
    import: 'default',
    eager: true,
    exhaustive: true,
  }),
  cmaps: import.meta.glob<string>(['/node_modules/pdfjs-dist/cmaps/*.bcmap', '/node_modules/pdfjs-dist/cmaps/LICENSE'], {
    query: '?url&no-inline',
    import: 'default',
    eager: true,
    exhaustive: true,
  }),
  wasm: import.meta.glob<string>(
    ['/node_modules/pdfjs-dist/wasm/*_nowasm_fallback.js', '/node_modules/pdfjs-dist/wasm/LICENSE*'],
    { query: '?url&no-inline', import: 'default', eager: true, exhaustive: true },
  ),
};

/** The folder of some assets, as PDF.js wants it (with the trailing slash). */
function folder(files: Record<string, string>): string {
  const url = Object.values(files)[0];
  if (!url) throw new Error('The PDF.js data files are missing.');
  return url.slice(0, url.lastIndexOf('/') + 1);
}

/** Images of a page larger than this (in pixels) are not drawn: a hostile PDF cannot exhaust memory. */
const MAX_IMAGE_PIXELS = 40_000_000;
/** The most pixels a page is drawn with (iOS refuses larger canvases). */
const MAX_CANVAS_PIXELS = 16_000_000;
const MAX_PIXEL_RATIO = 3;

/** How every PDF is opened (see above). */
export const DOCUMENT_OPTIONS = {
  disableFontFace: true,
  useSystemFonts: false,
  useWasm: false,
  useWorkerFetch: true,
  enableXfa: false,
  maxImageSize: MAX_IMAGE_PIXELS,
  verbosity: VerbosityLevel.ERRORS,
  standardFontDataUrl: folder(PDFJS_ASSETS.standard_fonts),
  cMapUrl: folder(PDFJS_ASSETS.cmaps),
  cMapPacked: true,
  wasmUrl: folder(PDFJS_ASSETS.wasm),
} as const;

const cancelled = (err: unknown): boolean => err instanceof Error && err.name === 'RenderingCancelledException';

/**
 * The first page of a PDF drawn with its long edge `edge` pixels long, and its number
 * of pages. The document is closed afterwards; `signal` stops PDF.js early.
 */
export async function renderFirstPage(
  data: Uint8Array,
  edge: number,
  signal?: AbortSignal,
): Promise<{ canvas: HTMLCanvasElement; pages: number }> {
  const task = getDocument({ ...DOCUMENT_OPTIONS, data });
  const stop = () => void task.destroy();
  signal?.addEventListener('abort', stop, { once: true });
  try {
    const doc = await task.promise;
    const page = await doc.getPage(1);
    const unscaled = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: edge / Math.max(unscaled.width, unscaled.height) });
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(viewport.width));
    canvas.height = Math.max(1, Math.round(viewport.height));
    await page.render({ canvas, viewport }).promise;
    return { canvas, pages: doc.numPages };
  } finally {
    signal?.removeEventListener('abort', stop);
    await task.destroy();
  }
}

/** An open PDF of the viewer. */
export interface PdfDocument {
  readonly pages: number;
  /**
   * Draws page `number` (from 1) on `canvas`, `cssWidth` CSS pixels wide and sharp on
   * dense screens. A newer call stops it: it then resolves false instead of true.
   */
  render(number: number, canvas: HTMLCanvasElement, cssWidth: number): Promise<boolean>;
  destroy(): Promise<void>;
}

/** Opens a PDF for the viewer: from the server (`url`, same origin) or from its bytes. */
export async function openPdf(source: { url: string } | { data: Uint8Array }): Promise<PdfDocument> {
  const task = getDocument({ ...DOCUMENT_OPTIONS, ...source });
  let doc: PDFDocumentProxy;
  try {
    doc = await task.promise;
  } catch (err) {
    await task.destroy();
    throw err;
  }
  let ticket = 0;
  let drawing: RenderTask | null = null;

  async function render(number: number, canvas: HTMLCanvasElement, cssWidth: number): Promise<boolean> {
    const mine = ++ticket;
    drawing?.cancel();
    drawing = null;
    const page = await doc.getPage(number);
    if (mine !== ticket) return false;
    const unscaled = page.getViewport({ scale: 1 });
    const scale = cssWidth / unscaled.width;
    const ratio = Math.min(Math.max(globalThis.devicePixelRatio || 1, 1), MAX_PIXEL_RATIO);
    let viewport = page.getViewport({ scale: scale * ratio });
    const pixels = viewport.width * viewport.height;
    if (pixels > MAX_CANVAS_PIXELS) viewport = page.getViewport({ scale: scale * ratio * Math.sqrt(MAX_CANVAS_PIXELS / pixels) });
    canvas.width = Math.max(1, Math.round(viewport.width));
    canvas.height = Math.max(1, Math.round(viewport.height));
    canvas.style.width = `${Math.round(cssWidth)}px`;
    canvas.style.height = `${Math.round(unscaled.height * scale)}px`;
    const task = page.render({ canvas, viewport });
    drawing = task;
    try {
      await task.promise;
      return mine === ticket;
    } catch (err) {
      if (cancelled(err)) return false;
      throw err;
    } finally {
      if (drawing === task) drawing = null;
    }
  }

  return {
    pages: doc.numPages,
    render,
    destroy: () => task.destroy(),
  };
}
