"use strict";
// Wire types shared with the backend. Source of truth: docs/PROTOCOL.md.
// Keep in sync with src/agentic_os/orchestrator/events.py and web/ routes.
Object.defineProperty(exports, "__esModule", { value: true });
exports.BACKGROUND_HEADER = exports.WS_CLOSE_FORBIDDEN_ORIGIN = exports.WS_CLOSE_UNAUTHORIZED = exports.TEXT_EXTENSIONS = exports.MAX_THUMBNAIL_SIDE = exports.MAX_THUMBNAIL_BYTES = exports.MAX_TEXT_BYTES = exports.MAX_PDF_PAGES = exports.MAX_PDF_BYTES = exports.DOWNSCALE_EDGE = exports.MAX_IMAGE_SIDE = exports.MAX_IMAGE_BYTES = exports.MAX_TURN_ATTACHMENT_BYTES = exports.MAX_ATTACHMENTS = exports.CONVERSATION_QUERY_MAX_LENGTH = exports.MODEL_ID_PATTERN = exports.TURN_MODES = exports.AGENTS = void 0;
exports.AGENTS = ['claude', 'chatgpt'];
exports.TURN_MODES = ['solo', 'duel', 'debate', 'refine'];
/** Any id matching this pattern is accepted, so new models work before they are listed. */
exports.MODEL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/@[\]-]{0,99}$/;
/**
 * GET /api/conversations?q=…: the titles that contain the text, ignoring case and
 * accents (literally: `%` and `_` are plain characters), paged with `limit` and
 * `before` like the whole list. The text is trimmed: a blank one is no search, and
 * one longer than this many characters gets 422.
 */
exports.CONVERSATION_QUERY_MAX_LENGTH = 200;
/** Attachments of one message. */
exports.MAX_ATTACHMENTS = 5;
/** Raw bytes of all the attachments of one message. */
exports.MAX_TURN_ATTACHMENT_BYTES = 20_000_000;
exports.MAX_IMAGE_BYTES = 7_000_000;
/** Pixels per side of an image. */
exports.MAX_IMAGE_SIDE = 8000;
/**
 * The models see an image with its long edge at most this long: the browser downscales
 * a larger one before uploading it (same fidelity for the models, far less upload).
 */
exports.DOWNSCALE_EDGE = 2576;
exports.MAX_PDF_BYTES = 20_000_000;
exports.MAX_PDF_PAGES = 100;
exports.MAX_TEXT_BYTES = 200_000;
/** A thumbnail: a PNG or WebP of at most these bytes and pixels per side. */
exports.MAX_THUMBNAIL_BYTES = 100_000;
exports.MAX_THUMBNAIL_SIDE = 512;
/** Extensions of the text files accepted (their content must be UTF-8 without NUL). */
exports.TEXT_EXTENSIONS = [
    'txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'yaml', 'yml', 'xml', 'html', 'htm', 'log', 'ini', 'toml', 'cfg',
    'py', 'js', 'ts', 'jsx', 'tsx', 'svelte', 'css', 'scss', 'sql', 'sh', 'bash', 'rs', 'go', 'java', 'kt', 'c', 'h',
    'cpp', 'hpp', 'cs', 'rb', 'php', 'swift', 'lua', 'r', 'pl',
];
/** WebSocket close codes used by the server. */
exports.WS_CLOSE_UNAUTHORIZED = 4401;
exports.WS_CLOSE_FORBIDDEN_ORIGIN = 4403;
// ---------------------------------------------------------------- sessions
/**
 * Request header (value "1") of the REST requests the app makes by itself, not because
 * the owner did something: refreshes after `hello` or a reconnection, periodic
 * refreshes, retries. The server checks the session without counting them as activity,
 * like a WebSocket ping, so an unused tab does not keep the session alive. Every
 * POST /api/auth/logout carries it too: a logout that fails must not extend the session.
 */
exports.BACKGROUND_HEADER = 'X-AOS-Background';
