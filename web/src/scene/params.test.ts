import { describe, expect, it } from 'vitest';
import {
  activityTargets,
  agreementToSeparation,
  cssHexColor,
  damp,
  errorEnvelope,
  errorFlicker,
  flashEnvelope,
  isSceneMood,
  isTransientMood,
  moodTargets,
  sanitizeIntensity,
  SCENE_MOODS,
  SEPARATION_DEFAULT,
  SEPARATION_FAR,
  SEPARATION_NEAR,
  wrapAngle,
} from './params';

describe('mood targets', () => {
  it('covers every mood of the contract', () => {
    expect(SCENE_MOODS.sort()).toEqual(['consensus', 'debate', 'error', 'idle', 'speaking', 'synthesis', 'thinking']);
  });

  it('shows the stream only while debating or synthesising', () => {
    for (const mood of SCENE_MOODS) {
      const { stream } = moodTargets(mood);
      if (mood === 'debate' || mood === 'synthesis') expect(stream).toBeGreaterThan(0.5);
      else expect(stream).toBe(0);
    }
  });

  it('treats consensus and error as one-shot effects that settle to idle', () => {
    expect(isTransientMood('consensus')).toBe(true);
    expect(isTransientMood('error')).toBe(true);
    expect(isTransientMood('debate')).toBe(false);
    expect(moodTargets('consensus')).toEqual(moodTargets('idle'));
    expect(moodTargets('error')).toEqual(moodTargets('idle'));
  });

  it('validates mood names', () => {
    expect(isSceneMood('debate')).toBe(true);
    expect(isSceneMood('toString')).toBe(false);
    expect(isSceneMood(undefined)).toBe(false);
  });
});

describe('agreementToSeparation', () => {
  it('uses the default distance when agreement is unknown', () => {
    expect(agreementToSeparation(null)).toBe(SEPARATION_DEFAULT);
    expect(agreementToSeparation(Number.NaN)).toBe(SEPARATION_DEFAULT);
  });

  it('brings the orbs closer as agreement grows, clamped to 0..100', () => {
    expect(agreementToSeparation(0)).toBeCloseTo(SEPARATION_FAR);
    expect(agreementToSeparation(100)).toBeCloseTo(SEPARATION_NEAR);
    expect(agreementToSeparation(-20)).toBeCloseTo(SEPARATION_FAR);
    expect(agreementToSeparation(250)).toBeCloseTo(SEPARATION_NEAR);
    const values = [0, 25, 50, 75, 100].map(agreementToSeparation);
    for (let i = 1; i < values.length; i++) expect(values[i]!).toBeLessThan(values[i - 1]!);
  });
});

describe('activityTargets', () => {
  it('keeps both agents at rest brightness when none is active', () => {
    expect(activityTargets({})).toEqual({ claude: 0.7, chatgpt: 0.7 });
    expect(activityTargets(null)).toEqual({ claude: 0.7, chatgpt: 0.7 });
  });

  it('dims the inactive agent', () => {
    const t = activityTargets({ claude: true });
    expect(t.claude).toBe(1);
    expect(t.chatgpt).toBeLessThan(0.5);
    expect(activityTargets({ claude: true, chatgpt: true })).toEqual({ claude: 1, chatgpt: 1 });
  });
});

describe('sanitizeIntensity', () => {
  it('clamps and defaults non-finite values', () => {
    expect(sanitizeIntensity(undefined)).toBe(0.5);
    expect(sanitizeIntensity(Number.NaN)).toBe(0.5);
    expect(sanitizeIntensity(Number.POSITIVE_INFINITY)).toBe(0.5);
    expect(sanitizeIntensity(-1)).toBe(0);
    expect(sanitizeIntensity(3)).toBe(1);
    expect(sanitizeIntensity(0.25)).toBe(0.25);
  });
});

describe('damp', () => {
  it('moves towards the target without overshooting, independent of frame rate', () => {
    const oneStep = damp(0, 1, 2, 0.5);
    let twoSteps = damp(0, 1, 2, 0.25);
    twoSteps = damp(twoSteps, 1, 2, 0.25);
    expect(oneStep).toBeGreaterThan(0);
    expect(oneStep).toBeLessThan(1);
    expect(twoSteps).toBeCloseTo(oneStep, 10);
  });

  it('ignores zero or invalid time steps', () => {
    expect(damp(0.3, 1, 2, 0)).toBe(0.3);
    expect(damp(0.3, 1, 2, Number.NaN)).toBe(0.3);
  });
});

describe('wrapAngle', () => {
  it('wraps to (-PI, PI]', () => {
    expect(wrapAngle(0)).toBe(0);
    expect(wrapAngle(Math.PI * 2)).toBeCloseTo(0);
    expect(wrapAngle(Math.PI * 3)).toBeCloseTo(Math.PI);
    expect(wrapAngle(-Math.PI * 1.5)).toBeCloseTo(Math.PI / 2);
  });
});

describe('effect envelopes', () => {
  it('flash rises quickly and fades out', () => {
    expect(flashEnvelope(-1, false)).toBe(0);
    expect(flashEnvelope(0.12, false)).toBeCloseTo(1);
    expect(flashEnvelope(1, false)).toBeLessThan(0.5);
    expect(flashEnvelope(10, false)).toBe(0);
  });

  it('reduced-motion flash is gentler (slower attack)', () => {
    expect(flashEnvelope(0.1, true)).toBeLessThan(flashEnvelope(0.1, false));
  });

  it('error holds briefly and ends within about two seconds', () => {
    expect(errorEnvelope(0.3)).toBe(1);
    expect(errorEnvelope(1)).toBeLessThan(1);
    expect(errorEnvelope(3)).toBe(0);
  });

  it('error flicker stays under 3 flashes per second and is off with reduced motion', () => {
    expect(errorFlicker(0.4, 1, true)).toBe(1);
    // Sample one second finely and count brightness minima.
    let minima = 0;
    let prev = errorFlicker(0, 1, false);
    let falling = false;
    for (let t = 0.001; t <= 1; t += 0.001) {
      const v = errorFlicker(t, 1, false);
      if (v > prev && falling) minima++;
      falling = v < prev;
      prev = v;
    }
    expect(minima).toBeLessThan(3);
    expect(errorFlicker(0.3, 0, false)).toBe(1);
  });
});

describe('cssHexColor', () => {
  it('accepts hex colours and falls back otherwise', () => {
    expect(cssHexColor(' #dd6b3b ', '#000')).toBe('#dd6b3b');
    expect(cssHexColor('#abc', '#000')).toBe('#abc');
    expect(cssHexColor('rgb(1 2 3)', '#000')).toBe('#000');
    expect(cssHexColor('', '#123456')).toBe('#123456');
    expect(cssHexColor(null, '#123456')).toBe('#123456');
  });
});
