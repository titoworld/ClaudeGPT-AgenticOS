// PDF.js for the thumbnails and the previews of attached PDFs (docs/adr/0009-attachments.md),
// set up for the app's Content-Security-Policy (script-src 'self', worker-src 'self' blob:,
// font-src 'self', connect-src 'self'): the worker is a file of the app (never a blob:
// worker), no WebAssembly is compiled, glyphs are drawn as paths (no FontFace), and every
// file it needs comes from the app itself. PDF.js itself is a double here.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

interface FakePage {
  number: number;
  render: ReturnType<typeof vi.fn>;
}

const state = vi.hoisted(() => ({
  pages: [] as FakePage[],
  destroyed: 0,
  numPages: 3,
  failure: null as Error | null,
  /** Pages being drawn wait for `release()` instead of finishing on their own. */
  hold: false,
  held: [] as (() => void)[],
  release(): void {
    for (const finish of this.held.splice(0)) finish();
  },
}));

vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  version: '6.3.289',
  VerbosityLevel: { ERRORS: 0, WARNINGS: 1, INFOS: 5 },
  getDocument: vi.fn(() => {
    const doc = {
      numPages: state.numPages,
      getPage: vi.fn(async (number: number) => {
        const page: FakePage = {
          number,
          render: vi.fn(() => {
            let cancel!: () => void;
            const promise = new Promise<void>((resolve, reject) => {
              cancel = () => reject(Object.assign(new Error('Rendering cancelled'), { name: 'RenderingCancelledException' }));
              if (state.hold) state.held.push(resolve);
              else setTimeout(resolve, 0);
            });
            return { promise, cancel: vi.fn(cancel) };
          }),
        };
        state.pages.push(page);
        return {
          ...page,
          getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale, scale }),
          cleanup: vi.fn(),
        };
      }),
    };
    return {
      promise: state.failure ? Promise.reject(state.failure) : Promise.resolve(doc),
      destroy: vi.fn(async () => {
        state.destroyed++;
      }),
    };
  }),
}));

import * as pdfjs from 'pdfjs-dist/legacy/build/pdf.mjs';
import { DOCUMENT_OPTIONS, openPdf, PDFJS_ASSETS, renderFirstPage } from './pdf';

