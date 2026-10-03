// Texts of AttachmentCard, AttachmentViewer, TextPreview, PdfPages, lib/attachments.ts, lib/composer-attachments.svelte.ts, lib/attachment-work.ts, lib/media.ts, lib/image-header.ts, lib/load-pdf.ts, lib/pdf.ts. English is the source: Spanish and Catalan have the same keys and
// parameters (TypeScript checks it).

import { plural } from '../index.svelte';

export const en = {
  // lib/attachments.ts: why a file is refused, as the server says it (src/agentic_os/attachments.py).
  refused: {
    unsupported: 'This type of file is not accepted. You can attach images (PNG, JPEG, GIF or WebP), PDFs and text files (UTF-8).',
    svg: 'SVG images are not accepted, because they can carry code. Convert it to PNG and attach it again.',
    heic: 'HEIC images are not accepted. Convert it to JPEG (or take a screenshot of it) and attach it again.',
    empty: 'The file is empty.',
    tooMany: (max: number) => `A message can carry at most ${max} attachments.`,
    turnTooLarge: (limit: string) => `The attachments of a message cannot add up to more than ${limit}.`,
    tooLarge: {
      image: (limit: string) => `The file is too large: an image can be at most ${limit}.`,
      pdf: (limit: string) => `The file is too large: a PDF can be at most ${limit}.`,
      text: (limit: string) => `The file is too large: a text file can be at most ${limit}.`,
    },
    imageSide: (width: string, height: string, max: string) => `The image is ${width} x ${height} pixels: at most ${max} per side.`,
    pdfPages: (pages: string, max: string) => `The PDF has ${pages} pages: at most ${max}.`,
  },
  // lib/media.ts, lib/composer-attachments.svelte.ts: what the browser says itself.
  failed: {
    unreadableImage: 'The image could not be read: the file is not valid.',
    connection: 'The file could not be uploaded: the connection failed.',
    upload: (status: number) => `The file could not be uploaded (error ${status}).`,
    gone: 'This attachment is no longer on the server: remove it and attach it again.',
  },
  // lib/attachments.ts: what a card says (the type, the pages); a file without a name.
  kinds: { image: 'Image', pdf: 'PDF', text: 'Text file' },
  file: 'File',
  pages: (n: number, count: string) => plural(n, `${count} page`, `${count} pages`),
  unnamed: 'file',
  // AttachmentCard
  card: {
    uploading: 'Uploading…',
    tokens: (n: string) => `≈ ${n} tokens`,
    /** Before the pages of a PDF's warning: one page, or more (`many`). */
    pageAbbr: (many: boolean): string => (many ? 'pp.' : 'p.'),
    open: 'Open preview',
    retry: 'Try again',
    remove: (name: string) => `Remove ${name}`,
    removeTitle: 'Remove attachment',
  },
  // AttachmentViewer
  viewer: { download: 'Download', close: 'Close preview' },
  // TextPreview
  text: { failed: 'The file could not be loaded. You can download it.', loading: 'Loading…' },
  // PdfPages
  pdf: {
    failed: 'The PDF could not be shown. You can download it.',
    loading: 'Loading the PDF…',
    pages: 'Pages',
    previous: 'Previous page',
    next: 'Next page',
    page: (page: number, pages: number) => `Page ${page} of ${pages}`,
  },
};

