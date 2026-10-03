// The view of a refine turn («Perfecciona», docs/adr/0010-refine-mode.md): the living
// document with its versions and their diff, each round's summary and reviews, the stop
// buttons and what they say, why it stopped, the same view after a reload, and the
// layout that stacks on a phone.
import { flushSync } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { formatMoney } from '../lib/costs';
import { i18n } from '../lib/i18n/index.svelte';
import type { TurnEvent } from '../lib/protocol';
import { withOutcome } from '../lib/test-fixtures';
import {
  cancelledRefineEvents,
  cancelledRefineMessages,
  countWords,
  editorFallbackEvents,
  editorFallbackMessages,
  INTERNAL_ERROR,
  legacyMessages,
  mergeCancelledEvents,
  mergeCancelledMessages,
  OVER_BUDGET,
  REFINE_TURN_OPTIONS,
  refineEvents,
  refineEventsUntil,
  refineMessages,
  rejectedMergeCancelledEvents,
  rejectedMergeCancelledMessages,
  ROUND_TOTAL,
  truncatedCopyEvents,
  truncatedCopyMessages,
  V1,
  V2,
} from '../lib/test-refine';
import { stopBars } from '../lib/stop-bars.svelte';
import { cleanup, render, textOf } from '../lib/test-render';
import { applyTurnEvent, createLiveTurn, turnsFromMessages, type TurnView } from '../lib/turns.svelte';

const mocks = vi.hoisted(() => ({ stopAfterRound: vi.fn(), cancel: vi.fn(), prefs: { narrow: false, reducedMotion: false } }));

vi.mock('../lib/app.svelte', () => ({
  app: { eurPerUsd: 0.9, stopAfterRound: mocks.stopAfterRound, cancel: mocks.cancel },
}));
vi.mock('../lib/prefs.svelte', () => ({ prefs: mocks.prefs }));

import Turn from './Turn.svelte';

beforeEach(() => {
  mocks.stopAfterRound.mockReset();
  mocks.cancel.mockReset();
  mocks.prefs.narrow = false;
  // The panel brings a version into view when a round asks to show it.
  Element.prototype.scrollIntoView ??= function () {};
});

afterEach(cleanup);

// Intl puts a no-break space before "€" in Catalan.
const plain = (s: string) => s.replace(/ | /g, ' ');

function liveRefine(events: TurnEvent[]): TurnView {
  const turn = createLiveTurn({
    requestId: events[0]!.request_id,
    question: 'Escriu un pla de llançament per a la beta.',
    mode: 'refine',
    options: REFINE_TURN_OPTIONS,
    conversationId: 12,
    createdAt: '2026-10-02T10:00:00Z',
  });
  for (const ev of events) applyTurnEvent(turn, ev);
  return turn;
}

const until = (stop: (e: TurnEvent) => boolean) => liveRefine(refineEventsUntil(stop));
const storedTurn = (messages = refineMessages()) => turnsFromMessages(messages, 12)[0]!;

const view = (turn: TurnView) => render(Turn, { turn, plannedRounds: 2 });
const panel = (root: HTMLElement) => root.querySelector<HTMLElement>('section.refine-doc')!;
const select = (root: HTMLElement) => panel(root).querySelector<HTMLSelectElement>('select')!;
const diffButton = (root: HTMLElement) => panel(root).querySelector<HTMLButtonElement>('button.diff-toggle')!;
const rounds = (root: HTMLElement) => [...root.querySelectorAll<HTMLDetailsElement>('details.refine-round')];
const roundSummary = (root: HTMLElement, round: number) => plain(textOf(rounds(root)[round - 1]!.querySelector('summary')));
const roundBody = (root: HTMLElement, round: number) => plain(textOf(rounds(root)[round - 1]!.querySelector('.round-body')));
const controls = (root: HTMLElement) => root.querySelector<HTMLElement>('.refine-controls');
const button = (root: HTMLElement, label: string) =>
  [...root.querySelectorAll<HTMLButtonElement>('button')].find((b) => textOf(b) === label) ?? null;

function pick(root: HTMLElement, label: RegExp): void {
  const s = select(root);
  const option = [...s.options].find((o) => label.test(o.textContent ?? ''));
  expect(option, `no version ${label}`).toBeDefined();
  s.value = option!.value;
  s.dispatchEvent(new Event('change', { bubbles: true }));
  flushSync();
}

