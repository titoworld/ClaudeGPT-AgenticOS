// PDF.js (lib/pdf.ts, a chunk of its own) is loaded once, the first time a PDF is
// attached or previewed; a load that failed (the network) is tried again next time.

type PdfModule = typeof import('./pdf');

let loading: Promise<PdfModule> | null = null;

export function loadPdf(): Promise<PdfModule> {
  loading ??= import('./pdf').catch((err: unknown) => {
    loading = null;
    throw err;
  });
  return loading;
}
