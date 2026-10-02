// A refine turn («Perfecciona», docs/adr/0010-refine-mode.md) in the turn model: what
// its events build live (the rounds, what each call is, the stop the owner asked for, why
// it ended), and the same view rebuilt from its stored messages after a reload.
import { describe, expect, it } from 'vitest';
import type { Message, TurnEvent, Usage } from './protocol';
import { outcome, priced, sequence } from './test-fixtures';
import {
  BUDGET_WORDS,
  cancelledRefineEvents,
  cancelledRefineMessages,
  copiedMergeEvents,
  copiedMergeMessages,
  countWords,
  declinedEditEvents,
  declinedEditMessages,
  editorFallbackEvents,
  editorFallbackMessages,
  failedReviewEvents,
  failedReviewMessages,
  NO_CHANGES,
  OVER_BUDGET,
  REFINE_OPTIONS,
  REFINE_TURN_OPTIONS,
  refineEvents,
  refineEventsUntil,
  refineMessages,
  refusedAnswerMessages,
  rejectedMergeCancelledEvents,
  rejectedMergeCancelledMessages,
  reviewCancelledEvents,
  reviewCancelledMessages,
  R2_CHATGPT,
  R3_CLAUDE,
  ROUND_TOTAL,
  ROUND_USAGE,
  shortenCancelledEvents,
  shortenCancelledMessages,
  shortenFailedEvents,
  shortenFailedMessages,
  V1,
  V2,
} from './test-refine';
import {
  addUsage,
  applyTurnEvent,
  createLiveTurn,
  INCOMPLETE_KIND,
  latestRefineScore,
  turnsFromMessages,
  type RefineRoundView,
  type TurnView,
} from './turns.svelte';

function liveRefine(events: TurnEvent[]): TurnView {
  const turn = createLiveTurn({
    requestId: events[0]!.request_id,
    question: 'Escriu un pla de llançament per a la beta.',
    mode: 'refine',
    options: REFINE_TURN_OPTIONS,
    conversationId: 12,
  });
  for (const ev of events) applyTurnEvent(turn, ev);
  return turn;
}

const stored = (messages: Message[]): TurnView => turnsFromMessages(messages, 12)[0]!;

/** What each call of a turn is, in order: "agent kind round role status". */
const calls = (turn: TurnView): string[] =>
  turn.streams.map((s) => `${s.agent} ${s.kind} ${s.round} ${s.refineRole} ${s.status}`);

const ROUND_2: RefineRoundView = {
  round: 2,
  version: 2,
  accepted: true,
  reason: null,
  words: countWords(V2),
  budgetWords: BUDGET_WORDS,
  changes: [{ kind: 'defect', text: 'La fase 2 té data' }],
  proposals: { claude: 1, chatgpt: 0 },
  scores: { claude: 70, chatgpt: 85 },
  converged: false,
  usage: ROUND_USAGE[2],
  total: ROUND_TOTAL[2],
};

