// The background scene's host: effects turned off and on again while the deferred load
// is only scheduled (audit A19), and the reduced-motion preference changing while the
// app is open, before, while and after the scene is created (A20). The scene itself is
// a double that records how it was created and what it was told.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const scene = vi.hoisted(() => ({
  created: [] as { reducedMotion: boolean; quality: string }[],
  calls: [] as string[],
  /** While set, creating the scene waits for it (its shaders compiling: compileAsync). */
  compiling: null as Promise<void> | null,
}));

vi.mock('../scene/index', () => ({
  createScene: async (_canvas: HTMLCanvasElement, options: { reducedMotion: boolean; quality: string }) => {
    scene.created.push({ ...options });
    if (scene.compiling) await scene.compiling;
    const record =
      (name: string) =>
      (...args: unknown[]): void => {
        scene.calls.push(`${name}(${args.map((a) => JSON.stringify(a)).join(',')})`);
      };
    return {
      setMood: record('setMood'),
      pulse: record('pulse'),
      setActive: record('setActive'),
      setAgreement: record('setAgreement'),
      setQuality: record('setQuality'),
      setPaused: record('setPaused'),
      setReducedMotion: record('setReducedMotion'),
      dispose: record('dispose'),
    };
  },
}));

/** A media query whose answer the test changes (the system setting). */
class FakeMediaQuery {
  readonly listeners: ((e: { matches: boolean }) => void)[] = [];
  constructor(
    readonly media: string,
    public matches: boolean,
  ) {}
  addEventListener(_type: 'change', listener: (e: { matches: boolean }) => void): void {
    this.listeners.push(listener);
  }
  removeEventListener(): void {}
  set(matches: boolean): void {
    this.matches = matches;
    for (const listener of this.listeners) listener({ matches });
  }
}

let reducedMotion: FakeMediaQuery;

