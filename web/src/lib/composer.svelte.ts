// The composer's state.
//
// - Turn options. They start from the saved defaults, and an option the owner changes
//   in this tab is theirs from then on: saved defaults (the first load, a save,
//   settings changed elsewhere) never overwrite it (audit A11, N11). The other options
//   always show their saved default, so new settings only change those whose default
//   changed.
// - The draft. A question is counted as the server counts it, and one the server would
//   refuse is never sent; one it rejected before storing it comes back (N19).
// - What Enter does: it sends with a fine pointer (mouse, trackpad) and makes a new line
//   with a coarse one (phones, tablets), where the button or Ctrl/Cmd+Enter sends (N20).
// - The attachments of the next question (lib/composer-attachments.svelte.ts).

import { ComposerAttachments } from './composer-attachments.svelte';
import type { Agent, Attachment, RuntimeSettings, TurnMode } from './protocol';
import { DEFAULT_SETTINGS } from './settings';

/**
 * The longest question the server takes, in characters (docs/PROTOCOL.md; the engine's
 * `max_question_chars`). It fails a longer one before the turn starts, storing nothing.
 */
export const MAX_QUESTION_CHARS = 100_000;

/**
 * The longest WebSocket message the server reads, in characters (docs/PROTOCOL.md;
 * `MAX_MESSAGE_CHARS` of the server's ws.py). It answers a longer one with an `error`
 * that cannot say which turn it was, so the client never sends one. A question within
 * its own limit only gets there with many characters that JSON escapes (a control
 * character takes 6). Within both limits a message stays far below the 1 MiB frame the
 * server accepts.
 */
export const MAX_MESSAGE_CHARS = 512 * 1024;

/** From this share of the limit on, the composer shows how long the question is. */
export const COUNT_FROM = 0.9;

/**
 * Characters of `text` as the server counts them: code points (Python's `len`), so an
 * emoji is one. A lone surrogate is one as well: JSON sends it as `\udXXX`.
 */
export function charCount(text: string): number {
  let count = text.length;
  for (let i = 0; i < text.length - 1; i++) {
    const unit = text.charCodeAt(i);
    if (unit < 0xd800 || unit > 0xdbff) continue;
    const next = text.charCodeAt(i + 1);
    if (next >= 0xdc00 && next <= 0xdfff) {
      count--;
      i++;
    }
  }
  return count;
}

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
  /** The attachments of the next question. */
  readonly attachments = new ComposerAttachments();
  /** The owner sent while attachments were still uploading: the question goes once they are up. */
  waitingUploads = $state(false);
  /**
   * The main pointer is coarse: a finger (phones, tablets). There Enter makes a new line,
   * as the on-screen keyboard's Return key promises, and the button sends (N20).
   */
  coarsePointer = $state(false);
  #options: ComposerOptions = $state(composerDefaults(DEFAULT_SETTINGS));
  /** Options the owner changed in this tab (any assignment from outside this class). */
  readonly #touched = new Set<ComposerOption>();

  constructor() {
    const pointer = typeof matchMedia === 'function' ? matchMedia('(pointer: coarse)') : null;
    if (pointer) {
      this.coarsePointer = pointer.matches;
      // A tablet can get a trackpad, or lose it.
      pointer.addEventListener('change', (e) => (this.coarsePointer = e.matches));
    }
  }

  /**
   * Puts back a question the server never stored, with its attachments, unless the owner
   * is writing another.
   */
  restore(question: string, attachments: readonly Attachment[] = []): void {
    if (!question.trim() || this.draft.trim()) return;
    this.draft = question;
    this.attachments.restore(attachments);
  }

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
