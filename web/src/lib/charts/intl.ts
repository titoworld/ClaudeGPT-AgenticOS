// `Intl` formats of the charts in the language in force (lib/i18n): each one made the
// first time a language needs it, never at module load.

import { i18n } from '../i18n/index.svelte';

/** A getter of an `Intl` object for the language in force, made once per language. */
export function perLocale<T>(make: (tag: string) => T): () => T {
  const made = new Map<string, T>();
  return () => {
    const tag = i18n.tag;
    let value = made.get(tag);
    if (value === undefined) {
      value = make(tag);
      made.set(tag, value);
    }
    return value;
  };
}
