// What the view of a refine turn («Perfecciona», docs/adr/0010-mode-perfecciona.md) shows:
// the versions of its living document, the block of each round, what the turn is doing
// now and why it stopped. Pure functions of a TurnView, live or stored, so a reloaded turn
// shows what the live one did (its rounds are rebuilt from the messages: turns.svelte.ts).

import { AGENT_LABEL, formatInt } from './format';
import { formatMoney } from './costs';
import { AGENTS, type Agent, type RefineChange, type RefineStopReason } from './protocol';
import { isTerminal, REFINE_ROUND_REASONS, type RefineRoundView, type StreamView, type TurnView } from './turns.svelte';

const CHANGE_KIND_LABEL: Record<string, string> = {
  defect: 'Defecte',
  clarity: 'Claredat',
  simplification: 'Simplificació',
  requirement: 'Requisit',
  merge: 'Fusió',
};

/** A kind of change in Catalan (one the client does not know is kept as it is). */
export const changeKindLabel = (kind: string): string => CHANGE_KIND_LABEL[kind] ?? kind;

/** Why a refine turn stopped, in Catalan. */
export const STOP_REASON_LABEL: Record<RefineStopReason, string> = {
  owner: "L'has aturat",
  unchanged: 'Cap dels dos hi troba res a canviar',
  converged: 'Tots dos el puntuen per sobre del llindar',
  max_rounds: 'Màxim de rondes',
  budget: 'Pressupost esgotat',
  failed: 'Els dos models han fallat',
};

/** The last item of `items` that `test` accepts (the lib is ES2022: no Array.prototype.findLast). */
function lastOf<T>(items: readonly T[], test: (item: T) => boolean): T | undefined {
  for (let i = items.length - 1; i >= 0; i--) if (test(items[i]!)) return items[i];
  return undefined;
}

/** Words as the engine counts them (`domain.words`: runs of non-space). */
export const countWords = (text: string): number => text.split(/\s+/).filter(Boolean).length;

export type VersionState = 'writing' | 'accepted' | 'rejected' | 'interrupted';

/** A version of the document, as the selector lists it. */
export interface VersionView {
  /** Its stream's id: two versions can have the same number (one rejected, a later one accepted). */
  key: string;
  stream: StreamView;
  /**
   * The number the engine gives it: the current version's plus one (the first is 1), so a
   * version that was not accepted shares its number with the next one written.
   */
  number: number;
  round: number;
  /**
   * The shortening of a version over the word limit: the same editor's next version in the
   * same round, round 1 included (a second merge by the other editor is not one).
   */
  retry: boolean;
  state: VersionState;
  /** Why it did not become the current version (Catalan). */
  reason: string | null;
  /** Its words (counted while it is being written). */
  words: number;
  budgetWords: number | null;
  changelog: RefineChange[];
  /** Version 1 stored without a call: a copy of an answer, since nobody could merge them. */
  copied: boolean;
}

/**
 * Every version the editor wrote, in order (a call that failed wrote none). One still being
 * written (no meta yet) gets the number the engine will give it: the current one's plus one.
 * A second version of a round is the shortening of the first when the same editor wrote the
 * first and it passed the word limit; else another editor's (a merge after one that was cut off).
 */
export function refineVersions(turn: TurnView): VersionView[] {
  const versions: VersionView[] = [];
  let current = 0;
  for (const s of turn.streams) {
    if (s.refineRole !== 'version' || s.status === 'failed') continue;
    const before = versions.at(-1);
    const shortening =
      before !== undefined &&
      before.round === s.round &&
      before.stream.agent === s.agent &&
      before.reason === REFINE_ROUND_REASONS.overBudget;
    const info = s.refine?.role === 'version' ? s.refine : null;
    let state: VersionState;
    if (s.status === 'streaming') state = 'writing';
    else if (s.status === 'interrupted') state = 'interrupted';
    else state = info?.accepted ? 'accepted' : 'rejected';
    const number = info?.version ?? current + 1;
    if (state === 'accepted') current = number;
    versions.push({
      key: s.id,
      stream: s,
      number,
      round: s.round,
      retry: shortening,
      state,
      reason: info?.reason ?? null,
      words: info?.words ?? countWords(s.text),
      budgetWords: info?.budgetWords ?? null,
      changelog: info?.changelog ?? [],
      copied: info?.copiedFrom != null,
    });
  }
  return versions;
}

/** The current version: the last one accepted. */
export const currentVersion = (versions: readonly VersionView[]): VersionView | null =>
  lastOf(versions, (v) => v.state === 'accepted') ?? null;

/**
 * The version a refine turn keeps as its answer, once it ended: the one its final message
 * stored (the answer later turns see). Stopped with «Atura ara», the engine stores the current
 * version as that message without streaming it: live, the current version is the answer; a
 * reloaded turn has the message. Null while it runs, and when it ended without one: cancelled
 * or failed before any version was accepted, or failed (or never finished) before storing it.
 */
export function keptVersion(turn: TurnView, versions: readonly VersionView[] = refineVersions(turn)): number | null {
  if (!isTerminal(turn.status)) return null;
  const current = currentVersion(versions);
  const final = turn.streams.find((s) => s.refineRole === 'final' && s.status === 'done');
  if (final) return (final.refine?.role === 'final' ? final.refine.version : null) ?? current?.number ?? 1;
  const stored = turn.status === 'done' || (turn.status === 'cancelled' && turn.live);
  return stored ? (current?.number ?? null) : null;
}

