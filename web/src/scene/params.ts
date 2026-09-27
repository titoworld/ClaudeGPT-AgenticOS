// Pure, DOM-free scene logic: mood targets, smoothing and input sanitising.
// Kept separate from three.js so it can be unit-tested in jsdom.

import type { Agent } from '../lib/protocol';
import type { SceneMood } from './types';

/** Continuous targets the render loop eases towards for each mood. */
export interface MoodTargets {
  /** Particle stream between the orbs (0 = hidden, 1 = full debate). */
  stream: number;
  /** Synthesis: orbs spiral towards the core and the stream feeds it. */
  synth: number;
  /** Slow "breathing" of orbs and core while waiting for the first token. */
  breathe: number;
  /** Extra energy (displacement / glow) shared by both orbs. */
  energy: number;
  /** Brightness of the central core. */
  core: number;
}

const TARGETS: Record<SceneMood, MoodTargets> = {
  idle: { stream: 0, synth: 0, breathe: 0, energy: 0, core: 0.45 },
  thinking: { stream: 0, synth: 0, breathe: 1, energy: 0.15, core: 0.7 },
  speaking: { stream: 0.12, synth: 0, breathe: 0, energy: 0.35, core: 0.55 },
  debate: { stream: 1, synth: 0, breathe: 0, energy: 0.5, core: 0.65 },
  synthesis: { stream: 0.75, synth: 1, breathe: 0.3, energy: 0.6, core: 1 },
  // Transient moods: the one-shot effect is triggered separately and the
  // continuous state settles back to idle ("brief flash, then back to idle").
  consensus: { stream: 0, synth: 0, breathe: 0, energy: 0, core: 0.45 },
  error: { stream: 0, synth: 0, breathe: 0, energy: 0, core: 0.45 },
};

export const SCENE_MOODS = Object.keys(TARGETS) as SceneMood[];

export function isSceneMood(value: unknown): value is SceneMood {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(TARGETS, value);
}

export function moodTargets(mood: SceneMood): MoodTargets {
  return TARGETS[mood];
}

/** Moods that fire a one-shot effect and then leave the scene idle. */
export function isTransientMood(mood: SceneMood): boolean {
  return mood === 'consensus' || mood === 'error';
}

/** Distance between the two orb centres (world units). */
export const SEPARATION_DEFAULT = 3.9;
export const SEPARATION_FAR = 4.5; // agreement 0
export const SEPARATION_NEAR = 2.35; // agreement 100

/** Maps debate agreement (0..100, null = unknown) to the target orb distance. */
export function agreementToSeparation(value: number | null | undefined): number {
  if (value == null || !Number.isFinite(value)) return SEPARATION_DEFAULT;
  const t = clamp01(value / 100);
  // Ease so that small disagreements still read as clearly apart.
  const eased = t * t * (3 - 2 * t);
  return SEPARATION_FAR + (SEPARATION_NEAR - SEPARATION_FAR) * eased;
}

/** Per-agent brightness targets: the inactive agent is dimmed. */
export function activityTargets(active: Partial<Record<Agent, boolean>> | null | undefined): Record<Agent, number> {
  const claude = active?.claude === true;
  const chatgpt = active?.chatgpt === true;
  if (!claude && !chatgpt) return { claude: 0.7, chatgpt: 0.7 };
  return { claude: claude ? 1 : 0.32, chatgpt: chatgpt ? 1 : 0.32 };
}

/** Streamed chunk intensity: finite, clamped to 0..1, default 0.5. */
export function sanitizeIntensity(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0.5;
  return clamp01(value);
}

export function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

/** Frame-rate independent exponential smoothing towards `target`. */
export function damp(current: number, target: number, lambda: number, dt: number): number {
  if (!Number.isFinite(dt) || dt <= 0) return current;
  return target + (current - target) * Math.exp(-lambda * dt);
}

/** Wraps an angle to (-PI, PI]. */
export function wrapAngle(a: number): number {
  const TAU = Math.PI * 2;
  let r = a % TAU;
  if (r <= -Math.PI) r += TAU;
  else if (r > Math.PI) r -= TAU;
  return r;
}

/**
 * Envelope of the consensus flash: fast attack, slower release.
 * `t` is seconds since the trigger; returns 0 when the effect is over.
 */
export function flashEnvelope(t: number, reducedMotion: boolean): number {
  if (t < 0) return 0;
  const attack = reducedMotion ? 0.5 : 0.12;
  const release = reducedMotion ? 1.6 : 0.9;
  if (t < attack) return t / attack;
  const v = Math.exp(-(t - attack) / release);
  return v < 0.01 ? 0 : v;
}

/** Envelope of the error tint: hold briefly, then fade out (about 1.8 s). */
export function errorEnvelope(t: number): number {
  if (t < 0) return 0;
  if (t < 0.1) return t / 0.1;
  if (t < 0.6) return 1;
  const v = Math.exp(-(t - 0.6) / 0.45);
  return v < 0.01 ? 0 : v;
}

/**
 * Brightness multiplier during the error effect: a smooth dip well under the
 * 3 flashes/second photosensitivity limit (WCAG 2.3.1); none with reduced motion.
 */
export function errorFlicker(t: number, envelope: number, reducedMotion: boolean): number {
  if (reducedMotion || envelope <= 0) return 1;
  return 1 - envelope * 0.22 * (0.5 + 0.5 * Math.sin(t * Math.PI * 2 * 2.2));
}

/** Accepts only plain hex colours from CSS variables (never arbitrary strings). */
export function cssHexColor(value: string | null | undefined, fallback: string): string {
  const v = (value ?? '').trim();
  return /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(v) ? v : fallback;
}
