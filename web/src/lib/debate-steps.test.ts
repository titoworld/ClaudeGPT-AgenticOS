import { describe, expect, it } from 'vitest';
import { debateSteps } from './debate-steps';
import type { TurnEvent } from './protocol';
import {
  cancelledDebateEvents,
  cancelledDebateMessages,
  debateEvents,
  debateMessages,
  degradedSynthesisEvents,
  fallbackSynthesisEvents,
  QUICK_DEBATE,
  quickDebateMessages,
} from './test-fixtures';
import { applyTurnEvent, createLiveTurn, turnsFromMessages, type TurnView } from './turns.svelte';

const states = (turn: TurnView, plannedRounds = 2) =>
  debateSteps(turn, plannedRounds).map((s) => `${s.label}: ${s.stateLabel}`);

describe('debateSteps', () => {
  it('a reloaded debate that was stopped reads like it did live (F4)', () => {
    const live = createLiveTurn({
      requestId: 'req-x',
      question: 'Debat llarg',
      mode: 'debate',
      conversationId: 5,
      options: { debate: { rounds: 3, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: false },
    });
    for (const ev of cancelledDebateEvents()) applyTurnEvent(live, ev);
    const [stored] = turnsFromMessages(cancelledDebateMessages(), 5);

    const expected = ['Respostes: fet', 'Revisió 1: aturat', 'Revisió 2: pendent', 'Revisió 3: pendent', 'Síntesi: pendent'];
    expect(states(live)).toEqual(expected);
    expect(states(stored!)).toEqual(expected);
  });

  it('labels rounds left out "omès per consens" only when the debate reached consensus', () => {
    // Consensus in round 1 of 3: rounds 2 and 3 were skipped by consensus.
    const live = createLiveTurn({
      requestId: 'req-1',
      question: 'Pregunta?',
      mode: 'debate',
      options: { debate: { rounds: 3, consensus_threshold: 85, synthesizer: 'claude' }, use_cache: true },
    });
    for (const ev of debateEvents()) applyTurnEvent(live, ev);
    expect(states(live)).toEqual([
      'Respostes: fet',
      'Revisió 1: fet',
      'Revisió 2: fet',
      'Revisió 3: omès per consens',
      'Síntesi: fet',
    ]);

    // A finished debate without consensus (e.g. a degraded synthesis after a failure).
    const [stored] = turnsFromMessages(debateMessages());
    stored!.options!.debate.rounds = 3;
    stored!.consensus = { reached: false, round: 2, scores: { claude: 60, chatgpt: 50 } };
    expect(states(stored!).at(3)).toBe('Revisió 3: omès');
  });

  it('shows a running debate in progress', () => {
    const live = createLiveTurn({ requestId: 'req-x', question: 'q', mode: 'debate' });
    for (const ev of cancelledDebateEvents().slice(0, 9)) applyTurnEvent(live, ev);
    expect(states(live)).toEqual(['Respostes: fet', 'Revisió 1: en curs', 'Revisió 2: pendent', 'Síntesi: pendent']);
  });

  describe('the synthesis step follows its last attempt (A1)', () => {
    const liveDebate = (events: TurnEvent[]): TurnView => {
      const t = createLiveTurn({ requestId: events[0]!.request_id, question: 'q', mode: 'debate', options: QUICK_DEBATE });
      for (const ev of events) applyTurnEvent(t, ev);
      return t;
    };
    const upTo = (events: TurnEvent[], match: (e: TurnEvent) => boolean) => events.slice(0, events.findIndex(match));
    const synthesisStep = (t: TurnView) => debateSteps(t, 0).at(-1)!;

    it('a failed attempt is no progress while the other agent synthesizes', () => {
      const events = fallbackSynthesisEvents();
      const failed = liveDebate(upTo(events, (e) => e.type === 'stream.started' && e.stream_id === 's2'));
      expect(synthesisStep(failed)).toMatchObject({ label: 'Síntesi', state: 'active', progress: 0 });
      const streaming = liveDebate(upTo(events, (e) => e.type === 'stream.completed' && e.stream_id === 's2'));
      expect(synthesisStep(streaming)).toMatchObject({ state: 'active', progress: 0 });
      const done = liveDebate(upTo(events, (e) => e.type === 'turn.completed'));
      expect(synthesisStep(done)).toMatchObject({ state: 'active', progress: 1 });
    });

    it('reads the same live and after a reload', () => {
      const fallback = liveDebate(fallbackSynthesisEvents());
      const [stored] = turnsFromMessages(quickDebateMessages({ agent: 'chatgpt', content: 'Síntesi de ChatGPT' }));
      expect(states(fallback, 0)).toEqual(['Respostes: fet', 'Síntesi: fet']);
      expect(states(stored!, 0)).toEqual(states(fallback, 0));
      expect(debateSteps(stored!, 0).map((s) => s.progress)).toEqual(debateSteps(fallback, 0).map((s) => s.progress));

      const degraded = liveDebate(degradedSynthesisEvents());
      const [storedDegraded] = turnsFromMessages(quickDebateMessages({ agent: 'claude', content: 'Resposta de Claude', degraded: true }));
      expect(debateSteps(degraded, 0).map((s) => [s.stateLabel, s.progress])).toEqual([
        ['fet', 1],
        ['fet', 1],
      ]);
      expect(debateSteps(storedDegraded!, 0)).toEqual(debateSteps(degraded, 0));
    });
  });
});
