// Adaptive quality: GPU probing, pixel-ratio budget and an FPS watchdog.
// DOM-free except `probeRenderer`, which only needs a WebGL context.

import type { SceneQuality } from './types';

const SOFTWARE_RE = /swiftshader|llvmpipe|softpipe|software|basic render|lavapipe|mesa offscreen/i;

/** True for CPU rasterisers (VMs, remote desktops, headless, blocklisted GPUs). */
export function isSoftwareRenderer(renderer: string | null | undefined): boolean {
  return SOFTWARE_RE.test(renderer ?? '');
}

/** Reads the unmasked renderer string of an existing context ('' if hidden). */
export function probeRenderer(gl: WebGLRenderingContext | WebGL2RenderingContext): string {
  try {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    const value: unknown = gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
    return typeof value === 'string' ? value : '';
  } catch {
    return '';
  }
}

const RANK: Record<SceneQuality, number> = { off: 0, low: 1, high: 2 };

/** Software renderers are capped to 'low'; the watchdog can only lower further. */
export function effectiveQuality(requested: SceneQuality, software: boolean, ceiling: SceneQuality = 'high'): SceneQuality {
  let q = requested;
  if (software && RANK[q] > RANK.low) q = 'low';
  if (RANK[q] > RANK[ceiling]) q = ceiling;
  return q;
}

export function isSceneQuality(value: unknown): value is SceneQuality {
  return value === 'high' || value === 'low' || value === 'off';
}

/** Max pixels rendered per frame in 'high' (about a 1440p canvas at DPR 1.6). */
export const PIXEL_BUDGET_HIGH = 4_200_000;

/**
 * Device pixel ratio to render at. 'high': up to 2, within the pixel budget;
 * 'low'/'off': 1. `scale` (<= 1) is the watchdog's extra step-down.
 */
export function pixelRatioFor(
  quality: SceneQuality,
  devicePixelRatio: number,
  cssWidth: number,
  cssHeight: number,
  scale = 1,
): number {
  const dpr = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  let pr = quality === 'high' ? Math.min(dpr, 2) : Math.min(dpr, 1);
  if (quality === 'high') {
    const area = Math.max(1, cssWidth * cssHeight);
    pr = Math.min(pr, Math.sqrt(PIXEL_BUDGET_HIGH / area));
  }
  pr *= scale;
  return Math.max(0.5, Math.round(pr * 100) / 100);
}

export interface WatchdogOptions {
  /** Average frame time (ms) above which a window counts as slow. */
  slowFrameMs?: number;
  /** Frames per measurement window. */
  windowFrames?: number;
  /** Consecutive slow windows before reporting. */
  strikes?: number;
  /** Frames ignored after a reset (shader compilation, resize...). */
  warmupFrames?: number;
}

/**
 * Collects frame intervals and reports 'slow' once the average stays above the
 * threshold for `strikes` consecutive windows. Deltas over 250 ms (tab switch,
 * breakpoint, GC pause) are ignored so one hiccup never lowers quality.
 */
export class FrameWatchdog {
  private readonly slowFrameMs: number;
  private readonly windowFrames: number;
  private readonly strikesNeeded: number;
  private readonly warmupFrames: number;
  private warmup = 0;
  private frames = 0;
  private total = 0;
  private strikes = 0;

  constructor(options: WatchdogOptions = {}) {
    this.slowFrameMs = options.slowFrameMs ?? 28;
    this.windowFrames = options.windowFrames ?? 60;
    this.strikesNeeded = options.strikes ?? 2;
    this.warmupFrames = options.warmupFrames ?? 30;
    this.warmup = this.warmupFrames;
  }

  reset(): void {
    this.warmup = this.warmupFrames;
    this.frames = 0;
    this.total = 0;
    this.strikes = 0;
  }

  /** Feed one frame interval (ms). Returns 'slow' when quality should drop. */
  sample(deltaMs: number): 'ok' | 'slow' {
    if (!Number.isFinite(deltaMs) || deltaMs <= 0 || deltaMs > 250) return 'ok';
    if (this.warmup > 0) {
      this.warmup--;
      return 'ok';
    }
    this.frames++;
    this.total += deltaMs;
    if (this.frames < this.windowFrames) return 'ok';
    const avg = this.total / this.frames;
    this.frames = 0;
    this.total = 0;
    this.strikes = avg > this.slowFrameMs ? this.strikes + 1 : 0;
    if (this.strikes >= this.strikesNeeded) {
      this.reset();
      return 'slow';
    }
    return 'ok';
  }
}
