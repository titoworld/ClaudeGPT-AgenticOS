// The images and PDFs the browser prepares before an upload (docs/PROTOCOL.md «Adjunts»):
// images larger than the models use are downscaled (WebP, quality 0.9; GIF as they are),
// photos stored turned (EXIF orientation) are turned upright, and every image or PDF gets
// a small thumbnail within the server's limits. jsdom has no canvas: the decoder and the
// encoders are doubles that record what they are asked.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AttachmentError, tooLargeMessage } from './attachments';
import { MAX_THUMBNAIL_BYTES, MAX_THUMBNAIL_SIDE } from './protocol';
import { exif, gif, jfif, jpeg, png, webpLossless } from './test-images';

vi.mock('./pdf', () => ({ renderFirstPage: vi.fn() }));

import { blobToDataUrl, pdfPreview, PDF_PREVIEW_TIMEOUT_MS, prepareImage, readHead, THUMBNAIL_EDGE } from './media';
import { renderFirstPage } from './pdf';

interface Encode {
  requested: string;
  type: string;
  quality: number | undefined;
  width: number;
  height: number;
}

/** The browser's image decoder and canvas encoders, as the tests set them. */
class Browser {
  width = 1000;
  height = 800;
  decodeFails = false;
  /** Types the canvas can encode (Safari cannot encode WebP: it gives PNG). */
  encoders = new Set(['image/png', 'image/jpeg', 'image/webp']);
  /** Bytes of an encoded image. */
  size: (type: string, width: number, height: number) => number = (type, w, h) =>
    Math.ceil((w * h) / (type === 'image/png' ? 2 : 20));
  readonly encodes: Encode[] = [];
  readonly closed: number[] = [];
  /** Per canvas: whether it was filled (white background) before an image was drawn on it. */
  readonly drawn: { width: number; height: number; filledFirst: boolean }[] = [];
}

let browser: Browser;

beforeEach(() => {
  browser = new Browser();
  let bitmaps = 0;
  vi.stubGlobal(
    'createImageBitmap',
    vi.fn(async () => {
      if (browser.decodeFails) throw new DOMException('The source image could not be decoded.', 'InvalidStateError');
      const id = ++bitmaps;
      return { width: browser.width, height: browser.height, close: () => browser.closed.push(id) };
    }),
  );
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    const canvas = this;
    let filled = false;
    return {
      fillStyle: '',
      fillRect: () => {
        filled = true;
      },
      drawImage: () => {
        browser.drawn.push({ width: canvas.width, height: canvas.height, filledFirst: filled });
      },
      imageSmoothingEnabled: true,
      imageSmoothingQuality: 'low',
    } as unknown as CanvasRenderingContext2D;
  } as unknown as typeof HTMLCanvasElement.prototype.getContext);
  vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (
    this: HTMLCanvasElement,
    callback: BlobCallback,
    type = 'image/png',
    quality?: number,
  ) {
    const actual = browser.encoders.has(type) ? type : 'image/png';
    browser.encodes.push({ requested: type, type: actual, quality, width: this.width, height: this.height });
    const size = browser.size(actual, this.width, this.height);
    setTimeout(() => callback(new Blob([new Uint8Array(size)], { type: actual })), 0);
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.mocked(renderFirstPage).mockReset();
});

const file = (name: string, type: string, size = 1000) => new File([new Uint8Array(size)], name, { type });

