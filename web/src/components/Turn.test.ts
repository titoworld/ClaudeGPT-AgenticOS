// Which synthesis a debate shows when the first attempt is not the result (audit A1).
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TurnEvent } from '../lib/protocol';
import {
  degradedSynthesisEvents,
  fallbackSynthesisEvents,
  keptRevisionEvents,
  keptRevisionMessages,
  oneSurvivorDebateEvents,
  QUICK_DEBATE,
  quickDebateMessages,
} from '../lib/test-fixtures';
import { cleanup, render, textOf } from '../lib/test-render';
import { applyTurnEvent, createLiveTurn, turnsFromMessages, type TurnView } from '../lib/turns.svelte';

vi.mock('../lib/app.svelte', () => ({ app: { eurPerUsd: 0.86 } }));

import Turn from './Turn.svelte';

afterEach(cleanup);

function liveDebate(events: TurnEvent[]): TurnView {
  const turn = createLiveTurn({
    requestId: events[0]!.request_id,
    question: 'Pregunta?',
    mode: 'debate',
    options: QUICK_DEBATE,
    conversationId: 8,
  });
  for (const ev of events) applyTurnEvent(turn, ev);
  return turn;
}

/** Text of the synthesis card of a rendered turn. */
function synthesisCard(turn: TurnView): string {
  const card = render(Turn, { turn, plannedRounds: 0 }).querySelector('article.card.synthesis');
  expect(card).not.toBeNull();
  return textOf(card);
}

describe('Turn: the synthesis card (A1)', () => {
  it("shows the other agent's synthesis after the first attempt failed, as after a reload", () => {
    const live = synthesisCard(liveDebate(fallbackSynthesisEvents()));
    expect(live).toContain('Síntesi per ChatGPT');
    expect(live).toContain('Síntesi de ChatGPT');
    expect(live).toContain("Claude no ha pogut fer la síntesi; l'ha feta ChatGPT.");
    expect(live).not.toContain('Síntesi a mig');
    expect(live).not.toContain('No ha pogut respondre');

    const [stored] = turnsFromMessages(quickDebateMessages({ agent: 'chatgpt', content: 'Síntesi de ChatGPT' }), 8);
    expect(synthesisCard(stored!)).toBe(live);
  });

  it('shows the fallback attempt while it runs, not the failed one', () => {
    const events = fallbackSynthesisEvents();
    const cut = events.findIndex((e) => e.type === 'stream.completed' && e.stream_id === 's2');
    const card = synthesisCard(liveDebate(events.slice(0, cut)));
    expect(card).toContain('Síntesi per ChatGPT');
    expect(card).toContain('Escrivint…');
    expect(card).toContain('Claude no ha pogut fer la síntesi; ara la fa ChatGPT.');
    expect(card).not.toContain('Síntesi a mig');
    expect(card).not.toContain('No ha pogut respondre');
  });

  it('shows the answer kept as final when nobody could synthesize, as after a reload', () => {
    const live = synthesisCard(liveDebate(degradedSynthesisEvents()));
    expect(live).toContain('Síntesi per Claude');
    expect(live).toContain('Resposta de Claude');
    expect(live).toContain("No s'ha pogut fer la síntesi: es mostra l'última resposta de Claude.");
    expect(live).not.toContain('Error de Claude.');
    expect(live).not.toContain('No ha pogut respondre');

    const stored = quickDebateMessages({ agent: 'claude', content: 'Resposta de Claude', degraded: true });
    expect(synthesisCard(turnsFromMessages(stored, 8)[0]!)).toBe(live);
  });

  it('says both things when the answer kept as final was itself cut off', () => {
    const events = degradedSynthesisEvents().map((e) =>
      e.type === 'stream.completed' && e.stream_id === 's3' ? { ...e, truncated: true } : e,
    );
    const live = synthesisCard(liveDebate(events));
    expect(live).toContain("No s'ha pogut fer la síntesi: es mostra l'última resposta de Claude.");
    expect(live).toContain('Incompleta');
    expect(live).toContain("Resposta incompleta: s'ha tallat abans d'acabar.");

    const stored = quickDebateMessages({ agent: 'claude', content: 'Resposta de Claude', degraded: true }).map((m) =>
      m.id === 63 ? { ...m, meta: { ...m.meta, truncated: true as const, finish_reason: 'max_tokens' } } : m,
    );
    const reloaded = synthesisCard(turnsFromMessages(stored, 8)[0]!);
    expect(reloaded).toContain("No s'ha pogut fer la síntesi: es mostra l'última resposta de Claude.");
    expect(reloaded).toContain("Resposta incompleta: s'ha arribat al límit de sortida.");
  });

  it("marks the partner's answer kept after an agent failed its first answer", () => {
    const live = synthesisCard(liveDebate(oneSurvivorDebateEvents()));
    expect(live).toContain('Síntesi per ChatGPT');
    expect(live).toContain("No s'ha pogut fer la síntesi: es mostra l'última resposta de ChatGPT.");

    const stored = quickDebateMessages({ agent: 'chatgpt', content: 'Resposta de ChatGPT', degraded: true }, ['chatgpt']);
    expect(synthesisCard(turnsFromMessages(stored, 8)[0]!)).toBe(live);
  });
});

describe('Turn: revision rounds', () => {
  it('tells each round which revisions keep the previous answer, live and after a reload', () => {
    const turn = createLiveTurn({ requestId: 'req-k', question: 'Pregunta?', mode: 'debate', conversationId: 9 });
    for (const ev of keptRevisionEvents()) applyTurnEvent(turn, ev);
    const claudeRevision = (t: TurnView) =>
      textOf(render(Turn, { turn: t, plannedRounds: 1 }).querySelector('section[aria-label="Revisió de Claude"]'));

    const live = claudeRevision(turn);
    expect(live).toContain("Revisió incompleta: s'ha tallat abans d'acabar. Es manté la resposta anterior.");
    expect(live).not.toContain('Resposta revisada');

    const reloaded = claudeRevision(turnsFromMessages(keptRevisionMessages(), 9)[0]!);
    expect(reloaded).toContain("Revisió incompleta: s'ha arribat al límit de sortida. Es manté la resposta anterior.");
    expect(reloaded).not.toContain('Resposta revisada');
  });
});