describe('a live refine turn', () => {
  it('knows what each call is from the phase it started in, and then from its meta', () => {
    const turn = liveRefine(refineEvents());
    expect(calls(turn)).toEqual([
      'claude answer 0 answer done',
      'chatgpt answer 0 answer done',
      'claude revision 1 version done',
      'claude revision 2 review done',
      'chatgpt revision 2 review done',
      'claude revision 2 version done',
      'claude revision 3 review done',
      'chatgpt revision 3 review done',
      'claude revision 3 version done',
      'claude revision 3 version done',
      'claude synthesis 3 final done',
    ]);
    // While the editor writes, before its meta arrives, the phase says it is a version.
    const writing = liveRefine(refineEventsUntil((e) => e.type === 'stream.completed' && e.stream_id === 'e2'));
    expect(writing.streams.at(-1)).toMatchObject({ refineRole: 'version', status: 'streaming', refine: null, text: V2 });
  });

  it("keeps each message's meta.refine as its stream.completed brings it", () => {
    const turn = liveRefine(refineEvents());
    const byId = (id: string) => turn.streams.find((s) => s.id === id)!;
    expect(byId('e1').refine).toEqual({
      role: 'version', version: 1, words: countWords(V1), budgetWords: BUDGET_WORDS, accepted: true, reason: null,
      changelog: [
        { kind: 'merge', text: "L'estructura en fases de Claude" },
        { kind: 'merge', text: 'La fase de seguiment de ChatGPT' },
      ],
      copiedFrom: null,
    });
    expect(byId('r2c').refine).toEqual({
      role: 'review', score: 70, unchanged: false,
      changes: [{ kind: 'defect', text: 'Fase 2: no té data — sense data no es pot planificar' }],
    });
    expect(byId('r2g').refine).toEqual({ role: 'review', score: 85, unchanged: true, changes: [] });
    expect(byId('s3').refine).toMatchObject({ role: 'version', version: 3, accepted: false, reason: OVER_BUDGET });
    expect(byId('f').refine).toEqual({
      role: 'final', version: 2, words: countWords(V2), budgetWords: BUDGET_WORDS, stopReason: 'owner',
    });
  });

  it('records the end of every round as refine.round says it', () => {
    const turn = liveRefine(refineEvents());
    expect(turn.refineRounds.map((r) => [r.round, r.version, r.accepted, r.reason])).toEqual([
      [1, 1, true, null],
      [2, 2, true, null],
      [3, 2, false, OVER_BUDGET],
    ]);
    expect(turn.refineRounds[1]).toEqual(ROUND_2);
    expect(turn.refineRounds[0]).toMatchObject({ proposals: { claude: null, chatgpt: null }, scores: { claude: null, chatgpt: null } });
  });

  it('ignores a replayed refine.round, as every event it already has', () => {
    const events = refineEvents();
    const turn = liveRefine(events);
    const round = events.find((e) => e.type === 'refine.round')!;
    expect(applyTurnEvent(turn, round)).toBe(false);
    expect(turn.refineRounds).toHaveLength(3);
  });

  it('says it will stop after the round turn.stopping names, and why it ended', () => {
    const before = liveRefine(refineEventsUntil((e) => e.type === 'turn.stopping'));
    before.stopRequested = true; // the owner asked (app.stopAfterRound)
    expect(before.stoppingRound).toBeNull();
    const stopping = refineEvents().find((e) => e.type === 'turn.stopping')!;
    applyTurnEvent(before, stopping);
    expect(before.stoppingRound).toBe(3);
    expect(before.stopRequested).toBe(false);
    expect(before.stopReason).toBeNull();

    const ended = liveRefine(refineEvents());
    expect(ended.status).toBe('done');
    expect(ended.stopReason).toBe('owner');
    expect(ended.finalMessageIds).toEqual([81]);
  });

  it('takes why it ended from turn.completed', () => {
    for (const reason of ['converged', 'unchanged', 'max_rounds', 'budget', 'failed'] as const) {
      expect(liveRefine(refineEvents('req-p', reason)).stopReason).toBe(reason);
    }
  });

  it('a turn cancelled with «Atura ara» keeps its last version as final', () => {
    const turn = liveRefine(cancelledRefineEvents());
    expect(turn.status).toBe('cancelled');
    // turn.cancelled does not say why; the final message it stored does.
    expect(turn.stopReason).toBe('owner');
    expect(turn.streams.find((s) => s.refineRole === 'final')?.text).toBe(V2);
    expect(calls(turn).filter((c) => c.endsWith('interrupted'))).toEqual([
      'claude revision 3 review interrupted',
      'chatgpt revision 3 review interrupted',
    ]);
  });

  it('a turn the app replays after a reload learns its mode before its calls', () => {
    // app.svelte.ts creates a turn it follows without knowing it as a debate, until turn.started.
    const turn = createLiveTurn({ requestId: 'req-p', question: '', mode: 'debate' });
    for (const ev of refineEvents()) applyTurnEvent(turn, ev);
    expect(turn.mode).toBe('refine');
    expect(calls(turn)).toEqual(calls(liveRefine(refineEvents())));
  });

  it('other modes have no refine roles', () => {
    const turn = createLiveTurn({ requestId: 'r', question: 'q', mode: 'debate' });
    for (const ev of sequence('r', [
      { type: 'turn.started', conversation_id: 1, turn_id: 2, mode: 'debate', new_conversation: false },
      { type: 'phase', phase: 'revision', round: 1 },
      { type: 'stream.started', stream_id: 'c1', agent: 'claude', kind: 'revision', round: 1, model: 'm' },
    ])) applyTurnEvent(turn, ev);
    expect(turn.streams[0]?.refineRole).toBeNull();
  });
});

