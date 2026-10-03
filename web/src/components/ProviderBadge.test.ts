// A provider's badge in the sidebar: whether it is available, its usage windows and its
// details, in the language in force (each language writes a percentage its own way).
import { flushSync } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';
import { i18n } from '../lib/i18n/index.svelte';
import type { ProviderStatus } from '../lib/protocol';
import { cleanup, render, textOf } from '../lib/test-render';
import ProviderBadge from './ProviderBadge.svelte';

const CLAUDE: ProviderStatus = {
  agent: 'claude',
  mode: 'cli',
  available: true,
  model: 'claude-sonnet',
  detail: '',
  limits: [{ window: '5h', used_percent: 42.4, resets_at: null, status: 'allowed' }],
};

afterEach(() => {
  cleanup();
  i18n.set('ca');
});

describe('ProviderBadge', () => {
  it('in Catalan, English and Spanish', () => {
    const root = render(ProviderBadge, { provider: CLAUDE });
    const meter = () => root.querySelector('[role=meter]')!;
    expect(textOf(root.querySelector('.pct'))).toBe('42 %');
    expect(meter().getAttribute('aria-label')).toBe('Ús de la finestra de 5h');
    expect(textOf(root.querySelector('.avail .sr-only'))).toBe('Disponible');
    expect(textOf(root.querySelector('.mode'))).toBe('Subscripció');

    i18n.set('en');
    flushSync();
    expect(textOf(root.querySelector('.pct'))).toBe('42%');
    expect(meter().getAttribute('aria-valuetext')).toBe('42%');
    expect(meter().getAttribute('aria-label')).toBe('Usage of the 5h window');
    expect(textOf(root.querySelector('.avail .sr-only'))).toBe('Available');
    expect(textOf(root.querySelector('.mode'))).toBe('Subscription');
    expect(textOf(root.querySelector('[role=tooltip]'))).toBe('No details.');

    i18n.set('es');
    flushSync();
    expect(textOf(root.querySelector('.pct'))).toBe('42 %');
    expect(meter().getAttribute('aria-label')).toBe('Uso de la ventana de 5h');
    expect(textOf(root.querySelector('.avail .sr-only'))).toBe('Disponible');
    expect(textOf(root.querySelector('.mode'))).toBe('Suscripción');
    expect(textOf(root.querySelector('[role=tooltip]'))).toBe('Sin detalles.');
  });
});