beforeEach(() => {
  state.pages = [];
  state.destroyed = 0;
  state.numPages = 3;
  state.failure = null;
  state.hold = false;
  state.held = [];
  vi.mocked(pdfjs.getDocument).mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const params = (call = 0) => vi.mocked(pdfjs.getDocument).mock.calls[call]![0] as Record<string, unknown>;

describe('PDF.js set up for the Content-Security-Policy', () => {
  it('loads its worker from a file of the app, never from a blob: URL', () => {
    const src = pdfjs.GlobalWorkerOptions.workerSrc;
    expect(src).toMatch(/^\/[^/].*pdf\.worker(\.min)?\.mjs$/);
  });

  it('opens every PDF with options that need no eval, WebAssembly, FontFace or other origin', async () => {
    await renderFirstPage(new Uint8Array([37, 80, 68, 70]), 256);
    await openPdf({ url: '/api/attachments/7/content' });
    for (const call of [0, 1]) {
      expect(params(call)).toMatchObject({
        disableFontFace: true, // glyphs drawn as paths: no FontFace, nothing for font-src
        useSystemFonts: false,
        useWasm: false, // WebAssembly would need 'wasm-unsafe-eval'
        useWorkerFetch: true,
        enableXfa: false,
        verbosity: 0,
      });
      expect(params(call)).toMatchObject(DOCUMENT_OPTIONS);
    }
    // Every file it fetches by name comes from the app itself: a folder of its own each.
    for (const [option, folder] of [
      ['standardFontDataUrl', 'standard_fonts'],
      ['cMapUrl', 'cmaps'],
      ['wasmUrl', 'wasm'],
    ] as const) {
      expect(DOCUMENT_OPTIONS[option]).toMatch(new RegExp(`^/[^/].*/${folder}/$`));
    }
    for (const value of Object.values(DOCUMENT_OPTIONS)) {
      if (typeof value === 'string') expect(value).toMatch(/^\/[^/]/);
    }
    expect(params(0).data).toEqual(new Uint8Array([37, 80, 68, 70]));
    expect(params(1).url).toBe('/api/attachments/7/content');
    expect(params(1).withCredentials).toBeUndefined(); // same origin: the session cookie goes as usual
  });

  it('ships every file PDF.js fetches by name, at its base URL plus its name', async () => {
    const module: string = 'node:fs'; // not a literal: the web code has no Node types
    const fs = (await import(/* @vite-ignore */ module)) as { readdirSync(path: string): string[] };
    const here: string = import.meta.url;
    const folder = (name: string) =>
      fs.readdirSync(decodeURIComponent(new URL(`../../node_modules/pdfjs-dist/${name}`, here).pathname));
    const expected = {
      standard_fonts: folder('standard_fonts').filter((f) => /\.(pfb|ttf)$/.test(f)),
      cmaps: folder('cmaps').filter((f) => f.endsWith('.bcmap')),
      wasm: ['jbig2_nowasm_fallback.js', 'openjpeg_nowasm_fallback.js'],
    };
    const bases = { standard_fonts: 'standardFontDataUrl', cmaps: 'cMapUrl', wasm: 'wasmUrl' } as const;
    for (const [name, files] of Object.entries(expected) as [keyof typeof expected, string[]][]) {
      expect(files.length, name).toBeGreaterThan(1);
      // The development server's URLs carry the import's query; PDF.js asks without it.
      const urls = Object.values(PDFJS_ASSETS[name]).map((url) => url.split('?')[0]);
      for (const file of files) expect(urls, `${name}/${file}`).toContain(`${DOCUMENT_OPTIONS[bases[name]]}${file}`);
    }
    expect(Object.keys(PDFJS_ASSETS.wasm).filter((f) => f.endsWith('.wasm'))).toEqual([]);
  });
});

describe('the first page, for a thumbnail', () => {
  it('is drawn with its long edge at the size asked, and the document is closed', async () => {
    state.numPages = 12;
    const { canvas, pages } = await renderFirstPage(new Uint8Array([1]), 256);
    expect(pages).toBe(12);
    expect([canvas.width, canvas.height]).toEqual([192, 256]);
    expect(state.pages.map((p) => p.number)).toEqual([1]);
    expect(state.pages[0]!.render.mock.calls[0]![0]).toMatchObject({ canvas });
    expect(state.destroyed).toBe(1);
  });

  it('a PDF that cannot be opened is an error, and nothing is left open', async () => {
    state.failure = new Error('Invalid PDF structure.');
    await expect(renderFirstPage(new Uint8Array([1]), 256)).rejects.toThrow('Invalid PDF structure.');
    expect(state.destroyed).toBe(1);
  });
});

describe('a PDF in the viewer', () => {
  it('draws any page at a CSS width, sharp on dense screens', async () => {
    vi.stubGlobal('devicePixelRatio', 2);
    const doc = await openPdf({ url: '/api/attachments/7/content' });
    expect(doc.pages).toBe(3);
    const canvas = document.createElement('canvas');
    await doc.render(2, canvas, 300);
    expect([canvas.width, canvas.height]).toEqual([600, 800]);
    expect([canvas.style.width, canvas.style.height]).toEqual(['300px', '400px']);
    expect(state.pages.map((p) => p.number)).toEqual([2]);
    await doc.destroy();
    expect(state.destroyed).toBe(1);
  });

  it('a new page cancels the one still being drawn', async () => {
    state.hold = true;
    const doc = await openPdf({ url: '/api/attachments/7/content' });
    const canvas = document.createElement('canvas');
    const first = doc.render(1, canvas, 300);
    await vi.waitFor(() => expect(state.pages[0]?.render).toHaveBeenCalled());
    const second = doc.render(2, canvas, 300);
    await expect(first).resolves.toBe(false); // cancelled: not an error
    expect(state.pages[0]!.render.mock.results[0]!.value.cancel).toHaveBeenCalled();
    await vi.waitFor(() => expect(state.pages[1]?.render).toHaveBeenCalled());
    state.release();
    await expect(second).resolves.toBe(true);

    // Asked for before its page even loaded: never drawn.
    const third = doc.render(3, canvas, 300);
    const fourth = doc.render(1, canvas, 300);
    await expect(third).resolves.toBe(false);
    await vi.waitFor(() => expect(state.pages.at(-1)?.render).toHaveBeenCalled());
    state.release();
    await expect(fourth).resolves.toBe(true);
    expect(state.pages.filter((p) => p.number === 3).every((p) => p.render.mock.calls.length === 0)).toBe(true);
  });
});

/** A file of the installed pdfjs-dist, read from disk. */
async function pdfjsFile(path: string): Promise<string> {
  const module: string = 'node:fs'; // not a literal: the web code has no Node types
  const fs = (await import(/* @vite-ignore */ module)) as { readFileSync(path: string, encoding: 'utf8'): string };
  const here: string = import.meta.url;
  return fs.readFileSync(decodeURIComponent(new URL(`../../node_modules/pdfjs-dist/${path}`, here).pathname), 'utf8');
}

/**
 * The one Function constructor of the legacy build: core-js's last way to find the global
 * object, after `globalThis` (which every browser has had since 2019), so never reached.
 */
const GLOBAL_FALLBACK = '||function(){return this}()||Function("return this")()';

describe('the pinned PDF.js build', () => {
  it('never evaluates code, so script-src needs no unsafe-eval', async () => {
    for (const path of [
      'legacy/build/pdf.min.mjs',
      'legacy/build/pdf.worker.min.mjs',
      'wasm/jbig2_nowasm_fallback.js',
      'wasm/openjpeg_nowasm_fallback.js',
    ]) {
      const code = await pdfjsFile(path);
      expect(code.length, path).toBeGreaterThan(1000);
      const rest = code.replaceAll(GLOBAL_FALLBACK, '');
      expect(rest, path).not.toMatch(/\beval\s*\(|\bFunction\s*\(\s*["'`]|\bnew\s+Function\s*\(/);
      expect(rest.length, path).toBeGreaterThanOrEqual(code.length - GLOBAL_FALLBACK.length);
    }
  });

  it('is loaded from the build that brings the JavaScript it needs (the legacy one)', () => {
    expect(pdfjs.GlobalWorkerOptions.workerSrc).toMatch(/legacy\/build\/pdf\.worker\.min\.mjs$/);
  });
});
