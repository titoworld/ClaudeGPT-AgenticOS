// Hidden characters in model output (Trojan Source, CVE-2021-42574, and Unicode
// tag smuggling): which ones are revealed, and the ⟨U+XXXX⟩ marks that reveal
// them, both on screen (renderMarkdown, and PlainText.svelte for model text shown
// outside Markdown) and on the clipboard (copy buttons).
//
// - Inside code every bidi control and invisible character is revealed. In prose
//   they are too, except what legitimate text needs: the directional marks
//   (LRM/RLM/ALM, which do not reorder like embeddings or overrides) and a joiner
//   or soft hyphen next to a letter (ZWNJ/ZWJ in Persian or Indic words). A run of
//   joiners, as used to smuggle data, is revealed from its second character on.
//   So even where the Markdown scanner below and marked disagree about what is
//   code, nothing that can hide or reorder text reaches the clipboard unmarked.
// - A block with too many of them (or an answer past the overall budget) does not
//   get one mark each: they are removed and one mark with the count stands in for
//   them, so a smuggled payload cannot turn into megabytes of spans.
//
// Standalone on purpose, with no imports: markdown.ts uses it for the screen and
// the answer's copy button for the clipboard, without either one pulling in the
// other (the entry chunk, with the login screen, needs neither). No regex
// lookbehind: Safari before 16.4 throws a SyntaxError when a module with one loads.

/** Explicit bidi embeddings, overrides and isolates: they reorder what is shown. */
const BIDI_CONTROLS = String.raw`\u202A-\u202E\u2066-\u2069`;
// Zero-width and invisible characters: the listed Trojan Source set plus the
// Arabic letter mark, invisible math operators, the Mongolian vowel separator
// and Unicode tags (used to smuggle hidden instructions to other models).
const INVISIBLE = String.raw`\u00AD\u061C\u180E\u200B-\u200F\u2060-\u2064\uFEFF\u{E0000}-\u{E007F}`;
// Legitimate uses, kept as they are: a joiner between two emoji, matched together
// with the emoji before it (and its optional VS16) instead of a lookbehind, and
// subdivision flags (black flag, 3-6 lowercase/digit tags, cancel tag).
const EMOJI_SEQUENCES =
  String.raw`[\p{Extended_Pictographic}\p{Emoji_Modifier}]\uFE0F?\u200D(?=\p{Extended_Pictographic})` +
  String.raw`|\u{1F3F4}[\u{E0030}-\u{E0039}\u{E0061}-\u{E007A}]{3,6}\u{E007F}`;

/** Hidden characters, in capture group 1 (emoji sequences match without it). */
const HIDDEN = new RegExp(`${EMOJI_SEQUENCES}|([${BIDI_CONTROLS}${INVISIBLE}])`, 'gu');
const MAYBE_HIDDEN = new RegExp(`[${BIDI_CONTROLS}${INVISIBLE}]`, 'u');
/** Always kept in prose: directional marks. */
const DIRECTION_MARKS = new Set(['\u061C', '\u200E', '\u200F']);
/** Kept in prose next to a letter: joiners and the soft hyphen. */
const LETTER_JOINERS = new Set(['\u00AD', '\u200C', '\u200D']);
const LETTER = /[\p{L}\p{M}\p{N}]/u;

function charBefore(text: string, index: number): string {
  const low = text.charCodeAt(index - 1);
  const start = low >= 0xdc00 && low <= 0xdfff && index >= 2 ? index - 2 : index - 1;
  return start < 0 ? '' : String.fromCodePoint(text.codePointAt(start) ?? 0);
}

function charAfter(text: string, index: number): string {
  const cp = text.codePointAt(index);
  return cp === undefined ? '' : String.fromCodePoint(cp);
}

