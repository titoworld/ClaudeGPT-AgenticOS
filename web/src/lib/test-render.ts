// Mounts Svelte components in jsdom for tests (never imported by application code).
// Needs the browser build of Svelte: vite.config.ts resolves the `browser`
// condition when Vitest runs.
import { flushSync, mount, unmount, type Component } from 'svelte';

const mounted: (() => void)[] = [];

/** Mount `component` into a fresh element of the document and flush its first render. */
export function render<Props extends Record<string, any>>(component: Component<Props>, props: Props): HTMLElement {
  const target = document.createElement('div');
  document.body.append(target);
  const instance = mount(component, { target, props });
  flushSync();
  mounted.push(() => {
    void unmount(instance);
    target.remove();
  });
  return target;
}

/** Unmount everything `render` mounted (use in `afterEach`). */
export function cleanup(): void {
  for (const destroy of mounted.splice(0)) destroy();
}

/** Text of an element as a reader gets it: whitespace collapsed. */
export const textOf = (el: Element | null | undefined): string => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
