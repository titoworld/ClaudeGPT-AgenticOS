// A line diff of two texts, for what changed between two versions of a refine turn's
// document (docs/adr/0010-mode-perfecciona.md: the owner sees every version's diff, so
// growth is visible). Small on purpose, instead of a dependency: Myers' O(ND) algorithm
// (the fewest lines removed and added) on the lines between a common start and end.
// A round changes at most a few lines, so D is small and a long document stays quick;
// a text rewritten from top to bottom is shown as all removed and all added instead.

export type DiffOp = 'same' | 'add' | 'del';

export interface DiffLine {
  op: DiffOp;
  text: string;
  /** Its number in the text before (from 1); null for an added line. */
  before: number | null;
  /** Its number in the text after (from 1); null for a removed line. */
  after: number | null;
}

/** A part of a diff as the view shows it: lines around the changes, or unchanged lines folded. */
export type DiffPart = { kind: 'lines'; lines: DiffLine[] } | { kind: 'skip'; count: number; lines: DiffLine[] };

/**
 * Edits (lines removed plus added) the search goes up to. Past it, the part between the
 * common start and end is shown as removed and added whole: its memory grows with the
 * square of the edits, and so many changes no longer read as a diff anyway.
 */
const MAX_EDITS = 1000;

/** The lines of a text, with any line end; a line end at the very end adds no empty line. */
function splitLines(text: string): string[] {
  if (!text) return [];
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  if (lines.length > 1 && lines.at(-1) === '') lines.pop();
  return lines;
}

/**
 * The edit script from `a` to `b` (each line an id), or null past `maxEdits`. Myers'
 * forward search keeps, before each step d, the furthest point of every diagonal it can
 * have reached (k from -d-1 to d+1): enough to walk the path back from the end.
 */
function myers(a: Int32Array, b: Int32Array, maxEdits: number): DiffOp[] | null {
  const n = a.length;
  const m = b.length;
  const max = n + m;
  if (max === 0) return [];
  const offset = max + 1;
  const v = new Int32Array(2 * max + 3);
  const trace: Int32Array[] = [];
  for (let d = 0; d <= Math.min(max, maxEdits); d++) {
    trace.push(v.slice(offset - d - 1, offset + d + 2));
    for (let k = -d; k <= d; k += 2) {
      const down = k === -d || (k !== d && v[offset + k - 1]! < v[offset + k + 1]!);
      let x = down ? v[offset + k + 1]! : v[offset + k - 1]! + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x++;
        y++;
      }
      v[offset + k] = x;
      if (x >= n && y >= m) return backtrack(trace, n, m);
    }
  }
  return null;
}

function backtrack(trace: Int32Array[], n: number, m: number): DiffOp[] {
  const ops: DiffOp[] = [];
  let x = n;
  let y = m;
  for (let d = trace.length - 1; d >= 0; d--) {
    const saved = trace[d]!;
    const at = (k: number): number => saved[k + d + 1]!;
    const k = x - y;
    const prevK = k === -d || (k !== d && at(k - 1) < at(k + 1)) ? k + 1 : k - 1;
    const prevX = at(prevK);
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) {
      ops.push('same');
      x--;
      y--;
    }
    if (d > 0) ops.push(x === prevX ? 'add' : 'del');
    x = prevX;
    y = prevY;
  }
  return ops.reverse();
}

/** The lines of `after` against those of `before`: kept, removed and added, in reading order. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = splitLines(before);
  const b = splitLines(after);
  const ids = new Map<string, number>();
  const id = (line: string): number => {
    let known = ids.get(line);
    if (known === undefined) {
      known = ids.size;
      ids.set(line, known);
    }
    return known;
  };
  const ai = Int32Array.from(a, id);
  const bi = Int32Array.from(b, id);

  let start = 0;
  while (start < a.length && start < b.length && ai[start] === bi[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && ai[endA - 1] === bi[endB - 1]) {
    endA--;
    endB--;
  }
  const middle =
    myers(ai.subarray(start, endA), bi.subarray(start, endB), MAX_EDITS) ??
    [...Array<DiffOp>(endA - start).fill('del'), ...Array<DiffOp>(endB - start).fill('add')];
  const ops: DiffOp[] = [...Array<DiffOp>(start).fill('same'), ...middle, ...Array<DiffOp>(a.length - endA).fill('same')];

  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  for (const op of ops) {
    if (op === 'same') {
      lines.push({ op, text: a[i]!, before: i + 1, after: j + 1 });
      i++;
      j++;
    } else if (op === 'del') {
      lines.push({ op, text: a[i]!, before: i + 1, after: null });
      i++;
    } else {
      lines.push({ op, text: b[j]!, before: null, after: j + 1 });
      j++;
    }
  }
  return lines;
}

/** How many lines a diff adds and removes. */
export function diffStats(lines: readonly DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of lines) {
    if (line.op === 'add') added++;
    else if (line.op === 'del') removed++;
  }
  return { added, removed };
}

/**
 * The diff as the view shows it: every change with `context` unchanged lines around it,
 * and the unchanged lines further away folded (a single one is shown instead: folding it
 * saves nothing). Without any change the whole text is one folded part.
 */
export function diffHunks(lines: readonly DiffLine[], context = 2): DiffPart[] {
  if (!lines.length) return [];
  if (lines.every((line) => line.op === 'same')) return [{ kind: 'skip', count: lines.length, lines: [...lines] }];
  const shown = lines.map(() => false);
  lines.forEach((line, i) => {
    if (line.op === 'same') return;
    for (let j = Math.max(0, i - context); j <= Math.min(lines.length - 1, i + context); j++) shown[j] = true;
  });
  for (let i = 0; i < lines.length; i++) {
    if (!shown[i] && (i === 0 || shown[i - 1]) && (i === lines.length - 1 || shown[i + 1])) shown[i] = true;
  }
  const parts: DiffPart[] = [];
  let run: DiffLine[] = [];
  let runShown = shown[0]!;
  const flush = (): void => {
    if (!run.length) return;
    parts.push(runShown ? { kind: 'lines', lines: run } : { kind: 'skip', count: run.length, lines: run });
    run = [];
  };
  lines.forEach((line, i) => {
    if (shown[i] !== runShown) {
      flush();
      runShown = shown[i]!;
    }
    run.push(line);
  });
  flush();
  return parts;
}
