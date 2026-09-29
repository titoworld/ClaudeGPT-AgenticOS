// What the browser has to load before the login screen, and what it can parse.
import { afterEach, describe, expect, it, vi } from 'vitest';

/** Every source file of the app, as text (tests and fixtures do not ship). */
const SOURCES = import.meta.glob<string>(['/src/**/*.ts', '/src/**/*.svelte', '!/src/**/*.test.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
});

// Static imports and re-exports (`import type` is erased); `import('…')` stays lazy.
const STATIC_IMPORT = /^\s*(?:import|export)\s+(?!type\s)(?:[^'";]*?\sfrom\s+)?['"]([^'"]+)['"]/gm;

function resolve(from: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null;
  const parts = from.split('/').slice(0, -1);
  for (const seg of spec.split('/')) {
    if (seg === '..') parts.pop();
    else if (seg !== '.') parts.push(seg);
  }
  const path = parts.join('/');
  return [path, `${path}.ts`, `${path}/index.ts`].find((p) => p in SOURCES) ?? path;
}

/** Modules and packages the entry chunk needs: everything `main.ts` imports statically. */
function entryGraph(): { modules: Set<string>; packages: Set<string> } {
  const modules = new Set<string>();
  const packages = new Set<string>();
  const queue = ['/src/main.ts'];
  while (queue.length) {
    const file = queue.pop()!;
    if (modules.has(file)) continue;
    modules.add(file);
    for (const m of (SOURCES[file] ?? '').matchAll(STATIC_IMPORT)) {
      const spec = m[1]!;
      const target = resolve(file, spec);
      if (target === null) packages.add(spec.split('/')[0]!);
      else if (target in SOURCES) queue.push(target);
    }
  }
  return { modules, packages };
}

describe('entry chunk (login screen)', () => {
  it('does not pull in marked, DOMPurify, highlight.js or three.js (K11)', () => {
    const { modules, packages } = entryGraph();
    // The walk reached the login screen's copy button.
    expect(modules).toContain('/src/views/Login.svelte');
    expect(modules).toContain('/src/components/CopyButton.svelte');
    expect(modules).toContain('/src/lib/clipboard.ts');
    for (const lazy of [
      '/src/lib/markdown.ts',
      '/src/lib/hidden-chars.ts',
      '/src/lib/pdf.ts',
      // The work on attached files (canvas, sniffing, image headers) comes with the first attachment.
      '/src/lib/attachment-work.ts',
      '/src/lib/media.ts',
      '/src/lib/image-header.ts',
    ]) {
      expect(modules).not.toContain(lazy);
    }
    for (const heavy of ['marked', 'dompurify', 'highlight.js', 'three', 'pdfjs-dist']) expect(packages).not.toContain(heavy);
  });

  it('loads PDF.js only when a PDF is attached or previewed (its own chunk)', () => {
    const importers = Object.entries(SOURCES)
      .filter(([, text]) => [...text.matchAll(STATIC_IMPORT)].some((m) => m[1]!.startsWith('pdfjs-dist')))
      .map(([file]) => file);
    expect(importers).toEqual(['/src/lib/pdf.ts']);
    // lib/pdf.ts itself is never imported statically: only with import('./pdf').
    const staticPdf = Object.entries(SOURCES)
      .filter(([file, text]) => [...text.matchAll(STATIC_IMPORT)].some((m) => resolve(file, m[1]!) === '/src/lib/pdf.ts'))
      .map(([file]) => file);
    expect(staticPdf).toEqual([]);
    const lazy = Object.entries(SOURCES).filter(([, text]) => /import\(\s*['"]\.{1,2}\/(?:lib\/)?pdf['"]\s*\)/.test(text));
    expect(lazy.length).toBeGreaterThan(0);
  });

  it('keeps the clipboard and hidden-character helpers free of imports', () => {
    for (const file of ['/src/lib/clipboard.ts', '/src/lib/hidden-chars.ts']) {
      expect([...(SOURCES[file] ?? '').matchAll(STATIC_IMPORT)], file).toEqual([]);
    }
  });

  it('copies model text only through answerForClipboard (hidden characters revealed)', () => {
    const buttons = Object.entries(SOURCES).flatMap(([file, text]) =>
      [...text.matchAll(/<CopyButton\b[^>]*>/g)].map((m) => ({ file, tag: m[0] })),
    );
    expect(buttons.length).toBeGreaterThan(1);
    for (const { file, tag } of buttons) {
      // The login screen copies a fixed command of its own.
      if (file === '/src/views/Login.svelte') expect(tag).toContain('text={SETUP_COMMAND}');
      else expect(tag, file).toContain('prepare={answerForClipboard}');
    }
  });
});

describe('shipped sources', () => {
  it('write bidi controls and invisible characters as escapes, never literally (Trojan Source)', () => {
    const hidden = /[\u00AD\u061C\u180E\u200B-\u200F\u202A-\u202E\u2060-\u2069\uFEFF\u{E0000}-\u{E007F}]/u;
    expect(Object.keys(SOURCES).filter((file) => hidden.test(SOURCES[file]!))).toEqual([]);
  });
});

describe('regex lookbehind (Safari < 16.4 throws a SyntaxError at load, K12)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('no shipped source uses it', () => {
    const offenders = Object.entries(SOURCES)
      .filter(([, text]) => /\(\?<[=!]/.test(text))
      .map(([file]) => file);
    expect(offenders).toEqual([]);
  });

  it('the markdown and clipboard modules load where it is not supported', async () => {
    const Native = RegExp;
    const unsupported = (source: unknown): void => {
      if (/\(\?<[=!]/.test(String(source instanceof Native ? source.source : source))) {
        throw new SyntaxError('Invalid regular expression: invalid group specifier name');
      }
    };
    vi.stubGlobal(
      'RegExp',
      new Proxy(Native, {
        construct(target, args: [string | RegExp, string?]) {
          unsupported(args[0]);
          return Reflect.construct(target, args) as RegExp;
        },
        apply(target, self, args: [string | RegExp, string?]) {
          unsupported(args[0]);
          return Reflect.apply(target, self, args) as RegExp;
        },
      }),
    );
    vi.resetModules();
    const hidden = await import('./hidden-chars');
    const markdown = await import('./markdown');
    await import('./clipboard');
    expect(hidden.revealHidden('a\u202Eb').text).toBe('a⟨U+202E⟩b');
    expect(markdown.renderMarkdown('`x\u200By` 👩\u200D💻')).toContain('⟨U+200B⟩');
  });
});
