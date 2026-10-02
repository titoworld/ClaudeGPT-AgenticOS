// Enhances rendered markdown: a copy button and language label on each code
// block, then lazy syntax highlighting. Buttons are created with DOM APIs
// (no HTML strings) so nothing here goes through the sanitizer.
// Blocks where renderMarkdown revealed hidden characters (⟨U+XXXX⟩ marks, one
// mark with their count when there were too many, or class invisible-removed
// when the answer had used up its marks) get a warning on the code bar and are
// not highlighted: highlighting would flatten the marks. The copy button copies
// the visible text, marks included.

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
    bar.append(label);
    const removed = code?.classList.contains('invisible-removed') ?? false;
    if (code && (removed || code.querySelector('.invisible-char'))) {
      code.removeAttribute('class'); // keep it out of the highlighter
      if (removed) code.className = 'invisible-removed';
      const warning = document.createElement('span');
      warning.className = 'code-warning';
      warning.textContent = 'Caràcters invisibles';
      const how = removed
        ? "La resposta en tenia massa per marcar-los tots: els d'aquest bloc s'han eliminat."
        : code.querySelector('.invisible-collapsed')
          ? "N'hi havia massa per marcar-los un per un: s'han eliminat i en lloc seu es mostra quants eren."
          : 'Es mostren com a ⟨U+…⟩ i es copien igual.';
      warning.title =
        'Aquest codi conté caràcters invisibles o de control de direcció que poden amagar què fa. ' + how;
      bar.append(warning);
    }
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
