// PDF.js is loaded once, when a PDF is first attached or previewed: every caller at the
// time (several PDFs attached together, a preview) shares that one load.
import { describe, expect, it, vi } from 'vitest';

vi.mock('./pdf', () => ({ openPdf: vi.fn(), renderFirstPage: vi.fn() }));

import { loadPdf } from './load-pdf';

describe('loadPdf', () => {
  it('shares one load between every caller', async () => {
    const first = loadPdf();
    expect(loadPdf()).toBe(first);
    const pdf = await first;
    expect(pdf.openPdf).toBeTypeOf('function');
    expect(await loadPdf()).toBe(pdf);
  });
});
