// The header of an image as it is stored, read in the browser before its upload
// (docs/PROTOCOL.md «Adjunts»): the size the server reads (src/agentic_os/attachments.py)
// and a JPEG's EXIF orientation, as the browser reads it. The browser shows a photo as its
// orientation says, but the models get the pixels as stored: lib/media.ts turns an image
// stored otherwise than it is shown upright before uploading it.
import { describe, expect, it } from 'vitest';
import { readImageHeader, storedUpright } from './image-header';
import {
  ascii,
  concat,
  exif,
  gif,
  jfif,
  jpeg,
  patched,
  png,
  segment,
  webpExtended,
  webpLossless,
  webpLossy,
  xmp,
} from './test-images';

/** An APPn segment of about 60 kB (an ICC profile, a preview...). */
const large = (marker: number, fill: number) => segment(marker, new Uint8Array(60_000).fill(fill));

describe('the size an image is stored at, read from its header as the server reads it', () => {
  it.each([
    ['a PNG', png(640, 480), 'image/png', [640, 480]],
    ['a PNG of 8.000 x 1', png(8000, 1), 'image/png', [8000, 1]],
    ['a JPEG', jpeg(4032, 3024), 'image/jpeg', [4032, 3024]],
    ['a JPEG with its frame after 180 kB of segments', jpeg(300, 200, { segments: [jfif, large(0xe2, 1), large(0xe2, 2), large(0xed, 0xff)] }), 'image/jpeg', [300, 200]],
    ['a progressive JPEG', jpeg(1200, 800, { sof: 0xc2 }), 'image/jpeg', [1200, 800]],
    ['a lossy WebP', webpLossy(1024, 768), 'image/webp', [1024, 768]],
    ['a lossless WebP', webpLossless(16383, 2), 'image/webp', [16383, 2]],
    ['an extended WebP (its canvas)', webpExtended(2576, 1449), 'image/webp', [2576, 1449]],
  ] as const)('%s', (_label, data, mime, [width, height]) => {
    expect(readImageHeader(data, mime).size).toEqual({ width, height });
  });

  it.each([
    ['a PNG with a side of 0', png(0, 10), 'image/png'],
    ['a truncated IHDR', png(10, 10).subarray(0, 20), 'image/png'],
    ['a PNG without its IHDR first', concat([0x89, ...ascii('PNG'), 0x0d, 0x0a, 0x1a, 0x0a], new Uint8Array(16)), 'image/png'],
    ['a JPEG whose height comes later (DNL)', jpeg(10, 0), 'image/jpeg'],
    ['a JPEG without a frame', concat([0xff, 0xd8], jfif, new Uint8Array(10)), 'image/jpeg'],
    ['a JPEG with a scan before its frame', concat([0xff, 0xd8, 0xff, 0xda, 0, 8], new Uint8Array(10)), 'image/jpeg'],
    ['a JPEG whose only C4 segment is a Huffman table, not a frame', concat([0xff, 0xd8], segment(0xc4, new Uint8Array(20))), 'image/jpeg'],
    ['a lossy WebP without its start code', patched(webpLossy(10, 10), 23, [0, 0, 0]), 'image/webp'],
    ['a truncated lossless WebP', webpLossless(10, 10).subarray(0, 24), 'image/webp'],
    ['a lossless WebP without its signature', patched(webpLossless(10, 10), 20, [0]), 'image/webp'],
    ['a WebP with another first chunk', patched(webpLossy(10, 10), 12, ascii('ALPH')), 'image/webp'],
    ['a GIF (never re-encoded, so not read)', gif(320, 240), 'image/gif'],
  ] as const)('none for %s', (_label, data, mime) => {
    expect(readImageHeader(data, mime).size).toBeNull();
  });
});

