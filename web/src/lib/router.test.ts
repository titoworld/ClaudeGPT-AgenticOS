import { describe, expect, it } from 'vitest';
import { parseHash, routeHash } from './router.svelte';

describe('hash router', () => {
  it('parses the known routes', () => {
    expect(parseHash('')).toEqual({ name: 'chat', id: null });
    expect(parseHash('#/')).toEqual({ name: 'chat', id: null });
    expect(parseHash('#/c/42')).toEqual({ name: 'chat', id: 42 });
    expect(parseHash('#/tauler')).toEqual({ name: 'dashboard' });
  });

  it('falls back to a new conversation for unknown hashes', () => {
    expect(parseHash('#/c/abc')).toEqual({ name: 'chat', id: null });
    expect(parseHash('#/whatever')).toEqual({ name: 'chat', id: null });
  });

  it('round-trips', () => {
    for (const hash of ['#/', '#/c/7', '#/tauler']) expect(routeHash(parseHash(hash))).toBe(hash);
  });
});