export const es: typeof en = {
  refused: {
    unsupported: 'Este tipo de archivo no se admite. Puedes adjuntar imágenes (PNG, JPEG, GIF o WebP), PDF y archivos de texto (UTF-8).',
    svg: 'Las imágenes SVG no se admiten porque pueden llevar código. Conviértela a PNG y vuelve a adjuntarla.',
    heic: 'Las imágenes HEIC no se admiten. Conviértela a JPEG (o hazle una captura) y vuelve a adjuntarla.',
    empty: 'El archivo está vacío.',
    tooMany: (max: number) => `Un mensaje puede llevar como máximo ${max} adjuntos.`,
    turnTooLarge: (limit: string) => `Los adjuntos de un mensaje no pueden sumar más de ${limit}.`,
    tooLarge: {
      image: (limit: string) => `El archivo es demasiado grande: una imagen puede ocupar como máximo ${limit}.`,
      pdf: (limit: string) => `El archivo es demasiado grande: un PDF puede ocupar como máximo ${limit}.`,
      text: (limit: string) => `El archivo es demasiado grande: un archivo de texto puede ocupar como máximo ${limit}.`,
    },
    imageSide: (width: string, height: string, max: string) => `La imagen mide ${width} x ${height} píxeles: como máximo ${max} por lado.`,
    pdfPages: (pages: string, max: string) => `El PDF tiene ${pages} páginas: como máximo ${max}.`,
  },
  failed: {
    unreadableImage: 'No se ha podido leer la imagen: el archivo no es válido.',
    connection: 'No se ha podido subir el archivo: la conexión ha fallado.',
    upload: (status: number) => `No se ha podido subir el archivo (error ${status}).`,
    gone: 'Este adjunto ya no está en el servidor: quítalo y vuelve a adjuntarlo.',
  },
  kinds: { image: 'Imagen', pdf: 'PDF', text: 'Archivo de texto' },
  file: 'Archivo',
  pages: (n: number, count: string) => plural(n, `${count} página`, `${count} páginas`),
  unnamed: 'archivo',
  card: {
    uploading: 'Subiendo…',
    tokens: (n: string) => `≈ ${n} tokens`,
    pageAbbr: (many: boolean) => (many ? 'págs.' : 'pág.'),
    open: 'Abrir la vista previa',
    retry: 'Reintentar',
    remove: (name: string) => `Quitar ${name}`,
    removeTitle: 'Quitar el adjunto',
  },
  viewer: { download: 'Descargar', close: 'Cerrar la vista previa' },
  text: { failed: 'No se ha podido cargar el archivo. Puedes descargarlo.', loading: 'Cargando…' },
  pdf: {
    failed: 'No se ha podido mostrar el PDF. Puedes descargarlo.',
    loading: 'Cargando el PDF…',
    pages: 'Páginas',
    previous: 'Página anterior',
    next: 'Página siguiente',
    page: (page: number, pages: number) => `Página ${page} de ${pages}`,
  },
};

export const ca: typeof en = {
  refused: {
    unsupported: "Aquest tipus de fitxer no s'admet. Pots adjuntar imatges (PNG, JPEG, GIF o WebP), PDF i fitxers de text (UTF-8).",
    svg: "Les imatges SVG no s'admeten, perquè poden portar codi. Converteix-la a PNG i torna-la a adjuntar.",
    heic: "Les imatges HEIC no s'admeten. Converteix-la a JPEG (o fes-ne una captura) i torna-la a adjuntar.",
    empty: 'El fitxer és buit.',
    tooMany: (max: number) => `Un missatge pot portar com a màxim ${max} adjunts.`,
    turnTooLarge: (limit: string) => `Els adjunts d'un missatge no poden sumar més de ${limit}.`,
    tooLarge: {
      image: (limit: string) => `El fitxer és massa gran: una imatge pot tenir com a molt ${limit}.`,
      pdf: (limit: string) => `El fitxer és massa gran: un PDF pot tenir com a molt ${limit}.`,
      text: (limit: string) => `El fitxer és massa gran: un fitxer de text pot tenir com a molt ${limit}.`,
    },
    imageSide: (width: string, height: string, max: string) => `La imatge fa ${width} x ${height} píxels: com a molt ${max} per costat.`,
    pdfPages: (pages: string, max: string) => `El PDF té ${pages} pàgines: com a molt ${max}.`,
  },
  failed: {
    unreadableImage: "No s'ha pogut llegir la imatge: el fitxer no és vàlid.",
    connection: "No s'ha pogut pujar el fitxer: la connexió ha fallat.",
    upload: (status: number) => `No s'ha pogut pujar el fitxer (error ${status}).`,
    gone: "Aquest adjunt ja no és al servidor: treu-lo i torna'l a adjuntar.",
  },
  kinds: { image: 'Imatge', pdf: 'PDF', text: 'Fitxer de text' },
  file: 'Fitxer',
  pages: (n: number, count: string) => plural(n, `${count} pàgina`, `${count} pàgines`),
  unnamed: 'fitxer',
  card: {
    uploading: 'Pujant…',
    tokens: (n: string) => `≈ ${n} tokens`,
    pageAbbr: (_many: boolean) => 'pàg.',
    open: 'Obre la vista prèvia',
    retry: 'Torna-ho a provar',
    remove: (name: string) => `Treu ${name}`,
    removeTitle: "Treu l'adjunt",
  },
  viewer: { download: 'Descarrega', close: 'Tanca la vista prèvia' },
  text: { failed: "No s'ha pogut carregar el fitxer. Pots descarregar-lo.", loading: 'Carregant…' },
  pdf: {
    failed: "No s'ha pogut mostrar el PDF. Pots descarregar-lo.",
    loading: 'Carregant el PDF…',
    pages: 'Pàgines',
    previous: 'Pàgina anterior',
    next: 'Pàgina següent',
    page: (page: number, pages: number) => `Pàgina ${page} de ${pages}`,
  },
};
