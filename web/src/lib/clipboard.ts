// Clipboard helper with a fallback for non-secure contexts (plain http on a LAN).

import { revealHidden } from './markdown';

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.className = 'sr-only';
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

/**
 * Copy model text with its hidden characters (bidi controls, zero-width
 * characters) replaced by the ⟨U+XXXX⟩ marks the rendered answer shows, so a
 * pasted command is the one that was on screen. `revealed` counts them.
 */
export async function copyVisible(text: string): Promise<{ ok: boolean; revealed: number }> {
  const { text: visible, revealed } = revealHidden(text);
  return { ok: await copyText(visible), revealed };
}
