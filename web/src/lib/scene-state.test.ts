import { describe, expect, it } from 'vitest';
import { deriveSceneState, IDLE_SCENE, sameScene } from './scene-state';
import { applyTurnEvent, createLiveTurn } from './turns.svelte';
import { debateEvents } from './test-fixtures';

describe('deriveSceneState', () => {
  const events = debateEvents();
  const upTo = (n: number) => {
    const turn = createLiveTurn({ requestId: 'req-1', question: 'q', mode: 'debate' });
    for (const ev of events.slice(0, n)) applyTurnEvent(turn, ev);
    return turn;
  };

  it('is idle without a running turn', () => {
    expect(deriveSceneState(null)).toEqual(IDLE_SCENE);
    expect(deriveSceneState(upTo(events.length))).toEqual(IDLE_SCENE);
  });

  it('thinks until the first token, then speaks with the active agents', () => {
    expect(deriveSceneState(upTo(4)).mood).toBe('thinking');
    const speaking = deriveSceneState(upTo(6));
    expect(speaking.mood).toBe('speaking');
    expect(speaking.active).toEqual({ claude: true, chatgpt: true });
  });

  it('debates during revisions with the latest agreement', () => {
    const debate = deriveSceneState(upTo(17)); // round 1 completed: 70 and 90
    expect(debate.mood).toBe('debate');
    expect(debate.agreement).toBe(80);
  });

  it('synthesizes with only the synthesizer active', () => {
    const synth = deriveSceneState(upTo(26));
    expect(synth.mood).toBe('synthesis');
    expect(synth.active).toEqual({ claude: true });
  });

  it('compares states structurally', () => {
    expect(sameScene(IDLE_SCENE, { mood: 'idle', active: { claude: false }, agreement: null })).toBe(true);
    expect(sameScene(IDLE_SCENE, { mood: 'thinking', active: {}, agreement: null })).toBe(false);
  });
});