beforeEach(() => {
  vi.useFakeTimers();
  scene.created = [];
  scene.calls = [];
  scene.compiling = null;
  // WebGL2 is "available" in jsdom.
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(((kind: string) =>
    kind === 'webgl2' ? { getExtension: () => ({ loseContext: () => {} }) } : null) as never);
  reducedMotion = new FakeMediaQuery('(prefers-reduced-motion: reduce)', false);
  vi.stubGlobal('matchMedia', (query: string) =>
    query === reducedMotion.media ? reducedMotion : new FakeMediaQuery(query, false),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function newHost(quality: 'high' | 'low' | 'off' = 'high', reduced = false) {
  vi.resetModules();
  const { SceneHost } = await import('./scene-host.svelte');
  const host = new SceneHost();
  host.configure({ quality, reducedMotion: reduced });
  return host;
}

/** The scene being created waits (its shaders compiling) until the function returned is called. */
function holdCreation(): () => void {
  let compiled!: () => void;
  scene.compiling = new Promise<void>((resolve) => (compiled = resolve));
  return compiled;
}

const idleKinds = ['setTimeout (no requestIdleCallback)', 'requestIdleCallback'] as const;

describe('effects turned off and on again before the scene loads (A19)', () => {
  for (const idle of idleKinds) {
    it(`loads the scene once and shows it (${idle})`, async () => {
      if (idle === 'requestIdleCallback') {
        vi.stubGlobal('requestIdleCallback', (fn: () => void, opts?: { timeout?: number }) =>
          setTimeout(fn, opts?.timeout ?? 50),
        );
        vi.stubGlobal('cancelIdleCallback', (id: number) => clearTimeout(id));
      }
      const host = await newHost('high');
      const detach = host.attach(document.createElement('canvas'));
      expect(host.status).toBe('loading');
      host.setQuality('off'); // Configuració > Efectes visuals > Desactivats...
      expect(host.status).toBe('off'); // nothing is loading any more
      host.setQuality('high'); // ...and back to Alts
      expect(host.status).toBe('loading');
      await vi.advanceTimersByTimeAsync(5_000);
      expect(scene.created).toHaveLength(1);
      expect(host.status).toBe('on');
      detach();
    });
  }

  it('from a saved «off»: on, off and on again within the delay', async () => {
    const host = await newHost('off');
    host.attach(document.createElement('canvas'));
    expect(host.status).toBe('off');
    host.setQuality('high');
    expect(host.status).toBe('loading');
    await vi.advanceTimersByTimeAsync(100); // within the 200 ms fallback delay
    host.setQuality('off');
    host.setQuality('low');
    await vi.advanceTimersByTimeAsync(5_000);
    expect(scene.created).toEqual([{ reducedMotion: false, quality: 'low' }]);
    expect(host.status).toBe('on');
  });

  it('turned off while the scene is being loaded: it ends off, and on again uses it', async () => {
    const host = await newHost('high');
    host.attach(document.createElement('canvas'));
    vi.advanceTimersByTime(200); // the deferred load has started: the import is under way
    host.setQuality('off');
    expect(host.status).toBe('loading');
    await vi.advanceTimersByTimeAsync(5_000);
    expect(host.status).toBe('off');
    // Created after the change, so created off (a change while it is created: below).
    expect(scene.created).toEqual([{ reducedMotion: false, quality: 'off' }]);
    host.setQuality('high');
    expect(host.status).toBe('on');
    expect(scene.calls.filter((c) => c.startsWith('setQuality'))).toEqual(['setQuality("high")']);
    expect(scene.created).toHaveLength(1);
  });

  it.each([
    ['low', 'high', 'on'],
    ['high', 'off', 'off'],
  ] as const)('changed while the scene is being created (%s, then %s): the scene gets the last choice', async (
    from,
    to,
    status,
  ) => {
    const compiled = holdCreation();
    const host = await newHost(from);
    host.attach(document.createElement('canvas'));
    await vi.advanceTimersByTimeAsync(5_000); // the scene is being created
    expect(scene.created).toEqual([{ reducedMotion: false, quality: from }]);
    host.setQuality(to);
    compiled();
    await vi.advanceTimersByTimeAsync(10);
    expect(host.status).toBe(status);
    expect(scene.calls.filter((c) => c.startsWith('setQuality'))).toEqual([`setQuality("${to}")`]);
  });
});

describe('reduced motion changed while the app is open (A20)', () => {
  it('creates the scene with the value it has when it loads', async () => {
    const host = await newHost('high', false);
    host.attach(document.createElement('canvas'));
    host.setReducedMotion(true);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(scene.created).toEqual([{ reducedMotion: true, quality: 'high' }]);
    expect(scene.calls.filter((c) => c.startsWith('setReducedMotion'))).toEqual([]);
  });

  it('tells a scene that exists, once per change', async () => {
    const host = await newHost('high', false);
    host.attach(document.createElement('canvas'));
    await vi.advanceTimersByTimeAsync(5_000);
    expect(host.status).toBe('on');
    host.setReducedMotion(true);
    host.setReducedMotion(true);
    host.setReducedMotion(false);
    expect(scene.calls.filter((c) => c.startsWith('setReducedMotion'))).toEqual([
      'setReducedMotion(true)',
      'setReducedMotion(false)',
    ]);
  });

  it.each([
    ['turned on', [true], ['setReducedMotion(true)']],
    ['turned on and off again', [true, false], []],
  ] as [string, boolean[], string[]][])(
    'tells a scene the setting changed while it was being created (%s)',
    async (_change, changes, told) => {
      const compiled = holdCreation();
      const host = await newHost('high', false);
      host.attach(document.createElement('canvas'));
      await vi.advanceTimersByTimeAsync(5_000); // the scene is being created
      expect(scene.created).toEqual([{ reducedMotion: false, quality: 'high' }]);
      for (const reduced of changes) host.setReducedMotion(reduced);
      compiled();
      await vi.advanceTimersByTimeAsync(10);
      expect(host.status).toBe('on');
      expect(scene.calls.filter((c) => c.startsWith('setReducedMotion'))).toEqual(told);
    },
  );

  it('follows the system setting from the backdrop, before and after the scene loads', async () => {
    vi.resetModules();
    const { prefs } = await import('./prefs.svelte');
    const { sceneHost } = await import('./scene-host.svelte');
    const { render, cleanup } = await import('./test-render');
    const { flushSync } = await import('svelte');
    const { default: SceneBackdrop } = await import('../components/SceneBackdrop.svelte');
    render(SceneBackdrop, {});
    expect(sceneHost.status).toBe('loading');

    // The system setting changes before the deferred load runs: the scene starts reduced.
    reducedMotion.set(true);
    flushSync();
    expect(prefs.reducedMotion).toBe(true);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(sceneHost.status).toBe('on');
    expect(scene.created).toEqual([{ reducedMotion: true, quality: 'high' }]);

    // It changes again with the scene running: the scene follows, without being recreated.
    reducedMotion.set(false);
    flushSync();
    reducedMotion.set(true);
    flushSync();
    expect(scene.calls.filter((c) => c.startsWith('setReducedMotion'))).toEqual([
      'setReducedMotion(false)',
      'setReducedMotion(true)',
    ]);
    expect(scene.created).toHaveLength(1);
    cleanup();
  });
});