describe('a stored refine turn (after a reload)', () => {
  it('rebuilds the same calls, metas, rounds and end as the live one', () => {
    const live = liveRefine(refineEvents());
    const turn = stored(refineMessages());
    expect(turn.mode).toBe('refine');
    expect(turn.status).toBe('done');
    expect(turn.options?.refine).toEqual(REFINE_OPTIONS);
    expect(calls(turn)).toEqual(calls(live));
    expect(turn.streams.map((s) => s.refine)).toEqual(live.streams.map((s) => s.refine));
    expect(turn.refineRounds).toEqual(live.refineRounds);
    expect(turn.stopReason).toBe('owner');
    expect(turn.finalMessageIds).toEqual([81]);
    expect(turn.stoppingRound).toBeNull();
  });

  it('a cancelled one keeps its final version and the rounds that ended', () => {
    const turn = stored(cancelledRefineMessages());
    expect(turn.status).toBe('cancelled');
    expect(turn.stopReason).toBe('owner');
    expect(turn.refineRounds.map((r) => r.round)).toEqual([1, 2]);
    expect(turn.refineRounds).toEqual(liveRefine(cancelledRefineEvents()).refineRounds);
    expect(turn.streams.find((s) => s.refineRole === 'final')?.text).toBe(V2);
  });

  it('reads the options from meta.options.refine too', () => {
    const messages = refineMessages().map((m) =>
      m.kind === 'question' ? { ...m, meta: { ...m.meta, refine: undefined, options: REFINE_TURN_OPTIONS } } : m,
    );
    expect(stored(messages).options?.refine).toEqual(REFINE_OPTIONS);
  });

  it('a round where nobody found anything to change wrote no version', () => {
    const messages = refineMessages().filter((m) => m.round !== 3 || m.kind === 'synthesis');
    const unchanged = (id: number, agent: 'claude' | 'chatgpt', score: number): Message => ({
      id, turn_id: 70, kind: 'revision', agent, round: 3, final: false, content: 'UNCHANGED', created_at: '2026-10-02T10:00:00Z',
      meta: { model: 'm', usage: priced(100, 10, 0.001), refine: { role: 'review', score, unchanged: true, changes: [] } },
    });
    const turn = stored([...messages.slice(0, -1), unchanged(77, 'claude', 95), unchanged(78, 'chatgpt', 96), messages.at(-1)!]);
    expect(turn.refineRounds.at(-1)).toMatchObject({
      round: 3, version: 2, accepted: false, reason: 'Cap dels dos hi ha trobat res a canviar.',
      proposals: { claude: 0, chatgpt: 0 }, scores: { claude: 95, chatgpt: 96 }, changes: [],
    });
  });

  it('a round that ended without any version, when the models failed, says so as the live one did', () => {
    // Round 3's edit failed with both editors: its reviews are stored, no version is.
    const messages = refineMessages('failed')
      .filter((m) => m.id !== 79 && m.id !== 80)
      .map((m) =>
        m.kind === 'question'
          ? {
              ...m,
              meta: {
                ...m.meta,
                outcome: outcome({
                  status: 'completed', usage: ROUND_TOTAL[3], final_message_ids: [81], stop_reason: 'failed',
                  failures: [
                    { agent: 'claude', kind: 'unavailable', message: 'No respon.', round: 3 },
                    { agent: 'chatgpt', kind: 'unavailable', message: 'No respon.', round: 3 },
                  ],
                }),
              },
            }
          : m,
      );
    const turn = stored(messages);
    expect(turn.refineRounds.at(-1)).toMatchObject({
      round: 3, version: 2, accepted: false, reason: 'Els models han fallat i la ronda no ha escrit cap versió.',
      proposals: { claude: 0, chatgpt: 1 },
    });
    expect(turn.streams.filter((s) => s.status === 'failed').map((s) => `${s.agent} ${s.refineRole}`)).toEqual([
      'claude version',
      'chatgpt version',
    ]);
  });

  it('marks the last round converged when that is why it stopped', () => {
    const turn = stored(refineMessages('converged'));
    expect(turn.stopReason).toBe('converged');
    expect(turn.refineRounds.map((r) => r.converged)).toEqual([false, false, true]);
  });

  it('shows a review that failed where it failed, with no proposals', () => {
    const messages = refineMessages()
      .filter((m) => m.id !== 75)
      .map((m) =>
        m.kind === 'question'
          ? {
              ...m,
              meta: {
                ...m.meta,
                outcome: outcome({
                  status: 'completed', usage: ROUND_TOTAL[3], final_message_ids: [81], stop_reason: 'owner',
                  failures: [{ agent: 'chatgpt', kind: 'timeout', message: 'Temps esgotat.', round: 2 }],
                }),
              },
            }
          : m,
      );
    const turn = stored(messages);
    const failed = turn.streams.filter((s) => s.status === 'failed');
    expect(failed.map((s) => `${s.agent} ${s.round} ${s.refineRole} ${s.error?.message}`)).toEqual([
      'chatgpt 2 review Temps esgotat.',
    ]);
    // In the order of the round: the reviews, then the version.
    expect(calls(turn).slice(3, 6)).toEqual([
      'claude revision 2 review done',
      'chatgpt revision 2 review failed',
      'claude revision 2 version done',
    ]);
    expect(turn.refineRounds[1]).toMatchObject({ proposals: { claude: 1, chatgpt: null }, scores: { claude: 70, chatgpt: null } });
  });

  it('a refine turn that never ended is incomplete', () => {
    const messages = refineMessages().map((m) => (m.kind === 'question' ? { ...m, meta: { ...m.meta, outcome: null } } : m));
    const turn = stored(messages);
    expect(turn.status).toBe('failed');
    expect(turn.error?.kind).toBe(INCOMPLETE_KIND);
  });
});

