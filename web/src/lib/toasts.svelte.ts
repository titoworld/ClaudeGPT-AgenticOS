// Transient notifications, announced through an aria-live region.

export type ToastKind = 'info' | 'success' | 'error';

export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

class Toasts {
  items: Toast[] = $state([]);
  #next = 1;

  push(text: string, kind: ToastKind = 'info', ms = kind === 'error' ? 6000 : 3500): void {
    // Avoid stacking the same message (e.g. repeated network errors).
    if (this.items.some((t) => t.text === text)) return;
    const id = this.#next++;
    this.items.push({ id, kind, text });
    setTimeout(() => this.dismiss(id), ms);
  }

  dismiss(id: number): void {
    this.items = this.items.filter((t) => t.id !== id);
  }
}

export const toasts = new Toasts();
