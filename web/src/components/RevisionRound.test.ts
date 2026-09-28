// A revision round after a reload: notes of unchanged answers and cut-off revisions.
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Message, MessageMeta } from '../lib/protocol';
import { debateMessages, keptRevisionEvents, keptRevisionMessages } from '../lib/test-fixtures';
import { cleanup, render, textOf } from '../lib/test-render';
import {
  applyTurnEvent,
  createLiveTurn,
  keptAnswers,
  revisionRounds,
  turnsFromMessages,
  type TurnView,
} from '../lib/turns.svelte';

vi.mock('../lib/app.svelte', () => ({ app: { eurPerUsd: 0.86 } }));

import RevisionRound from './RevisionRound.svelte';

afterEach(cleanup);

/** Renders one revision round of a turn, as Turn.svelte does. */
function renderRound(turn: TurnView, round = 1): { claude: string; chatgpt: string; target: HTMLElement } {
  const group = revisionRounds(turn).find((g) => g.round === round)!;
  const target = render(RevisionRound, {
    round: group.round,
    streams: group.streams,
    threshold: 85,
    active: false,
    kept: keptAnswers(turn),
  });
  return {
    target,
    claude: textOf(target.querySelector('section[aria-label="Revisió de Claude"]')),
    chatgpt: textOf(target.querySelector('section[aria-label="Revisió de ChatGPT"]')),
  };
}

/** Stored messages with extra (or replaced) meta for some message ids. */
function withMeta(messages: Message[], extra: Record<number, MessageMeta>): Message[] {
  return messages.map((m) => (extra[m.id] ? { ...m, meta: { ...m.meta, ...extra[m.id] } } : m));
}

/** Round 1 of the stored debate (Claude: message 43, ChatGPT: message 44, unchanged), with extra meta. */
function roundOne(extra: Record<number, MessageMeta>): { claude: string; chatgpt: string; target: HTMLElement } {
  return renderRound(turnsFromMessages(withMeta(debateMessages(), extra))[0]!);
}

describe('RevisionRound', () => {
  it('shows the note of an unchanged answer next to «Sense canvis»', () => {
    const { claude, chatgpt, target } = roundOne({ 44: { unchanged_note: 'my previous answer already covers this' } });
    expect(chatgpt).toContain('Sense canvis');
    expect(chatgpt).toContain('my previous answer already covers this');
    expect(textOf(target.querySelector('.unchanged-note'))).toBe('Nota del model: «my previous answer already covers this»');
    expect(claude).not.toContain('my previous answer');
  });

  it('reveals the hidden characters of the note, which is model text shown outside Markdown', () => {
    const { target } = roundOne({ 44: { unchanged_note: 'ja hi és‮ exe.txt​ i می‌خواهم' } });
    const note = target.querySelector('.unchanged-note')!;
    expect(textOf(note)).toBe('Nota del model: «ja hi és⟨U+202E⟩ exe.txt⟨U+200B⟩ i می‌خواهم»');
    expect(note.textContent).not.toMatch(/[‮​]/u);
    expect([...note.querySelectorAll('.invisible-char')].map((mark) => mark.getAttribute('title'))).toEqual([
      'Caràcter invisible o de control de direcció (U+202E)',
      'Caràcter invisible o de control de direcció (U+200B)',
    ]);
  });

  it('shows no note for a revision that changed its answer', () => {
    const { claude } = roundOne({ 43: { unchanged_note: 'nota' } });
    expect(claude).not.toContain('nota');
    expect(claude).not.toContain('Sense canvis');
  });

  it('marks a revision that was cut off', () => {
    const { claude, chatgpt } = roundOne({ 43: { truncated: true, finish_reason: 'max_tokens' } });
    expect(claude).toContain('Incompleta');
    expect(claude).toContain("Resposta incompleta: s'ha arribat al límit de sortida.");
    expect(claude).toContain('Resposta revisada');
    expect(claude).not.toContain('Es manté la resposta anterior');
    expect(chatgpt).not.toContain('Incompleta');
  });
});

