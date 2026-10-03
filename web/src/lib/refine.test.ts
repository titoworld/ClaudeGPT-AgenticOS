// What the refine view («Perfecciona», docs/adr/0010-refine-mode.md) shows of a turn:
// the versions of the living document, each round's block, the live status and why it
// stopped, the same live and after a reload.
import { afterEach, describe, expect, it } from 'vitest';
import { i18n } from './i18n/index.svelte';
import type { Message, TurnEvent } from './protocol';
import {
  changeKindLabel,
  currentVersion,
  keptVersion,
  liveStatus,
  previousAccepted,
  reasonText,
  refineVersions,
  roundBlocks,
  shownDocument,
  spentUsd,
  stopReasonText,
  STOP_REASON_LABEL,
} from './refine';
import { withOutcome } from './test-fixtures';
import {
  cancelledRefineEvents,
  cancelledRefineMessages,
  countWords,
  INTERNAL_ERROR,
  legacyMessages,
  mergeCancelledEvents,
  OVER_BUDGET,
  REFINE_TURN_OPTIONS,
  refineEvents,
  refineEventsUntil,
  refineMessages,
  rejectedMergeCancelledEvents,
  rejectedMergeCancelledMessages,
  shortenedMergeEvents,
  ROUND_TOTAL,
  V1,
  V2,
  V3_LONG,
  V3_SHORT,
} from './test-refine';
import { applyTurnEvent, createLiveTurn, REFINE_REASON_CODES, turnsFromMessages, type TurnView } from './turns.svelte';

function live(events: TurnEvent[]): TurnView {
  const turn = createLiveTurn({ requestId: events[0]!.request_id, question: 'q', mode: 'refine', options: REFINE_TURN_OPTIONS, conversationId: 12 });
  for (const ev of events) applyTurnEvent(turn, ev);
  return turn;
}

const until = (stop: (e: TurnEvent) => boolean) => live(refineEventsUntil(stop));
const stored = () => turnsFromMessages(refineMessages(), 12)[0]!;

/** Each version as the selector shows it: "number state words (retry)". */
const listed = (turn: TurnView) =>
  refineVersions(turn).map((v) => `v${v.number} ${v.state} ${v.words}${v.retry ? ' retry' : ''}`);

describe('refineVersions: the versions of the document', () => {
  it('lists every version the editor wrote, with whether it became the current one', () => {
    const expected = [
      `v1 accepted ${countWords(V1)}`,
      `v2 accepted ${countWords(V2)}`,
      `v3 rejected ${countWords(V3_LONG)}`,
      `v3 rejected ${countWords(V3_SHORT)} retry`,
    ];
    expect(listed(live(refineEvents()))).toEqual(expected);
    expect(listed(stored())).toEqual(expected);
    const rejected = refineVersions(stored()).at(-1)!;
    expect(rejected.reason).toBe(OVER_BUDGET);
    expect(rejected.changelog).toEqual([{ kind: 'requirement', text: 'Mètriques de seguiment' }]);
  });

  it('marks the shortening of a merge over the word limit, in round 1 too, but not another editor\'s merge', () => {
    expect(listed(live(shortenedMergeEvents()))).toEqual([
      `v1 rejected ${countWords(V3_LONG)}`,
      `v1 accepted ${countWords(V1)} retry`,
    ]);
    // Claude's merge was cut off and ChatGPT merges: a second merge, not a shortening.
    expect(refineVersions(live(rejectedMergeCancelledEvents())).map((v) => v.retry)).toEqual([false, false]);
  });

  it('counts the words of a version while it is being written', () => {
    const writing = until((e) => e.type === 'stream.completed' && e.stream_id === 'e2');
    expect(listed(writing)).toEqual([`v1 accepted ${countWords(V1)}`, `v2 writing ${countWords(V2)}`]);
  });

  it('numbers the versions as the engine does: the next one is the current one + 1, whatever the round', () => {
    // Round 3 kept version 2; round 4 writes the next version, which is the third.
    const events = refineEvents().filter((e) => e.type !== 'turn.completed' && !(e.type.startsWith('stream.') && 'stream_id' in e && e.stream_id === 'f'));
    let seq = events.length;
    const next = (draft: object) => ({ ...draft, request_id: 'req-p', seq: ++seq }) as TurnEvent;
    const turn = live([
      ...events,
      next({ type: 'phase', phase: 'review', round: 4 }),
      next({ type: 'phase', phase: 'edit', round: 4 }),
      next({ type: 'stream.started', stream_id: 'e4', agent: 'claude', kind: 'revision', round: 4, model: 'm' }),
      next({ type: 'stream.delta', stream_id: 'e4', section: 'answer', text: V2 + ' Final.' }),
    ]);
    expect(listed(turn).at(-1)).toBe(`v3 writing ${countWords(V2) + 1}`);
    expect(liveStatus(turn)).toEqual({ round: 4, text: 'Claude escriu la versió 3' });
  });

  it('a version cut by «Atura ara» is interrupted', () => {
    const events = refineEventsUntil((e) => e.type === 'stream.completed' && e.stream_id === 'e2');
    const cancelled = live([...events, { type: 'turn.cancelled', request_id: 'req-p', seq: events.length + 1, usage: ROUND_TOTAL[1] }]);
    expect(listed(cancelled).at(-1)).toBe(`v2 interrupted ${countWords(V2)}`);
  });

  it('the current version is the last accepted one, and each diff compares with the one before it', () => {
    const versions = refineVersions(stored());
    expect(currentVersion(versions)?.number).toBe(2);
    expect(previousAccepted(versions, versions[1]!)?.number).toBe(1);
    expect(previousAccepted(versions, versions[3]!)?.number).toBe(2); // a rejected one: against the current one then
    expect(previousAccepted(versions, versions[0]!)).toBeNull();
  });
});

