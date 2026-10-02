// The sticky stop bars of the running refine turns on screen (RefineControls.svelte) and how
// tall they are, so that the conversation (Chat.svelte) brings what gets the keyboard focus
// into view above them, never behind them (WCAG 2.2, 2.4.11). Browsers honour the scroll
// padding of the scroller when they bring the focus into view, also for what is already in
// the scrollport; a scroll margin of what gets the focus, only when it is out of it.

class StopBars {
  #heights: Record<string, number> = $state({});

  /** The height (px) of the tallest bar on screen; 0 without any. */
  get height(): number {
    return Math.max(0, ...Object.values(this.#heights));
  }

  /** The bar `id` is on screen, `height` px tall. */
  set(id: string, height: number): void {
    this.#heights[id] = height;
  }

  /** The bar `id` is gone. */
  remove(id: string): void {
    delete this.#heights[id];
  }
}

export const stopBars = new StopBars();
