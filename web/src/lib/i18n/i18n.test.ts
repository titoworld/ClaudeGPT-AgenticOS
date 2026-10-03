// The interface's languages (docs/adr/0011-internationalization.md): which one a page
// opens in, the owner's choice, the catalogs, and the formats of each language.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { richParts } from '../../components/RichText.svelte';
import { CATALOGS } from './catalog';

/** A fresh i18n module (as after a page load), with the browser's languages `languages`. */
async function load(languages: string[]) {
  vi.resetModules();
  vi.stubGlobal('navigator', { ...navigator, languages, language: languages[0] ?? '' });
  const i18n = await import('./index.svelte');
  const format = await import('../format');
  const text = await import('../text');
  return { ...i18n, ...format, ...text };
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('the language a page opens in', () => {
  it("is the browser's first language of ours", async () => {
    expect((await load(['fr-FR', 'es-ES', 'en'])).i18n.locale).toBe('es');
    expect((await load(['ca-ES-valencia'])).i18n.locale).toBe('ca');
    expect((await load(['en-US'])).i18n.locale).toBe('en');
  });

  it('is English when the browser has none of ours', async () => {
    expect((await load(['de-DE', 'fr'])).i18n.locale).toBe('en');
    expect((await load([])).i18n.locale).toBe('en');
  });

  it("is the owner's choice once made, in this browser", async () => {
    const first = await load(['es']);
    first.i18n.set('ca');
    expect(document.documentElement.lang).toBe('ca');
    const again = await load(['es']);
    expect(again.i18n.locale).toBe('ca');
    expect(document.documentElement.lang).toBe('ca');
  });
});

describe('the catalogs', () => {
  /** Every text of a catalog, by its path ("common.modes.solo.label"), called when it is a function. */
  function texts(node: unknown, path = ''): [string, unknown][] {
    if (node && typeof node === 'object') return Object.entries(node).flatMap(([k, v]) => texts(v, path ? `${path}.${k}` : k));
    return [[path, node]];
  }

  it('have the same texts in the three languages, none empty', () => {
    const keys = (locale: 'en' | 'es' | 'ca') => texts(CATALOGS[locale]).map(([k]) => k);
    expect(keys('es')).toEqual(keys('en'));
    expect(keys('ca')).toEqual(keys('en'));
    for (const locale of ['en', 'es', 'ca'] as const) {
      for (const [key, value] of texts(CATALOGS[locale])) {
        const english = texts(CATALOGS.en).find(([k]) => k === key)![1];
        expect(typeof value, key).toBe(typeof english);
        if (typeof value === 'string') expect(value.trim(), key).not.toBe('');
        if (typeof value === 'function') expect(value.length, key).toBe((english as (...args: unknown[]) => string).length);
      }
    }
  });
});

describe('a change of language', () => {
  it('repaints the texts and the formats', async () => {
    const env = await load(['en']);
    expect(env.MODE_LABEL.debate).toBe('Council');
    expect(env.formatEur(1234.5)).toBe('€1,234.50');
    expect(env.formatK(3200)).toBe('3.2k');
    env.i18n.set('es');
    expect(env.MODE_LABEL.debate).toBe('Consejo');
    expect(env.formatEur(1234.5)).toBe('1234,50\u00a0€');
    env.i18n.set('ca');
    expect(env.MODE_LABEL.debate).toBe('Consell');
    expect(env.PROVIDER_MODE_LABEL.cli).toBe('Subscripció');
    expect(env.formatEur(1234.5)).toBe('1.234,50\u00a0€');
    expect(env.formatK(3200)).toBe('3,2k');
  });
});

describe('rich texts', () => {
  it('give their bold and code parts as parts, never as HTML', () => {
    expect(richParts('Set `AOS_PUBLIC_ORIGIN` to **this page**.')).toEqual([
      { kind: 'text', text: 'Set ' },
      { kind: 'code', text: 'AOS_PUBLIC_ORIGIN' },
      { kind: 'text', text: ' to ' },
      { kind: 'bold', text: 'this page' },
      { kind: 'text', text: '.' },
    ]);
    expect(richParts('<b>no</b>')).toEqual([{ kind: 'text', text: '<b>no</b>' }]);
  });
});
