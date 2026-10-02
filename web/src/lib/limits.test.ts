import { afterAll, describe, expect, it, vi } from 'vitest';

// A zone east of UTC: a reset shortly after local midnight falls on the previous UTC day.
// (Node reads TZ again when it changes; `process` is not typed in this project.)
type Env = Record<string, string | undefined>;
const previousTz = vi.hoisted(() => {
  const env = (globalThis as unknown as { process: { env: Env } }).process.env;
  const tz = env.TZ;
  env.TZ = 'Europe/Madrid';
  return tz;
});

import { LIMIT_WARN_PERCENT, limitLevel, resetText, shortUntil } from './limits';

afterAll(() => {
  const env = (globalThis as unknown as { process: { env: Env } }).process.env;
  if (previousTz === undefined) delete env.TZ;
  else env.TZ = previousTz;
});

describe('resetText (F6)', () => {
  const now = new Date('2026-09-27T08:00:00Z'); // Sunday 27, 10:00 in Madrid

  it('names the local day of the local time it shows', () => {
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe('Europe/Madrid');
    // Wednesday 30 at 01:30 in Madrid is still Tuesday 29 in UTC.
    expect(resetText('2026-09-29T23:30:00Z', now)).toBe('Es restableix d’aquí a 3 dies (30 de set., 01:30)');
  });

  it('shows only the time for a reset later today', () => {
    expect(resetText('2026-09-27T15:00:00Z', now)).toBe('Es restableix d’aquí a 7 hores (17:00)');
  });

  it('shortUntil counts down in minutes, hours and days', () => {
    expect(shortUntil('2026-09-27T08:00:30Z', now)).toBe('ara');
    expect(shortUntil('2026-09-27T08:35:00Z', now)).toBe('35 min');
    expect(shortUntil('2026-09-27T10:00:00Z', now)).toBe('2 h');
    expect(shortUntil('2026-09-30T08:00:00Z', now)).toBe('3 d');
  });
});

describe('limitLevel', () => {
  it('uses the status and the used share', () => {
    expect(limitLevel('allowed', 10)).toBe('ok');
    expect(limitLevel('allowed', LIMIT_WARN_PERCENT)).toBe('warn');
    expect(limitLevel('warning', null)).toBe('warn');
    expect(limitLevel('allowed', 100)).toBe('bad');
    expect(limitLevel('rejected', 5)).toBe('bad');
  });
});