describe('an image before its upload', () => {
  it('larger than 2.576 px: downscaled and encoded as WebP, quality 0.9', async () => {
    [browser.width, browser.height] = [4000, 3000];
    const photo = file('foto.jpg', 'image/jpeg', 2_000_000);
    const prepared = await prepareImage(photo, 'image/jpeg');
    expect(prepared.upload).not.toBe(photo);
    expect(prepared.upload.type).toBe('image/webp');
    expect([prepared.width, prepared.height]).toEqual([2576, 1932]);
    expect(browser.encodes[0]).toEqual({ requested: 'image/webp', type: 'image/webp', quality: 0.9, width: 2576, height: 1932 });
    expect(browser.closed).not.toHaveLength(0); // the decoded bitmap is released
  });

  it('within the limits: uploaded as it is, with a thumbnail of about 256 px', async () => {
    [browser.width, browser.height] = [1000, 800];
    const photo = file('foto.png', 'image/png', 300_000);
    const prepared = await prepareImage(photo, 'image/png');
    expect(prepared.upload).toBe(photo);
    expect([prepared.width, prepared.height]).toEqual([1000, 800]);
    expect(THUMBNAIL_EDGE).toBe(256);
    expect(prepared.thumbnail?.type).toBe('image/webp');
    expect(browser.encodes).toEqual([{ requested: 'image/webp', type: 'image/webp', quality: 0.8, width: 256, height: 204 }]);
  });

  it('a GIF goes as it is, however large (its thumbnail is its first frame)', async () => {
    [browser.width, browser.height] = [5000, 3000];
    const gif = file('animacio.gif', 'image/gif', 3_000_000);
    const prepared = await prepareImage(gif, 'image/gif');
    expect(prepared.upload).toBe(gif);
    expect([prepared.width, prepared.height]).toEqual([5000, 3000]);
    expect(prepared.thumbnail).not.toBeNull();
    expect(browser.encodes.every((e) => Math.max(e.width, e.height) <= THUMBNAIL_EDGE)).toBe(true);
  });

  it('a GIF the server would refuse is refused here, before uploading it', async () => {
    [browser.width, browser.height] = [9000, 100];
    await expect(prepareImage(file('a.gif', 'image/gif'), 'image/gif')).rejects.toMatchObject({
      status: 422,
      message: 'La imatge fa 9.000 x 100 píxels: com a molt 8.000 per costat.',
    });
    [browser.width, browser.height] = [100, 100];
    await expect(prepareImage(file('a.gif', 'image/gif', 7_000_001), 'image/gif')).rejects.toMatchObject({
      status: 413,
      message: tooLargeMessage('image'),
    });
  });

  it('where the browser cannot encode WebP: JPEG for a photo, PNG for the rest', async () => {
    browser.encoders = new Set(['image/png', 'image/jpeg']);
    [browser.width, browser.height] = [4000, 3000];
    const jpeg = await prepareImage(file('foto.jpg', 'image/jpeg'), 'image/jpeg');
    expect(jpeg.upload.type).toBe('image/jpeg');
    expect(browser.encodes.slice(0, 2).map((e) => [e.requested, e.quality])).toEqual([
      ['image/webp', 0.9],
      ['image/jpeg', 0.9],
    ]);
    const png = await prepareImage(file('captura.png', 'image/png'), 'image/png');
    expect(png.upload.type).toBe('image/png');
  });

  it('a PNG still too heavy becomes a JPEG on a white background (no transparency)', async () => {
    browser.encoders = new Set(['image/png', 'image/jpeg']);
    browser.size = (type) => (type === 'image/png' ? 8_000_000 : 900_000);
    [browser.width, browser.height] = [3000, 3000];
    const prepared = await prepareImage(file('captura.png', 'image/png'), 'image/png');
    expect(prepared.upload.type).toBe('image/jpeg');
    const jpegCanvas = browser.drawn.find((d) => d.width === 2576 && d.filledFirst);
    expect(jpegCanvas).toBeDefined();
  });

  it('refuses an image that is still too heavy once downscaled', async () => {
    browser.size = () => 7_500_000;
    [browser.width, browser.height] = [4000, 3000];
    await expect(prepareImage(file('foto.jpg', 'image/jpeg'), 'image/jpeg')).rejects.toMatchObject({
      status: 413,
      message: tooLargeMessage('image'),
    });
  });

  it('refuses an image the browser cannot read', async () => {
    browser.decodeFails = true;
    const err = await prepareImage(file('trencada.png', 'image/png'), 'image/png').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AttachmentError);
    expect(err).toMatchObject({ status: 422, message: "No s'ha pogut llegir la imatge: el fitxer no és vàlid." });
  });

  it('keeps the thumbnail within the server limits, smaller if needed, or makes none', async () => {
    [browser.width, browser.height] = [1000, 1000];
    browser.size = (_type, w) => (w > 200 ? MAX_THUMBNAIL_BYTES + 1 : 40_000);
    const smaller = await prepareImage(file('a.png', 'image/png'), 'image/png');
    expect(smaller.thumbnail).not.toBeNull();
    expect(smaller.thumbnail!.size).toBeLessThanOrEqual(MAX_THUMBNAIL_BYTES);
    expect(browser.encodes.at(-1)!.width).toBeLessThanOrEqual(200);

    browser.size = () => MAX_THUMBNAIL_BYTES + 1;
    const none = await prepareImage(file('b.png', 'image/png'), 'image/png');
    expect(none.thumbnail).toBeNull();
    expect(none.upload.size).toBe(1000); // the upload itself is not affected
    expect(browser.encodes.every((e) => Math.max(e.width, e.height) <= MAX_THUMBNAIL_SIDE)).toBe(true);
  });
});

