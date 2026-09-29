// Development-only harness for the background scene (scene-harness.html).
// Not referenced from index.html, so `vite build` never includes it.
// URL parameters: ?mood=debate&quality=low&agreement=60&active=claude&clean&column&reduced&gpu
// (`gpu` treats a software rasteriser as a GPU so 'high' can be previewed headless;
// without `reduced`, the system's reduced-motion setting applies, live as in the app).

import '../styles/tokens.css';
import './harness.css';
import type { Agent } from '../lib/protocol';
import { isSceneMood } from './params';
import { isSceneQuality } from './quality';
import type { SceneController, SceneMood, SceneQuality } from './types';

const params = new URLSearchParams(location.search);
const canvas = document.querySelector<HTMLCanvasElement>('#scene');
const stats = document.querySelector<HTMLOutputElement>('#stats');
const column = document.querySelector<HTMLElement>('.mock-column');
if (!canvas || !stats || !column) throw new Error('harness markup missing');

if (params.has('clean')) document.body.classList.add('clean');
column.hidden = !params.has('column');

const systemMotion = matchMedia('(prefers-reduced-motion: reduce)');
const reducedMotion = (): boolean => params.has('reduced') || systemMotion.matches;
const initialQuality = params.get('quality');
let quality: SceneQuality = isSceneQuality(initialQuality) ? initialQuality : 'high';
const initialMood = params.get('mood');
let mood: SceneMood = isSceneMood(initialMood) ? initialMood : 'idle';
const active: Partial<Record<Agent, boolean>> = {};
for (const a of (params.get('active') ?? '').split(',')) if (a === 'claude' || a === 'chatgpt') active[a] = true;
const agreementParam = params.get('agreement');
let agreement: number | null = agreementParam === null ? null : Number(agreementParam);

let scene: SceneController | null = null;

async function create(): Promise<void> {
  scene?.dispose();
  scene = null;
  const { createSceneWith } = await import('./index');
  const s = await createSceneWith(canvas!, { reducedMotion: reducedMotion(), quality }, { ignoreSoftwareRenderer: params.has('gpu') });
  s.setActive(active);
  s.setAgreement(agreement);
  s.setMood(mood);
  scene = s;
  (window as unknown as { __scene?: SceneController }).__scene = s;
  syncPressed();
}

function syncPressed(): void {
  for (const b of document.querySelectorAll<HTMLButtonElement>('[data-mood]')) {
    b.setAttribute('aria-pressed', String(b.dataset.mood === mood));
  }
  for (const b of document.querySelectorAll<HTMLButtonElement>('[data-quality]')) {
    b.setAttribute('aria-pressed', String(b.dataset.quality === quality));
  }
}

systemMotion.addEventListener('change', () => scene?.setReducedMotion(reducedMotion()));

document.addEventListener('click', (e) => {
  const el = e.target instanceof HTMLElement ? e.target : null;
  const m = el?.dataset.mood;
  if (m && isSceneMood(m)) {
    mood = m;
    scene?.setMood(m);
  }
  const q = el?.dataset.quality;
  if (q && isSceneQuality(q)) {
    quality = q;
    scene?.setQuality(q);
  }
  if (el?.id === 'recreate') void create();
  if (el?.id === 'dispose') {
    scene?.dispose();
    scene = null;
  }
  syncPressed();
});

document.addEventListener('change', (e) => {
  const el = e.target instanceof HTMLInputElement ? e.target : null;
  if (!el) return;
  const a = el.dataset.active;
  if (a === 'claude' || a === 'chatgpt') {
    active[a] = el.checked;
    scene?.setActive({ ...active });
  }
  if (el.id === 'paused') scene?.setPaused(el.checked);
  if (el.id === 'column') column.hidden = !el.checked;
});

document.querySelector<HTMLInputElement>('#agreement')?.addEventListener('input', (e) => {
  const v = Number((e.target as HTMLInputElement).value);
  agreement = v < 0 ? null : v;
  scene?.setAgreement(agreement);
});

// Simulated token stream: random chunk intensities on the active agents.
const autopulse = document.querySelector<HTMLInputElement>('#autopulse');
window.setInterval(() => {
  if (!autopulse?.checked || !scene) return;
  for (const a of ['claude', 'chatgpt'] as const) {
    if (active[a] !== false && Math.random() < 0.6) scene.pulse(a, Math.random());
  }
}, 90);

// Main-thread frame rate (approximates the scene's when it is animating).
let frames = 0;
let last = performance.now();
const tick = (now: number) => {
  frames++;
  if (now - last >= 1000) {
    stats.value = `${Math.round((frames * 1000) / (now - last))} fps · ${quality}`;
    frames = 0;
    last = now;
  }
  requestAnimationFrame(tick);
};
requestAnimationFrame(tick);

create().catch((err: unknown) => {
  stats.value = `WebGL no disponible: ${err instanceof Error ? err.message : String(err)}`;
});