describe('latestRefineScore: how fit for the brief the reviews find the document', () => {
  it('averages the scores of the latest round that has any', () => {
    expect(latestRefineScore(liveRefine(refineEvents()))).toBe(90);
    expect(latestRefineScore(liveRefine(refineEventsUntil((e) => e.type === 'refine.round' && e.round === 3)))).toBe(90);
    expect(latestRefineScore(liveRefine(refineEventsUntil((e) => e.type === 'stream.completed' && e.stream_id === 'r3c')))).toBe(77.5);
    expect(latestRefineScore(liveRefine(refineEventsUntil((e) => e.type === 'phase' && e.round === 2)))).toBeNull();
  });
});

/** Rounds with their costs to the nanodollar: the live ones add the calls, a reload may subtract. */
const billed = (rounds: RefineRoundView[]) => {
  const round9 = (u: Usage | null) => u && { ...u, cost_usd: u.cost_usd == null ? null : Math.round(u.cost_usd * 1e9) / 1e9 };
  return rounds.map((r) => ({ ...r, usage: round9(r.usage), total: round9(r.total) }));
};

describe('a stored refine turn bills each round as the live one did (P8 review)', () => {
  it('counts a billed call that failed in the round it failed in', () => {
    const live = liveRefine(failedReviewEvents());
    const turn = stored(failedReviewMessages());
    expect(billed(turn.refineRounds)).toEqual(billed(live.refineRounds));
    expect(turn.refineRounds[1]?.usage?.cost_usd).toBeCloseTo(ROUND_USAGE[2].cost_usd!, 12);
  });

  it('counts an attempt another model declined in the round of the call it came before', () => {
    expect(billed(stored(declinedEditMessages()).refineRounds)).toEqual(billed(liveRefine(declinedEditEvents()).refineRounds));
  });

  it("ends with the turn's total, the one its footer shows", () => {
    for (const messages of [failedReviewMessages(), declinedEditMessages()]) {
      const turn = stored(messages);
      expect(turn.refineRounds.at(-1)?.total?.cost_usd).toBeCloseTo(turn.usage!.cost_usd!, 12);
      expect(turn.refineRounds.at(-1)?.total?.input_tokens).toBe(turn.usage!.input_tokens);
    }
  });

  it('puts what no failure explains (a check of a PDF for ChatGPT) before round 1', () => {
    const check = priced(2000, 150, 0.0081);
    const messages = refineMessages().map((m) =>
      m.kind === 'question'
        ? { ...m, meta: { ...m.meta, outcome: outcome({ status: 'completed', usage: addUsage(ROUND_TOTAL[3], check), final_message_ids: [81], stop_reason: 'owner' }) } }
        : m,
    );
    const rounds = stored(messages).refineRounds;
    expect(rounds.map((r) => r.usage?.cost_usd)).toEqual(liveRefine(refineEvents()).refineRounds.map((r) => r.usage?.cost_usd));
    expect(rounds[0]?.total?.cost_usd).toBeCloseTo(ROUND_TOTAL[1].cost_usd! + 0.0081, 12);
  });

  it('with billed failures in more than one round, the last of them gets them all: the totals from it on are exact', () => {
    // Claude's review of round 3 failed as well, billed like ChatGPT's of round 2.
    const messages = failedReviewMessages()
      .filter((m) => m.id !== 77)
      .map((m) => {
        if (m.kind === 'question') {
          const failures = [
            { agent: 'chatgpt' as const, kind: 'invalid', message: NO_CHANGES, round: 2 },
            { agent: 'claude' as const, kind: 'invalid', message: NO_CHANGES, round: 3 },
          ];
          return { ...m, meta: { ...m.meta, outcome: outcome({ status: 'completed', usage: ROUND_TOTAL[3], final_message_ids: [81], stop_reason: 'owner', failures }) } };
        }
        return m.id === 81 ? { ...m, meta: { ...m.meta, unstored_usage: addUsage(R2_CHATGPT, R3_CLAUDE) } } : m;
      });
    const [, round2, round3] = stored(messages).refineRounds;
    expect(round3?.total?.cost_usd).toBeCloseTo(ROUND_TOTAL[3].cost_usd!, 12);
    expect(round3?.usage?.cost_usd).toBeCloseTo(ROUND_USAGE[3].cost_usd! + R2_CHATGPT.cost_usd!, 12);
    // Before it, at most what the turn had billed.
    expect(round2?.total?.cost_usd).toBeCloseTo(ROUND_TOTAL[2].cost_usd! - R2_CHATGPT.cost_usd!, 12);
  });

  it('a failure before round 1 adds to what else came before it (a check of a PDF)', () => {
    const turn = stored(refusedAnswerMessages());
    expect(turn.refineRounds.map((r) => r.usage)).toEqual([ROUND_USAGE[1]]);
    // Round 1 is the last: its total is the turn's, the answer, the check, the refusal and the merge.
    expect(turn.refineRounds[0]?.total?.cost_usd).toBeCloseTo(turn.usage!.cost_usd!, 12);
    expect(turn.refineRounds[0]?.total?.input_tokens).toBe(turn.usage!.input_tokens);
  });

  it('a round of calls without a price costs nothing once the turn has one, as live', () => {
    const live = liveRefine(copiedMergeEvents());
    expect(live.refineRounds[0]?.usage?.cost_usd).toBe(0);
    expect(stored(copiedMergeMessages()).refineRounds).toEqual(live.refineRounds);
  });
});

