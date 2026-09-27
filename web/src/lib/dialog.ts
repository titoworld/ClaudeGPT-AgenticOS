// Helpers for native <dialog> modals (focus trap, Esc and top layer for free).

import type { Attachment } from 'svelte/attachments';

/** Close the dialog when the backdrop (the dialog box itself) is clicked. */
export const lightDismiss: Attachment<HTMLDialogElement> = (dialog) => {
  let downOnBackdrop = false;
  const onDown = (e: PointerEvent) => {
    downOnBackdrop = e.target === dialog;
  };
  const onClick = (e: MouseEvent) => {
    if (downOnBackdrop && e.target === dialog) dialog.close();
    downOnBackdrop = false;
  };
  dialog.addEventListener('pointerdown', onDown);
  dialog.addEventListener('click', onClick);
  return () => {
    dialog.removeEventListener('pointerdown', onDown);
    dialog.removeEventListener('click', onClick);
  };
};

/** Open or close a dialog to match `open`. */
export function syncDialog(dialog: HTMLDialogElement | undefined, open: boolean): void {
  if (!dialog) return;
  if (open && !dialog.open) dialog.showModal();
  else if (!open && dialog.open) dialog.close();
}