describe('shownDocument: what the panel shows when the owner picks nothing', () => {
  it('the current version, and the final one once it ended', () => {
    expect(shownDocument(until((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3))).toMatchObject({
      kind: 'version', version: { number: 2 },
    });
    expect(shownDocument(live(refineEvents()))).toMatchObject({ kind: 'version', version: { number: 2 }, final: true });
    expect(shownDocument(stored())).toMatchObject({ kind: 'version', version: { number: 2 }, final: true });
  });

  it('the merge while it is being written, before there is any version', () => {
    const merging = until((e) => e.type === 'stream.completed' && e.stream_id === 'e1');
    expect(shownDocument(merging)).toMatchObject({ kind: 'version', version: { number: 1, state: 'writing' }, final: false });
    expect(shownDocument(until((e) => e.type === 'phase' && e.phase === 'edit'))).toBeNull();
  });

  it('the final message alone when no version of the turn came with it', () => {
    // A version 1 stored without a call has no stream of its own until it is reloaded.
    const turn = live(refineEvents());
    turn.streams = turn.streams.filter((s) => s.refineRole !== 'version');
    expect(shownDocument(turn)).toMatchObject({ kind: 'final', text: V2, number: 2 });
  });
});

describe('keptVersion: the version the turn keeps as its answer', () => {
  const reload = (messages: Parameters<typeof turnsFromMessages>[0]) => turnsFromMessages(messages, 12)[0]!;

  it('is the final one once it ended, and none while it runs', () => {
    expect(keptVersion(live(refineEvents()))).toBe(2);
    expect(keptVersion(stored())).toBe(2);
    expect(keptVersion(until((e) => e.type === 'turn.completed'))).toBeNull();
  });

  it('is the current one of a turn stopped with «Atura ara», whose final message the server stores without streaming it', () => {
    expect(keptVersion(live(cancelledRefineEvents('req-q', false)))).toBe(2);
    expect(keptVersion(live(cancelledRefineEvents()))).toBe(2);
    expect(keptVersion(reload(cancelledRefineMessages()))).toBe(2);
  });

  it('after a reload, only the final message stored says it: a cancelled turn whose final write failed kept none', () => {
    const lost = cancelledRefineMessages().filter((m) => m.kind !== 'synthesis');
    expect(keptVersion(reload(withOutcome(lost, { ...lost[0]!.meta.outcome!, final_message_ids: [] })))).toBeNull();
  });

  it('is none when the turn was cancelled before any version was accepted', () => {
    expect(keptVersion(live(mergeCancelledEvents()))).toBeNull();
    expect(keptVersion(live(rejectedMergeCancelledEvents()))).toBeNull();
    expect(keptVersion(reload(rejectedMergeCancelledMessages()))).toBeNull();
  });

  it('is none when it failed, or never ended, before storing it', () => {
    const events = refineEventsUntil((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3);
    const failed: TurnEvent = { type: 'turn.failed', request_id: 'req-p', seq: events.length + 1, error: INTERNAL_ERROR, usage: ROUND_TOTAL[2] };
    expect(keptVersion(live([...events, failed]))).toBeNull();
    expect(keptVersion(reload(withOutcome(refineMessages().filter((m) => m.id <= 78), null)))).toBeNull();
    // Once its final message was stored, that is the answer, whatever came after.
    expect(keptVersion(reload(withOutcome(refineMessages(), null)))).toBe(2);
  });
});

describe('roundBlocks: each round, with its end and its calls', () => {
  it('gives every round its reviews, its versions and how it ended, live and after a reload', () => {
    const describe = (turn: TurnView) =>
      roundBlocks(turn).map((b) => ({
        round: b.round,
        ended: b.summary != null,
        reviews: Object.keys(b.reviews),
        edits: b.edits.map((s) => s.status),
      }));
    const expected = [
      { round: 1, ended: true, reviews: [], edits: ['done'] },
      { round: 2, ended: true, reviews: ['claude', 'chatgpt'], edits: ['done'] },
      { round: 3, ended: true, reviews: ['claude', 'chatgpt'], edits: ['done', 'done'] },
    ];
    expect(describe(live(refineEvents()))).toEqual(expected);
    expect(describe(stored())).toEqual(expected);
  });

  it('shows the round in course before it ends', () => {
    const reviewing = until((e) => e.type === 'stream.completed' && e.stream_id === 'r3c');
    const last = roundBlocks(reviewing).at(-1)!;
    expect(last.round).toBe(3);
    expect(last.summary).toBeNull();
    expect(last.reviews.claude?.status).toBe('streaming');
  });

  it('a cancelled turn keeps the round it stopped in live, and only the rounds that ended after a reload', () => {
    expect(roundBlocks(live(cancelledRefineEvents())).map((b) => [b.round, b.summary != null])).toEqual([
      [1, true],
      [2, true],
      [3, false],
    ]);
    expect(roundBlocks(turnsFromMessages(cancelledRefineMessages(), 12)[0]!).map((b) => b.round)).toEqual([1, 2]);
  });
});

describe('liveStatus: what the turn is doing now', () => {
  it('says the round and its part', () => {
    expect(liveStatus(until((e) => e.type === 'stream.completed' && e.stream_id === 'c0'))).toEqual({
      round: 0, text: "Les dues IA responen l'encàrrec",
    });
    expect(liveStatus(until((e) => e.type === 'stream.completed' && e.stream_id === 'e1'))).toEqual({
      round: 1, text: 'Claude fusiona les respostes en la versió 1',
    });
    expect(liveStatus(until((e) => e.type === 'stream.completed' && e.stream_id === 'r2c'))).toEqual({
      round: 2, text: 'Revisen la versió 1',
    });
    expect(liveStatus(until((e) => e.type === 'stream.completed' && e.stream_id === 'e2'))).toEqual({
      round: 2, text: 'Claude escriu la versió 2',
    });
    expect(liveStatus(until((e) => e.type === 'stream.completed' && e.stream_id === 's3'))).toEqual({
      round: 3, text: 'Claude escurça la versió 3',
    });
  });

  it('says when a round has ended, before the next one starts or the turn ends', () => {
    expect(liveStatus(until((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3))).toEqual({
      round: 2, text: 'Ronda acabada',
    });
  });

  it('says it keeps the last version when it ends with it', () => {
    expect(liveStatus(until((e) => e.type === 'stream.completed' && e.stream_id === 'f'))).toEqual({
      round: 3, text: 'Desa la versió 2 com a resposta final',
    });
    const ending = until((e) => e.type === 'stream.started' && e.stream_id === 'f');
    applyTurnEvent(ending, { type: 'phase', request_id: 'req-p', seq: ending.lastSeq + 1, phase: 'synthesis', round: 3 });
    expect(liveStatus(ending)).toEqual({ round: 3, text: 'Desa la versió 2 com a resposta final' });
  });
});

describe('why it stopped, in Catalan', () => {
  it('has a text for every reason', () => {
    expect(STOP_REASON_LABEL).toEqual({
      owner: "L'has aturat",
      unchanged: 'Cap dels dos hi troba res a canviar',
      converged: 'Tots dos el puntuen per sobre del llindar',
      max_rounds: 'Màxim de rondes',
      budget: 'Pressupost esgotat',
      failed: 'Els dos models han fallat',
    });
  });

  it('adds the limit that stopped it', () => {
    const turn = live(refineEvents());
    expect(stopReasonText(turn)).toBe("L'has aturat");
    for (const [reason, text] of [
      ['converged', 'Tots dos el puntuen per sobre del llindar (90)'],
      ['max_rounds', 'Màxim de rondes (6)'],
      ['budget', 'Pressupost esgotat (2 €)'],
      ['unchanged', 'Cap dels dos hi troba res a canviar'],
      ['failed', 'Els dos models han fallat'],
    ] as const) {
      // Intl puts a no-break space before "€" in Catalan.
      expect(stopReasonText(live(refineEvents('req-p', reason)))?.replace(/\u00a0/g, ' ')).toBe(text);
    }
    expect(stopReasonText(until((e) => e.type === 'turn.completed'))).toBeNull();
  });
});

describe('spentUsd: what the turn has billed so far', () => {
  it("adds the calls after the last round's end to its total, and takes the turn's total at the end", () => {
    expect(spentUsd(until((e) => e.type === 'phase' && e.round === 1))).toBeCloseTo(0.009);
    expect(spentUsd(until((e) => e.type === 'phase' && e.phase === 'edit' && e.round === 2))).toBeCloseTo(
      (ROUND_TOTAL[1].cost_usd ?? 0) + 0.0045 + 0.0016,
    );
    expect(spentUsd(live(refineEvents()))).toBe(ROUND_TOTAL[3].cost_usd);
  });
});

describe('changeKindLabel', () => {
  it('names each kind of change, and keeps one it does not know', () => {
    expect(['defect', 'clarity', 'simplification', 'requirement', 'merge', 'other'].map(changeKindLabel)).toEqual([
      'Defecte', 'Claredat', 'Simplificació', 'Requisit', 'Fusió', 'other',
    ]);
  });
});

describe('reasonText: why a version was not accepted, or a round wrote none', () => {
  afterEach(() => i18n.set('ca'));

  it("is the client's own text for a code it knows, in the language of the interface", () => {
    // The server wrote the reason in the turn's language (Catalan here), with its code.
    const rejected = refineVersions(live(refineEvents())).at(-1)!;
    expect([rejected.reason, rejected.reasonCode]).toEqual([OVER_BUDGET, 'over_budget']);
    expect(reasonText(rejected)).toBe(OVER_BUDGET);
    i18n.set('en');
    expect(reasonText(rejected)).toBe('The new version went over the word limit.');
    expect(reasonText(live(refineEvents()).refineRounds.at(-1)!)).toBe('The new version went over the word limit.');
    i18n.set('es');
    expect(reasonText(rejected)).toBe('La nueva versión superaba el límite de palabras.');
    expect(REFINE_REASON_CODES.map((code) => reasonText({ reason: null, reasonCode: code }))).toEqual([
      'La nueva versión superaba el límite de palabras.',
      'El editor no ha escrito ninguna versión completa.',
      'La nueva versión es igual a la anterior.',
      'Ninguno de los dos ha encontrado nada que cambiar.',
      'Los modelos han fallado y la ronda no ha escrito ninguna versión.',
    ]);
  });

  it('is the reason as it came for a code it does not know, and null without a reason', () => {
    i18n.set('en');
    expect(reasonText({ reason: 'Massa lenta.', reasonCode: null })).toBe('Massa lenta.');
    expect(reasonText({ reason: null, reasonCode: null })).toBeNull();
  });

  it('finds the shortening of a version over the word limit by its code, not by its text', () => {
    // The same turn run in English: its reasons in English, with their codes.
    const english: Message[] = refineMessages().map((m) => {
      const refine = m.meta.refine;
      if (!refine || !('role' in refine) || refine.role !== 'version' || !refine.reason) return m;
      return { ...m, meta: { ...m.meta, refine: { ...refine, reason: 'The new version went over the word limit.' } } };
    });
    expect(listed(turnsFromMessages(english, 12)[0]!)).toEqual(listed(stored()));
    // A turn stored before the codes: its Catalan reasons stand for them.
    const legacy = turnsFromMessages(legacyMessages(refineMessages()), 12)[0]!;
    expect(listed(legacy)).toEqual(listed(stored()));
    expect(refineVersions(legacy).map((v) => v.reasonCode)).toEqual([null, null, 'over_budget', 'over_budget']);
  });
});

describe('in English and Spanish', () => {
  afterEach(() => i18n.set('ca'));

  const statusAfter = (stream: string) => liveStatus(until((e) => e.type === 'stream.completed' && e.stream_id === stream)).text;

  it('says what the turn is doing', () => {
    i18n.set('en');
    expect(statusAfter('c0')).toBe('Both AIs are answering the brief');
    expect(statusAfter('e1')).toBe('Claude is merging the answers into version 1');
    expect(statusAfter('r2c')).toBe('Reviewing version 1');
    expect(statusAfter('e2')).toBe('Claude is writing version 2');
    expect(statusAfter('s3')).toBe('Claude is shortening version 3');
    expect(statusAfter('f')).toBe('Saving version 2 as the final answer');
    expect(liveStatus(until((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3)).text).toBe('Round finished');
    i18n.set('es');
    expect(statusAfter('c0')).toBe('Las dos IA responden al encargo');
    expect(statusAfter('e1')).toBe('Claude fusiona las respuestas en la versión 1');
    expect(statusAfter('r2c')).toBe('Revisan la versión 1');
    expect(statusAfter('s3')).toBe('Claude acorta la versión 3');
    expect(statusAfter('f')).toBe('Guardando la versión 2 como respuesta final');
  });

  it('says why it stopped, and names the kinds of change', () => {
    i18n.set('en');
    expect(stopReasonText(live(refineEvents()))).toBe('You stopped it');
    expect(stopReasonText(live(refineEvents('req-p', 'converged')))).toBe('Both score it above the threshold (90)');
    expect(stopReasonText(live(refineEvents('req-p', 'max_rounds')))).toBe('Maximum number of rounds (6)');
    expect(['defect', 'clarity', 'simplification', 'requirement', 'merge', 'other'].map(changeKindLabel)).toEqual([
      'Defect', 'Clarity', 'Simplification', 'Requirement', 'Merge', 'other',
    ]);
    i18n.set('es');
    expect(STOP_REASON_LABEL).toEqual({
      owner: 'Lo has parado',
      unchanged: 'Ninguno de los dos encuentra nada que cambiar',
      converged: 'Los dos lo puntúan por encima del umbral',
      max_rounds: 'Máximo de rondas',
      budget: 'Presupuesto agotado',
      failed: 'Los dos modelos han fallado',
    });
    expect(stopReasonText(live(refineEvents('req-p', 'failed')))).toBe('Los dos modelos han fallado');
    expect(['defect', 'clarity', 'simplification', 'requirement', 'merge'].map(changeKindLabel)).toEqual([
      'Defecto', 'Claridad', 'Simplificación', 'Requisito', 'Fusión',
    ]);
  });
});
