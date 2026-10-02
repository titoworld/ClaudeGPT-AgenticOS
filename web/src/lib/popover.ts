// Positioning for native popovers (they live in the top layer, so CSS anchoring
// to their button is not available everywhere yet).

import type { Attachment } from 'svelte/attachments';

/** Open the popover just above `anchor()`, `maxWidth` wide, kept inside the viewport. */
export function placeAbove(anchor: () => HTMLElement | undefined, maxWidth: number): Attachment<HTMLElement> {
  return (el) => {
    const onBeforeToggle = (e: Event) => {
      const button = anchor();
      if ((e as ToggleEvent).newState !== 'open' || !button) return;
      const r = button.getBoundingClientRect();
      const width = Math.min(maxWidth, window.innerWidth - 24);
      const left = Math.max(12, Math.min(r.left, window.innerWidth - width - 12));
      el.style.setProperty('left', `${left}px`);
      el.style.setProperty('bottom', `${window.innerHeight - r.top + 8}px`);
      el.style.setProperty('width', `${width}px`);
      el.style.setProperty('max-height', `${Math.max(200, r.top - 20)}px`);
    };
    el.addEventListener('beforetoggle', onBeforeToggle);
    return () => el.removeEventListener('beforetoggle', onBeforeToggle);
  };
}