/** The hidden character of a match to reveal, or undefined (emoji, kept mark or joiner). */
function hiddenOf(text: string, m: RegExpMatchArray, code: boolean): string | undefined {
  const hidden = m[1];
  if (hidden === undefined || code) return hidden;
  if (DIRECTION_MARKS.has(hidden)) return undefined;
  if (!LETTER_JOINERS.has(hidden)) return hidden;
  const index = m.index ?? 0;
  const byLetter = LETTER.test(charBefore(text, index)) || LETTER.test(charAfter(text, index + hidden.length));
  return byLetter ? undefined : hidden;
}

/** A block with more hidden characters than this shows one mark with their count. */
export const MAX_MARKS_PER_BLOCK = 200;
/** Marks one text (a whole answer) may show; past it, blocks are collapsed too. */
export const MAX_MARKS = 2000;

const codePoint = (char: string): string =>
  `U+${(char.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, '0')}`;

/** A mark that reveals hidden characters: one ⟨U+XXXX⟩, or the count of a collapsed block. */
export interface HiddenMark {
  text: string;
  /** Explanation for the tooltip. */
  title: string;
  /** Stands in for all the hidden characters of a block that had too many. */
  collapsed: boolean;
}

function markFor(char: string): HiddenMark {
  const cp = codePoint(char);
  return { text: `⟨${cp}⟩`, title: `Caràcter invisible o de control de direcció (${cp})`, collapsed: false };
}

