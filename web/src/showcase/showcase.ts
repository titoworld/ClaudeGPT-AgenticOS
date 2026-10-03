// Development-only harness: the whole app against a fake server (server.ts) with example
// conversations (conversations.ts, texts in content/<lang>/), for the README screenshots and
// to look at the interface without a backend or spending tokens.
// `?lang=` picks the language of the interface and of the examples: en (default), es or ca.
// `?shot=` picks the view:
//   start        a new conversation (the four modes)
//   council      a council debate that reached consensus (default)
//   attachments  a duel over a PDF, with Claude's check for ChatGPT on the subscription
//   refine       a Refine turn in its fourth round
//   dashboard    the usage dashboard
//   settings     the settings, over the council
// `?narrow` opens the sidebar as on a phone (use a narrow window). The live turns replay
// their events at once; nothing streams after the page has loaded.

import { mount } from 'svelte';
import '../styles/tokens.css';
import '../app.css';
import { i18n, LOCALES, type Locale } from '../lib/i18n/index.svelte';
import {
  conversationList,
  DEBATE_ID,
  debateMessages,
  PDF_ID,
  PDF_REQUEST,
  pdfAttachment,
  pdfEvents,
  pdfMessages,
  REFINE_ID,
  REFINE_REQUEST,
  refineEvents,
  refineMessages,
} from './conversations';
import { live, ShowcaseSocket, showcaseApi } from './server';

/** The quote PDF of each language and its thumbnail, by path: ./content/<lang>/<name>. */
const FILES = import.meta.glob<string>(['./content/*/*.pdf', './content/*/*.webp'], { query: '?url', import: 'default', eager: true });

const ROUTES: Record<string, string> = {
  start: '#/',
  council: `#/c/${DEBATE_ID}`,
  attachments: `#/c/${PDF_ID}`,
  refine: `#/c/${REFINE_ID}`,
  dashboard: '#/tauler',
  settings: `#/c/${DEBATE_ID}`,
};

const params = new URLSearchParams(location.search);
const shot = params.get('shot') ?? 'council';
const route = ROUTES[shot];
if (!route) throw new Error(`Unknown shot «${shot}»: ${Object.keys(ROUTES).join(', ')}`);

function locale(value: string): Locale {
  const found = LOCALES.find((l) => l === value);
  if (!found) throw new Error(`Unknown lang «${value}»: ${LOCALES.join(', ')}`);
  return found;
}

const lang = locale(params.get('lang') ?? 'en');

function file(name: string): string {
  const url = FILES[`./content/${lang}/${name}`];
  if (!url) throw new Error(`Missing content/${lang}/${name}`);
  return url;
}

async function bytes(url: string): Promise<Uint8Array> {
  const response = await fetch(url);
  return new Uint8Array(await response.arrayBuffer());
}

const attachment = pdfAttachment(lang);
const pdfUrl = file(attachment.name);
const thumbnailUrl = file(attachment.name.replace(/\.pdf$/, '.webp'));

// The files, before fetch is replaced: the fake server answers every request afterwards.
const [pdf, thumbnail] = await Promise.all([bytes(pdfUrl), bytes(thumbnailUrl)]);

const { api, fetch: serverFetch } = showcaseApi();
api.conversations = conversationList(lang);
api.messages.set(DEBATE_ID, debateMessages(lang));
api.messages.set(PDF_ID, pdfMessages(lang));
api.messages.set(REFINE_ID, refineMessages(lang));
api.addAttachment(attachment, pdf);
const stored = api.attachments.get(attachment.id);
if (stored) stored.thumbnail = thumbnail;
live.set(REFINE_REQUEST, { conversationId: REFINE_ID, events: refineEvents(lang) });
live.set(PDF_REQUEST, { conversationId: PDF_ID, events: pdfEvents(lang) });

// A clean browser: no preferences, no draft, logged in, in the language of `?lang=`.
localStorage.clear();
sessionStorage.clear();
i18n.set(lang);
history.replaceState(null, '', `${location.pathname}${location.search}${route}`);
window.fetch = serverFetch;
window.WebSocket = ShowcaseSocket as unknown as typeof WebSocket;

// An <img> loads its address without fetch: the PDF's thumbnail points to its file instead.
const thumbnailPath = `/api/attachments/${attachment.id}/thumbnail`;
const imageAddress = (value: string): string => (value === thumbnailPath ? thumbnailUrl : value);
const src = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
if (src?.set) {
  const set = src.set;
  Object.defineProperty(HTMLImageElement.prototype, 'src', {
    ...src,
    set(this: HTMLImageElement, value: string) {
      set.call(this, imageAddress(value));
    },
  });
}
const setAttribute = Element.prototype.setAttribute;
Element.prototype.setAttribute = function (this: Element, name: string, value: string) {
  setAttribute.call(this, name, this instanceof HTMLImageElement && name === 'src' ? imageAddress(value) : value);
};

// The app reads the route and the storage when its modules load: only now.
const [{ default: App }, { app }] = await Promise.all([import('../App.svelte'), import('../lib/app.svelte')]);
const target = document.getElementById('app');
if (!target) throw new Error('Missing #app');
mount(App, { target });

/** Runs `then` once `ready` holds (the app loads the session and the settings first). */
function when(ready: () => boolean, then: () => void): void {
  const timer = setInterval(() => {
    if (!ready()) return;
    clearInterval(timer);
    then();
  }, 50);
}

if (shot === 'settings') when(() => app.settingsStatus === 'ready', () => (app.settingsOpen = true));
if (params.has('narrow')) when(() => app.auth === 'ready', () => (app.sidebarOpen = true));
