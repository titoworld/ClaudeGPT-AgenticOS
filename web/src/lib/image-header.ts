// The header of an image as it is stored, read in the browser before its upload
// (lib/media.ts, docs/PROTOCOL.md «Adjunts»):
//
// - the size its pixels are stored at, read as the server reads it
//   (src/agentic_os/attachments.py: PNG IHDR, JPEG SOF, WebP VP8, VP8L or VP8X);
// - a JPEG's EXIF orientation, read as the browser reads it: the first APP1 «Exif»
//   segment with the tag, before the scan.
//
// The browser shows a photo as its orientation says, but the models get its pixels as
// they are stored: they read no metadata. An image stored otherwise than it is shown is
// therefore turned upright before its upload. A GIF is never re-encoded (it would lose
// its animation), so its header is not read here.

export interface Size {
  width: number;
  height: number;
}

export interface ImageHeader {
  /** The size its pixels are stored at; null when it cannot be read (or a side is 0). */
  size: Size | null;
  /** Its EXIF orientation, 1 to 8 (1: shown as stored). Only a JPEG's is read. */
  orientation: number;
}

const bytesOf = (data: Uint8Array): DataView => new DataView(data.buffer, data.byteOffset, data.byteLength);

const positive = (size: Size | null): Size | null => (size && size.width > 0 && size.height > 0 ? size : null);

// ------------------------------------------------------------ JPEG

/** Start-of-frame markers (not DHT C4, JPG C8 or DAC CC, which share the range). */
const JPEG_SOF = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
/** Markers without a length: TEM, SOI and the restart markers. */
const JPEG_STANDALONE = new Set([0x01, 0xd8, 0xd0, 0xd1, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7]);
const JPEG_APP1 = 0xe1;
const JPEG_SOS = 0xda;
const JPEG_EOI = 0xd9;
/**
 * A real JPEG has a few dozen segments before its scan: past this many markers the file
 * is not read any further (it is not a valid image).
 */
const JPEG_MAX_MARKERS = 1000;

/** «Exif» and NUL; then a fill byte, and the TIFF block. */
const EXIF_SIGNATURE = [0x45, 0x78, 0x69, 0x66, 0x00];
const TIFF_START = 6;
const TIFF_LITTLE_ENDIAN = 0x4949; // «II»
const TIFF_BIG_ENDIAN = 0x4d4d; // «MM»
const TIFF_MAGIC = 42;
const IFD_ENTRY_BYTES = 12;
const ORIENTATION_TAG = 0x0112;
const SHORT = 3;

/**
 * The orientation in an APP1 segment's payload, 1 to 8 (a value out of range counts as
 * 1), or null when the payload is not EXIF or its first IFD has no Orientation tag (as
 * one SHORT): a later segment may have it.
 */
function exifOrientation(payload: Uint8Array): number | null {
  if (payload.length < TIFF_START + 8 || EXIF_SIGNATURE.some((byte, i) => payload[i] !== byte)) return null;
  const tiff = bytesOf(payload.subarray(TIFF_START));
  const order = tiff.getUint16(0);
  if (order !== TIFF_LITTLE_ENDIAN && order !== TIFF_BIG_ENDIAN) return null;
  const little = order === TIFF_LITTLE_ENDIAN;
  if (tiff.getUint16(2, little) !== TIFF_MAGIC) return null;
  const ifd = tiff.getUint32(4, little);
  if (ifd + 2 > tiff.byteLength) return null;
  const entries = tiff.getUint16(ifd, little);
  for (let i = 0, at = ifd + 2; i < entries && at + IFD_ENTRY_BYTES <= tiff.byteLength; i++, at += IFD_ENTRY_BYTES) {
    if (tiff.getUint16(at, little) !== ORIENTATION_TAG) continue;
    if (tiff.getUint16(at + 2, little) !== SHORT || tiff.getUint32(at + 4, little) !== 1) continue;
    const value = tiff.getUint16(at + 8, little); // a SHORT sits in the first two bytes of the four
    return value >= 1 && value <= 8 ? value : 1;
  }
  return null;
}

