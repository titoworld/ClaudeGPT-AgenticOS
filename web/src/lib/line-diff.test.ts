// The line diff of two versions of a refine turn's document (lib/line-diff.ts): what the
// owner reads as what changed from one version to the next, computed in the browser.
import { describe, expect, it } from 'vitest';
import { diffHunks, diffStats, lineDiff, type DiffLine } from './line-diff';

/** The diff as compact strings: "  same", "- removed", "+ added". */
const show = (lines: DiffLine[]): string[] =>
  lines.map((l) => `${l.op === 'same' ? ' ' : l.op === 'add' ? '+' : '-'} ${l.text}`);

/** The text each side had: the diff must give both back exactly. */
const before = (lines: DiffLine[]) => lines.filter((l) => l.op !== 'add').map((l) => l.text);
const after = (lines: DiffLine[]) => lines.filter((l) => l.op !== 'del').map((l) => l.text);

/** A small deterministic generator, for texts that differ in many places. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe('lineDiff', () => {
  it('keeps every line of identical texts', () => {
    expect(show(lineDiff('# Pla\n\nFase 1', '# Pla\n\nFase 1'))).toEqual(['  # Pla', '  ', '  Fase 1']);
  });

  it('marks a changed line as removed and added, in place', () => {
    const lines = lineDiff('# Pla\n- Fase 1\n- Fase 2\n- Fase 3', '# Pla\n- Fase 1\n- Fase 2 (abans de l’1 de desembre)\n- Fase 3');
    expect(show(lines)).toEqual(['  # Pla', '  - Fase 1', '- - Fase 2', '+ - Fase 2 (abans de l’1 de desembre)', '  - Fase 3']);
  });

  it('numbers the lines of each side from 1', () => {
    const lines = lineDiff('a\nb\nc', 'a\nc\nd');
    expect(lines.map((l) => [l.op, l.before, l.after])).toEqual([
      ['same', 1, 1],
      ['del', 2, null],
      ['same', 3, 2],
      ['add', null, 3],
    ]);
  });

  it('gives a whole new text as added and a removed one as removed', () => {
    expect(show(lineDiff('', 'a\nb'))).toEqual(['+ a', '+ b']);
    expect(show(lineDiff('a\nb', ''))).toEqual(['- a', '- b']);
    expect(lineDiff('', '')).toEqual([]);
  });

  it('finds the fewest changes (the classic example: 5 edits)', () => {
    const a = [...'ABCABBA'].join('\n');
    const b = [...'CBABAC'].join('\n');
    const lines = lineDiff(a, b);
    expect(lines.filter((l) => l.op !== 'same')).toHaveLength(5);
    expect(before(lines)).toEqual([...'ABCABBA']);
    expect(after(lines)).toEqual([...'CBABAC']);
  });

  it('reads Windows and old Mac line ends as line ends', () => {
    expect(show(lineDiff('a\r\nb\r\nc', 'a\nb\rc'))).toEqual(['  a', '  b', '  c']);
  });

  it('always gives back both texts, whatever they are', () => {
    const random = rng(42);
    const pick = (n: number) => Array.from({ length: n }, () => 'abcde'[Math.floor(random() * 5)]!);
    for (let i = 0; i < 200; i++) {
      const a = pick(Math.floor(random() * 30));
      const b = pick(Math.floor(random() * 30));
      const lines = lineDiff(a.join('\n'), b.join('\n'));
      expect(before(lines)).toEqual(a.length ? a : []);
      expect(after(lines)).toEqual(b.length ? b : []);
    }
  });

  it('gives up looking for the fewest changes in a text rewritten from top to bottom, but still gives both back', () => {
    const a = Array.from({ length: 3000 }, (_, i) => `línia ${i}`);
    const b = Array.from({ length: 3000 }, (_, i) => `altra ${i}`);
    const t0 = performance.now();
    const lines = lineDiff(a.join('\n'), b.join('\n'));
    expect(performance.now() - t0).toBeLessThan(2000);
    expect(before(lines)).toEqual(a);
    expect(after(lines)).toEqual(b);
  });

  it('is quick for a long document with a few changes', () => {
    const a = Array.from({ length: 5000 }, (_, i) => `Paràgraf ${i} del document.`);
    const b = [...a];
    b[10] = 'Un paràgraf nou al principi.';
    b.splice(2500, 1);
    b.push('Una conclusió.');
    const t0 = performance.now();
    const lines = lineDiff(a.join('\n'), b.join('\n'));
    expect(performance.now() - t0).toBeLessThan(500);
    expect(diffStats(lines)).toEqual({ added: 2, removed: 2 });
  });
});

describe('diffHunks: the changes with a little context', () => {
  const doc = Array.from({ length: 20 }, (_, i) => `l${i + 1}`);

  it('folds the unchanged lines far from any change', () => {
    const changed = [...doc];
    changed[9] = 'L10';
    const parts = diffHunks(lineDiff(doc.join('\n'), changed.join('\n')), 2);
    expect(parts.map((p) => (p.kind === 'skip' ? `… ${p.count}` : show(p.lines).join(' | ')))).toEqual([
      '… 7',
      '  l8 |   l9 | - l10 | + L10 |   l11 |   l12',
      '… 8',
    ]);
  });

  it('keeps two changes close to each other in one part', () => {
    const changed = [...doc];
    changed[4] = 'L5';
    changed[8] = 'L9';
    const parts = diffHunks(lineDiff(doc.join('\n'), changed.join('\n')), 2);
    expect(parts.map((p) => p.kind)).toEqual(['skip', 'lines', 'skip']);
  });

  it('does not fold a single unchanged line', () => {
    const changed = [...doc];
    changed[2] = 'L3';
    changed[8] = 'L9';
    // l4..l8 between the changes is 5 lines: 2 of context each side and one left, shown as is.
    const parts = diffHunks(lineDiff(doc.join('\n'), changed.join('\n')), 2);
    expect(parts.map((p) => p.kind)).toEqual(['lines', 'skip']);
  });

  it('folds the whole text when nothing changed, and keeps the folded lines to show them', () => {
    const [part, ...rest] = diffHunks(lineDiff('a\nb', 'a\nb'), 2);
    expect(rest).toEqual([]);
    expect(part?.kind === 'skip' && [part.count, show(part.lines)]).toEqual([2, ['  a', '  b']]);
    expect(diffHunks([], 2)).toEqual([]);
  });
});
