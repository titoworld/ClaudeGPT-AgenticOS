// Per-browser preferences (localStorage) and system media preferences.

import type { SceneQuality } from '../scene/types';

const KEY_EFFECTS = 'aos.effects';
const KEY_SIDEBAR = 'aos.sidebar-collapsed';

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // private mode or storage disabled: keep the in-memory value
  }
}

function readEffects(): SceneQuality {
  const v = read(KEY_EFFECTS);
  return v === 'high' || v === 'low' || v === 'off' ? v : 'high';
}

function media(query: string): MediaQueryList | null {
  return typeof matchMedia === 'function' ? matchMedia(query) : null;
}

class Prefs {
  effects: SceneQuality = $state(readEffects());
  sidebarCollapsed: boolean = $state(read(KEY_SIDEBAR) === '1');
  reducedMotion: boolean = $state(false);
  narrow: boolean = $state(false);

  constructor() {
    const motion = media('(prefers-reduced-motion: reduce)');
    if (motion) {
      this.reducedMotion = motion.matches;
      motion.addEventListener('change', (e) => (this.reducedMotion = e.matches));
    }
    const narrow = media('(max-width: 860px)');
    if (narrow) {
      this.narrow = narrow.matches;
      narrow.addEventListener('change', (e) => (this.narrow = e.matches));
    }
  }

  setEffects(q: SceneQuality): void {
    this.effects = q;
    write(KEY_EFFECTS, q);
  }

  setSidebarCollapsed(collapsed: boolean): void {
    this.sidebarCollapsed = collapsed;
    write(KEY_SIDEBAR, collapsed ? '1' : '0');
  }
}

export const prefs = new Prefs();

export const EFFECTS_LABEL: Record<SceneQuality, string> = { high: 'Alts', low: 'Baixos', off: 'Desactivats' };