/** A JPEG's frame size (its first frame header's) and EXIF orientation, from its segments before the scan. */
function readJpeg(data: Uint8Array): ImageHeader {
  const bytes = bytesOf(data);
  const end = data.length;
  let frame: Size | undefined; // until its frame header is read
  let orientation: number | null = null;
  let index = 2;
  for (let markers = 0; markers < JPEG_MAX_MARKERS && (frame === undefined || orientation === null); markers++) {
    index = data.indexOf(0xff, index); // stray bytes between segments are skipped
    if (index < 0) break;
    while (index < end && data[index] === 0xff) index++; // the marker's 0xFF and the fill bytes before it
    if (index >= end) break;
    const marker = data[index++]!;
    if (JPEG_STANDALONE.has(marker)) continue;
    if (marker === JPEG_EOI || marker === JPEG_SOS || index + 2 > end) break; // what follows the scan is image data
    const length = bytes.getUint16(index);
    if (length < 2) break;
    if (frame === undefined && JPEG_SOF.has(marker)) {
      if (length < 7 || index + 7 > end) break;
      frame = { width: bytes.getUint16(index + 5), height: bytes.getUint16(index + 3) };
    } else if (orientation === null && marker === JPEG_APP1) {
      orientation = exifOrientation(data.subarray(index + 2, index + length));
    }
    index += length;
  }
  return { size: positive(frame ?? null), orientation: orientation ?? 1 };
}

// ------------------------------------------------------------ PNG and WebP

/** The IHDR chunk's length (13) and type, first after the signature. */
const PNG_IHDR = [0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52];

function pngSize(data: Uint8Array): Size | null {
  if (data.length < 24 || PNG_IHDR.some((byte, i) => data[8 + i] !== byte)) return null;
  const bytes = bytesOf(data);
  return { width: bytes.getUint32(16), height: bytes.getUint32(20) };
}

const uint24 = (data: Uint8Array, at: number): number => data[at]! | (data[at + 1]! << 8) | (data[at + 2]! << 16);

function webpSize(data: Uint8Array): Size | null {
  if (data.length < 30) return null;
  const bytes = bytesOf(data);
  const chunk = String.fromCharCode(...data.subarray(12, 16));
  if (chunk === 'VP8 ') {
    // Lossy: a key frame's header, after its start code.
    if (data[23] !== 0x9d || data[24] !== 0x01 || data[25] !== 0x2a) return null;
    return { width: bytes.getUint16(26, true) & 0x3fff, height: bytes.getUint16(28, true) & 0x3fff };
  }
  if (chunk === 'VP8L') {
    // Lossless: after its signature, 14 bits each.
    if (data[20] !== 0x2f) return null;
    const bits = bytes.getUint32(21, true);
    return { width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1 };
  }
  // Extended: the canvas.
  if (chunk === 'VP8X') return { width: uint24(data, 24) + 1, height: uint24(data, 27) + 1 };
  return null;
}

// ------------------------------------------------------------ the header

/** What the header of a PNG, JPEG or WebP (`mime`, from its content) says. */
export function readImageHeader(data: Uint8Array, mime: string): ImageHeader {
  if (mime === 'image/jpeg') return readJpeg(data);
  const size = mime === 'image/png' ? pngSize(data) : mime === 'image/webp' ? webpSize(data) : null;
  return { size: positive(size), orientation: 1 };
}

/**
 * Whether an image's pixels are stored as the browser shows them, `decoded` being its size
 * decoded as shown (upright): not for a JPEG with an EXIF orientation, nor for an image
 * decoded at another size than its header gives (turned by metadata of its own). A header
 * that cannot be read says nothing: the server reads it, or refuses the image.
 */
export function storedUpright(data: Uint8Array, mime: string, decoded: Size): boolean {
  const { size, orientation } = readImageHeader(data, mime);
  if (orientation !== 1) return false;
  return size === null || (size.width === decoded.width && size.height === decoded.height);
}
