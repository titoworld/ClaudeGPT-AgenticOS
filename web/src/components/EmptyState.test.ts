// The empty conversation: what each mode does, «Perfecciona» included (ADR 0010).
import { flushSync } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';
import { i18n } from '../lib/i18n/index.svelte';
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

describe('EmptyState in English and Spanish', () => {
  afterEach(() => i18n.set('ca'));

  it('repaints the modes and the examples when the language changes', () => {
    i18n.set('en');
    const picked: string[] = [];
    const root = render(EmptyState, { onPick: (prompt: string) => picked.push(prompt) });
    const names = () => [...root.querySelectorAll('.modes li strong')].map((s) => textOf(s));
    expect(textOf(root.querySelector('h1'))).toBe('What do you want to ask the council?');
    expect(names()).toEqual(['Solo', 'Duel', 'Council', 'Refine']);
    expect(textOf(root.querySelector('.examples h2'))).toBe('Try an example');

    i18n.set('es');
    flushSync();
    expect(textOf(root.querySelector('h1'))).toBe('¿Qué quieres preguntar al consejo?');
    expect(names()).toEqual(['Solo', 'Duelo', 'Consejo', 'Perfecciona']);
    root.querySelector<HTMLButtonElement>('.example')!.click();
    expect(picked).toEqual(['Explícame la diferencia entre TCP y UDP con una analogía cotidiana.']);
  });
});
