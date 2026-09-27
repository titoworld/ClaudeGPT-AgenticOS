// Roving-focus keyboard navigation between chart marks (one tab stop per chart).

/** Next focused index for a key press, or null when the key is not handled. */
export function navIndex(key: string, current: number, count: number): number | null {
  if (count <= 0) return null;
  const i = Math.min(Math.max(current, 0), count - 1);
  switch (key) {
    case 'ArrowRight':
    case 'ArrowDown':
      return Math.min(count - 1, i + 1);
    case 'ArrowLeft':
    case 'ArrowUp':
      return Math.max(0, i - 1);
    case 'Home':
      return 0;
    case 'End':
      return count - 1;
    case 'PageUp':
      return Math.max(0, i - 7);
    case 'PageDown':
      return Math.min(count - 1, i + 7);
    default:
      return null;
  }
}