describe('RevisionRound: revisions that keep the previous answer', () => {
  const storedKept = (extra: Record<number, MessageMeta> = {}): TurnView =>
    turnsFromMessages(withMeta(keptRevisionMessages(), extra))[0]!;

  it('says that the revision was cut off and the previous answer stays, not that the answer is incomplete', () => {
    // Message 83: Claude's reply was cut off in its critique; its content is the first answer again.
    const { claude } = renderRound(storedKept());
    expect(claude).toContain('Incompleta');
    expect(claude).toContain("Revisió incompleta: s'ha arribat al límit de sortida. Es manté la resposta anterior.");
    expect(claude).not.toContain('Resposta incompleta');
    expect(claude).not.toContain('Resposta revisada');
    expect(claude).not.toContain('Sense canvis');
  });

  it('says the same of an unchanged revision cut off after UNCHANGED', () => {
    const { chatgpt } = renderRound(storedKept({ 84: { truncated: true, finish_reason: 'max_tokens' } }));
    expect(chatgpt).toContain('Sense canvis');
    expect(chatgpt).toContain("Revisió incompleta: s'ha arribat al límit de sortida. Es manté la resposta anterior.");
    expect(chatgpt).not.toContain('Resposta incompleta');
  });

  it('marks a kept answer that was already incomplete', () => {
    // Message 84 keeps ChatGPT's first answer (82), which the content filter had cut off.
    const { claude, chatgpt } = renderRound(storedKept());
    expect(chatgpt).toContain('Sense canvis');
    expect(chatgpt).toContain('La resposta que es manté és incompleta: tallada pel filtre de contingut.');
    expect(chatgpt).not.toContain('Resposta revisada');
    expect(claude).not.toContain('La resposta que es manté és incompleta');
  });

  it('says that the previous answer stays when a complete reply brought no new answer', () => {
    const messages = keptRevisionMessages().map((m) => {
      if (m.id !== 83) return m;
      const { truncated: _t, finish_reason: _f, ...meta } = m.meta;
      return { ...m, meta: { ...meta, agreement: 70 } };
    });
    const { claude } = renderRound(turnsFromMessages(messages)[0]!);
    expect(claude).toContain('Es manté la resposta anterior.');
    expect(claude).not.toContain('Resposta revisada');
    expect(claude).not.toContain('Incompleta');
    expect(claude).not.toContain('Sense canvis');
  });

  it('keeps marking a new answer that was cut off as an incomplete answer', () => {
    const messages = keptRevisionMessages().map((m) => (m.id === 83 ? { ...m, content: 'Hola món, amb més con' } : m));
    const { claude } = renderRound(turnsFromMessages(messages)[0]!);
    expect(claude).toContain('Resposta revisada');
    expect(claude).toContain("Resposta incompleta: s'ha arribat al límit de sortida.");
    expect(claude).not.toContain('Es manté la resposta anterior');
  });

  it('shows the same live, where the event carries no reason', () => {
    const turn = createLiveTurn({ requestId: 'req-k', question: 'Pregunta?', mode: 'debate', conversationId: 9 });
    for (const ev of keptRevisionEvents()) applyTurnEvent(turn, ev);
    const { claude, chatgpt } = renderRound(turn);
    expect(claude).toContain("Revisió incompleta: s'ha tallat abans d'acabar. Es manté la resposta anterior.");
    expect(claude).not.toContain('Resposta incompleta');
    expect(claude).not.toContain('Resposta revisada');
    expect(chatgpt).toContain('Sense canvis');
    expect(chatgpt).toContain("La resposta que es manté és incompleta: s'ha tallat abans d'acabar.");
    expect(chatgpt).not.toContain('Resposta revisada');
  });

  it('keeps showing a revised answer while it streams, even when it begins with the previous one', () => {
    const turn = createLiveTurn({ requestId: 'req-k', question: 'Pregunta?', mode: 'debate', conversationId: 9 });
    const events = keptRevisionEvents();
    const kept = events.findIndex((e) => e.type === 'stream.delta' && e.stream_id === 'c1' && e.section === 'answer');
    for (const ev of events.slice(0, kept + 1)) applyTurnEvent(turn, ev);
    const { claude } = renderRound(turn);
    expect(claude).toContain('Resposta revisada');
    expect(claude).not.toContain('Es manté la resposta anterior');
  });

  it('follows the answer across rounds: a new answer clears the mark, a kept one carries it', () => {
    // Claude revises in round 1 (43) and keeps that in round 2 (45); ChatGPT keeps its first answer (42) twice.
    const cut = { truncated: true as const, finish_reason: 'max_tokens' };
    const turn = turnsFromMessages(withMeta(debateMessages(), { 41: cut, 42: cut }))[0]!;
    const two = renderRound(turn, 2);
    expect(two.chatgpt).toContain("La resposta que es manté és incompleta: s'ha arribat al límit de sortida.");
    expect(two.claude).not.toContain('és incompleta');
  });
});
