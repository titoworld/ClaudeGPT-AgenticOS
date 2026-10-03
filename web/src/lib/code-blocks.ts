// Enhances rendered markdown: a copy button and language label on each code
// block, then lazy syntax highlighting. Buttons are created with DOM APIs
// (no HTML strings) so nothing here goes through the sanitizer.
// Blocks where renderMarkdown revealed hidden characters (⟨U+XXXX⟩ marks, one
// mark with their count when there were too many, or class invisible-removed
// when the answer had used up its marks) get a warning on the code bar and are
// not highlighted: highlighting would flatten the marks. The copy button copies
// the visible text, marks included. The bar's texts are in the language in force when
// it is made (Markdown.svelte makes it again when the language changes).

import { copyText } from './clipboard';
import { i18n } from './i18n/index.svelte';

let highlighter: Promise<typeof import('./highlight')> | null = null;

function languageOf(code: Element): string | null {
  return /language-([\w+#-]+)/.exec(code.className)?.[1] ?? null;
}

export function enhanceCodeBlocks(root: HTMLElement): void {
  const blocks = root.querySelectorAll<HTMLPreElement>('pre:not([data-enhanced])');
  if (!blocks.length) return;
  const texts = i18n.m.turn.code;
  for (const pre of blocks) {
    pre.dataset.enhanced = '';
    const code = pre.querySelector('code');
    const lang = code ? languageOf(code) : null;
    const bar = document.createElement('div');
    bar.className = 'code-bar';
    const label = document.createElement('span');
    label.className = 'code-lang';
    label.textContent = lang ?? texts.code;
    bar.append(label);
    const removed = code?.classList.contains('invisible-removed') ?? false;
    if (code && (removed || code.querySelector('.invisible-char'))) {
      code.removeAttribute('class'); // keep it out of the highlighter
      if (removed) code.className = 'invisible-removed';
      const warning = document.createElement('span');
      warning.className = 'code-warning';
      warning.textContent = texts.hidden;
      const how = removed ? texts.removed : code.querySelector('.invisible-collapsed') ? texts.collapsed : texts.marked;
      warning.title = texts.hiddenTitle(how);
      bar.append(warning);
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'code-copy';
    button.textContent = texts.copy;
    button.setAttribute('aria-label', texts.copyCode);
    button.addEventListener('click', () => {
      void copyText(code?.textContent ?? pre.textContent ?? '').then((ok) => {
        const now = i18n.m.turn.code;
        button.textContent = ok ? now.copied : now.error;
        button.dataset.state = ok ? 'ok' : 'error';
        setTimeout(() => {
          button.textContent = i18n.m.turn.code.copy;
          delete button.dataset.state;
        }, 1600);
      });
    });
    bar.append(button);
    pre.prepend(bar);
  }
  if (root.querySelector('pre code[class*="language-"]')) {
    highlighter ??= import('./highlight');
    void highlighter.then((m) => m.highlightAll(root)).catch(() => {
      highlighter = null; // chunk failed to load (offline); retry next time
    });
  }
}
