import { describe, expect, it } from 'vitest';
import { effectiveQuality, FrameWatchdog, isSceneQuality, isSoftwareRenderer, PIXEL_BUDGET_HIGH, pixelRatioFor } from './quality';

describe('isSoftwareRenderer', () => {
  it('detects CPU rasterisers', () => {
    expect(isSoftwareRenderer('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)')).toBe(true);
    expect(isSoftwareRenderer('llvmpipe (LLVM 15.0.7, 256 bits)')).toBe(true);
    expect(isSoftwareRenderer('Microsoft Basic Render Driver')).toBe(true);
    expect(isSoftwareRenderer('Software Adapter')).toBe(true);
  });

  it('does not flag real GPUs or missing info', () => {
    expect(isSoftwareRenderer('ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)')).toBe(false);
    expect(isSoftwareRenderer('Apple M2')).toBe(false);
    expect(isSoftwareRenderer('')).toBe(false);
    expect(isSoftwareRenderer(undefined)).toBe(false);
  });
});

describe('effectiveQuality', () => {
  it('caps software renderers at low', () => {
    expect(effectiveQuality('high', true)).toBe('low');
    expect(effectiveQuality('low', true)).toBe('low');
    expect(effectiveQuality('off', true)).toBe('off');
  });

  it('respects the watchdog ceiling', () => {
    expect(effectiveQuality('high', false, 'low')).toBe('low');
    expect(effectiveQuality('high', false)).toBe('high');
    expect(effectiveQuality('off', false, 'low')).toBe('off');
  });

  it('validates quality names', () => {
    expect(isSceneQuality('low')).toBe(true);
    expect(isSceneQuality('ultra')).toBe(false);
  });
});

describe('pixelRatioFor', () => {
  it('uses up to DPR 2 in high and 1 in low/off', () => {
    expect(pixelRatioFor('high', 3, 800, 600)).toBe(2);
    expect(pixelRatioFor('high', 1, 800, 600)).toBe(1);
    expect(pixelRatioFor('low', 2, 800, 600)).toBe(1);
    expect(pixelRatioFor('off', 2, 800, 600)).toBe(1);
  });

  it('keeps high within the pixel budget on huge canvases', () => {
    const pr = pixelRatioFor('high', 2, 2560, 1440);
    expect(pr).toBeLessThan(2);
    expect(2560 * 1440 * pr * pr).toBeLessThanOrEqual(PIXEL_BUDGET_HIGH * 1.01);
  });

  it('applies the watchdog scale with a floor and survives bad input', () => {
    expect(pixelRatioFor('low', 1, 800, 600, 0.75)).toBe(0.75);
    expect(pixelRatioFor('low', 1, 800, 600, 0.1)).toBe(0.5);
    expect(pixelRatioFor('high', Number.NaN, 800, 600)).toBe(1);
  });
});

describe('FrameWatchdog', () => {
  const opts = { slowFrameMs: 28, windowFrames: 10, strikes: 2, warmupFrames: 5 };

  it('stays quiet at a healthy frame rate', () => {
    const w = new FrameWatchdog(opts);
    for (let i = 0; i < 200; i++) expect(w.sample(16.7)).toBe('ok');
  });

  it('reports slow after consecutive slow windows, then starts over', () => {
    const w = new FrameWatchdog(opts);
    const results: string[] = [];
    for (let i = 0; i < 5 + 20; i++) results.push(w.sample(50));
    expect(results.filter((r) => r === 'slow')).toHaveLength(1);
    expect(results.at(-1)).toBe('slow');
    // After reporting it warms up again before judging.
    for (let i = 0; i < 5 + 10; i++) expect(w.sample(50)).toBe('ok');
  });

  it('ignores hiccups such as tab switches or GC pauses', () => {
    const w = new FrameWatchdog(opts);
    for (let i = 0; i < 100; i++) expect(w.sample(i % 10 === 0 ? 900 : 16)).toBe('ok');
  });

  it('forgives a single slow window', () => {
    const w = new FrameWatchdog(opts);
    for (let i = 0; i < 5; i++) w.sample(16); // warm-up
    for (let i = 0; i < 10; i++) expect(w.sample(50)).toBe('ok'); // one slow window
    for (let i = 0; i < 30; i++) expect(w.sample(16)).toBe('ok');
  });
});