describe('a photo stored turned (EXIF orientation), as phones take them', () => {
  // The browser shows it upright, and so does its thumbnail; the models get the pixels as
  // stored, without the metadata (Claude does not read it, nor does Codex's decoder). So it
  // is turned upright here, at its size, before its upload.
  /** What was encoded for the upload: a thumbnail's long edge is THUMBNAIL_EDGE at most. */
  const upload = (encodes: Encode[]) => encodes.filter((e) => Math.max(e.width, e.height) > THUMBNAIL_EDGE);

  it('is re-encoded upright at its size (WebP, quality 0.9), never uploaded as it is', async () => {
    [browser.width, browser.height] = [200, 400]; // decoded upright
    const photo = new File([jpeg(400, 200, { segments: [exif(6)] })], 'foto.jpg', { type: 'image/jpeg' });
    const prepared = await prepareImage(photo, 'image/jpeg');
    expect(createImageBitmap).toHaveBeenCalledWith(photo, { imageOrientation: 'from-image' });
    expect(prepared.upload).not.toBe(photo);
    expect(prepared.upload.type).toBe('image/webp');
    expect([prepared.width, prepared.height]).toEqual([200, 400]);
    expect(upload(browser.encodes)).toEqual([{ requested: 'image/webp', type: 'image/webp', quality: 0.9, width: 200, height: 400 }]);
    expect(browser.encodes.at(-1)).toMatchObject({ quality: 0.8, width: 128, height: 256 }); // its thumbnail, upright too
    expect(browser.closed).not.toHaveLength(0);
  });

  it('also half a turn or mirrored, where the size stays (a JPEG where WebP cannot be encoded)', async () => {
    browser.encoders = new Set(['image/png', 'image/jpeg']);
    [browser.width, browser.height] = [400, 200];
    for (const orientation of [2, 3, 4]) {
      browser.encodes.length = 0;
      const photo = new File([jpeg(400, 200, { segments: [jfif, exif(orientation, { littleEndian: true })] })], 'foto.jpg');
      const prepared = await prepareImage(photo, 'image/jpeg');
      expect(prepared.upload.type).toBe('image/jpeg');
      expect(upload(browser.encodes).map((e) => [e.requested, e.type, e.quality, e.width, e.height])).toEqual([
        ['image/webp', 'image/png', 0.9, 400, 200],
        ['image/jpeg', 'image/jpeg', 0.9, 400, 200],
      ]);
    }
  });

  it('any image the browser shows at another size than its header gives (turned by metadata of its own)', async () => {
    [browser.width, browser.height] = [200, 400];
    const shot = new File([png(400, 200)], 'captura.png', { type: 'image/png' });
    const prepared = await prepareImage(shot, 'image/png');
    expect(prepared.upload).not.toBe(shot);
    expect(prepared.upload.type).toBe('image/webp');
    expect([prepared.width, prepared.height]).toEqual([200, 400]);
  });

  it('one stored as it is shown goes as it is, and a GIF always does (its animation)', async () => {
    [browser.width, browser.height] = [400, 200];
    const asShown: [Uint8Array<ArrayBuffer>, string][] = [
      [jpeg(400, 200, { segments: [exif(1)] }), 'image/jpeg'],
      [jpeg(400, 200, { segments: [jfif] }), 'image/jpeg'],
      [png(400, 200), 'image/png'],
      [webpLossless(400, 200), 'image/webp'],
      [gif(200, 400), 'image/gif'],
    ];
    for (const [bytes, mime] of asShown) {
      const image = new File([bytes], 'imatge', { type: mime });
      const prepared = await prepareImage(image, mime);
      expect(prepared.upload, mime).toBe(image);
      expect([prepared.width, prepared.height]).toEqual([400, 200]);
    }
    expect(upload(browser.encodes)).toEqual([]);
  });
});

describe('a PDF before its upload', () => {
  function pageCanvas(width: number, height: number): HTMLCanvasElement {
    const canvas = document.createElement('canvas');
    [canvas.width, canvas.height] = [width, height];
    return canvas;
  }

  it('gets its first page as the thumbnail, and its page count', async () => {
    vi.mocked(renderFirstPage).mockResolvedValue({ canvas: pageCanvas(198, 256), pages: 12 });
    const pdf = new File([new TextEncoder().encode('%PDF-1.7 …')], 'informe.pdf', { type: 'application/pdf' });
    const preview = await pdfPreview(pdf);
    expect(preview.pages).toBe(12);
    expect(preview.thumbnail?.type).toBe('image/webp');
    const [data, edge] = vi.mocked(renderFirstPage).mock.calls[0]!;
    expect(new TextDecoder().decode(data)).toBe('%PDF-1.7 …');
    expect(edge).toBe(THUMBNAIL_EDGE);
  });

  it('one PDF.js cannot render gets no thumbnail (the card shows an icon)', async () => {
    vi.mocked(renderFirstPage).mockRejectedValue(new Error('Invalid PDF structure.'));
    expect(await pdfPreview(file('trencat.pdf', 'application/pdf'))).toEqual({ thumbnail: null, pages: null });
  });

  it('nor one that takes too long', async () => {
    vi.useFakeTimers();
    vi.mocked(renderFirstPage).mockReturnValue(new Promise(() => {}));
    const preview = pdfPreview(file('lent.pdf', 'application/pdf'));
    await vi.advanceTimersByTimeAsync(PDF_PREVIEW_TIMEOUT_MS);
    expect(await preview).toEqual({ thumbnail: null, pages: null });
  });
});

describe('small helpers', () => {
  it('reads the first bytes of a file and makes data URLs', async () => {
    const head = await readHead(new File([new TextEncoder().encode('%PDF-1.7 and more')], 'a.pdf'));
    expect(new TextDecoder().decode(head)).toBe('%PDF-1.7 and mor');
    expect(await blobToDataUrl(new Blob(['hola'], { type: 'text/plain' }))).toBe('data:text/plain;base64,aG9sYQ==');
  });
});
