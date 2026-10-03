// Composer options and the saved defaults (audit A11, N11), and the length of a
// question as the server counts it (N19).
import { describe, expect, it } from 'vitest';
import { charCount, ComposerState, composerDefaults, MAX_MESSAGE_CHARS, MAX_QUESTION_CHARS } from './composer.svelte';
import type { RuntimeSettings } from './protocol';
import { DEFAULT_SETTINGS, normalizeSettings } from './settings';

const saved = (change: Partial<RuntimeSettings>): RuntimeSettings => normalizeSettings({ ...DEFAULT_SETTINGS, ...change });

const SOLO = saved({
  default_mode: 'solo',
  default_target: 'chatgpt',
  debate: { rounds: 1, consensus_threshold: 70, synthesizer: 'chatgpt' },
  use_cache: false,
});

const options = (c: ComposerState) => ({
  mode: c.mode,
  target: c.target,
  rounds: c.rounds,
  threshold: c.threshold,
  synthesizer: c.synthesizer,
  useCache: c.useCache,
});

const refine = (c: ComposerState) => ({
  refineRounds: c.refineRounds,
  refineBudget: c.refineBudget,
  refineWords: c.refineWords,
  refineConverge: c.refineConverge,
  refineThreshold: c.refineThreshold,
  refineEditor: c.refineEditor,
});

describe('ComposerState: the refine options («Perfecciona»)', () => {
  const REFINE = saved({
    refine: { max_rounds: 20, budget_eur: 5.5, max_words: 800, stop_on_convergence: false, convergence_threshold: 75, editor: 'chatgpt' },
  });

  it('takes their saved defaults, and gives them as the options of a refine turn', () => {
    const c = new ComposerState();
    expect(refine(c)).toEqual({
      refineRounds: 12, refineBudget: 3, refineWords: null, refineConverge: true, refineThreshold: 90, refineEditor: 'claude',
    });
    c.applyDefaults(REFINE);
    expect(c.refineOptions()).toEqual(REFINE.refine);
  });

  it('remembers the ones the owner changed, like the other options', () => {
    const c = new ComposerState();
    c.refineRounds = 30;
    c.refineWords = 1200;
    expect(c.touched('refineRounds')).toBe(true);
    expect(c.touched('refineBudget')).toBe(false);
    c.applyDefaults(REFINE);
    expect(c.refineOptions()).toEqual({ ...REFINE.refine, max_rounds: 30, max_words: 1200 });
    c.refineWords = null; // back to the automatic limit, on purpose
    c.applyDefaults(REFINE);
    expect(c.refineOptions().max_words).toBeNull();
  });

  it('is never the mode chosen without the owner: saved settings never give it', () => {
    const c = new ComposerState();
    c.applyDefaults(saved({ default_mode: 'refine' as never }));
    expect(c.mode).toBe('debate');
    c.mode = 'refine'; // the owner's choice stays
    c.applyDefaults(SOLO);
    expect(c.mode).toBe('refine');
  });
});

describe('ComposerState', () => {
  it('starts from the built-in defaults, with nothing chosen', () => {
    const c = new ComposerState();
    expect({ ...options(c), ...refine(c) }).toEqual(composerDefaults(DEFAULT_SETTINGS));
    expect(c.touched('mode')).toBe(false);
  });

  it('takes the saved defaults', () => {
    const c = new ComposerState();
    c.applyDefaults(SOLO);
    expect(options(c)).toEqual({ mode: 'solo', target: 'chatgpt', rounds: 1, threshold: 70, synthesizer: 'chatgpt', useCache: false });
  });

  it('never overwrites what the owner chose, even with a default that changed', () => {
    const c = new ComposerState();
    c.mode = 'duel';
    c.rounds = 4;
    expect(c.touched('mode')).toBe(true);
    expect(c.touched('target')).toBe(false);
    c.applyDefaults(SOLO);
    expect(options(c)).toMatchObject({ mode: 'duel', rounds: 4, target: 'chatgpt', threshold: 70 });
    c.applyDefaults(saved({ ...SOLO, default_mode: 'debate', debate: { rounds: 0, consensus_threshold: 90, synthesizer: 'claude' } }));
    expect(options(c)).toMatchObject({ mode: 'duel', rounds: 4, threshold: 90, synthesizer: 'claude' });
  });

  it('counts a choice equal to the default as a choice', () => {
    const c = new ComposerState();
    c.useCache = true; // the built-in default, picked explicitly
    c.applyDefaults(SOLO);
    expect(c.useCache).toBe(true);
  });
});

/** docs/PROTOCOL.md, read from disk: Vite serves nothing from outside web/. */
async function protocolDoc(): Promise<string> {
  const module: string = 'node:fs'; // not a literal: the web code has no Node types
  const fs = (await import(/* @vite-ignore */ module)) as { readFileSync(path: string, encoding: 'utf8'): string };
  // A path, since the URL class here is jsdom's; and from a variable, since Vite rewrites
  // `new URL(…, import.meta.url)` into the URL it would serve the file at.
  const here: string = import.meta.url;
  return fs.readFileSync(decodeURIComponent(new URL('../../../docs/PROTOCOL.md', here).pathname), 'utf8');
}

describe('the length of a question (N19)', () => {
  it('uses the limits the protocol documents, like the server', async () => {
    // tests/test_docs.py checks that these numbers are the server's constants.
    const doc = await protocolDoc();
    const documented = (pattern: RegExp): number => Number(pattern.exec(doc)?.[1]?.replaceAll(',', ''));
    expect(documented(/The question \(`text`\) can have at most ([\d,]+) characters/)).toBe(MAX_QUESTION_CHARS);
    expect(documented(/No client message can exceed ([\d,]+) characters/)).toBe(MAX_MESSAGE_CHARS);
    expect([MAX_QUESTION_CHARS, MAX_MESSAGE_CHARS]).toEqual([100_000, 524_288]);
  });

  it('counts characters as the server does: code points', () => {
    expect(charCount('')).toBe(0);
    expect(charCount('Què?')).toBe(4);
    expect(charCount('😀')).toBe(1);
    expect(charCount('a😀b\u{E0100}')).toBe(4);
    expect(charCount('👩\u200D💻')).toBe(3);
    // A lone surrogate reaches the server as \ud800, one character (the server then refuses it).
    expect(charCount('\uD800')).toBe(1);
    expect(charCount('\uDC00\uD800x')).toBe(3);
  });
});
