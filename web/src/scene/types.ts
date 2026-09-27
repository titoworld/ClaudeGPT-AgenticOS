// Contract between the app shell and the lazily loaded three.js scene.
// The shell does: const { createScene } = await import('./scene'); and drives it.

import type { Agent } from '../lib/protocol';

export type SceneMood =
  | 'idle' // slow ambient orbit
  | 'thinking' // waiting for the first token
  | 'speaking' // one or both agents streaming (see pulse)
  | 'debate' // critique rounds: energy flows between the two orbs
  | 'synthesis' // the orbs converge towards the core
  | 'consensus' // brief celebratory flash, then back to idle
  | 'error'; // brief red flicker, then back to idle

export type SceneQuality = 'high' | 'low' | 'off';

export interface SceneOptions {
  reducedMotion: boolean;
  quality: SceneQuality;
}

export interface SceneController {
  setMood(mood: SceneMood): void;
  /** Called for each streamed chunk; intensity ~ chunk length (0..1). */
  pulse(agent: Agent, intensity?: number): void;
  /** Which agents are currently active (glow brighter). */
  setActive(active: Partial<Record<Agent, boolean>>): void;
  /** Debate agreement 0..100 (null = unknown): higher brings the orbs closer. */
  setAgreement(value: number | null): void;
  setQuality(quality: SceneQuality): void;
  /** Pause rendering (e.g. tab hidden) without disposing. */
  setPaused(paused: boolean): void;
  dispose(): void;
}

export type CreateScene = (canvas: HTMLCanvasElement, options: SceneOptions) => Promise<SceneController>;
