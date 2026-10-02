/**
 * Background WebGL scene (lazy chunk): two noise-displaced orbs (Claude, warm;
 * ChatGPT, teal), an iridescent core, an ambient particle field and a debate
 * stream flowing between the orbs. UnrealBloom in 'high' quality.
 *
 * Only ever loaded through `import('./scene')`, so three.js never lands in the
 * entry chunk. Implements the contract in ./types.ts.
 */
import {
  AdditiveBlending,
  BufferGeometry,
  Color,
  Float32BufferAttribute,
  Mesh,
  NoToneMapping,
  PerspectiveCamera,
  PlaneGeometry,
  Points,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  SRGBColorSpace,
  Timer,
  Vector2,
  Vector3,
  WebGLRenderer,
  type IUniform,
} from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

import type { Agent } from '../lib/protocol';
import {
  activityTargets,
  agreementToSeparation,
  cssHexColor,
  damp,
  errorEnvelope,
  errorFlicker,
  flashEnvelope,
  isSceneMood,
  isTransientMood,
  moodTargets,
  sanitizeIntensity,
  sceneMotion,
  SEPARATION_DEFAULT,
  wrapAngle,
  type MoodTargets,
} from './params';
import { effectiveQuality, FrameWatchdog, isSceneQuality, isSoftwareRenderer, pixelRatioFor, probeRenderer } from './quality';
import {
  BG_FRAG,
  BG_VERT,
  CORE_FRAG,
  CORE_VERT,
  FIELD_VERT,
  HALO_FRAG,
  HALO_VERT,
  ORB_FRAG,
  ORB_VERT,
  POINT_FRAG,
  RING_FRAG,
  RING_VERT,
  STREAM_VERT,
} from './shaders';
import type { CreateScene, SceneController, SceneMood, SceneOptions, SceneQuality } from './types';

const FIELD_COUNT = { high: 1700, low: 650 } as const;
const STREAM_COUNT = { high: 1600, low: 700 } as const;
const ORB_RADIUS = 0.74;
const CORE_RADIUS = 0.19;
const FOV = 34;
const BLOOM = { strength: 0.72, radius: 0.42, threshold: 0.34 } as const;
const RING_SECONDS = 1.9;

/** Deterministic PRNG (mulberry32) so the composition is stable between loads. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function readPalette() {
  let css: CSSStyleDeclaration | null = null;
  try {
    css = getComputedStyle(document.documentElement);
  } catch {
    css = null;
  }
  const pick = (name: string, fallback: string) => new Color(cssHexColor(css?.getPropertyValue(name), fallback));
  return {
    bg: pick('--bg', '#07080d'),
    claude: pick('--claude', '#dd6b3b'),
    claudeGlow: pick('--claude-glow', '#ff9a6a'),
    chatgpt: pick('--chatgpt', '#1aa383'),
    chatgptGlow: pick('--chatgpt-glow', '#5ff0cc'),
  };
}

const u = <T>(value: T): IUniform<T> => ({ value });

/** Development-only knobs (harness): never used by the app shell. */
export interface DevOverrides {
  /** Treat a software rasteriser as a GPU (to preview 'high' headless). */
  ignoreSoftwareRenderer?: boolean;
}

export const createScene: CreateScene = (canvas, options) => createSceneWith(canvas, options);

