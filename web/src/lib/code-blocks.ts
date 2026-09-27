// Enhances rendered markdown: a copy button and language label on each code
// block, then lazy syntax highlighting. Buttons are created with DOM APIs
// (no HTML strings) so nothing here goes through the sanitizer.

import { copyText } from './clipboard';

let highlighter: Promise<typeof import('./highlight')> | null = null;

function languageOf(code: Element): string | null {
  return /language-([\w+#-]+)/.exec(code.className)?.[1] ?? null;
}

export function enhanceCodeBlocks(root: HTMLElement): void {
  const blocks = root.querySelectorAll<HTMLPreElement>('pre:not([data-enhanced])');
  if (!blocks.length) return;
  for (const pre of blocks) {
    pre.dataset.enhanced = '';
    const code = pre.querySelector('code');
    const lang = code ? languageOf(code) : null;
    const bar = document.createElement('div');
    bar.className = 'code-bar';
    const label = document.createElement('span');
    label.className = 'code-lang';
    label.textContent = lang ?? 'codi';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'code-copy';
    button.textContent = 'Copia';
    button.setAttribute('aria-label', 'Copia el codi');
    button.addEventListener('click', () => {
      void copyText(code?.textContent ?? pre.textContent ?? '').then((ok) => {
        button.textContent = ok ? 'Copiat' : 'Error';
        button.dataset.state = ok ? 'ok' : 'error';
        setTimeout(() => {
          button.textContent = 'Copia';
          delete button.dataset.state;
        }, 1600);
      });
    });
    bar.append(label, button);
    pre.prepend(bar);
  }
  if (root.querySelector('pre code[class*="language-"]')) {
    highlighter ??= import('./highlight');
    void highlighter.then((m) => m.highlightAll(root)).catch(() => {
      highlighter = null; // chunk failed to load (offline); retry next time
    });
  }
}
