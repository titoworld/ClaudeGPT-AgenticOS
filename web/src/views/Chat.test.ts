// The conversation keeps what gets the keyboard focus above the sticky stop bar of a running
// refine turn (RefineControls.svelte), never behind it (WCAG 2.2, 2.4.11): browsers honour the
// scroll padding of the scroller when they bring the focus into view.
import { flushSync } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { app } from '../lib/app.svelte';
import { i18n } from '../lib/i18n/index.svelte';
import { stopBars } from '../lib/stop-bars.svelte';
import { cleanup, render, textOf } from '../lib/test-render';
import Chat from './Chat.svelte';

beforeEach(() => {
  // jsdom has no ResizeObserver (the chat follows its content's height).
  vi.stubGlobal('ResizeObserver', class { observe(): void {} unobserve(): void {} disconnect(): void {} });
});

afterEach(() => {
  stopBars.remove('bar');
  cleanup();
  vi.unstubAllGlobals();
});

const scroller = (root: HTMLElement) => root.querySelector<HTMLElement>('.scroller')!;

describe('the room of the stop bar', () => {
  it('while a bar shows, the scroller keeps its height, its offset and a gap free for the focus', () => {
    const root = render(Chat, {});
    expect(scroller(root).style.scrollPaddingBottom).toBe('');
    stopBars.set('bar', 107);
    flushSync();
    expect(scroller(root).style.scrollPaddingBottom).toBe('calc(107px + 0.6rem + var(--pill-room, 0rem) + 0.75rem)');
    stopBars.set('bar', 57); // the window widened: the bar is one line
    flushSync();
    expect(scroller(root).style.scrollPaddingBottom).toBe('calc(57px + 0.6rem + var(--pill-room, 0rem) + 0.75rem)');
    stopBars.remove('bar');
    flushSync();
    expect(scroller(root).style.scrollPaddingBottom).toBe('');
  });

  it('with two bars, the tallest one', () => {
    const root = render(Chat, {});
    stopBars.set('bar', 57);
    stopBars.set('other', 107);
    flushSync();
    expect(scroller(root).style.scrollPaddingBottom).toBe('calc(107px + 0.6rem + var(--pill-room, 0rem) + 0.75rem)');
    stopBars.remove('other');
    flushSync();
    expect(scroller(root).style.scrollPaddingBottom).toBe('calc(57px + 0.6rem + var(--pill-room, 0rem) + 0.75rem)');
  });
});

describe('a conversation in English and Spanish', () => {
  afterEach(() => {
    i18n.set('ca');
    app.convs.currentId = null;
    app.convs.loading = false;
    app.convs.loadError = null;
  });

  it('says that it loads, that it could not, and that it has no messages yet', () => {
    i18n.set('en');
    app.convs.currentId = 5;
    app.convs.loading = true;
    const root = render(Chat, {});
    expect(textOf(root.querySelector('.loading'))).toBe('Loading the conversation…');
    app.convs.loading = false;
    app.convs.loadError = 'The conversation could not be loaded.';
    flushSync();
    expect(textOf(root.querySelector('.load-error'))).toBe('The conversation could not be loaded. Try again');
    app.convs.loadError = null;
    i18n.set('es');
    flushSync();
    expect(textOf(root.querySelector('.empty-conv'))).toBe('Esta conversación todavía no tiene mensajes.');
  });
});