export async function createSceneWith(
  canvas: HTMLCanvasElement,
  options: SceneOptions,
  dev: DevOverrides = {},
): Promise<SceneController> {
  // The system's reduced-motion setting; it can change while the scene runs (setReducedMotion).
  let reducedMotion = options.reducedMotion === true;
  let motion = sceneMotion(reducedMotion);
  const renderer = new WebGLRenderer({
    canvas,
    antialias: false, // bloom softens edges; MSAA would double the cost on weak GPUs
    alpha: false,
    stencil: false,
    powerPreference: 'high-performance',
  });
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = NoToneMapping; // shaders apply their own soft shoulder
  const palette = readPalette();
  renderer.setClearColor(palette.bg, 1);

  const software = dev.ignoreSoftwareRenderer !== true && isSoftwareRenderer(probeRenderer(renderer.getContext()));
  const rand = rng(0x5eed);

  const scene = new Scene();
  const camera = new PerspectiveCamera(FOV, 1, 0.1, 120);
  camera.position.set(0, 0.3, 10.5);

  // Uniforms shared by every material (same objects, updated once per frame).
  const shared = { uTime: u(0), uError: u(0), uGain: u(1) };
  const pointUniforms = { uPointScale: u(400), uPixelRatio: u(1) };

  const geometries: BufferGeometry[] = [];
  const materials: ShaderMaterial[] = [];
  const track = <G extends BufferGeometry>(g: G): G => (geometries.push(g), g);
  const mat = (m: ShaderMaterial): ShaderMaterial => (materials.push(m), m);

  // ------------------------------------------------------------ background
  const bgGeo = track(new BufferGeometry());
  bgGeo.setAttribute('position', new Float32BufferAttribute([-1, -1, 0, 3, -1, 0, -1, 3, 0], 3));
  const bgUniforms = {
    ...shared,
    uAspect: u(1),
    uFocus: u(new Vector2(0.6, 0.5)),
    uBg: u(palette.bg.clone()),
    uTintA: u(palette.claude.clone()),
    uTintB: u(palette.chatgpt.clone()),
  };
  const bg = new Mesh(
    bgGeo,
    mat(new ShaderMaterial({ vertexShader: BG_VERT, fragmentShader: BG_FRAG, uniforms: bgUniforms, depthTest: false, depthWrite: false })),
  );
  bg.frustumCulled = false;
  bg.renderOrder = -100;
  scene.add(bg);

  // ------------------------------------------------------------ orbs
  const orbGeo = track(new SphereGeometry(1, 112, 80));
  const makeOrb = (base: Color, glow: Color, seed: number) => {
    const uniforms = {
      ...shared,
      uAmp: u(0.04),
      uFreq: u(1.1),
      uSpeed: u(0.18),
      uSeed: u(seed),
      uDeep: u(base.clone().multiplyScalar(0.16)),
      uBase: u(base.clone()),
      uGlow: u(glow.clone()),
      uIntensity: u(0.7),
    };
    const mesh = new Mesh(orbGeo, mat(new ShaderMaterial({ vertexShader: ORB_VERT, fragmentShader: ORB_FRAG, uniforms })));
    mesh.scale.setScalar(ORB_RADIUS);
    scene.add(mesh);
    return { mesh, uniforms };
  };
  const orbs: Record<Agent, ReturnType<typeof makeOrb>> = {
    // Glow gains balance perceived brightness (the teal glow is far more luminous).
    claude: makeOrb(palette.claude, palette.claudeGlow.clone().multiplyScalar(1.08), 3.1),
    chatgpt: makeOrb(palette.chatgpt, palette.chatgptGlow.clone().multiplyScalar(0.8), 17.7),
  };

  // ------------------------------------------------------------ halos (glow without bloom, too)
  const haloGeo = track(new PlaneGeometry(2, 2));
  const makeHalo = (color: Color, size: number, falloff: number) => {
    const uniforms = { ...shared, uCenter: u(new Vector3()), uSize: u(size), uColor: u(color.clone()), uStrength: u(0.3), uFalloff: u(falloff) };
    const mesh = new Mesh(
      haloGeo,
      mat(
        new ShaderMaterial({
          vertexShader: HALO_VERT,
          fragmentShader: HALO_FRAG,
          uniforms,
          transparent: true,
          depthWrite: false,
          blending: AdditiveBlending,
        }),
      ),
    );
    mesh.frustumCulled = false;
    mesh.renderOrder = 5;
    scene.add(mesh);
    return { mesh, uniforms };
  };
  const halos: Record<Agent | 'core', ReturnType<typeof makeHalo>> = {
    claude: makeHalo(palette.claudeGlow, 1.9, 9),
    chatgpt: makeHalo(palette.chatgptGlow.clone().multiplyScalar(0.8), 1.9, 9),
    core: makeHalo(new Color(0.8, 0.84, 1), 0.9, 10),
  };

  // ------------------------------------------------------------ core
  const coreGeo = track(new SphereGeometry(1, 48, 32));
  const coreUniforms = { ...shared, uIntensity: u(0.5) };
  const core = new Mesh(coreGeo, mat(new ShaderMaterial({ vertexShader: CORE_VERT, fragmentShader: CORE_FRAG, uniforms: coreUniforms })));
  core.scale.setScalar(CORE_RADIUS);
  scene.add(core);

  // ------------------------------------------------------------ ambient field
  const fieldGeo = track(new BufferGeometry());
  {
    const pos = new Float32Array(FIELD_COUNT.high * 3);
    const seed = new Float32Array(FIELD_COUNT.high * 3);
    for (let i = 0; i < FIELD_COUNT.high; i++) {
      const far = rand() < 0.3;
      let x: number, y: number, z: number;
      if (far) {
        // Distant star shell behind the scene.
        const th = rand() * Math.PI * 2;
        const ph = Math.acos(1 - rand() * 1.2);
        const r = 26 + rand() * 10;
        x = r * Math.sin(ph) * Math.cos(th) * 1.4;
        y = r * Math.cos(ph) * 0.55 - 4;
        z = -Math.abs(r * Math.sin(ph) * Math.sin(th)) - 6;
      } else {
        // Dust: a wide, flattened cloud around the orbs.
        const g = (rand() + rand() + rand() - 1.5) / 1.5;
        x = (rand() * 2 - 1) * 13;
        y = g * 4.2;
        z = -14 + rand() * 18;
      }
      pos.set([x, y, z], i * 3);
      seed.set([rand(), far ? rand() * 0.35 : Math.pow(rand(), 2.2), rand()], i * 3);
    }
    fieldGeo.setAttribute('position', new Float32BufferAttribute(pos, 3));
    fieldGeo.setAttribute('aSeed', new Float32BufferAttribute(seed, 3));
  }
  const fieldUniforms = {
    ...shared,
    ...pointUniforms,
    uTintA: u(palette.claudeGlow.clone().multiplyScalar(0.8)),
    uTintB: u(palette.chatgptGlow.clone().multiplyScalar(0.8)),
    uStar: u(new Color(0.62, 0.68, 0.86)),
  };
  const field = new Points(
    fieldGeo,
    mat(
      new ShaderMaterial({
        vertexShader: FIELD_VERT,
        fragmentShader: POINT_FRAG,
        uniforms: fieldUniforms,
        transparent: true,
        depthWrite: false,
        blending: AdditiveBlending,
      }),
    ),
  );
  field.frustumCulled = false;
  field.renderOrder = 1;
  scene.add(field);

  // ------------------------------------------------------------ debate stream
  const streamGeo = track(new BufferGeometry());
  {
    const n = STREAM_COUNT.high;
    const seed = new Float32Array(n * 4);
    const lane = new Float32Array(n * 2);
    for (let i = 0; i < n; i++) {
      const big = rand() < 0.06;
      // Alternate directions so any draw-range prefix carries both flows.
      seed.set([rand(), i % 2 === 0 ? 1 : -1, 0.7 + rand() * 0.6, big ? 0.09 + rand() * 0.03 : 0.022 + rand() * 0.04], i * 4);
      const r = Math.sqrt(rand());
      const a = rand() * Math.PI * 2;
      lane.set([Math.cos(a) * r, Math.sin(a) * r], i * 2);
    }
    // `position` is only needed for the draw count; the shader computes positions.
    streamGeo.setAttribute('position', new Float32BufferAttribute(new Float32Array(n * 3), 3));
    streamGeo.setAttribute('aSeed', new Float32BufferAttribute(seed, 4));
    streamGeo.setAttribute('aLane', new Float32BufferAttribute(lane, 2));
  }
  const streamUniforms = {
    ...shared,
    ...pointUniforms,
    uStream: u(0),
    uSynth: u(0),
    uFlowPhase: u(0),
    uOrbRadius: u(ORB_RADIUS),
    uA: u(new Vector3(-2, 0, 0)),
    uB: u(new Vector3(2, 0, 0)),
    uCore: u(new Vector3()),
    uColA: u(palette.claudeGlow.clone().multiplyScalar(1.15)),
    uColB: u(palette.chatgptGlow.clone().multiplyScalar(1.05)),
  };
  const stream = new Points(
    streamGeo,
    mat(
      new ShaderMaterial({
        vertexShader: STREAM_VERT,
        fragmentShader: POINT_FRAG,
        uniforms: streamUniforms,
        transparent: true,
        depthWrite: false,
        blending: AdditiveBlending,
      }),
    ),
  );
  stream.frustumCulled = false;
  stream.renderOrder = 3;
  stream.visible = false;
  scene.add(stream);

  // ------------------------------------------------------------ consensus ring
  const ringGeo = track(new PlaneGeometry(12, 12));
  const ringUniforms = {
    ...shared,
    uRadius: u(0.3),
    uWidth: u(0.07),
    uAlpha: u(0),
    uColA: u(palette.claudeGlow.clone()),
    uColB: u(palette.chatgptGlow.clone()),
  };
  const ring = new Mesh(
    ringGeo,
    mat(
      new ShaderMaterial({
        vertexShader: RING_VERT,
        fragmentShader: RING_FRAG,
        uniforms: ringUniforms,
        transparent: true,
        depthWrite: false,
        blending: AdditiveBlending,
      }),
    ),
  );
  ring.rotation.x = -1.12;
  ring.renderOrder = 4;
  ring.visible = false;
  scene.add(ring);

  // ------------------------------------------------------------ post-processing (created on demand)
  let composer: EffectComposer | null = null;
  let bloom: UnrealBloomPass | null = null;
  let renderPass: RenderPass | null = null;
  let outputPass: OutputPass | null = null;
  const ensureComposer = () => {
    if (composer) return composer;
    composer = new EffectComposer(renderer); // HalfFloat targets
    renderPass = new RenderPass(scene, camera);
    bloom = new UnrealBloomPass(new Vector2(256, 256), BLOOM.strength, BLOOM.radius, BLOOM.threshold);
    outputPass = new OutputPass();
    composer.addPass(renderPass);
    composer.addPass(bloom);
    composer.addPass(outputPass);
    return composer;
  };

  // ------------------------------------------------------------ state
  let mood: SceneMood = 'idle';
  let target: MoodTargets = moodTargets('idle');
  const cur = { ...target };
  let sepTarget = SEPARATION_DEFAULT;
  let sep = SEPARATION_DEFAULT;
  let actTarget = activityTargets(null);
  const act = { ...actTarget };
  const pulses: Record<Agent, number> = { claude: 0, chatgpt: 0 };
  let orbitPhase = 0;
  let sceneTime = 0; // scaled for reduced motion
  let clock = 0; // real seconds since start (effect envelopes)
  let flashAt = -1e9;
  let errorAt = -1e9;
  const pointer = { x: 0, y: 0 };
  const cam = { x: 0, y: 0.3 };
  let baseZ = 10.5;

  let requested: SceneQuality = isSceneQuality(options.quality) ? options.quality : 'high';
  let ceiling: SceneQuality = 'high';
  let level: SceneQuality = effectiveQuality(requested, software, ceiling);
  let prScale = software ? 0.75 : 1;
  let paused = false;
  let contextLost = false;
  let disposed = false;
  let running = false;
  let lastTs = 0;
  const watchdog = new FrameWatchdog({ slowFrameMs: software ? 45 : 28 });
  const timer = new Timer();
  timer.connect(document);
  const tmp = new Vector3();

  // ------------------------------------------------------------ per-frame update
  const update = (dt: number) => {
    clock += dt;
    sceneTime += dt * motion.timeScale;
    shared.uTime.value = sceneTime;

    // Continuous parameters ease towards the mood targets.
    const k = motion.easing;
    cur.stream = damp(cur.stream, target.stream, target.stream > cur.stream ? k * 0.9 : k * 1.4, dt);
    cur.synth = damp(cur.synth, target.synth, k * 0.55, dt);
    cur.breathe = damp(cur.breathe, target.breathe, k, dt);
    cur.energy = damp(cur.energy, target.energy, k, dt);
    cur.core = damp(cur.core, target.core, k, dt);
    sep = damp(sep, sepTarget, motion.separationEasing, dt);
    for (const a of ['claude', 'chatgpt'] as const) {
      act[a] = damp(act[a], actTarget[a], 3, dt);
      pulses[a] *= Math.exp(-dt * 4.5);
    }

    // One-shot effects.
    const flash = flashEnvelope(clock - flashAt, reducedMotion);
    const err = errorEnvelope(clock - errorAt);
    shared.uError.value = err;
    shared.uGain.value = errorFlicker(clock - errorAt, err, reducedMotion) * (1 + flash * 0.06);

    // Orbits: gentle sway at rest; during synthesis the orbs spiral in and revolve.
    const s = cur.synth;
    const sEase = s * s * (3 - 2 * s);
    orbitPhase += dt * motion.timeScale * sEase * 0.7;
    if (s < 0.05) orbitPhase += wrapAngle(-orbitPhase) * (1 - Math.exp(-dt * 0.9));
    const radius = sep / 2 + (1.3 - sep / 2) * sEase;
    const sway = Math.sin(sceneTime * 0.09) * 0.1;
    const breath = cur.breathe * Math.sin(sceneTime * 1.35);
    const orbScale = ORB_RADIUS * (1 - 0.3 * sEase) * (1 + 0.035 * breath);
    for (const [a, angle, phase] of [
      ['claude', Math.PI + orbitPhase + sway, 0],
      ['chatgpt', orbitPhase + sway, 2.1],
    ] as const) {
      const orb = orbs[a];
      orb.mesh.position.set(
        radius * Math.cos(angle),
        // Tilted orbit: while revolving (synthesis) the orbs pass above and
        // below each other instead of colliding in depth.
        radius * Math.sin(angle) * 0.5 + Math.sin(sceneTime * 0.45 + phase) * 0.07,
        radius * Math.sin(angle) * 0.6,
      );
      orb.mesh.scale.setScalar(orbScale);
      orb.mesh.rotation.y += dt * motion.timeScale * 0.06;
      const p = pulses[a];
      const un = orb.uniforms;
      un.uAmp.value = 0.035 + cur.energy * 0.03 + Math.min(p, 1.2) * 0.06 + cur.breathe * 0.012 * (0.5 + 0.5 * breath);
      un.uSpeed.value = 0.14 + cur.energy * 0.2 + Math.min(p, 1.2) * 0.25;
      un.uIntensity.value = act[a] * (0.78 + 0.35 * cur.energy + 0.12 * breath) + p * 0.55 + flash * 0.3;
      const halo = halos[a];
      halo.uniforms.uCenter.value.copy(orb.mesh.position);
      halo.uniforms.uSize.value = 1.9 * (orbScale / ORB_RADIUS);
      halo.uniforms.uStrength.value = (level === 'high' ? 0.22 : 0.32) * un.uIntensity.value;
    }

    // Core: brighter while synthesising, rotates slowly.
    const coreI = cur.core * (1 + 0.25 * cur.breathe * (0.5 + 0.5 * Math.sin(sceneTime * 1.35 + 1))) + flash * 0.9;
    coreUniforms.uIntensity.value = coreI;
    core.scale.setScalar(CORE_RADIUS * (1 + 0.35 * sEase + 0.25 * flash));
    core.rotation.y += dt * motion.timeScale * 0.22;
    core.rotation.x += dt * motion.timeScale * 0.09;
    halos.core.uniforms.uStrength.value = (level === 'high' ? 0.14 : 0.22) * coreI;
    halos.core.uniforms.uSize.value = 0.9 * (1 + 0.5 * sEase + 0.6 * flash);

    // Debate stream. The flow runs with the scene's clock, so a change of speed
    // (reduced motion switched on or off) moves no particle.
    streamUniforms.uFlowPhase.value += dt * motion.timeScale * motion.flow;
    streamUniforms.uStream.value = cur.stream;
    streamUniforms.uSynth.value = sEase;
    streamUniforms.uA.value.copy(orbs.claude.mesh.position);
    streamUniforms.uB.value.copy(orbs.chatgpt.mesh.position);
    streamUniforms.uOrbRadius.value = orbScale;
    stream.visible = cur.stream > 0.003;

    // Consensus shock ring (skipped with reduced motion: flash only).
    const ringT = (clock - flashAt) / RING_SECONDS;
    ring.visible = motion.ring && ringT >= 0 && ringT < 1;
    if (ring.visible) {
      const e = 1 - Math.pow(1 - ringT, 3);
      ringUniforms.uRadius.value = 0.35 + e * 3.7;
      ringUniforms.uWidth.value = 0.03 + e * 0.06;
      ringUniforms.uAlpha.value = Math.pow(1 - ringT, 1.8) * 0.55;
    }
    if (bloom) bloom.strength = BLOOM.strength * (1 + flash * 0.55);

    // Camera: subtle pointer parallax (none with reduced motion).
    cam.x = damp(cam.x, pointer.x * 0.38, 2.5, dt);
    cam.y = damp(cam.y, 0.3 - pointer.y * 0.22, 2.5, dt);
    camera.position.set(cam.x, cam.y, baseZ);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld();
    tmp.set(0, 0, 0).project(camera);
    bgUniforms.uFocus.value.set(tmp.x * 0.5 + 0.5, tmp.y * 0.5 + 0.5);
  };

  const draw = (useComposer: boolean) => {
    if (useComposer && composer) composer.render();
    else renderer.render(scene, camera);
  };

  const frame = (ts: number) => {
    if (disposed) return;
    timer.update(ts);
    const raw = lastTs > 0 ? ts - lastTs : 0;
    lastTs = ts;
    const dt = Math.min(timer.getDelta(), 0.1);
    update(dt);
    draw(level === 'high');
    if (watchdog.sample(raw) === 'slow') downgrade();
  };

  // ------------------------------------------------------------ sizing and quality
  const resize = () => {
    if (disposed) return;
    const w = Math.max(1, canvas.clientWidth);
    const h = Math.max(1, canvas.clientHeight);
    const pr = pixelRatioFor(level, window.devicePixelRatio || 1, w, h, prScale);
    renderer.setPixelRatio(pr);
    renderer.setSize(w, h, false);
    if (composer && level === 'high') {
      composer.setPixelRatio(pr);
      composer.setSize(w, h);
    }
    const aspect = w / h;
    camera.aspect = aspect;
    // Keep both orbs in frame on narrow screens; weight the composition
    // centre-right on wide ones so the chat column stays legible.
    baseZ = aspect < 1.25 ? Math.max(10.5, 13.4 / aspect) : 10.5;
    const shift = Math.min(1, Math.max(0, (aspect - 1.1) / 0.6)) * 0.15;
    camera.setViewOffset(1000 * aspect, 1000, -shift * 1000 * aspect, 0, 1000 * aspect, 1000);
    camera.updateProjectionMatrix();
    pointUniforms.uPixelRatio.value = pr;
    pointUniforms.uPointScale.value = (h * pr) / (2 * Math.tan((FOV * Math.PI) / 360));
    bgUniforms.uAspect.value = aspect;
    field.geometry.setDrawRange(0, level === 'high' ? FIELD_COUNT.high : FIELD_COUNT.low);
    stream.geometry.setDrawRange(0, level === 'high' ? STREAM_COUNT.high : STREAM_COUNT.low);
    if (level === 'off' && !contextLost) renderStatic();
  };

  const renderStatic = () => {
    // Settle every eased value so the still frame shows the resting state.
    Object.assign(cur, target);
    sep = sepTarget;
    Object.assign(act, actTarget);
    update(0);
    draw(false);
  };

  const syncLoop = () => {
    const shouldRun = !disposed && !paused && !contextLost && level !== 'off';
    if (shouldRun && !running) {
      running = true;
      lastTs = 0;
      watchdog.reset();
      renderer.setAnimationLoop(frame);
    } else if (!shouldRun && running) {
      running = false;
      renderer.setAnimationLoop(null);
    }
  };

  const applyQuality = () => {
    level = effectiveQuality(requested, software, ceiling);
    if (level === 'high') ensureComposer();
    watchdog.reset();
    resize();
    syncLoop();
  };

  const downgrade = () => {
    if (level === 'high') {
      const pr = renderer.getPixelRatio();
      if (pr > 1.05) prScale = Math.max(1 / pr, 0.7) * prScale;
      else ceiling = 'low';
    } else if (level === 'low') {
      const floor = software ? 0.5 : 0.6;
      if (prScale <= floor) return;
      prScale = Math.max(floor, prScale * 0.8);
    }
    applyQuality();
  };

  // ------------------------------------------------------------ listeners
  const ro = new ResizeObserver(() => resize());
  ro.observe(canvas);
  const onPointer = (e: PointerEvent) => {
    const w = window.innerWidth || 1;
    const h = window.innerHeight || 1;
    pointer.x = (e.clientX / w) * 2 - 1;
    pointer.y = (e.clientY / h) * 2 - 1;
  };
  if (motion.parallax) window.addEventListener('pointermove', onPointer, { passive: true });
  const onLost = (e: Event) => {
    e.preventDefault(); // allow the browser to restore the context
    contextLost = true;
    syncLoop();
  };
  const onRestored = () => {
    contextLost = false;
    resize();
    syncLoop();
  };
  canvas.addEventListener('webglcontextlost', onLost, false);
  canvas.addEventListener('webglcontextrestored', onRestored, false);

  // Compile every program up-front so the first animated frames don't stall.
  stream.visible = true;
  ring.visible = true;
  try {
    await renderer.compileAsync(scene, camera);
  } catch {
    // Compilation errors surface on the first render; nothing to do here.
  }
  stream.visible = false;
  ring.visible = false;
  applyQuality();

  const safe =
    <A extends unknown[]>(fn: (...args: A) => void) =>
    (...args: A): void => {
      if (disposed) return;
      try {
        fn(...args);
      } catch (err) {
        if (import.meta.env.DEV) console.warn('[scene]', err);
      }
    };

  const controller: SceneController = {
    setMood: safe((next: SceneMood) => {
      if (!isSceneMood(next)) return;
      if (next === 'consensus') flashAt = clock;
      if (next === 'error') errorAt = clock;
      mood = isTransientMood(next) ? 'idle' : next;
      target = moodTargets(mood);
      if (level === 'off') renderStatic();
    }),
    pulse: safe((agent: Agent, intensity?: number) => {
      if (agent !== 'claude' && agent !== 'chatgpt') return;
      const i = sanitizeIntensity(intensity);
      pulses[agent] = Math.min(1.6, pulses[agent] + 0.25 + 0.6 * i);
    }),
    setActive: safe((active: Partial<Record<Agent, boolean>>) => {
      actTarget = activityTargets(active);
      if (level === 'off') renderStatic();
    }),
    setAgreement: safe((value: number | null) => {
      sepTarget = agreementToSeparation(value);
      if (level === 'off') renderStatic();
    }),
    setQuality: safe((quality: SceneQuality) => {
      if (!isSceneQuality(quality) || quality === requested) return;
      requested = quality;
      ceiling = 'high'; // an explicit choice gets a fresh chance; the watchdog still guards it
      prScale = software ? 0.75 : 1;
      applyQuality();
    }),
    setPaused: safe((value: boolean) => {
      paused = value === true;
      syncLoop();
    }),
    setReducedMotion: safe((value: boolean) => {
      const reduced = value === true;
      if (reduced === reducedMotion) return;
      reducedMotion = reduced;
      motion = sceneMotion(reduced);
      if (motion.parallax) {
        window.addEventListener('pointermove', onPointer, { passive: true });
      } else {
        window.removeEventListener('pointermove', onPointer);
        // The camera eases back to the middle and stays there.
        pointer.x = 0;
        pointer.y = 0;
      }
    }),
    dispose: () => {
      if (disposed) return;
      disposed = true;
      running = false;
      try {
        renderer.setAnimationLoop(null);
        ro.disconnect();
        timer.dispose();
        window.removeEventListener('pointermove', onPointer);
        canvas.removeEventListener('webglcontextlost', onLost);
        canvas.removeEventListener('webglcontextrestored', onRestored);
        for (const g of geometries) g.dispose();
        for (const m of materials) m.dispose();
        renderPass?.dispose();
        bloom?.dispose();
        outputPass?.dispose();
        composer?.dispose();
        scene.clear();
        renderer.renderLists.dispose();
        renderer.dispose();
      } catch (err) {
        if (import.meta.env.DEV) console.warn('[scene] dispose', err);
      }
    },
  };
  return controller;
};
