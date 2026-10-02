// @vitest-environment node
// PDF.js fetches some files by name from a base URL: the standard fonts (it draws glyphs
// as paths, so a PDF that does not embed Helvetica needs them), the CMaps of CJK fonts
// and the image decoders of scanned pages (JBIG2, JPEG 2000) in their versions without
// WebAssembly. lib/pdf.ts imports them as assets; the build keeps their names, in a
// folder named after the pdfjs-dist version (the server caches /assets/ forever, so a
// new version must be a new folder), and every other asset keeps its hashed name.
import { describe, expect, it } from 'vitest';
import config from '../../vite.config';

/** The version of the installed pdfjs-dist, read from disk. */
async function pdfjsVersion(): Promise<string> {
  const module: string = 'node:fs'; // not a literal: the web code has no Node types
  const fs = (await import(/* @vite-ignore */ module)) as { readFileSync(path: string, encoding: 'utf8'): string };
  const here: string = import.meta.url;
  const path = decodeURIComponent(new URL('../../node_modules/pdfjs-dist/package.json', here).pathname);
  return (JSON.parse(fs.readFileSync(path, 'utf8')) as { version: string }).version;
}

type AssetFileNames = (asset: { names: string[]; originalFileNames: string[]; type: 'asset'; source: string }) => string;

function assetFileNames(): AssetFileNames {
  const resolved = config({ mode: 'production', command: 'build', isSsrBuild: false, isPreview: false });
  const output = resolved.build?.rolldownOptions?.output;
  const names = !Array.isArray(output) ? output?.assetFileNames : undefined;
  expect(typeof names).toBe('function');
  return names as unknown as AssetFileNames;
}

const asset = (original: string) => ({
  names: [original.split('/').at(-1)!],
  originalFileNames: [original],
  type: 'asset' as const,
  source: '',
});

describe('the PDF.js files the build ships', () => {
  it('keep their names, under a folder named after the pdfjs-dist version', async () => {
    const version = await pdfjsVersion();
    const name = assetFileNames();
    for (const file of [
      'standard_fonts/FoxitSerif.pfb',
      'standard_fonts/LiberationSans-Regular.ttf',
      'standard_fonts/LICENSE_FOXIT',
      'cmaps/UniJIS-UCS2-H.bcmap',
      'cmaps/LICENSE',
      'wasm/jbig2_nowasm_fallback.js',
      'wasm/openjpeg_nowasm_fallback.js',
      'wasm/LICENSE_OPENJPEG',
    ]) {
      expect(name(asset(`node_modules/pdfjs-dist/${file}`)), file).toBe(`assets/pdfjs-${version}/${file}`);
    }
  });

  it('every other asset keeps its hashed name', () => {
    const name = assetFileNames();
    for (const original of [
      'src/assets/logo.png',
      'node_modules/pdfjs-dist/build/pdf.worker.min.mjs', // the worker: a hashed file like the app's
      'node_modules/other/standard_fonts/Foo.pfb',
      'node_modules/pdfjs-dist/standard_fonts/sub/Foo.pfb',
    ]) {
      expect(name(asset(original)), original).toBe('assets/[name]-[hash][extname]');
    }
  });
});
