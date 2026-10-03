// Answers cut off before the end (truncated): what the card says and what it copies.
import { flushSync } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { i18n } from '../lib/i18n/index.svelte';
import type { MessageMeta } from '../lib/protocol';
import { message, sequence, usage } from '../lib/test-fixtures';
import { cleanup, render, textOf } from '../lib/test-render';
import { applyTurnEvent, createLiveTurn, turnsFromMessages, type StreamView } from '../lib/turns.svelte';

vi.mock('../lib/app.svelte', () => ({ app: { eurPerUsd: 0.86 } }));

import AnswerCard from './AnswerCard.svelte';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TEXT = 'Primer pas: instal·la el paquet. Segon pas: configura';

/** An answer as a reloaded conversation has it. */
function stored(meta: MessageMeta, kind: 'answer' | 'synthesis' = 'answer'): StreamView {
  const [turn] = turnsFromMessages([
    message({ id: 2, turn_id: 1, kind, agent: 'claude', content: TEXT, final: true, meta: { model: 'm', usage: usage(10, 20), ...meta } }),
  ]);
  return turn!.streams[0]!;
}

/** The same answer as the live stream.completed event leaves it. */
function live(truncated: boolean | undefined): StreamView {
  const turn = createLiveTurn({ requestId: 'r', question: 'q', mode: 'solo', target: 'claude' });
  const events = sequence('r', [
    { type: 'stream.started', stream_id: 'x', agent: 'claude', kind: 'answer', round: 0, model: 'm' },
    { type: 'stream.delta', stream_id: 'x', section: 'text', text: TEXT },
    {
      type: 'stream.completed', stream_id: 'x', message_id: 2, usage: usage(10, 20), latency_ms: 5, ttft_ms: 1,
      agreement: null, unchanged: false, ...(truncated === undefined ? {} : { truncated }),
    },
  ]);
  for (const ev of events) applyTurnEvent(turn, ev);
  return turn.streams[0]!;
}

function card(stream: StreamView, variant: 'answer' | 'synthesis' = 'answer') {
  const target = render(AnswerCard, { agent: 'claude', stream, active: false, variant });
  return {
    target,
    status: textOf(target.querySelector('header .status')),
    note: textOf(target.querySelector('.truncation-note')),
  };
}

describe('AnswerCard: truncated answers', () => {
  it('says the answer is incomplete and why (stored reason)', () => {
    const { target, status, note } = card(stored({ truncated: true, finish_reason: 'max_tokens' }));
    expect(status).toBe('Incompleta');
    expect(note).toBe("Resposta incompleta: s'ha arribat al límit de sortida.");
    expect(target.querySelector('.truncation-note')?.getAttribute('role')).toBe('note');
    expect(textOf(target)).toContain(TEXT);
  });

  it('explains the other reasons in Catalan, also on the synthesis card', () => {
    expect(card(stored({ truncated: true, finish_reason: 'content_filter' }, 'synthesis'), 'synthesis').note).toBe(
      'Resposta incompleta: tallada pel filtre de contingut.',
    );
    expect(card(stored({ truncated: true, finish_reason: 'incomplete' })).note).toBe('Resposta incompleta: interrompuda.');
  });

  it('marks it live too, where the event carries no reason', () => {
    const { status, note } = card(live(true));
    expect(status).toBe('Incompleta');
    expect(note).toBe("Resposta incompleta: s'ha tallat abans d'acabar.");
  });

  it('leaves complete answers as they were', () => {
    for (const stream of [live(undefined), live(false), stored({ finish_reason: 'max_tokens' })]) {
      const { target, status, note } = card(stream);
      expect(status).toBe('Fet');
      expect(note).toBe('');
      expect(textOf(target)).not.toContain('incompleta');
    }
  });

  it('still copies the partial answer', async () => {
    const written: string[] = [];
    vi.stubGlobal('isSecureContext', true);
    vi.stubGlobal('navigator', { clipboard: { writeText: async (text: string) => void written.push(text) } });
    const { target } = card(stored({ truncated: true, finish_reason: 'max_tokens' }));
    const button = target.querySelector<HTMLButtonElement>('button[aria-label="Copia la resposta"]');
    expect(button).not.toBeNull();
    button!.click();
    await vi.waitFor(() => expect(written).toEqual([TEXT]));
  });
});

describe('AnswerCard: the tokens of the answer (A7)', () => {
  it("shows what it read from the provider's cache and what it wrote to it", () => {
    const cache = { ...usage(3, 100, 10_000), cache_write_tokens: 20_000 };
    const meta = textOf(card(stored({ usage: cache })).target.querySelector('footer.meta'));
    expect(meta).toContain('3 → 100 tokens');
    expect(meta).toContain('10.000 llegits de la memòria cau');
    expect(meta).toContain('20.000 escrits a la memòria cau');
    // Nothing about the cache when it was not used.
    expect(textOf(card(stored({})).target.querySelector('footer.meta'))).not.toContain('memòria cau');
  });
});

describe('AnswerCard in English and Spanish', () => {
  afterEach(() => i18n.set('ca'));

  it('in English: a synthesis, its status and what the answer used', () => {
    i18n.set('en');
    const cache = { ...usage(3, 100, 10_000), cache_write_tokens: 20_000 };
    const { target, status } = card(stored({ usage: cache }, 'synthesis'), 'synthesis');
    expect(textOf(target.querySelector('.synth-title'))).toBe('Synthesis');
    expect(textOf(target.querySelector('.by'))).toBe('by Claude');
    expect(status).toBe('Done');
    const meta = target.querySelector('footer.meta')!;
    expect(textOf(meta)).toContain('3 → 100 tokens');
    expect(textOf(meta)).toContain('10,000 read from the cache');
    expect(textOf(meta)).toContain('20,000 written to the cache');
    expect(meta.querySelector('.model')?.getAttribute('title')).toBe('Model');
    expect([...meta.querySelectorAll('.item')].map((item) => item.getAttribute('title'))).toEqual([
      '3 input tokens · 100 output',
      "Input tokens reused from the provider's cache",
      'Input tokens the provider saved to its cache to reuse them',
    ]);
  });

  it('in Spanish: an agent that has not answered yet, and one that did not answer', () => {
    i18n.set('es');
    const waiting = render(AnswerCard, { agent: 'chatgpt', stream: null, active: true });
    expect(textOf(waiting.querySelector('header .status'))).toBe('Pensando…');
    const silent = render(AnswerCard, { agent: 'chatgpt', stream: null, active: false });
    expect(textOf(silent.querySelector('.empty'))).toBe('No ha respondido.');
    const { status } = card(live(undefined));
    expect(status).toBe('Hecho');
  });

  it('follows a change of language, the bars of its code blocks too', () => {
    const [turn] = turnsFromMessages([
      message({ id: 2, turn_id: 1, kind: 'answer', agent: 'claude', content: '```js\nlet x = 1;\n```', final: true, meta: { model: 'm' } }),
    ]);
    const target = render(AnswerCard, { agent: 'claude', stream: turn!.streams[0]!, active: false });
    expect(textOf(target.querySelector('header .status'))).toBe('Fet');
    expect(target.querySelector('.code-copy')?.textContent).toBe('Copia');
    i18n.set('en');
    flushSync();
    expect(textOf(target.querySelector('header .status'))).toBe('Done');
    expect([...target.querySelectorAll('.code-copy')].map((button) => button.textContent)).toEqual(['Copy']);
    expect(target.querySelector('.code-copy')?.getAttribute('aria-label')).toBe('Copy the code');
  });
});
