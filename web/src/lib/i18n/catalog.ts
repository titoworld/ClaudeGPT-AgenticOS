// Every text of the interface, by language: one module per area (areas/), each with the
// same keys in English, Spanish and Catalan.

import * as app from './areas/app';
import * as attachments from './areas/attachments';
import * as common from './areas/common';
import * as composer from './areas/composer';
import * as dashboard from './areas/dashboard';
import * as pdf from './areas/pdf';
import * as refine from './areas/refine';
import * as settings from './areas/settings';
import * as turn from './areas/turn';

const AREAS = { common, app, composer, attachments, turn, pdf, refine, settings, dashboard };

type Areas = typeof AREAS;
export type Messages = { [A in keyof Areas]: Areas[A]['en'] };

function catalog(locale: 'en' | 'es' | 'ca'): Messages {
  return Object.fromEntries(Object.entries(AREAS).map(([name, area]) => [name, area[locale]])) as Messages;
}

export const CATALOGS = { en: catalog('en'), es: catalog('es'), ca: catalog('ca') } as const;
