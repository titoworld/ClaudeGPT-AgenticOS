// The composer's turn options. They start from the saved defaults, and an option
// the owner changes in this tab is theirs from then on: saved defaults (the first
// load, a save, settings changed elsewhere) never overwrite it (audit A11, N11).
// The other options always show their saved default, so new settings only change
// those whose default changed.

import type { Agent, RuntimeSettings, TurnMode } from './protocol';
import { DEFAULT_SETTINGS } from './settings';

/** Composer options that have a saved default. */
export interface ComposerOptions {
  mode: TurnMode;
  target: Agent;
  rounds: number;
  threshold: number;
  synthesizer: Agent;
  useCache: boolean;
}

export type ComposerOption = keyof ComposerOptions;

/** The composer options saved settings give. */
export function composerDefaults(s: RuntimeSettings): ComposerOptions {
  return {
    mode: s.default_mode,
    target: s.default_target,
    rounds: s.debate.rounds,
    threshold: s.debate.consensus_threshold,
    synthesizer: s.debate.synthesizer,
    useCache: s.use_cache,
  };
}

export class ComposerState {
  draft = $state('');
  #options: ComposerOptions = $state(composerDefaults(DEFAULT_SETTINGS));
  /** Options the owner changed in this tab (any assignment from outside this class). */
  readonly #touched = new Set<ComposerOption>();

  get mode(): TurnMode {
    return this.#options.mode;
  }
  set mode(value: TurnMode) {
    this.#choose('mode', value);
  }

  get target(): Agent {
    return this.#options.target;
  }
  set target(value: Agent) {
    this.#choose('target', value);
  }

  get rounds(): number {
    return this.#options.rounds;
  }
  set rounds(value: number) {
    this.#choose('rounds', value);
  }

  get threshold(): number {
    return this.#options.threshold;
  }
  set threshold(value: number) {
    this.#choose('threshold', value);
  }

  get synthesizer(): Agent {
    return this.#options.synthesizer;
  }
  set synthesizer(value: Agent) {
    this.#choose('synthesizer', value);
  }

  get useCache(): boolean {
    return this.#options.useCache;
  }
  set useCache(value: boolean) {
    this.#choose('useCache', value);
  }

  /** Whether the owner changed `option` in this tab. */
  touched(option: ComposerOption): boolean {
    return this.#touched.has(option);
  }

  /** Takes the saved defaults of `s` for the options the owner has not changed in this tab. */
  applyDefaults(s: RuntimeSettings): void {
    const values = composerDefaults(s);
    for (const option of Object.keys(values) as ComposerOption[]) {
      if (!this.#touched.has(option)) this.#set(option, values[option]);
    }
  }

  #choose<K extends ComposerOption>(option: K, value: ComposerOptions[K]): void {
    this.#set(option, value);
    this.#touched.add(option);
  }

  #set<K extends ComposerOption>(option: K, value: ComposerOptions[K]): void {
    this.#options[option] = value;
  }
}