function collapsedMark(count: number): HiddenMark {
  const what = count === 1 ? '1 caràcter invisible eliminat' : `${count} caràcters invisibles eliminats`;
  return {
    text: `⟨${what}⟩`,
    title:
      'Hi havia massa caràcters invisibles o de control de direcció per marcar-los un per un: ' +
      `${count === 1 ? "se n'ha eliminat 1" : `se n'han eliminat ${count}`} d'aquest bloc, que tampoc no es copien.`,
    collapsed: true,
  };
}

/** A piece of text and the block it belongs to (a code element or span, a paragraph). */
interface Piece<B> {
  text: string;
  code: boolean;
  block: B;
}

interface BlockPlan {
  count: number;
  collapsed: boolean;
  /** Whether a collapsed block still shows its one mark (false once it is shown). */
  badge: boolean;
  /** Collapsed past the budget: its characters go without any mark. */
  silent: boolean;
}

function countHidden(text: string, code: boolean): number {
  if (!MAYBE_HIDDEN.test(text)) return 0;
  let n = 0;
  for (const m of text.matchAll(HIDDEN)) if (hiddenOf(text, m, code) !== undefined) n++;
  return n;
}

/**
 * Decides which blocks show one mark per hidden character, in the order of
 * their first one (the same on screen and in the Markdown source); blocks
 * without any have no plan.
 */
function planBlocks<B>(pieces: readonly Piece<B>[]): Map<B, BlockPlan> {
  const plans = new Map<B, BlockPlan>();
  for (const p of pieces) {
    const n = countHidden(p.text, p.code);
    if (!n) continue;
    const plan = plans.get(p.block);
    if (plan) plan.count += n;
    else plans.set(p.block, { count: n, collapsed: false, badge: false, silent: false });
  }
  let used = 0;
  for (const plan of plans.values()) {
    if (plan.count <= MAX_MARKS_PER_BLOCK && used + plan.count <= MAX_MARKS) {
      used += plan.count;
      continue;
    }
    plan.collapsed = true;
    plan.badge = used < MAX_MARKS;
    plan.silent = !plan.badge;
    if (plan.badge) used++;
  }
  return plans;
}

/** The text of a piece split around its marks, or null when nothing in it changes. */
function revealParts(text: string, code: boolean, plan: BlockPlan | undefined): (string | HiddenMark)[] | null {
  if (!plan || !MAYBE_HIDDEN.test(text)) return null;
  const parts: (string | HiddenMark)[] = [];
  let last = 0;
  for (const m of text.matchAll(HIDDEN)) {
    const hidden = hiddenOf(text, m, code);
    if (hidden === undefined) continue; // an emoji sequence or a joiner between letters
    if (m.index > last) parts.push(text.slice(last, m.index));
    if (!plan.collapsed) parts.push(markFor(hidden));
    else if (plan.badge) {
      plan.badge = false; // one mark for the whole block, where its first character was
      parts.push(collapsedMark(plan.count));
    }
    last = m.index + hidden.length;
  }
  if (last === 0) return null;
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export interface Revealed {
  text: string;
  /** Hidden characters now shown as ⟨U+XXXX⟩ marks. */
  revealed: number;
  /** Hidden characters removed from collapsed blocks. */
  removed: number;
}

function revealPieces(pieces: readonly Piece<number>[]): Revealed {
  const plans = planBlocks(pieces);
  let text = '';
  for (const p of pieces) {
    const parts = revealParts(p.text, p.code, plans.get(p.block));
    if (!parts) text += p.text;
    else for (const part of parts) text += typeof part === 'string' ? part : part.text;
  }
  let revealed = 0;
  let removed = 0;
  for (const plan of plans.values()) {
    if (plan.collapsed) removed += plan.count;
    else revealed += plan.count;
  }
  return { text, revealed, removed };
}

/** Code (a command, a code block's text): every hidden character becomes a mark. */
export function revealHidden(text: string): Revealed {
  if (!MAYBE_HIDDEN.test(text)) return { text, revealed: 0, removed: 0 };
  return revealPieces([{ text, code: true, block: 0 }]);
}

/**
 * Model text shown as plain text, outside Markdown (the note of an unchanged
 * revision): the text split around the marks that reveal its hidden characters,
 * with the rules of prose, as the same words would show in a rendered answer.
 */
export function revealHiddenParts(text: string): (string | HiddenMark)[] {
  if (!MAYBE_HIDDEN.test(text)) return [text];
  const plans = planBlocks([{ text, code: false, block: 0 }]);
  return revealParts(text, false, plans.get(0)) ?? [text];
}

/**
 * A model answer as Markdown source, for the whole-answer copy: the same marks
 * the rendered answer shows, so the pasted text is the one that was on screen.
 */
export function revealHiddenMarkdown(src: string): Revealed {
  if (!MAYBE_HIDDEN.test(src)) return { text: src, revealed: 0, removed: 0 };
  return revealPieces(markdownPieces(src));
}

// ---------------------------------------------------------- answer copy

/**
 * What the copy button of a model answer puts on the clipboard (its Markdown
 * with the marks the rendered answer shows), and the toast that explains it.
 */
export function answerForClipboard(src: string): { text: string; notice: string | null } {
  const { text, revealed, removed } = revealHiddenMarkdown(src);
  return { text, notice: hiddenCopyNotice(revealed, removed) };
}

/** Toast after copying an answer with hidden characters, or null when it had none. */
export function hiddenCopyNotice(revealed: number, removed: number): string | null {
  const total = revealed + removed;
  if (!total) return null;
  if (total === 1) {
    return removed
      ? "La resposta tenia 1 caràcter invisible: no s'ha copiat."
      : "La resposta tenia 1 caràcter invisible: s'ha copiat com a ⟨U+…⟩.";
  }
  if (!removed) return `La resposta tenia ${total} caràcters invisibles: s'han copiat com a ⟨U+…⟩.`;
  if (!revealed) return `La resposta tenia ${total} caràcters invisibles: no s'han copiat.`;
  return (
    `La resposta tenia ${total} caràcters invisibles: ` +
    `${revealed} s'han copiat com a ⟨U+…⟩ i ${removed} no s'han copiat.`
  );
}

// ------------------------------------------------------------------ DOM

/** Elements whose text makes one block for the budget of marks. */
const BLOCK_ELEMENTS = 'p, li, h1, h2, h3, h4, h5, h6, td, th, blockquote, dt, dd, pre';

/**
 * Replace the hidden characters in the text of `root` (sanitized, rendered
 * Markdown) with marks built with DOM APIs: spans with class `invisible-char`
 * (plus `invisible-collapsed` for the count of a collapsed block). A `code`
 * element whose characters went without a mark (past the budget) gets the
 * class `invisible-removed`, so its code bar still warns.
 */
export function revealHiddenIn(root: Node): void {
  const doc = root.ownerDocument ?? document;
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const pieces: (Piece<Node> & { node: Text })[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const node = n as Text;
    if (!MAYBE_HIDDEN.test(node.data)) continue;
    const code = node.parentElement?.closest('code');
    const block = code ?? node.parentElement?.closest(BLOCK_ELEMENTS);
    const inside = block && root.contains(block) ? block : root;
    pieces.push({ node, text: node.data, code: code != null && inside === code, block: inside });
  }
  if (!pieces.length) return;
  const plans = planBlocks(pieces);
  for (const p of pieces) {
    const plan = plans.get(p.block);
    if (p.code && plan?.silent) (p.block as Element).classList.add('invisible-removed');
    const parts = revealParts(p.text, p.code, plan);
    if (!parts) continue;
    const fragment = doc.createDocumentFragment();
    for (const part of parts) {
      if (typeof part === 'string') {
        fragment.append(part);
        continue;
      }
      const span = doc.createElement('span');
      span.className = part.collapsed ? 'invisible-char invisible-collapsed' : 'invisible-char';
      span.title = part.title;
      span.textContent = part.text;
      fragment.append(span);
    }
    p.node.replaceWith(fragment);
  }
}

// ------------------------------------------------------- Markdown source

// Where code is in Markdown source, close enough to what marked renders for the
// whole-answer copy: fenced code blocks (also in lists and blockquotes), indented
// code blocks and inline code spans. When in doubt a region counts as code, which
// only reveals more on the clipboard.

const QUOTE_PREFIX = /^(?:[ \t]{0,3}>[ \t]?)*/;
const BLANK = /^[ \t]*$/;
const FENCE_OPEN = /^([ \t]*)((?:(?:[-+*]|\d{1,9}[.)])[ \t]+)*)(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^[ \t]*(`{3,}|~{3,})[ \t]*$/;
const LIST_ITEM = /^([ \t]*)([-+*]|\d{1,9}[.)])([ \t]+|$)/;
const HEADING = /^[ \t]{0,3}#{1,6}(?:[ \t]|$)/;

/** Columns of `s` (leading whitespace and list markers), with tab stops every 4. */
function columns(s: string): number {
  let col = 0;
  for (const ch of s) col = ch === '\t' ? col + 4 - (col % 4) : col + 1;
  return col;
}

const indentOf = (s: string): number => columns(/^[ \t]*/.exec(s)![0]);

interface Fence {
  char: string;
  length: number;
  depth: number;
  /** A fence in a list item ends with the item: at a less indented line. */
  indent: number;
}

/** Markdown source split into prose and code pieces; each code piece is its own block. */
function markdownPieces(src: string): Piece<number>[] {
  const pieces: Piece<number>[] = [];
  let blocks = 0;
  let para = '';
  let code = '';
  let codeBlock = 0;
  const flushPara = (): void => {
    if (para) inlinePieces(para, blocks++, () => blocks++, pieces);
    para = '';
  };
  const flushCode = (): void => {
    if (code) pieces.push({ text: code, code: true, block: codeBlock });
    code = '';
  };
  const startCode = (line: string): void => {
    flushPara();
    codeBlock = blocks++;
    code = line;
  };

  const items: number[] = []; // content columns of the open list items
  const base = (): number => items.at(-1) ?? 0;
  const closeItems = (indent: number): void => {
    while (items.length && indent < base()) items.pop();
  };
  let fence: Fence | null = null;
  let indented = false;
  let prevBlank = true;
  let inPara = false;

  for (const line of src.match(/[^\n]*\n|[^\n]+$/g) ?? []) {
    const content = line.replace(/\r?\n$/, '');
    const quote = QUOTE_PREFIX.exec(content)![0];
    const depth = quote.split('>').length - 1;
    const rest = content.slice(quote.length);
    const blank = BLANK.test(rest);

    if (fence) {
      if (depth >= fence.depth && (blank || indentOf(rest) >= fence.indent)) {
        code += line;
        const close = FENCE_CLOSE.exec(rest)?.[1];
        if (close && close[0] === fence.char && close.length >= fence.length) {
          fence = null;
          flushCode();
          prevBlank = inPara = false;
        }
        continue;
      }
      fence = null; // its blockquote or list item ended
      flushCode();
    }
    if (indented) {
      if (blank || indentOf(rest) >= base() + 4) {
        code += line;
        prevBlank = blank;
        continue;
      }
      indented = false;
      flushCode();
    }
    if (blank) {
      flushPara();
      pieces.push({ text: line, code: false, block: -1 });
      prevBlank = true;
      inPara = false;
      continue;
    }

    const indent = indentOf(rest);
    if (prevBlank) closeItems(indent);
    if (indent >= base() + 4) {
      if (!inPara) {
        startCode(line);
        indented = true;
        prevBlank = false;
        continue;
      }
      // else a continuation line of the paragraph
    } else {
      const open = FENCE_OPEN.exec(rest);
      const [, lead = '', markers = '', run = '', info = ''] = open ?? [];
      if (open && !(run[0] === '`' && info.includes('`'))) {
        closeItems(indent);
        if (markers) items.push(columns(lead + markers));
        startCode(line);
        fence = { char: run[0]!, length: run.length, depth, indent: base() };
        continue;
      }
      if (HEADING.test(rest)) {
        flushPara();
        para = line;
        flushPara(); // a heading is a block of its own
        prevBlank = inPara = false;
        continue;
      }
      const item = LIST_ITEM.exec(rest);
      if (item) {
        flushPara(); // so is each list item
        closeItems(indent);
        const [, lead = '', marker = '', gap = ''] = item;
        items.push(gap && columns(gap) <= 4 ? columns(lead + marker + gap) : columns(lead + marker) + 1);
      }
    }
    para += line;
    prevBlank = false;
    inPara = true;
  }
  flushPara();
  flushCode();
  return pieces;
}

/** Split a paragraph around its inline code spans (CommonMark backtick matching). */
function inlinePieces(text: string, block: number, nextBlock: () => number, out: Piece<number>[]): void {
  // Starts of the backtick runs of each length, to find closing runs in linear time.
  const runs = new Map<number, number[]>();
  for (const m of text.matchAll(/`+/g)) {
    const starts = runs.get(m[0].length);
    if (starts) starts.push(m.index);
    else runs.set(m[0].length, [m.index]);
  }
  const next = new Map<number, number>();
  let last = 0;
  let i = 0;
  while (runs.size && i < text.length) {
    const ch = text[i];
    if (ch === '\\') {
      i += 2; // an escaped backtick opens nothing
      continue;
    }
    if (ch !== '`') {
      i++;
      continue;
    }
    let end = i;
    while (text[end] === '`') end++;
    const length = end - i;
    const starts = runs.get(length) ?? [];
    let k = next.get(length) ?? 0;
    while (k < starts.length && starts[k]! < end) k++;
    next.set(length, k);
    const close = starts[k];
    if (close === undefined) {
      i = end; // no closing run: literal backticks
      continue;
    }
    if (i > last) out.push({ text: text.slice(last, i), code: false, block });
    out.push({ text: text.slice(i, close + length), code: true, block: nextBlock() });
    i = last = close + length;
  }
  if (last < text.length) out.push({ text: text.slice(last), code: false, block });
}
