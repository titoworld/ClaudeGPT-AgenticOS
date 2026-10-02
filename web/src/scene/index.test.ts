// The WebGL scene follows the reduced-motion preference while it runs (audit A20): its
// clock, the particle flow, the pointer parallax, the consensus ring and the error
// flicker change at once, without recreating the scene. three.js runs for real on the
// CPU; only the renderer is a stand-in (jsdom has no WebGL) that keeps the animation
// loop, so the test drives the frames, and records what each frame draws.
import type { Camera, Material, Object3D, Scene, ShaderMaterial } from 'three';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SceneController } from './types';

const fake = vi.hoisted(() => {
  class FakeRenderer {
    static last: FakeRenderer | null = null;
    outputColorSpace = '';
    toneMapping = 0;
    readonly renderLists = { dispose: (): void => {} };
    loop: ((timestamp: number) => void) | null = null;
    scene: unknown = null;
    camera: unknown = null;
    #pixelRatio = 1;
    constructor() {
      FakeRenderer.last = this;
    }
    setClearColor(): void {}
    getContext() {
      return { RENDERER: 0x1f01, getExtension: () => null, getParameter: () => 'Test GPU' };
    }
    compileAsync(): Promise<void> {
      return Promise.resolve();
    }
    setAnimationLoop(loop: ((timestamp: number) => void) | null): void {
      this.loop = loop;
    }
    setPixelRatio(ratio: number): void {
      this.#pixelRatio = ratio;
    }
    getPixelRatio(): number {
      return this.#pixelRatio;
    }
    setSize(): void {}
    render(scene: unknown, camera: unknown): void {
      this.scene = scene;
      this.camera = camera;
    }
    dispose(): void {}
  }
  return { FakeRenderer };
});

vi.mock('three', async (importOriginal) => ({
  ...(await importOriginal<typeof import('three')>()),
  WebGLRenderer: fake.FakeRenderer,
}));

const FRAME_MS = 1000 / 60;

