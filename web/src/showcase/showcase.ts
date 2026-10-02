// Development-only harness: the whole app against a fake server (server.ts) with example
// conversations in Catalan (conversations.ts), for the README screenshots and to look at
// the interface without a backend or spending tokens. `?shot=` picks the view:
//   inici        a new conversation (the four modes)
//   consell      a council debate that reached consensus (default)
//   adjunts      a duel over a PDF, with Claude's check for ChatGPT on the subscription
//   perfecciona  a «Perfecciona» turn in its fourth round
//   tauler       the usage dashboard
//   configuracio the settings, over the council
// `?narrow` opens the sidebar as on a phone (use a narrow window). The live turns replay
// their events at once; nothing streams after the page has loaded.

import { mount } from 'svelte';
import '../styles/tokens.css';
import '../app.css';
import {
  conversationList,
  DEBATE_ID,
  debateMessages,
  PDF_ATTACHMENT,
  PDF_ID,
  PDF_REQUEST,
  pdfEvents,
  pdfMessages,
  REFINE_ID,
  REFINE_REQUEST,
  refineEvents,
  refineMessages,
} from './conversations';
import pdfUrl from './pressupost-cuina.pdf?url';
import thumbnailUrl from './pressupost-cuina.webp?url';
import { live, ShowcaseSocket, showcaseApi } from './server';

const ROUTES: Record<string, string> = {
  inici: '#/',
  consell: `#/c/${DEBATE_ID}`,
  adjunts: `#/c/${PDF_ID}`,
  perfecciona: `#/c/${REFINE_ID}`,
  tauler: '#/tauler',
  configuracio: `#/c/${DEBATE_ID}`,
};

const params = new URLSearchParams(location.search);
const shot = params.get('shot') ?? 'consell';
const route = ROUTES[shot];
if (!route) throw new Error(`Unknown shot «${shot}»: ${Object.keys(ROUTES).join(', ')}`);

async function bytes(url: string): Promise<Uint8Array> {
  const response = await fetch(url);
  return new Uint8Array(await response.arrayBuffer());
}

// The files, before fetch is replaced: the fake server answers every request afterwards.
const [pdf, thumbnail] = await Promise.all([bytes(pdfUrl), bytes(thumbnailUrl)]);

const { api, fetch: serverFetch } = showcaseApi();
api.conversations = conversationList();
api.messages.set(DEBATE_ID, debateMessages());
api.messages.set(PDF_ID, pdfMessages(pdf.length));
api.messages.set(REFINE_ID, refineMessages());
api.addAttachment({ ...PDF_ATTACHMENT, size: pdf.length }, pdf);
const stored = api.attachments.get(PDF_ATTACHMENT.id);
if (stored) stored.thumbnail = thumbnail;
live.set(REFINE_REQUEST, { conversationId: REFINE_ID, events: refineEvents() });
live.set(PDF_REQUEST, { conversationId: PDF_ID, events: pdfEvents() });

// A clean browser: no preferences, no draft, logged in.
localStorage.clear();
sessionStorage.clear();
history.replaceState(null, '', `${location.pathname}${location.search}${route}`);
window.fetch = serverFetch;
window.WebSocket = ShowcaseSocket as unknown as typeof WebSocket;

// An <img> loads its address without fetch: the PDF's thumbnail points to its file instead.
const thumbnailPath = `/api/attachments/${PDF_ATTACHMENT.id}/thumbnail`;
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

if (shot === 'configuracio') when(() => app.settingsStatus === 'ready', () => (app.settingsOpen = true));
if (params.has('narrow')) when(() => app.auth === 'ready', () => (app.sidebarOpen = true));