describe("a JPEG's EXIF orientation, read as the browser reads it", () => {
  const orientation = (...segments: Uint8Array[]) => readImageHeader(jpeg(400, 200, { segments }), 'image/jpeg').orientation;

  it.each([1, 2, 3, 4, 5, 6, 7, 8])('%i, big-endian (MM) or little-endian (II)', (value) => {
    expect(orientation(exif(value))).toBe(value);
    expect(orientation(jfif, exif(value, { littleEndian: true }))).toBe(value);
  });

  it('1 without EXIF, or with other metadata in its APP1 (XMP)', () => {
    expect(orientation()).toBe(1);
    expect(orientation(jfif)).toBe(1);
    expect(orientation(xmp)).toBe(1);
  });

  it('the first EXIF with the tag counts, after other tags and segments', () => {
    const make: [number, number, number, number] = [0x010f, 2, 6, 0x26];
    expect(orientation(exif([make, [0x0112, 3, 1, 8]]))).toBe(8);
    expect(orientation(xmp, exif([make]), exif(6), exif(3))).toBe(6);
    // An EXIF segment after the frame header still comes before the image data.
    expect(readImageHeader(jpeg(400, 200, { beforeScan: [exif(5)] }), 'image/jpeg')).toEqual({
      size: { width: 400, height: 200 },
      orientation: 5,
    });
    // «Exif», NUL and then any fill byte.
    expect(orientation(exif(6, { fill: 0xff }))).toBe(6);
  });

  it('1 for a value out of range, or a tag that is not one SHORT', () => {
    expect(orientation(exif(0))).toBe(1);
    expect(orientation(exif(9))).toBe(1);
    expect(orientation(exif([[0x0112, 4, 1, 6]]))).toBe(1); // a LONG
    expect(orientation(exif([[0x0112, 3, 2, 6]]))).toBe(1); // two values
  });

  it('1 for broken EXIF: another magic number or byte order, an IFD past its end, cut short', () => {
    const tiff = 4 + 6; // after the marker, the length and «Exif\0\0»
    expect(orientation(patched(exif(6), tiff, ascii('XX')))).toBe(1);
    expect(orientation(patched(exif(6), tiff + 2, [0, 43]))).toBe(1);
    expect(orientation(exif(6, { ifdOffset: 500 }))).toBe(1);
    expect(readImageHeader(concat([0xff, 0xd8], exif(6).subarray(0, 24)), 'image/jpeg').orientation).toBe(1);
  });

  it('none after the scan: that is image data', () => {
    const photo = jpeg(400, 200);
    const trailing = concat(photo.subarray(0, -2), exif(6), [0xff, 0xd9]);
    expect(readImageHeader(trailing, 'image/jpeg')).toEqual({ size: { width: 400, height: 200 }, orientation: 1 });
  });

  it('is read only for a JPEG', () => {
    expect(readImageHeader(concat(png(400, 200), exif(6)), 'image/png').orientation).toBe(1);
  });

  it('gives hostile data up quickly', () => {
    const markers = new Uint8Array(7_000_002);
    for (let i = 0; i < markers.length; i += 2) [markers[i], markers[i + 1]] = [0xff, 0x01]; // millions of markers
    const fill = new Uint8Array(7_000_002).fill(0xff); // one endless run of fill bytes
    const none = new Uint8Array(7_000_002); // no marker at all
    for (const data of [markers, fill, none]) {
      data.set([0xff, 0xd8]);
      const started = performance.now();
      expect(readImageHeader(data, 'image/jpeg')).toEqual({ size: null, orientation: 1 });
      expect(performance.now() - started).toBeLessThan(1000);
    }
  });
});

describe('an image stored as the browser shows it (decoded upright)', () => {
  it.each([
    ['a photo turned a quarter by its EXIF (6), decoded upright', jpeg(400, 200, { segments: [exif(6)] }), 'image/jpeg', [200, 400], false],
    ['a photo turned half a turn by its EXIF (3): same size', jpeg(400, 200, { segments: [exif(3)] }), 'image/jpeg', [400, 200], false],
    ['a photo mirrored by its EXIF (2): same size', jpeg(400, 200, { segments: [exif(2)] }), 'image/jpeg', [400, 200], false],
    ['a photo as stored (EXIF 1)', jpeg(400, 200, { segments: [exif(1)] }), 'image/jpeg', [400, 200], true],
    ['a photo without EXIF', jpeg(400, 200, { segments: [jfif] }), 'image/jpeg', [400, 200], true],
    ['a photo decoded at another size than its header gives', jpeg(400, 200), 'image/jpeg', [200, 400], false],
    ['a PNG decoded at its size', png(400, 200), 'image/png', [400, 200], true],
    ['a PNG the browser turned (metadata of its own)', png(400, 200), 'image/png', [200, 400], false],
    ['a WebP decoded at its size', webpExtended(400, 200), 'image/webp', [400, 200], true],
    ['a WebP the browser turned', webpLossy(400, 200), 'image/webp', [200, 400], false],
    ['a header that cannot be read (the server reads it, or refuses the image)', new Uint8Array(64), 'image/png', [400, 200], true],
  ] as const)('%s', (_label, data, mime, [width, height], upright) => {
    expect(storedUpright(data, mime, { width, height })).toBe(upright);
  });
});
