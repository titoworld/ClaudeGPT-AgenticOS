// Composer options and the saved defaults (audit A11, N11).
import { describe, expect, it } from 'vitest';
import { ComposerState, composerDefaults } from './composer.svelte';
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

describe('ComposerState', () => {
  it('starts from the built-in defaults, with nothing chosen', () => {
    const c = new ComposerState();
    expect(options(c)).toEqual(composerDefaults(DEFAULT_SETTINGS));
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
