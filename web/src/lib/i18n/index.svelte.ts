// The language of the interface (docs/adr/0011-internationalization.md): English, Spanish
// or Catalan. The owner's choice (localStorage), else the browser's first language of
// ours, else English. Every text comes from the catalogs (catalog.ts, one module per area
// under areas/): read them through `i18n.m` where it is shown (a template, a $derived, a
// function a template calls), never into a constant, so a change of language repaints it.

import { CATALOGS, type Messages } from './catalog';

export type Locale = 'en' | 'es' | 'ca';
export const LOCALES: readonly Locale[] = ['en', 'es', 'ca'];

/** Each language in itself, as the picker offers it. */
export const LOCALE_NAMES: Record<Locale, string> = { en: 'English', es: 'Español', ca: 'Català' };

/** The locale of `Intl` formats: British English for day-month dates and a 24-hour clock. */
const INTL_TAGS: Record<Locale, string> = { en: 'en-GB', es: 'es-ES', ca: 'ca-ES' };

export const LOCALE_KEY = 'aos.lang';

/** `value` as a language of ours, by its primary subtag ("ca-ES" -> "ca"); null otherwise. */
export function asLocale(value: unknown): Locale | null {
  if (typeof value !== 'string') return null;
  const primary = value.trim().toLowerCase().split(/[-_]/)[0];
  return LOCALES.find((l) => l === primary) ?? null;
}

/** The browser's first language of ours (navigator.languages, in order), or null. */
export function browserLocale(): Locale | null {
  if (typeof navigator === 'undefined') return null;
  const list = navigator.languages?.length ? navigator.languages : [navigator.language];
  for (const tag of list) {
    const locale = asLocale(tag);
    if (locale) return locale;
  }
  return null;
}

function stored(): Locale | null {
  try {
    return asLocale(localStorage.getItem(LOCALE_KEY));
  } catch {
    return null;
  }
}

/** The language a page opens in: the owner's choice, the browser's, English. */
export const initialLocale = (): Locale => stored() ?? browserLocale() ?? 'en';

class I18n {
  locale: Locale = $state(initialLocale());

  constructor() {
    this.#apply();
  }

  /** The texts of the language in force. */
  get m(): Messages {
    return CATALOGS[this.locale];
  }

  /** The BCP 47 tag of `Intl` formats in the language in force. */
  get tag(): string {
    return INTL_TAGS[this.locale];
  }

  /** The owner chooses a language: the page and the server's texts follow it. */
  set(locale: Locale): void {
    this.locale = locale;
    try {
      localStorage.setItem(LOCALE_KEY, locale);
    } catch {
      // private mode or storage disabled: this page keeps it
    }
    this.#apply();
  }

  #apply(): void {
    if (typeof document !== 'undefined') document.documentElement.lang = this.locale;
  }
}

export const i18n = new I18n();

/** `one` for exactly 1, else `other` (the plural rule of all three languages, for counts). */
export const plural = <T>(n: number, one: T, other: T): T => (n === 1 ? one : other);

/**
 * A record whose values are texts of the language in force, made when they are read (a
 * template, a $derived): for the records of labels that code indexes (`MODE_LABEL[mode]`).
 */
export function textRecord<K extends string>(keys: readonly K[], text: (key: K) => string): Record<K, string> {
  const out = {} as Record<K, string>;
  for (const key of keys) Object.defineProperty(out, key, { enumerable: true, get: () => text(key) });
  return out;
}
