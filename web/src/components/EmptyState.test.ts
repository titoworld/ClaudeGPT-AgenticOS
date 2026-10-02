// The empty conversation: what each mode does, «Perfecciona» included (ADR 0010).
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, textOf } from '../lib/test-render';
import EmptyState from './EmptyState.svelte';

afterEach(cleanup);

describe('EmptyState', () => {
  it('describes the four modes', () => {
    const root = render(EmptyState, { onPick: () => {} });
    const modes = [...root.querySelectorAll('.modes li')].map((li) => textOf(li.querySelector('strong')));
    expect(modes).toEqual(['Solo', 'Duel', 'Consell', 'Perfecciona']);
    expect(textOf(root.querySelectorAll('.modes li')[3])).toBe(
      "Perfecciona Les dues IA milloren un sol document ronda rere ronda fins que l'aturis.",
    );
  });
});
