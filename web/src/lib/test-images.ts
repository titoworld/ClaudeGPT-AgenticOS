// Image headers built byte by byte for unit tests (never downloaded, never imported by
// application code), as the server's tests build theirs (tests/attachment_files.py): only
// as real as a header reader needs, since the doubles in the tests do the decoding.

type Bytes = readonly number[] | Uint8Array;

/** The parts, one after the other. */
export function concat(...parts: Bytes[]): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

/** A copy of `data` with `bytes` written at `at`. */
export function patched(data: Uint8Array, at: number, bytes: Bytes): Uint8Array<ArrayBuffer> {
  const copy = concat(data);
  copy.set(bytes, at);
  return copy;
}

export const ascii = (text: string): number[] => [...text].map((char) => char.charCodeAt(0));
const be16 = (n: number): number[] => [(n >>> 8) & 0xff, n & 0xff];
const le16 = (n: number): number[] => [n & 0xff, (n >>> 8) & 0xff];
const be32 = (n: number): number[] => [(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff];
const le32 = (n: number): number[] => [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff];
const le24 = (n: number): number[] => [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff];

/** A PNG signature and its IHDR chunk (the CRC is not checked). */
export const png = (width: number, height: number): Uint8Array<ArrayBuffer> =>
  concat([0x89, ...ascii('PNG'), 0x0d, 0x0a, 0x1a, 0x0a], be32(13), ascii('IHDR'), be32(width), be32(height), [8, 6, 0, 0, 0], be32(0));

export const gif = (width: number, height: number): Uint8Array<ArrayBuffer> =>
  concat(ascii('GIF89a'), le16(width), le16(height), [0, 0, 0], ascii(';'));

/** A JPEG segment: its marker, its length and its payload. */
export const segment = (marker: number, payload: Bytes): Uint8Array<ArrayBuffer> =>
  concat([0xff, marker], be16(payload.length + 2), payload);

export const jfif = segment(0xe0, [...ascii('JFIF'), 0, 1, 1, 0, 0, 1, 0, 1, 0, 0]);
/** XMP metadata: an APP1 segment that is not EXIF. */
export const xmp = segment(0xe1, [...ascii('http://ns.adobe.com/xap/1.0/'), 0, ...ascii('<x:xmpmeta/>')]);

/** An IFD entry: tag, type (3: SHORT, 4: LONG), count, and a value that fits in its four bytes. */
export type IfdEntry = [tag: number, type: number, count: number, value: number];

/**
 * An APP1 segment with EXIF: «Exif», NUL, a fill byte, then a TIFF block (big-endian
 * «MM» or little-endian «II») with one IFD of `entries`; a number gives just its
 * Orientation tag. `ifdOffset` is where the TIFF header says the IFD starts.
 */
export function exif(entries: number | IfdEntry[], { littleEndian = false, fill = 0, ifdOffset = 8 } = {}): Uint8Array<ArrayBuffer> {
  const u16 = littleEndian ? le16 : be16;
  const u32 = littleEndian ? le32 : be32;
  const list: IfdEntry[] = typeof entries === 'number' ? [[0x0112, 3, 1, entries]] : entries;
  // A value of two bytes sits in the first two of the four.
  const ifd = list.flatMap(([tag, type, count, value]) => [...u16(tag), ...u16(type), ...u32(count), ...(type === 3 ? [...u16(value), 0, 0] : u32(value))]);
  const tiff = concat(ascii(littleEndian ? 'II' : 'MM'), u16(42), u32(ifdOffset), u16(list.length), ifd, u32(0));
  return segment(0xe1, concat(ascii('Exif'), [0, fill], tiff));
}

/**
 * A JPEG: SOI, `segments` (APPn...), a fill byte, its frame header (SOF0 unless `sof`),
 * the segments `beforeScan`, a scan and EOI.
 */
export function jpeg(
  width: number,
  height: number,
  { segments = [], sof = 0xc0, beforeScan = [] }: { segments?: Bytes[]; sof?: number; beforeScan?: Bytes[] } = {},
): Uint8Array<ArrayBuffer> {
  const frame = concat([8], be16(height), be16(width), [3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1]);
  return concat(
    [0xff, 0xd8],
    ...segments,
    [0xff], // a fill byte before the next marker
    segment(sof, frame),
    ...beforeScan,
    [0xff, 0xda, 0, 8, 1, 1, 0, 0, 0x3f, 0],
    new Uint8Array(32),
    [0xff, 0xd9],
  );
}

function riff(chunk: string, payload: Bytes): Uint8Array<ArrayBuffer> {
  const body = concat(ascii('WEBP'), ascii(chunk), le32(payload.length), payload);
  return concat(ascii('RIFF'), le32(body.length), body);
}

export const webpLossy = (width: number, height: number): Uint8Array<ArrayBuffer> =>
  riff('VP8 ', concat([0x10, 0x02, 0x00, 0x9d, 0x01, 0x2a], le16(width), le16(height), new Uint8Array(16)));

export const webpLossless = (width: number, height: number): Uint8Array<ArrayBuffer> =>
  riff('VP8L', concat([0x2f], le32((width - 1) | ((height - 1) << 14)), new Uint8Array(16)));

export const webpExtended = (width: number, height: number): Uint8Array<ArrayBuffer> =>
  riff('VP8X', concat([0x10, 0, 0, 0], le24(width - 1), le24(height - 1), new Uint8Array(16)));