describe('the living document', () => {
  it('shows the current version, rendered as Markdown, with its words against the limit', () => {
    const root = view(until((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3));
    const doc = panel(root);
    expect(doc.querySelector('.md h1')?.textContent).toBe('Pla de llançament');
    expect(textOf(doc.querySelector('.md'))).toContain('abans del 15 de novembre');
    expect(textOf(doc.querySelector('header'))).toContain('Versió 2');
    expect(textOf(doc)).toContain(`${countWords(V2)} de 300 paraules`);
    expect(doc.querySelector('[role=meter]')?.getAttribute('aria-valuetext')).toBe(`${countWords(V2)} de 300 paraules`);
    expect([...select(root).options].map((o) => o.textContent)).toEqual(['v1', 'v2 · actual']);
  });

  it('never renders the model text as HTML', () => {
    const events = refineEvents().map((e) =>
      e.type === 'stream.delta' && e.stream_id === 'e2' && e.section === 'answer'
        ? { ...e, text: `${V2}\n\n<img src=x onerror=alert(1)><script>alert(2)</script>` }
        : e,
    );
    const doc = panel(view(liveRefine(events)));
    expect(doc.querySelector('img, script')).toBeNull();
    expect(textOf(doc)).toContain('<img src=x onerror=alert(1)>');
  });

  it('marks the versions that were not accepted, and says why when one is picked', () => {
    const root = view(liveRefine(refineEvents()));
    expect([...select(root).options].map((o) => o.textContent)).toEqual([
      'v1',
      'v2 · final',
      'v3 · rebutjada',
      'v3 · escurçada · rebutjada',
    ]);
    pick(root, /^v3 · rebutjada/);
    expect(textOf(panel(root).querySelector('.doc-note'))).toBe(
      "Aquesta versió no s'ha acceptat: La nova versió passava del límit de paraules.",
    );
    expect(textOf(panel(root))).toMatch(/\d+ de 300 paraules/);
    pick(root, /final/);
    expect(panel(root).querySelector('.doc-note')).toBeNull();
  });

  it('shows the version being written, and keeps showing the current one when the owner picked nothing', () => {
    const merging = view(until((e) => e.type === 'stream.completed' && e.stream_id === 'e1'));
    expect(textOf(panel(merging).querySelector('header'))).toContain('Versió 1');
    expect(textOf(panel(merging).querySelector('.doc-note'))).toBe("S'està escrivint la versió 1…");
    expect(panel(merging).querySelector('.doc-body')?.getAttribute('aria-busy')).toBe('true');

    const editing = view(until((e) => e.type === 'stream.completed' && e.stream_id === 'e2'));
    expect(textOf(panel(editing).querySelector('header'))).toContain('Versió 1');
    expect([...select(editing).options].map((o) => o.textContent)).toEqual(['v1 · actual', 'v2 · escrivint…']);
  });
});

describe('the diff against the previous accepted version', () => {
  it('shows the lines removed and added, as text', () => {
    const root = view(until((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3));
    expect(diffButton(root).getAttribute('aria-pressed')).toBe('false');
    diffButton(root).click();
    flushSync();
    expect(diffButton(root).getAttribute('aria-pressed')).toBe('true');
    const diff = panel(root).querySelector('.line-diff')!;
    expect(diff.querySelector('ol')?.getAttribute('aria-label')).toBe('Canvis de la versió 2 respecte de la versió 1');
    expect([...diff.querySelectorAll('li.del')].map(textOf)).toEqual(['Tret: - Fase 2: llançament públic']);
    expect([...diff.querySelectorAll('li.add')].map(textOf)).toEqual([
      'Afegit: - Fase 2: llançament públic, abans del 15 de novembre',
    ]);
    expect(textOf(diff.querySelector('.diff-stats'))).toBe('+1 −1 línies');
    // The changes the editor says it applied, next to the lines.
    expect(textOf(diff.querySelector('.applied'))).toBe('Canvis aplicats: Defecte La fase 2 té data');
    expect(panel(root).querySelector('.md')).toBeNull();
  });

  it('compares a rejected version with the current one when it was written', () => {
    const root = view(liveRefine(refineEvents()));
    pick(root, /^v3 · escurçada/);
    diffButton(root).click();
    flushSync();
    expect(panel(root).querySelector('.line-diff ol')?.getAttribute('aria-label')).toBe(
      'Canvis de la versió 3 respecte de la versió 2',
    );
    expect(textOf(panel(root).querySelector('.diff-stats'))).toBe('+2 −0 línies');
  });

  it('has nothing to compare the first version with', () => {
    const root = view(liveRefine(refineEvents()));
    pick(root, /^v1/);
    expect(diffButton(root).disabled).toBe(true);
  });
});

describe('each round', () => {
  it('sums it up: the changes and their kinds, the words, the proposals and scores, what it cost', () => {
    const root = view(liveRefine(refineEvents()));
    expect(rounds(root)).toHaveLength(3);
    expect(roundSummary(root, 1)).toBe(`Ronda 1 Fusió Versió 1 ${countWords(V1)} paraules ≈ 0,008 €`);
    expect(roundSummary(root, 2)).toBe(`Ronda 2 Versió 2 ${countWords(V2)} paraules ≈ 0,0144 €`);
    expect(roundSummary(root, 3)).toBe(`Ronda 3 Sense versió nova ${countWords(V2)} paraules ≈ 0,0318 €`);

    rounds(root)[1]!.open = true;
    flushSync();
    const body = roundBody(root, 2);
    expect(body).toContain('Canvis aplicats Defecte La fase 2 té data');
    expect(body).toContain(`Paraules ${countWords(V2)} de 300`);
    expect(body).toContain('Cost de la ronda ≈ 0,0144 €');
    expect(body).toContain('Total del torn ≈ 0,0305 €');
    expect(body).toContain('Claude 1 proposta Puntuació 70');
    expect(body).toContain('ChatGPT Sense canvis Puntuació 85');

    rounds(root)[2]!.open = true;
    flushSync();
    expect(roundBody(root, 3)).toContain('La nova versió passava del límit de paraules.');
    expect(roundBody(root, 3)).toContain('ChatGPT 1 proposta Puntuació 88');
  });

  it('keeps the reviews in a collapsible part, with each proposal and its kind', () => {
    const root = view(liveRefine(refineEvents()));
    rounds(root)[1]!.open = true;
    flushSync();
    const reviews = rounds(root)[1]!.querySelector<HTMLDetailsElement>('details.reviews')!;
    expect(reviews.open).toBe(false);
    expect(textOf(reviews.querySelector('summary'))).toBe('Revisions');
    reviews.open = true;
    flushSync();
    expect(textOf(reviews.querySelector('[aria-label="Revisió de Claude"]'))).toContain(
      'Defecte Fase 2: no té data — sense data no es pot planificar',
    );
    expect(textOf(reviews.querySelector('[aria-label="Revisió de ChatGPT"]'))).toContain('Sense canvis');
  });

  it('shows the round in course while it runs, open, with its reviews as they come', () => {
    const root = view(until((e) => e.type === 'stream.completed' && e.stream_id === 'r3c'));
    const last = rounds(root).at(-1)!;
    expect(last.open).toBe(true);
    expect(roundSummary(root, 3)).toContain('En curs');
    // A finished round closes once the next one starts.
    expect(rounds(root)[1]!.open).toBe(false);
  });

  it('can bring the version a round wrote into the document', () => {
    const root = view(liveRefine(refineEvents()));
    rounds(root)[2]!.open = true;
    flushSync();
    button(rounds(root)[2]!, 'Mostra la versió 3 (escurçada)')!.click();
    flushSync();
    expect(select(root).selectedOptions[0]?.textContent).toBe('v3 · escurçada · rebutjada');
  });
});

describe('stopping it', () => {
  it('while it runs, «Atura en acabar la ronda» and «Atura ara» ask the app', () => {
    const root = view(until((e) => e.type === 'turn.stopping'));
    const bar = controls(root)!;
    expect(plain(textOf(bar.querySelector('.status')))).toBe('Ronda 3 de 6 · Revisen la versió 2 ≈ 0,0305 € de 2 €');
    button(bar, 'Atura en acabar la ronda')!.click();
    expect(mocks.stopAfterRound).toHaveBeenCalledTimes(1);
    button(bar, 'Atura ara')!.click();
    expect(mocks.cancel).toHaveBeenCalledTimes(1);
  });

  it('says when it will stop, as soon as the owner asks and once the server confirms the round', () => {
    const asked = until((e) => e.type === 'turn.stopping');
    asked.stopRequested = true;
    let bar = controls(view(asked))!;
    expect(button(bar, 'Atura en acabar la ronda')).toBeNull();
    // The same button, so the keyboard focus stays on it; asking again does nothing.
    const stopping = bar.querySelector<HTMLButtonElement>('button.stopping')!;
    expect(textOf(stopping)).toBe("S'aturarà en acabar la ronda 3");
    expect(stopping.getAttribute('aria-disabled')).toBe('true');
    stopping.click();
    expect(mocks.stopAfterRound).not.toHaveBeenCalled();
    expect(button(bar, 'Atura ara')).not.toBeNull();

    bar = controls(view(until((e) => e.type === 'stream.completed' && e.stream_id === 'r3c')))!;
    expect(textOf(bar.querySelector('.stopping'))).toBe("S'aturarà en acabar la ronda 3");
    expect(textOf(bar.querySelector('[role=status]'))).toBe("S'aturarà en acabar la ronda 3");
  });

  it('a stop asked before the first version waits for it: the merge always runs', () => {
    const turn = until((e) => e.type === 'phase' && e.phase === 'edit');
    turn.stopRequested = true;
    expect(textOf(controls(view(turn))!.querySelector('.stopping'))).toBe("S'aturarà en acabar la ronda 1");
  });

  it('has no buttons once it ended, and says why it stopped', () => {
    const root = view(liveRefine(refineEvents()));
    expect(controls(root)).toBeNull();
    expect(textOf(panel(root).querySelector('.doc-end'))).toBe(
      "S'ha aturat a la ronda 3: L'has aturat. La resposta final és la versió 2.",
    );
  });

  it('gives every reason in Catalan', () => {
    const reasons = {
      converged: 'Tots dos el puntuen per sobre del llindar (90)',
      unchanged: 'Cap dels dos hi troba res a canviar',
      max_rounds: 'Màxim de rondes (6)',
      budget: 'Pressupost esgotat (2 €)',
      failed: 'Els dos models han fallat',
    } as const;
    for (const [reason, text] of Object.entries(reasons) as [keyof typeof reasons, string][]) {
      const end = plain(textOf(panel(view(liveRefine(refineEvents('req-p', reason)))).querySelector('.doc-end')));
      expect(end).toBe(`S'ha aturat a la ronda 3: ${text}. La resposta final és la versió 2.`);
    }
  });

  it('a turn stopped with «Atura ara» keeps its last version, without blaming the owner for a shutdown', () => {
    const root = view(liveRefine(cancelledRefineEvents()));
    expect(textOf(panel(root).querySelector('.doc-end'))).toBe(
      "Aquest torn s'ha aturat. Es conserva la versió 2 com a resposta final.",
    );
    expect(root.querySelector('.banner')).toBeNull();
    expect(controls(root)).toBeNull();
  });
});

describe('after a reload', () => {
  it('shows the same document, versions, rounds and end as the live turn', () => {
    const live = view(liveRefine(refineEvents()));
    const stored = view(storedTurn());
    expect(textOf(panel(stored))).toBe(textOf(panel(live)));
    expect([...select(stored).options].map((o) => o.textContent)).toEqual([...select(live).options].map((o) => o.textContent));
    expect([1, 2, 3].map((r) => roundSummary(stored, r))).toEqual([1, 2, 3].map((r) => roundSummary(live, r)));
    for (const root of [live, stored]) for (const details of rounds(root)) details.open = true;
    flushSync();
    expect([1, 2, 3].map((r) => roundBody(stored, r))).toEqual([1, 2, 3].map((r) => roundBody(live, r)));
    // Reloaded, every round starts folded.
    expect(rounds(view(storedTurn())).map((d) => d.open)).toEqual([false, false, false]);
  });

  it('a cancelled one shows the version it kept', () => {
    const root = view(storedTurn(cancelledRefineMessages()));
    expect(textOf(panel(root).querySelector('.doc-end'))).toBe(
      "Aquest torn s'ha aturat. Es conserva la versió 2 com a resposta final.",
    );
    expect(rounds(root)).toHaveLength(2);
  });
});

describe('on a phone', () => {
  it('stacks the panel', () => {
    mocks.prefs.narrow = true;
    const root = view(until((e) => e.type === 'turn.stopping'));
    expect(root.querySelector('.refine')?.classList.contains('stacked')).toBe(true);
    mocks.prefs.narrow = false;
    expect(view(until((e) => e.type === 'turn.stopping')).querySelector('.refine')?.classList.contains('stacked')).toBe(false);
  });
});

// What the turn keeps as its answer: only a version that was accepted, and, when it did not
// complete, only what was stored as its final message (the P8 review).
const STOPPED = "Aquest torn s'ha aturat.";
const NOTHING_KEPT = "No es conserva cap versió com a resposta final: encara no se n'havia acceptat cap.";
const chips = (root: HTMLElement) => [...panel(root).querySelectorAll('.heading .chip')].map(textOf);

describe('what a turn that did not complete keeps', () => {
  it('«Atura ara» during the merge keeps no version: the stop banner, and the text marked as not kept', () => {
    const live = view(liveRefine(mergeCancelledEvents()));
    expect(chips(live)).toEqual(['Interrompuda']);
    expect(textOf(panel(live).querySelector('.doc-end'))).toBe(NOTHING_KEPT);
    expect(textOf(live.querySelector('.banner'))).toBe(STOPPED);
    // Reloaded: the merge was never stored, so there is no document at all.
    const stored = view(storedTurn(mergeCancelledMessages()));
    expect(stored.querySelector('section.refine-doc')).toBeNull();
    expect(textOf(stored.querySelector('.banner'))).toBe(STOPPED);
  });

  it('a cancel after a merge that was not accepted keeps none either, live and after a reload', () => {
    for (const root of [view(liveRefine(rejectedMergeCancelledEvents())), view(storedTurn(rejectedMergeCancelledMessages()))]) {
      expect(chips(root)[0]).toBe('Rebutjada');
      expect(textOf(panel(root).querySelector('.doc-end'))).toBe(NOTHING_KEPT);
      expect(textOf(root.querySelector('.banner'))).toBe(STOPPED);
    }
  });

  it('«Atura ara» once a version was accepted keeps it, though the server stores that final message without streaming it', () => {
    const root = view(liveRefine(cancelledRefineEvents('req-q', false)));
    expect(textOf(panel(root).querySelector('.doc-end'))).toBe(
      "Aquest torn s'ha aturat. Es conserva la versió 2 com a resposta final.",
    );
    expect(chips(root)).toEqual(['Final']);
    expect(root.querySelector('.banner')).toBeNull();
  });

  it('a turn that failed after a version says which one was the last accepted, not that it is the final answer', () => {
    const events = refineEventsUntil((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3);
    const failed: TurnEvent = { type: 'turn.failed', request_id: 'req-p', seq: events.length + 1, error: INTERNAL_ERROR, usage: ROUND_TOTAL[2] };
    const root = view(liveRefine([...events, failed]));
    expect(chips(root)).toEqual(['Última acceptada']);
    expect(textOf(panel(root).querySelector('.doc-end'))).toBe(
      "L'última versió acceptada és la 2, però no s'ha desat com a resposta del torn.",
    );
    expect([...select(root).options].map((o) => o.textContent)).toEqual(['v1', 'v2 · última acceptada']);
    expect(textOf(root.querySelector('.banner'))).toBe("El torn ha fallat. S'ha produït un error intern i el torn s'ha aturat.");
  });

  it('so does a stored one that never ended (the server stopped during round 3)', () => {
    const root = view(storedTurn(withOutcome(refineMessages().filter((m) => m.id <= 78), null)));
    expect(chips(root)).toEqual(['Última acceptada']);
    expect(textOf(panel(root).querySelector('.doc-end'))).toBe(
      "L'última versió acceptada és la 2, però no s'ha desat com a resposta del torn.",
    );
    expect(textOf(root.querySelector('.banner'))).toBe('Aquest torn no es va completar.');
  });
});

describe('a document cut off at the output limit', () => {
  it('says so, live and after a reload: a version 1 copied from a cut-off answer, and the final answer it became', () => {
    for (const root of [view(liveRefine(truncatedCopyEvents())), view(storedTurn(truncatedCopyMessages()))]) {
      const doc = panel(root);
      expect(textOf(doc.querySelector('.truncation-note'))).toBe("Document incomplet: s'ha arribat al límit de sortida.");
      expect(chips(root)).toEqual(['Final', 'Incompleta']);
      expect(textOf(doc.querySelector('.doc-end'))).toBe("S'ha aturat a la ronda 1: L'has aturat. La resposta final és la versió 1.");
      expect([...select(root).options].map((o) => o.textContent)).toEqual([
        'v1 · rebutjada · incompleta',
        'v1 · rebutjada · incompleta',
        'v1 · final · incompleta',
      ]);
      rounds(root)[0]!.open = true;
      flushSync();
      expect([...rounds(root)[0]!.querySelectorAll('.edits li > span:first-child')].map(textOf)).toEqual([
        'Versió 1: rebutjada, incompleta',
        'Versió 1: rebutjada, incompleta',
        'Versió 1: acceptada, incompleta',
      ]);
    }
  });

  it('a complete version says nothing of it', () => {
    const root = view(liveRefine(refineEvents()));
    expect(panel(root).querySelector('.truncation-note')).toBeNull();
    expect(chips(root)).toEqual(['Final']);
  });
});

describe('the editor calls of a round', () => {
  it('lists one that failed before the version that replaced it, live and after a reload', () => {
    for (const root of [view(liveRefine(editorFallbackEvents())), view(storedTurn(editorFallbackMessages()))]) {
      for (const details of rounds(root)) details.open = true;
      flushSync();
      const edits = (round: number) => [...rounds(root)[round - 1]!.querySelectorAll('.edits li')].map(textOf);
      expect(edits(1)).toEqual(['Claude no ha pogut escriure la versió: Claude no respon.', 'Versió 1: acceptada Mostra la versió 1']);
      expect(edits(2)).toEqual(['ChatGPT no ha pogut escriure la versió: ChatGPT no respon.', 'Versió 2: acceptada Mostra la versió 2']);
    }
  });
});

describe('keyboard focus and the sticky stop bar (WCAG 2.2, 2.4.11)', () => {
  // jsdom lays nothing out: the bar measures what a browser would give it.
  const BAR_HEIGHT = 107;

  beforeEach(() => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(private readonly callback: () => void) {}
        observe(): void {
          this.callback();
        }
        unobserve(): void {}
        disconnect(): void {}
      },
    );
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('refine-controls') ? BAR_HEIGHT : 0;
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('tells the conversation how tall the bar is while it shows, so it keeps the focus above it (Chat.svelte)', () => {
    view(until((e) => e.type === 'turn.stopping'));
    expect(stopBars.height).toBe(BAR_HEIGHT);
    cleanup();
    expect(stopBars.height).toBe(0);
  });

  it('a turn that ended has no bar to keep the focus above', () => {
    view(liveRefine(refineEvents()));
    expect(stopBars.height).toBe(0);
  });
});

describe('in English and Spanish', () => {
  afterEach(() => i18n.set('ca'));

  /** Unfolds every round of the view. */
  function openRounds(root: HTMLElement): void {
    for (const details of rounds(root)) details.open = true;
    flushSync();
  }

  it('the document panel, in English', () => {
    i18n.set('en');
    const root = view(liveRefine(refineEvents()));
    const doc = panel(root);
    expect(textOf(doc.querySelector('h3'))).toBe('Version 2');
    expect(textOf(doc.querySelector('.chip'))).toBe('Final');
    expect([...select(root).options].map((o) => o.textContent)).toEqual([
      'v1',
      'v2 · final',
      'v3 · rejected',
      'v3 · shortened · rejected',
    ]);
    expect(textOf(doc)).toContain(`${countWords(V2)} of 300 words`);
    expect(doc.querySelector('[role=meter]')?.getAttribute('aria-label')).toBe('Words in the version');
    expect(doc.querySelector('[role=meter]')?.getAttribute('aria-valuetext')).toBe(`${countWords(V2)} of 300 words`);
    expect(textOf(doc.querySelector('.by'))).toBe('by Claude');
    expect(textOf(doc.querySelector('.doc-end'))).toBe('Stopped at round 3: You stopped it. The final answer is version 2.');
    expect(diffButton(root).title).toBe('Show what changed since version 1');
    expect(textOf(diffButton(root))).toBe('Changes');
    // The server wrote its reason in Catalan; its code gives the English one.
    pick(root, /^v3 · rejected/);
    expect(textOf(doc.querySelector('.doc-note'))).toBe('This version was not accepted: The new version went over the word limit.');
    pick(root, /^v1$/);
    expect(textOf(doc.querySelector('.doc-note'))).toBe('This is an earlier version: the final one is version 2.');
  });

  it('the document panel, in Spanish', () => {
    i18n.set('es');
    const root = view(liveRefine(refineEvents()));
    const doc = panel(root);
    expect(textOf(doc.querySelector('h3'))).toBe('Versión 2');
    expect([...select(root).options].map((o) => o.textContent)).toEqual([
      'v1',
      'v2 · final',
      'v3 · rechazada',
      'v3 · acortada · rechazada',
    ]);
    expect(textOf(doc)).toContain(`${countWords(V2)} de 300 palabras`);
    expect(textOf(doc.querySelector('.by'))).toBe('por Claude');
    expect(textOf(doc.querySelector('.doc-end'))).toBe(
      'Se ha detenido en la ronda 3: Lo has parado. La respuesta final es la versión 2.',
    );
    pick(root, /^v3 · rechazada/);
    expect(textOf(doc.querySelector('.doc-note'))).toBe(
      'Esta versión no se ha aceptado: La nueva versión superaba el límite de palabras.',
    );
  });

  it('the diff, in English and Spanish', () => {
    i18n.set('en');
    let root = view(until((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3));
    diffButton(root).click();
    flushSync();
    let diff = panel(root).querySelector('.line-diff')!;
    expect(diff.querySelector('ol')?.getAttribute('aria-label')).toBe('Changes in version 2 since version 1');
    expect([...diff.querySelectorAll('li.del')].map(textOf)).toEqual(['Removed: - Fase 2: llançament públic']);
    expect(textOf(diff.querySelector('li.add'))).toBe('Added: - Fase 2: llançament públic, abans del 15 de novembre');
    expect(textOf(diff.querySelector('.diff-stats'))).toBe('+1 −1 lines');
    expect(textOf(diff.querySelector('.applied'))).toBe('Changes applied: Defect La fase 2 té data');
    // A rejected version 3 adds lines at the end: the first ones, further away, fold.
    root = view(liveRefine(refineEvents()));
    pick(root, /^v3 · rejected/);
    diffButton(root).click();
    flushSync();
    expect(textOf(panel(root).querySelector('.line-diff li.skip'))).toBe('3 unchanged lines');
    cleanup();

    i18n.set('es');
    root = view(until((e) => e.type === 'phase' && e.phase === 'review' && e.round === 3));
    diffButton(root).click();
    flushSync();
    diff = panel(root).querySelector('.line-diff')!;
    expect(diff.querySelector('ol')?.getAttribute('aria-label')).toBe('Cambios de la versión 2 respecto a la versión 1');
    expect(textOf(diff.querySelector('li.del'))).toBe('Quitado: - Fase 2: llançament públic');
    expect(textOf(diff.querySelector('.diff-stats'))).toBe('+1 −1 líneas');
    expect(textOf(diff.querySelector('.applied'))).toBe('Cambios aplicados: Defecto La fase 2 té data');
    root = view(liveRefine(refineEvents()));
    pick(root, /^v3 · rechazada/);
    diffButton(root).click();
    flushSync();
    expect(textOf(panel(root).querySelector('.line-diff li.skip'))).toBe('3 líneas sin cambios');
  });

  it('a round, in English', () => {
    i18n.set('en');
    const root = view(liveRefine(refineEvents()));
    expect(roundSummary(root, 1)).toBe(`Round 1 Merge Version 1 ${countWords(V1)} words ≈ €0.008`);
    expect(roundSummary(root, 3)).toBe(`Round 3 No new version ${countWords(V2)} words ≈ €0.0318`);
    openRounds(root);
    const two = roundBody(root, 2);
    expect(two).toContain('Changes applied Defect La fase 2 té data');
    expect(two).toContain(`Words ${countWords(V2)} of 300`);
    expect(two).toContain('Round cost ≈ €0.0144');
    expect(two).toContain('Turn total ≈ €0.0305');
    expect(two).toContain('Claude 1 proposal Score 70');
    expect(two).toContain('ChatGPT No changes Score 85');
    expect(rounds(root)[1]!.querySelector('ul.agents')?.getAttribute('aria-label')).toBe('Reviews of round 2');
    const reviews = rounds(root)[1]!.querySelector<HTMLDetailsElement>('details.reviews')!;
    expect(textOf(reviews.querySelector('summary'))).toBe('Reviews');
    expect(reviews.querySelector('[aria-label="Review by ChatGPT"]')).not.toBeNull();
    // Round 3: why it wrote no version (the server's code, in English), and its versions.
    const three = roundBody(root, 3);
    expect(three).toContain('The new version went over the word limit.');
    expect(three).not.toContain(OVER_BUDGET);
    expect(three).toContain('Version 3: rejected');
    expect(three).toContain('Version 3 (shortened): rejected');
    button(rounds(root)[2]!, 'Show version 3 (shortened)')!.click();
    flushSync();
    expect(select(root).selectedOptions[0]?.textContent).toBe('v3 · shortened · rejected');
  });

  it('a round, in Spanish', () => {
    i18n.set('es');
    const root = view(liveRefine(refineEvents()));
    expect(roundSummary(root, 1)).toBe(`Ronda 1 Fusión Versión 1 ${countWords(V1)} palabras ≈ 0,008 €`);
    expect(roundSummary(root, 3)).toBe(`Ronda 3 Sin versión nueva ${countWords(V2)} palabras ≈ 0,0318 €`);
    openRounds(root);
    const two = roundBody(root, 2);
    expect(two).toContain(`Palabras ${countWords(V2)} de 300`);
    expect(two).toContain('Coste de la ronda ≈ 0,0144 €');
    expect(two).toContain('Total del turno ≈ 0,0305 €');
    expect(two).toContain('Claude 1 propuesta Puntuación 70');
    expect(two).toContain('ChatGPT Sin cambios Puntuación 85');
    const three = roundBody(root, 3);
    expect(three).toContain('La nueva versión superaba el límite de palabras.');
    expect(three).toContain('Versión 3 (acortada): rechazada');
    expect(button(rounds(root)[2]!, 'Muestra la versión 3 (acortada)')).not.toBeNull();
  });

  it('the controls: «Stop after this round» and «Stop now»', () => {
    i18n.set('en');
    let bar = controls(view(until((e) => e.type === 'turn.stopping')))!;
    expect(bar.getAttribute('aria-label')).toBe('Refine turn controls');
    expect(plain(textOf(bar.querySelector('.status')))).toBe(
      `Round 3 of 6 · Reviewing version 2 ≈ €0.0305 of ${plain(formatMoney(2))}`,
    );
    button(bar, 'Stop after this round')!.click();
    expect(mocks.stopAfterRound).toHaveBeenCalledTimes(1);
    expect(button(bar, 'Stop now')!.title).toBe('Cuts off the calls in progress; the last complete version is kept');
    button(bar, 'Stop now')!.click();
    expect(mocks.cancel).toHaveBeenCalledTimes(1);
    const asked = until((e) => e.type === 'turn.stopping');
    asked.stopRequested = true;
    bar = controls(view(asked))!;
    expect(textOf(bar.querySelector('button.stopping'))).toBe('Stopping after round 3');
    expect(textOf(bar.querySelector('[role=status]'))).toBe('Stopping after round 3');
    cleanup();

    i18n.set('es');
    bar = controls(view(until((e) => e.type === 'turn.stopping')))!;
    expect(plain(textOf(bar.querySelector('.status')))).toBe(
      `Ronda 3 de 6 · Revisan la versión 2 ≈ 0,0305 € de ${plain(formatMoney(2))}`,
    );
    expect(button(bar, 'Parar al acabar la ronda')).not.toBeNull();
    expect(button(bar, 'Parar ahora')).not.toBeNull();
    const again = until((e) => e.type === 'turn.stopping');
    again.stopRequested = true;
    expect(textOf(controls(view(again))!.querySelector('button.stopping'))).toBe('Parará al acabar la ronda 3');
  });

  it('a change of language repaints the panel', () => {
    const root = view(liveRefine(refineEvents()));
    expect(textOf(panel(root).querySelector('h3'))).toBe('Versió 2');
    i18n.set('en');
    flushSync();
    expect(textOf(panel(root).querySelector('h3'))).toBe('Version 2');
    expect(textOf(panel(root).querySelector('.doc-end'))).toBe('Stopped at round 3: You stopped it. The final answer is version 2.');
    expect(roundSummary(root, 1)).toBe(`Round 1 Merge Version 1 ${countWords(V1)} words ≈ €0.008`);
  });
});

describe('why a round wrote no version: the code the server sends (reason_code)', () => {
  afterEach(() => i18n.set('ca'));

  /** The events of `refineEvents` with round 3's end, and its first version's meta, changed by `change`. */
  function withRoundThree(change: Record<string, unknown>): TurnEvent[] {
    return refineEvents().map((e) => {
      if (e.type === 'refine.round' && e.round === 3) return { ...e, ...change } as unknown as TurnEvent;
      if (e.type === 'stream.completed' && e.stream_id === 'e3' && e.refine?.role === 'version') {
        return { ...e, refine: { ...e.refine, ...change } } as unknown as TurnEvent;
      }
      return e;
    });
  }

  it('shows the text of a code it knows in the language of the interface, whatever the reason says', () => {
    i18n.set('en');
    const root = view(liveRefine(withRoundThree({ reason: 'Una altra cosa.' })));
    rounds(root)[2]!.open = true;
    flushSync();
    expect(roundBody(root, 3)).toContain('The new version went over the word limit.');
    expect(roundBody(root, 3)).not.toContain('Una altra cosa.');
  });

  it('shows the reason as it came for a code it does not know', () => {
    i18n.set('en');
    const root = view(liveRefine(withRoundThree({ reason_code: 'too_slow', reason: 'Massa lenta per acabar.' })));
    rounds(root)[2]!.open = true;
    flushSync();
    expect(roundBody(root, 3)).toContain('Massa lenta per acabar.');
    pick(root, /^v3 · rejected/);
    expect(textOf(panel(root).querySelector('.doc-note'))).toBe('This version was not accepted: Massa lenta per acabar.');
  });

  it('still understands a turn stored before the codes, by its Catalan reasons', () => {
    const legacy = storedTurn(legacyMessages(refineMessages()));
    const coded = view(storedTurn());
    const old = view(legacy);
    expect(textOf(panel(old))).toBe(textOf(panel(coded)));
    expect([...select(old).options].map((o) => o.textContent)).toEqual([...select(coded).options].map((o) => o.textContent));
    cleanup();

    i18n.set('en');
    const root = view(legacy);
    // The shortening of a version over the word limit, found by the code its text stands for.
    expect([...select(root).options].map((o) => o.textContent)).toContain('v3 · shortened · rejected');
    pick(root, /^v3 · rejected/);
    expect(textOf(panel(root).querySelector('.doc-note'))).toBe('This version was not accepted: The new version went over the word limit.');
    rounds(root)[2]!.open = true;
    flushSync();
    expect(roundBody(root, 3)).toContain('The new version went over the word limit.');
  });
});