/** The version a diff of `v` compares with: the current one when `v` was written. */
export function previousAccepted(versions: readonly VersionView[], v: VersionView): VersionView | null {
  const before = versions.slice(0, versions.indexOf(v));
  return lastOf(before, (x) => x.state === 'accepted') ?? null;
}

/**
 * The document the panel shows unless the owner picks a version: the current version
 * (`final` once the turn ended: whether it is the turn's answer is `keptVersion`'s), the
 * merge while it is being written (or the first one written, when none was accepted), or the
 * final message alone when the turn has none of its versions (a version 1 copied without a
 * call comes with no stream of its own live). Null before there is any.
 */
export type ShownDocument =
  | { kind: 'version'; version: VersionView; final: boolean }
  | { kind: 'final'; text: string; number: number; stream: StreamView };

export function shownDocument(turn: TurnView, versions: readonly VersionView[] = refineVersions(turn)): ShownDocument | null {
  const ended = isTerminal(turn.status);
  const current = currentVersion(versions);
  if (current) return { kind: 'version', version: current, final: ended };
  const final = turn.streams.find((s) => s.refineRole === 'final' && s.text);
  if (final) {
    const info = final.refine?.role === 'final' ? final.refine : null;
    return { kind: 'final', text: final.text, number: info?.version ?? 1, stream: final };
  }
  const first = versions[0];
  return first ? { kind: 'version', version: first, final: false } : null;
}

/** A round of a refine turn: how it ended (null while it runs, or if it never ended) and its calls. */
export interface RoundBlock {
  round: number;
  summary: RefineRoundView | null;
  /** The review of each agent (from round 2), the latest one. */
  reviews: Partial<Record<Agent, StreamView>>;
  /** The editor's calls: its version, its shortening, or calls that failed. */
  edits: StreamView[];
}

/** Every round of the turn from round 1, in order: those that ended, and the one in course. */
export function roundBlocks(turn: TurnView): RoundBlock[] {
  const numbers = new Set(turn.refineRounds.map((r) => r.round));
  for (const s of turn.streams) {
    if (s.round >= 1 && (s.refineRole === 'review' || s.refineRole === 'version')) numbers.add(s.round);
  }
  return [...numbers]
    .sort((a, b) => a - b)
    .map((round) => {
      const own = turn.streams.filter((s) => s.round === round);
      const reviews: Partial<Record<Agent, StreamView>> = {};
      for (const agent of AGENTS) {
        const review = lastOf(own, (s) => s.agent === agent && s.refineRole === 'review');
        if (review) reviews[agent] = review;
      }
      return {
        round,
        summary: turn.refineRounds.find((r) => r.round === round) ?? null,
        reviews,
        edits: own.filter((s) => s.refineRole === 'version'),
      };
    });
}

/** What a running refine turn is doing: its round and the part of it, in Catalan. */
export function liveStatus(turn: TurnView): { round: number; text: string } {
  const round = turn.round;
  const versions = refineVersions(turn);
  const current = currentVersion(versions);
  // It ends: the current version becomes the final answer (stored without a call).
  if (turn.phase === 'synthesis' || turn.streams.some((s) => s.refineRole === 'final')) {
    return { round, text: current ? `Desa la versió ${current.number} com a resposta final` : 'Desa la resposta final' };
  }
  // Between the end of a round and what comes next: another round, or the end.
  if ((turn.phase === 'review' || turn.phase === 'edit') && turn.refineRounds.some((r) => r.round === round)) {
    return { round, text: 'Ronda acabada' };
  }
  if (turn.phase === 'review') {
    return { round, text: current ? `Revisen la versió ${current.number}` : 'Revisen el document' };
  }
  if (turn.phase === 'edit') {
    const writing = lastOf(versions, (v) => v.round === round && v.state === 'writing');
    const editor = AGENT_LABEL[writing?.stream.agent ?? turn.options?.refine?.editor ?? 'claude'];
    if (round <= 1) return { round, text: `${editor} fusiona les respostes en la versió 1` };
    const verb = writing?.retry ? 'escurça' : 'escriu';
    return { round, text: `${editor} ${verb} la versió ${writing?.number ?? round}` };
  }
  if (turn.phase === 'compaction') return { round: 0, text: "Compactant l'historial" };
  return { round, text: "Les dues IA responen l'encàrrec" };
}

/**
 * Why an ended refine turn stopped, in Catalan, with the limit that stopped it when it was
 * one; null while it runs or when nothing says.
 */
export function stopReasonText(turn: TurnView): string | null {
  const reason = turn.stopReason;
  if (!reason || !isTerminal(turn.status)) return null;
  const options = turn.options?.refine;
  const label = STOP_REASON_LABEL[reason];
  if (!options) return label;
  if (reason === 'converged') return `${label} (${formatInt(options.convergence_threshold)})`;
  if (reason === 'max_rounds') return `${label} (${formatInt(options.max_rounds)})`;
  if (reason === 'budget') return `${label} (${formatMoney(options.budget_eur)})`;
  return label;
}

/**
 * What the turn has billed so far, in USD: its total once it ended; while it runs, the
 * total of the last round that ended plus the calls after it (before any, every call's).
 * Null when nothing has a price.
 */
export function spentUsd(turn: TurnView): number | null {
  if (turn.usage) return turn.usage.cost_usd;
  const last = turn.refineRounds.at(-1);
  let usd = last?.total?.cost_usd ?? null;
  for (const s of turn.streams) {
    const cost = s.usage?.cost_usd;
    if (cost == null || (last && s.round <= last.round)) continue;
    usd = (usd ?? 0) + cost;
  }
  return usd;
}
