// Owns the full-screen canvas and the lazily loaded three.js scene.
//
// The scene chunk (~540 kB) is imported only after the first paint, when the
// main thread is idle. If WebGL2 is missing, the import fails or the user
// turned effects off, the page keeps the animated CSS gradient instead.
// The effects setting and the system's reduced-motion setting reach the scene
// live, before, while and after it loads (SceneBackdrop.svelte).

import { untrack } from 'svelte';
import type { Agent } from './protocol';
import type { SceneController, SceneMood, SceneQuality } from '../scene/types';
import { IDLE_SCENE, type SceneState } from './scene-state';

export type SceneStatus = 'idle' | 'loading' | 'on' | 'off';

const PULSE_MIN_INTERVAL_MS = 70;

function webgl2Available(): boolean {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2');
    if (!gl) return false;
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return true;
  } catch {
    return false;
  }
}

type IdleHandle = { kind: 'idle'; id: number } | { kind: 'timeout'; id: ReturnType<typeof setTimeout> };

export class SceneHost {
  status: SceneStatus = $state('idle');

  #ctrl: SceneController | null = null;
  #canvas: HTMLCanvasElement | null = null;
  #quality: SceneQuality = 'high';
  #reducedMotion = false;
  #state: SceneState = IDLE_SCENE;
  #applied: { mood?: SceneMood; claude?: boolean; chatgpt?: boolean; agreement?: number | null } = {};
  #pulseAt: Record<Agent, number> = { claude: 0, chatgpt: 0 };
  #idle: IdleHandle | null = null;
  #generation = 0;

  configure(options: { quality: SceneQuality; reducedMotion: boolean }): void {
    this.#quality = options.quality;
    this.#reducedMotion = options.reducedMotion;
  }

  /** Svelte attachment for the background <canvas>. */
  attach = (canvas: HTMLCanvasElement): (() => void) =>
    untrack(() => {
      this.#canvas = canvas;
      const generation = ++this.#generation;
      if (this.#quality === 'off') this.status = 'off';
      else this.#scheduleLoad(generation);
      const onVisibility = () => this.#ctrl?.setPaused(document.hidden);
      document.addEventListener('visibilitychange', onVisibility);
      return () => {
        document.removeEventListener('visibilitychange', onVisibility);
        this.#generation++;
        this.#cancelIdle();
        this.#ctrl?.dispose();
        this.#ctrl = null;
        this.#canvas = null;
        this.status = 'idle';
      };
    });

  setQuality(quality: SceneQuality): void {
    untrack(() => {
      this.#quality = quality;
      if (this.#ctrl) {
        this.#ctrl.setQuality(quality);
        this.status = quality === 'off' ? 'off' : 'on';
        return;
      }
      if (quality === 'off') {
        if (this.#idle) {
          // The load was only scheduled: nothing loads any more, so turning the
          // effects on again schedules a new one (audit A19).
          this.#cancelIdle();
          this.status = 'off';
        } else if (this.status !== 'loading') {
          this.status = 'off';
        }
        // Otherwise the scene is being created: it ends 'off' (see #load).
        return;
      }
      if (this.#canvas && this.status !== 'loading') this.#scheduleLoad(this.#generation);
    });
  }

  /**
   * The system's reduced-motion setting, live (audit A20): a scene loaded later starts
   * with it, one being created gets it as soon as it exists (see #load), and one that
   * exists changes at once.
   */
  setReducedMotion(reduced: boolean): void {
    untrack(() => {
      if (reduced === this.#reducedMotion) return;
      this.#reducedMotion = reduced;
      this.#ctrl?.setReducedMotion(reduced);
    });
  }

  /** Drive the scene from app state (deduplicated, cheap to call often). */
  apply(state: SceneState): void {
    this.#state = state;
    const ctrl = this.#ctrl;
    if (!ctrl) return;
    const a = this.#applied;
    if (a.mood !== state.mood) {
      ctrl.setMood(state.mood);
      a.mood = state.mood;
    }
    const claude = !!state.active.claude;
    const chatgpt = !!state.active.chatgpt;
    if (a.claude !== claude || a.chatgpt !== chatgpt) {
      ctrl.setActive({ claude, chatgpt });
      a.claude = claude;
      a.chatgpt = chatgpt;
    }
    if (a.agreement !== state.agreement) {
      ctrl.setAgreement(state.agreement);
      a.agreement = state.agreement;
    }
  }

  /** One-shot moods (consensus, error): the scene returns to idle by itself. */
  flash(mood: 'consensus' | 'error'): void {
    const ctrl = this.#ctrl;
    if (!ctrl) return;
    ctrl.setMood(mood);
    this.#applied.mood = 'idle';
    ctrl.setActive({ claude: false, chatgpt: false });
    this.#applied.claude = false;
    this.#applied.chatgpt = false;
  }

  /** Called on every streamed chunk; throttled per agent. */
  pulse(agent: Agent, chars: number): void {
    const ctrl = this.#ctrl;
    if (!ctrl) return;
    const now = performance.now();
    if (now - this.#pulseAt[agent] < PULSE_MIN_INTERVAL_MS) return;
    this.#pulseAt[agent] = now;
    ctrl.pulse(agent, Math.min(1, Math.max(0.15, chars / 80)));
  }

  #scheduleLoad(generation: number): void {
    this.#cancelIdle();
    this.status = 'loading';
    const run = () => {
      this.#idle = null;
      void this.#load(generation);
    };
    if (typeof requestIdleCallback === 'function') {
      this.#idle = { kind: 'idle', id: requestIdleCallback(run, { timeout: 1200 }) };
    } else {
      this.#idle = { kind: 'timeout', id: setTimeout(run, 200) };
    }
  }

  #cancelIdle(): void {
    const h = this.#idle;
    if (!h) return;
    if (h.kind === 'idle') cancelIdleCallback(h.id);
    else clearTimeout(h.id);
    this.#idle = null;
  }

  async #load(generation: number): Promise<void> {
    const canvas = this.#canvas;
    if (!canvas || generation !== this.#generation) return;
    if (!webgl2Available()) {
      this.status = 'off';
      return;
    }
    try {
      const { createScene } = await import('../scene/index');
      if (generation !== this.#generation) return;
      const created = { reducedMotion: this.#reducedMotion, quality: this.#quality };
      const ctrl = await createScene(canvas, created);
      if (generation !== this.#generation) {
        ctrl.dispose();
        return;
      }
      this.#ctrl = ctrl;
      this.#applied = {};
      // Either setting may have changed while the scene was being created (its shaders
      // compile asynchronously): the scene gets the current one (A19, A20).
      if (this.#quality !== created.quality) ctrl.setQuality(this.#quality);
      if (this.#reducedMotion !== created.reducedMotion) ctrl.setReducedMotion(this.#reducedMotion);
      ctrl.setPaused(document.hidden);
      this.status = this.#quality === 'off' ? 'off' : 'on';
      this.apply(this.#state);
    } catch (err) {
      console.warn('Escena 3D desactivada:', err);
      if (generation === this.#generation) this.status = 'off';
    }
  }
}

export const sceneHost = new SceneHost();