describe('a stored refine turn that did not complete keeps only the rounds that ended (P8 review)', () => {
  it('a round whose other review «Atura ara» cut never ended, though the review stored found nothing to change', () => {
    const turn = stored(reviewCancelledMessages());
    expect(turn.refineRounds.map((r) => r.round)).toEqual([1]);
    expect(turn.refineRounds).toEqual(liveRefine(reviewCancelledEvents()).refineRounds);
  });

  it('a round whose version over the word limit was being shortened never ended', () => {
    const turn = stored(shortenCancelledMessages());
    expect(turn.refineRounds.map((r) => r.round)).toEqual([1, 2]);
    expect(turn.refineRounds).toEqual(liveRefine(shortenCancelledEvents()).refineRounds);
  });

  it('a merge that was not accepted does not end round 1', () => {
    expect(stored(rejectedMergeCancelledMessages()).refineRounds).toEqual([]);
    expect(liveRefine(rejectedMergeCancelledEvents()).refineRounds).toEqual([]);
  });

  it('a round whose shortening failed ended, as it did live', () => {
    const turn = stored(shortenFailedMessages());
    expect(turn.refineRounds.map((r) => [r.round, r.reason])).toEqual([[1, null], [2, null], [3, OVER_BUDGET]]);
    expect(turn.refineRounds).toEqual(liveRefine(shortenFailedEvents()).refineRounds);
  });
});

describe('the calls of a stored refine turn, in the order they ran (P8 review)', () => {
  it('an editor call that failed comes before the version the next editor wrote, as live', () => {
    const turn = stored(editorFallbackMessages());
    expect(calls(turn)).toEqual(calls(liveRefine(editorFallbackEvents())));
    expect(calls(turn).slice(2, 4)).toEqual(['claude revision 1 version failed', 'chatgpt revision 1 version done']);
    // Round 2: ChatGPT edits first, since Claude's edit failed before.
    expect(calls(turn).slice(6, 8)).toEqual(['chatgpt revision 2 version failed', 'claude revision 2 version done']);
  });

  it('a version 1 copied without a call comes after the merges that failed', () => {
    expect(calls(stored(copiedMergeMessages()))).toEqual(calls(liveRefine(copiedMergeEvents())));
  });

  it("the reviews of a round in the order they started, Claude's first, whichever was stored first", () => {
    // ChatGPT's review of round 2 ended (and was stored) before Claude's.
    const swapped = refineMessages()
      .map((m) => (m.id === 74 ? { ...m, id: 75 } : m.id === 75 ? { ...m, id: 74 } : m))
      .sort((a, b) => a.id - b.id);
    expect(calls(stored(swapped))).toEqual(calls(liveRefine(refineEvents())));
  });
});
