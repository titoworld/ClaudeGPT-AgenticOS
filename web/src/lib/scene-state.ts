// What the 3D scene should show for the turn in focus (pure, testable).

import type { Agent } from './protocol';
import type { SceneMood } from '../scene/types';
import { isTerminal, latestAgreement, latestRefineScore, type TurnView } from './turns.svelte';

export interface SceneState {
  mood: SceneMood;
  active: Partial<Record<Agent, boolean>>;
  agreement: number | null;
}

export const IDLE_SCENE: SceneState = { mood: 'idle', active: {}, agreement: null };

export function deriveSceneState(turn: TurnView | null | undefined): SceneState {
  if (!turn || isTerminal(turn.status)) return IDLE_SCENE;
  const active: Partial<Record<Agent, boolean>> = {};
  let hasText = false;
  for (const s of turn.streams) {
    if (s.status !== 'streaming') continue;
    active[s.agent] = true;
    if (s.text || s.critique) hasText = true;
  }
  // A refine round: both review the document, then the editor writes it;
  // the orbs are as close as the reviews find the document fit for the brief.
  if (turn.phase === 'review') return { mood: 'debate', active, agreement: latestRefineScore(turn) };
  if (turn.phase === 'edit') return { mood: 'synthesis', active, agreement: latestRefineScore(turn) };
  if (turn.phase === 'synthesis') return { mood: 'synthesis', active, agreement: latestAgreement(turn) };
  if (turn.phase === 'revision') return { mood: 'debate', active, agreement: latestAgreement(turn) };
  if (hasText) return { mood: 'speaking', active, agreement: null };
  return { mood: 'thinking', active, agreement: null };
}

export function sameScene(a: SceneState, b: SceneState): boolean {
  return (
    a.mood === b.mood &&
    a.agreement === b.agreement &&
    !!a.active.claude === !!b.active.claude &&
    !!a.active.chatgpt === !!b.active.chatgpt
  );
}