beforeEach(() => {
  fake.FakeRenderer.last = null;
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      disconnect(): void {}
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The first object of the scene drawn with a shader material that has the uniform `name`. */
function objectWith(scene: Scene, name: string): { object: Object3D; uniforms: Record<string, { value: unknown }> } {
  const found: { object: Object3D; uniforms: Record<string, { value: unknown }> }[] = [];
  scene.traverse((object: Object3D) => {
    const material = (object as Object3D & { material?: Material }).material as ShaderMaterial | undefined;
    if (material?.uniforms && name in material.uniforms) found.push({ object, uniforms: material.uniforms });
  });
  if (!found[0]) throw new Error(`nothing in the scene has ${name}`);
  return found[0];
}

const uniformsWith = (scene: Scene, name: string) => objectWith(scene, name).uniforms;

async function start(reducedMotion: boolean) {
  const { createScene } = await import('./index');
  const ctrl: SceneController = await createScene(document.createElement('canvas'), { reducedMotion, quality: 'low' });
  const renderer = fake.FakeRenderer.last!;
  let now = performance.now();
  /** Runs `seconds` of frames at 60 fps; returns what `sample` gave at each frame. */
  const run = (seconds: number, sample: () => number = () => 0): number[] => {
    const samples: number[] = [];
    for (let i = 0; i < Math.round(seconds * 60); i++) {
      now += FRAME_MS;
      renderer.loop?.(now);
      samples.push(sample());
    }
    return samples;
  };
  run(0.1); // first frames: the renderer now knows the scene and the camera
  const scene = renderer.scene as Scene;
  const camera = renderer.camera as Camera;
  const shared = uniformsWith(scene, 'uTime');
  return {
    ctrl,
    run,
    scene,
    time: () => shared.uTime!.value as number,
    gain: () => shared.uGain!.value as number,
    cameraX: () => camera.position.x,
    ring: () => objectWith(scene, 'uRadius').object,
    flow: () => uniformsWith(scene, 'uStream'),
    /** The pointer at `x` (-1 left edge, 1 right edge) of the window. */
    point: (x: number) =>
      window.dispatchEvent(
        new MouseEvent('pointermove', { clientX: ((x + 1) / 2) * window.innerWidth, clientY: window.innerHeight / 2 }),
      ),
  };
}

describe('SceneController.setReducedMotion (A20)', () => {
  it('slows the clock and stops following the pointer, and undoes it', async () => {
    const s = await start(false);
    let t = s.time();
    s.run(1);
    expect(s.time() - t).toBeCloseTo(1, 2);
    s.point(1);
    s.run(3);
    expect(s.cameraX()).toBeGreaterThan(0.3);

    s.ctrl.setReducedMotion(true);
    t = s.time();
    s.run(1);
    expect(s.time() - t).toBeCloseTo(0.2, 2);
    s.point(-1);
    s.run(4);
    expect(Math.abs(s.cameraX())).toBeLessThan(0.01); // back in the middle, whatever the pointer does

    s.ctrl.setReducedMotion(false);
    t = s.time();
    s.run(1);
    expect(s.time() - t).toBeCloseTo(1, 2);
    s.point(-1);
    s.run(3);
    expect(s.cameraX()).toBeLessThan(-0.3);
    s.ctrl.dispose();
  });

  it('slows the particle flow without making it jump', async () => {
    const s = await start(false);
    const flow = s.flow();
    const phase = (): number => flow.uFlowPhase!.value as number;
    s.run(1);
    let p = phase();
    s.run(1);
    const normal = phase() - p;
    expect(normal).toBeGreaterThan(0.3);

    s.ctrl.setReducedMotion(true);
    p = phase();
    s.run(1 / 60);
    expect(phase() - p).toBeLessThan(normal / 60 + 1e-6); // one frame on: no jump
    p = phase();
    s.run(1);
    expect(phase() - p).toBeLessThan(normal / 20);
    s.ctrl.dispose();
  });

  it('shows a consensus without the ring and an error without the flicker', async () => {
    const s = await start(false);
    s.ctrl.setMood('consensus');
    expect(s.run(0.5, () => (s.ring().visible ? 1 : 0))).toContain(1);
    s.run(2);
    s.ctrl.setMood('error');
    expect(Math.min(...s.run(1, s.gain))).toBeLessThan(0.9);
    s.run(3);

    s.ctrl.setReducedMotion(true);
    s.ctrl.setMood('consensus');
    expect(s.run(0.5, () => (s.ring().visible ? 1 : 0))).not.toContain(1);
    s.run(3);
    s.ctrl.setMood('error');
    // The brightness never dips (the consensus flash may still add a little).
    expect(Math.min(...s.run(1, s.gain))).toBeGreaterThanOrEqual(1);
    s.ctrl.dispose();
  });

  it('starts reduced when asked to, and listens to the pointer only while it moves', async () => {
    const added = vi.spyOn(window, 'addEventListener');
    const removed = vi.spyOn(window, 'removeEventListener');
    const pointer = (spy: typeof added | typeof removed): number =>
      spy.mock.calls.filter(([type]) => type === 'pointermove').length;
    const s = await start(true);
    let t = s.time();
    s.run(1);
    expect(s.time() - t).toBeCloseTo(0.2, 2);
    s.point(1);
    s.run(3);
    expect(Math.abs(s.cameraX())).toBeLessThan(0.01);
    expect(pointer(added)).toBe(0);

    s.ctrl.setReducedMotion(true); // no change
    t = s.time();
    s.run(1);
    expect(s.time() - t).toBeCloseTo(0.2, 2);
    s.ctrl.setReducedMotion(false);
    s.ctrl.setReducedMotion(false);
    expect(pointer(added)).toBe(1);
    s.ctrl.setReducedMotion(true);
    expect(pointer(removed)).toBe(1);
    s.ctrl.dispose();
  });
});
