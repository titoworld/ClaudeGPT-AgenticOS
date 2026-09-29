# 0009. Adjunts: imatges, PDF i fitxers de text al xat

- Estat: Proposat
- Data: 2026-09-29

## Context

El propietari va demanar, el 28 de setembre de 2026, poder adjuntar imatges i documents al xat, amb miniatures com a claude.ai. Si ChatGPT no pot llegir un PDF, Claude l'ha d'interpretar i passar-li la seva lectura, i el text extret del PDF s'ha de contrastar amb el que llegeix Claude. Va demanar la millor manera de fer-ho.

**Què accepta cada proveïdor** (versions fixades, comprovat el 28 de setembre de 2026 sense cap crida a les API):

- **CLI de Claude 2.1.283** (entrada `stream-json`): el `message.content` accepta blocs `image` (base64, URL o fitxer; JPEG, PNG, GIF i WebP) i blocs `document` (PDF en base64, text, URL o fitxer). El seu esquema diu: «send images and documents in `message.content`».
- **Codex app-server 0.157.1** (`codex app-server generate-ts`): `UserInput` pot ser text, `image` (URL), `localImage` (camí), àudio, *skill* o menció. **No accepta cap PDF ni cap document.**
- **API de Claude:** blocs d'imatge i de document (PDF). **API d'OpenAI** (Responses): `input_image` i `input_file` (PDF).

**Límits d'Anthropic** (platform.claude.com/docs, `vision.md` i `pdf-support.md`, consultats el 28 de setembre de 2026):

- Imatges JPEG, PNG, GIF i WebP (d'una animació, només el primer fotograma), com a molt de 8000 × 8000 píxels i de 10 MB codificades en base64 per imatge (uns 7,5 MB de bytes). Amb més de 20 imatges en una petició, el límit per imatge és més estricte.
- El model redueix la imatge fins que el costat llarg fa 2576 píxels (Claude 4.7 i posteriors), i una imatge costa `ceil(w/28) × ceil(h/28)` tokens, com a molt 4784.
- PDF: la petició sencera fins a 32 MB, fins a 600 pàgines (100 quan la finestra de context és de menys d'1M de tokens), sense contrasenya ni xifratge. Cada pàgina s'envia com a text **i** com a imatge: uns 1.500–3.000 tokens de text per pàgina més els de la imatge.

**Límits d'OpenAI** (API Responses, guia «File inputs» de developers.openai.com, 29 de setembre de 2026). La pàgina no es pot obrir des de l'entorn on s'ha fet aquest canvi (el proxy de sortida la bloqueja), així que només se n'ha pogut llegir el que en mostra el cercador:

- Comprovat: cada fitxer ha de fer menys de 50 MB, i tots els fitxers d'una petició, 50 MB com a molt. Amb un model que veu imatges, de cada pàgina d'un PDF li arriben el text i la imatge. Un torn (20 MB de fitxers, uns 27 MB en base64) hi cap.
- Sense comprovar: un límit de pàgines per petició (fonts de 2025 que citaven la guia d'aleshores en donen 100 pàgines i 32 MB entre tots els fitxers) i els límits de les imatges (`input_image`).
- No s'hi afegeix cap comprovació pròpia mentre no es puguin llegir a la documentació (mai de memòria). Si l'API rebutja una petició, la crida de ChatGPT falla amb el missatge de l'API (`L'API d'OpenAI ha rebutjat la petició: …`) i el propietari pot tornar a enviar la pregunta amb menys pàgines.
- El mateix val per a Claude: els límits per petició (600 o 100 pàgines, i la finestra de context) poden deixar fora una combinació de PDF llargs encara que cadascun compleixi els límits de l'aplicació.

**Conseqüència de cost:** un PDF de 10 pàgines són desenes de milers de tokens d'entrada a cada crida. Si es reenvia a cada fase d'un debat (respostes, revisions i síntesi), el cost es multiplica.

## Decisió

Es fa en dues etapes: **P7a** (aquest canvi), els adjunts a tots els modes, i **P7b** (prevista), el contrast del PDF per a ChatGPT amb la subscripció.

### P7a. Adjunts a tots els modes

**Què s'accepta.** El tipus surt sempre del contingut, mai del nom ni del `Content-Type`:

- Imatges PNG, JPEG, GIF i WebP (signatura dels primers bytes). Les dimensions es llegeixen de les capçaleres (PNG IHDR, JPEG SOF, GIF, WebP VP8/VP8L/VP8X) en Python pur: el servidor no descodifica mai cap imatge.
- PDF (`%PDF-` al començament).
- Text: UTF-8 vàlid sense cap NUL, amb una extensió permesa (`txt md markdown csv tsv json yaml yml xml html htm log ini toml cfg py js ts jsx tsx svelte css scss sql sh bash rs go java kt c h cpp hpp cs rb php swift lua r pl`). Sempre es tracta com a text pla, mai no es mostra com a HTML.
- Tota la resta dona `415`: també l'SVG (pot portar codi), l'HEIC (convertir-lo a JPEG), els formats d'Office (convertir-los a PDF), l'àudio i els arxius comprimits.

**Límits** (constants de `src/agentic_os/attachments.py`, documentats a [PROTOCOL.md](../PROTOCOL.md)):

- Com a molt 5 adjunts per missatge i 20 MB entre tots, de manera que el límit més estricte de moltes imatges no s'aplica mai.
- Imatge: 7 MB i 8.000 píxels per costat. El navegador redueix abans de pujar-la tota imatge de més de 2.576 píxels al costat llarg: els models la veuen igual i la pujada és molt més petita. També torna a codificar, a la mateixa mida, una imatge de més de 7 MB, i dreta una foto que es mostra girada per la seva orientació EXIF (com les del mòbil): els models en reben els píxels tal com estan desats, sense les metadades, i la veurien girada.
- PDF: 20 MB i 100 pàgines, sense xifrar.
- Text: 200 kB.
- Massa gran: `413`. No vàlid (buit, massa pàgines o píxels, PDF xifrat, nom no vàlid): `422`. Tots els missatges, en català.

**Pujada.** `PUT /api/attachments?name=<nom>` amb el fitxer com a cos, sense multipart. Com les altres escriptures, necessita la sessió i un `Origin` permès, i té un temps màxim per rebre el cos. El cos s'escriu en un fitxer temporal privat a mesura que arriba i es talla al límit del seu tipus, que decideixen els primers bytes. És l'única ruta amb un cos de fins a 20 MB i 120 s (les altres continuen amb 1 MiB i 15 s), i només es llegeix amb sessió. Caddy li deixa passar el mateix, 20 MB, i la talla als 150 s, quan l'aplicació ja ha respost (`deploy/Caddyfile`; per a les altres peticions, 1 MiB i 30 s). El nom del fitxer va a la consulta de l'URL, i cap registre no la desa: el registre d'accés de l'aplicació (uvicorn) i els de Caddy, també el d'errors, només en guarden el camí.

**Emmagatzematge.**

- Fitxers adreçats pel contingut a `<data_dir>/attachments/<sha256[:2]>/<sha256>` (directoris 0700, fitxers 0600), al volum de dades: les còpies de seguretat els inclouen. Dues pujades del mateix fitxer en fan servir un de sol.
- Taules `attachments` i `message_attachments` (migració 4). La fila d'un PDF en desa el text extret, i la d'un fitxer de text, el contingut.
- Un adjunt que no s'ha enviat en cap torn s'esborra al cap de 24 h. Esborrar una conversa esborra els adjunts que només feia servir ella. Un fitxer s'esborra quan cap fila no el fa servir. Cada hora, una escombrada treu els fitxers que cap fila no fa servir (restes d'una caiguda).

**El text dels PDF.** El llegeix pypdf (dependència nova) en un procés a part: Python aïllat (`-I`), sense cap variable d'entorn del servidor, amb un màxim de 60 s i de 512 MiB d'espai d'adreces, un límit de CPU, i sense poder escriure fitxers, crear processos ni bolcar la memòria. Així un PDF hostil o enorme no pot encallar ni esgotar el servidor, i el procés del servidor no analitza mai cap PDF. Dos lectors alhora poden arribar a ocupar 1 GiB dels 1,5 GiB del contenidor de l'aplicació: per això cada lector és el primer procés que el nucli mata si el contenidor es queda sense memòria (`oom_score_adj` 1000), i no una CLI a mig torn ni el servidor; la pujada d'aquell PDF dona `422`. El text té un bloc per pàgina, introduït per la línia `--- Pàgina N ---`. És nul si no se n'ha pogut extreure res (un PDF escanejat) i es talla al milió de caràcters, amb un avís.

**Com se serveixen.** Només les imatges es mostren dins de l'aplicació (`Content-Disposition: inline`); els PDF i els fitxers de text es descarreguen (`attachment`). Tots porten el tipus detectat, `X-Content-Type-Options: nosniff` i una política `default-src 'none'; sandbox`. Res del que es puja no es serveix mai com a document de l'origen de l'aplicació, excepte les imatges.

**Miniatures.** El navegador les fa un sol cop, en adjuntar el fitxer (les imatges amb un `canvas`; els PDF, la primera pàgina amb PDF.js), i les puja: PNG o WebP de com a molt 100 kB i 512 píxels per costat. Així es veuen al compositor, als torns en directe i recarregats, i en altres dispositius.

**PDF.js al navegador** (`pdfjs-dist` 6.3.289, `web/src/lib/pdf.ts`), per a les miniatures i la vista prèvia, configurat perquè la CSP no canviï:

- Es carrega a part, només quan s'adjunta o es previsualitza un PDF.
- És la compilació «legacy»: l'altra necessita funcions de JavaScript que molts navegadors actuals encara no tenen (`Map.prototype.getOrInsertComputed`...), i aquesta les porta.
- El *worker* és un fitxer de l'aplicació, al mateix origen (`GlobalWorkerOptions.workerSrc`), mai un *worker* `blob:`.
- Sense WebAssembly (`useWasm: false`), que necessitaria `'wasm-unsafe-eval'`: els descodificadors de les pàgines escanejades (JBIG2, JPEG 2000) funcionen amb la seva versió en JavaScript.
- Els glifs es dibuixen com a camins (`disableFontFace: true`, `useSystemFonts: false`): cap `FontFace`, i les fonts d'un PDF no arriben mai al motor de fonts del navegador. Sense formularis XFA (`enableXfa: false`) i amb un màxim de píxels per imatge.
- Les fonts estàndard, els CMap i els descodificadors són fitxers de l'aplicació, a `/assets/pdfjs-<versió>/` (`vite.config.ts` en conserva el nom en una carpeta per versió, perquè el servidor desa `/assets/` a la memòria cau per sempre): cap URL externa.
- **Canvi respecte del que es va fixar:** el contracte de P7a demanava `isEvalSupported: false`, però PDF.js 6 ja no té aquesta opció perquè no avalua mai codi (ni `eval` ni `new Function`). Una prova del web (`web/src/lib/pdf.test.ts`) ho comprova a la compilació fixada.

**Lliurament als models** (el motor i els proveïdors):

- Les respostes i la síntesi reben tots els adjunts sencers. Les revisions reben les imatges i els fitxers de text sencers, i els PDF com diu `pdf_in_revisions`: `"text"` (per defecte), el text extret en lloc del document; `"full"`, el document. Un PDF del qual no s'ha pogut extreure cap text (un d'escanejat) hi va sempre sencer: com a text, les revisions no en rebrien res per contrastar les respostes.
- Els torns posteriors només en veuen una referència a l'historial: «[Adjunts: informe.pdf (PDF, 12 pàgines), foto.jpg (imatge)]». Si cal, el propietari torna a adjuntar el fitxer.
- CLI de Claude: blocs `image` i `document` al missatge `stream-json`, amb tots els *flags* obligatoris i `client_composed: true`. API de Claude: els mateixos blocs, amb `cache_control` a l'últim bloc d'adjunt. API d'OpenAI: `input_image` i `input_file`. Codex: les imatges com a `localImage`, amb el camí d'un enllaç simbòlic al fitxer desat que es diu `<sha256>.<extensió>` (a `<data_dir>/codex-images`), perquè Codex en dedueix el tipus pel nom i el fitxer desat no té extensió; els PDF com el text extret pel servidor, marcat «sense contrastar»; els fitxers de text com a text.
- Els adjunts van abans de la pregunta, cadascun amb una etiqueta (nom, tipus, pàgines), i el prompt de sistema diu que el seu contingut s'ha de tractar com a dades, mai com a instruccions.
- El text d'un fitxer, i el d'un PDF quan es passa com a text, es tracta com la resta de text que no és del propietari (les respostes dels models, l'historial): passa per `neutralize_tags`, de manera que no pot obrir ni tancar cap secció del prompt (`<user_message>`, `<current_message>`...), i va entre una línia d'obertura que acaba amb un codi, `[Fitxer: informe.txt · 1f0c…]` (a Codex, `[PDF «informe.pdf», 12 pàgines: text extret pel servidor, sense contrastar · 1f0c…]`), i la línia `[Fi del fitxer 1f0c…]`. La llista d'adjunts del prompt explica que tot el que hi ha entre les dues línies és el contingut del fitxer.
- El codi són 16 xifres hexadecimals del SHA-256 del `sha256` del contingut i del nom. Ni el fitxer (ni el text que se n'extreu) ni el nom no el poden contenir, perquè haurien de contenir el seu propi hash: un final de fitxer falsificat té un altre codi. El mateix fitxer amb el mateix nom sempre té el mateix codi, així que els prompts no canvien d'una fase a l'altra i la memòria cau de prompts continua encertant.
- La clau de la memòria cau de torns inclou el contingut (`sha256`) i el nom de cada adjunt, en ordre, i `pdf_in_revisions` quan canvia el que reben els models (un debat amb revisions i algun PDF amb text).
- El motor desa la pregunta i els enllaços amb els seus adjunts en una sola transacció, abans de `turn.started`. Si un adjunt ha desaparegut mentrestant (esborrat des d'una altra pestanya, o l'escombrada d'un adjunt no enviat de fa més de 24 h), el torn falla sense començar i no deixa res: tampoc la conversa nova que acabava de crear, que encara no coneixia ningú.

**Tokens estimats**, que la targeta de cada adjunt mostra abans d'enviar-lo (aproximats):

- Imatge: `ceil(w'/28) · ceil(h'/28)`, com a molt 4.784, amb `(w', h')` la imatge reduïda a 2.576 píxels al costat llarg.
- PDF: 3.600 per pàgina.
- Text: `ceil(caràcters / 4)`.

**Política de cost.**

- Les imatges van a totes les fases: costen poc i els models les necessiten per comprovar les afirmacions de l'altre.
- Els PDF van sencers a les respostes i a la síntesi. Les revisions en reben el text, llevat que el propietari triï «PDF a les revisions: sencer» al calaix de configuració (`pdf_in_revisions`) o que el PDF no en tingui.
- A l'API, la memòria cau de prompts (`cache_control` als blocs d'adjunt) fa que les repeticions dins d'un torn costin poc.
- Cada targeta mostra els tokens estimats abans d'enviar.

**Limitació de P7a.** Codex rep el text del PDF extret pel servidor, sense contrastar i marcat així: d'un PDF escanejat no en rep res. Ho resol P7b.

### P7b (prevista). El PDF per a ChatGPT amb la subscripció, contrastat per Claude

Només quan ChatGPT funciona amb Codex (mode `cli`): Claude i l'API d'OpenAI ja llegeixen els PDF.

1. **Extracció al servidor:** la de P7a (pypdf al procés limitat), que també divideix els PDF llargs en trams de pàgines.
2. **Comprovacions deterministes per pàgina** (gratuïtes, sempre): sense capa de text o gairebé (una pàgina escanejada o text en imatges); text brossa (U+FFFD, caràcters d'ús privat, `(cid:NN)`, caràcters de control, massa poques lletres); i indicis de text amagat, a partir del flux de contingut amb el visitant d'operadors de pypdf: mode de renderitzat 3 en una pàgina sense imatges (una capa OCR sobre un escaneig és legítima), una lletra molt petita, text fora de la `MediaBox` i farciment blanc quan es pot saber. Els indicis són avisos, no veredictes.
3. **Contrast de Claude** (la idea del propietari): una crida per PDF (per tram de fins a unes 20 pàgines en els llargs) amb el PDF com a document **i** el text extret de cada pàgina i els indicis del pas 2. Claude només respon les diferències, en JSON per pàgina: `{"page", "status": "ok" | "missing" | "garbled" | "partial" | "hidden", "text", "hidden", "visual"}`. L'anàlisi és estricta: una sortida mal formada, tallada o una negativa marca les pàgines afectades com a «no contrastat», mai com a parcials.
4. **Quan:** en adjuntar el PDF, si el mode del compositor necessita ChatGPT; si no, quan comença un torn que el necessita (ChatGPT espera, amb un temps màxim). El resultat es desa per `(sha256, versió del contrast, identitat de Claude)` i el reaprofiten totes les fases, els torns posteriors i les altres converses.
5. **El que veu ChatGPT, pàgina per pàgina:** `ok`, el text extret exacte; `missing`, la transcripció de Claude, marcada «[transcripció de Claude]»; `garbled`, la lectura de Claude, marcada; `partial`, el text extret més el complement de Claude, marcat; `hidden`, el text visible i l'avís «[Avís: la pàgina N conté text que no es veu; no s'ha passat]»; i les descripcions `visual`, marcades «[descripció de Claude]». Una capçalera diu que el text l'ha extret el servidor i l'ha contrastat Claude, i quines parts són de Claude.
6. Claude també rep els avisos de text amagat amb el seu PDF, de manera que tots dos models el tracten com a sospitós.
7. **Sense Claude** (mode de demostració, quota esgotada, error, negativa): ChatGPT rep el text extret, amb «[pàgina sense text extraïble]» a les pàgines sense text, i l'adjunt mostra «no contrastat».
8. **Interfície:** l'estat a la targeta (pujant → contrastant amb Claude → llest, o avisos), per exemple «Text contrastat: 11 de 12 pàgines coincideixen; la 4 és escanejada i l'ha transcrita Claude». La resposta de ChatGPT porta un distintiu quan ha llegit pàgines a través de Claude («Ha llegit les pàgines 4 i 9 a través de Claude»), i els prompts del debat ho diuen: un acord sobre aquestes parts no són dues lectures independents.
9. **Comptabilitat:** cada crida de contrast és una crida facturada amb el seu propi propòsit, a les estadístiques i al torn que la fa servir primer.
10. **Límits del contrast** (a l'ajuda de la interfície): és el judici d'un model. Troba bé el text que falta, el text brossa, el text amagat i les pàgines escanejades, però una diferència petita (una xifra en una taula llarga) se li pot escapar. On el PDF té capa de text, ChatGPT continua rebent el text exacte.

## Alternatives considerades

- **Pujada multipart/form-data:** caldria `python-multipart`, una dependència nova i més superfície d'anàlisi. Un cos cru amb el nom a la consulta n'hi ha prou per a un fitxer per petició.
- **El nom del fitxer en una capçalera (`X-AOS-File-Name`) en lloc de la consulta:** no arribaria als registres d'accés sense haver-los de filtrar, però canviaria el protocol. S'ha preferit que cap registre no desi la consulta de cap URL (uvicorn i Caddy), que també protegeix el text de les cerques (`?q=`).
- **Fiar-se del `Content-Type` o de l'extensió:** un HTML o un SVG etiquetat com a imatge es podria servir a l'origen de l'aplicació (XSS). El tipus surt del contingut i l'SVG es rebutja.
- **Desar els fitxers a SQLite:** faria créixer la base de dades i el WAL, i Codex necessita un camí per a `localImage`. Els fitxers adreçats pel contingut, al costat de la base de dades, són al mateix volum i a les mateixes còpies.
- **Descodificar les imatges al servidor (Pillow) per validar-les o reduir-les:** una dependència nativa amb un historial de vulnerabilitats als descodificadors. El navegador ja les redueix, i el servidor només necessita les dimensions, que llegeix de les capçaleres.
- **Llegir els PDF al procés del servidor:** un PDF hostil podria encallar o esgotar la memòria del procés que ho serveix tot. Per això es llegeixen en un procés a part amb límits.
- **Una altra biblioteca de PDF:** PyMuPDF és AGPL i nativa; pdfminer.six és més lenta i porta més dependències. pypdf (6.19.0) és Python pur, BSD-3, sense dependències obligatòries. Al navegador, `pdfjs-dist` (Mozilla, Apache-2.0) fa les miniatures i la vista prèvia.
- **Enviar els PDF sencers a totes les fases:** és el més simple i el més fidel, però multiplica el cost. La configuració deixa triar al propietari.
- **Renderitzar les pàgines del PDF com a imatges per a Codex (PDFium o poppler):** més fidel i independent de la capa de text, però amb dependències natives i més tokens. Es guarda per més endavant, com a alternativa a P7b.
- **Que Codex llegeixi el PDF amb les seves eines:** l'aplicació li desactiva les eines i el fa córrer en mode només lectura, i l'app-server 0.157.1 no té cap entrada de document.
- **Fer les miniatures al servidor:** caldria renderitzar imatges i PDF al servidor (dependències natives). El navegador les fa un sol cop i les puja.

## Conseqüències

- Dues dependències noves: `pypdf` (Python) i `pdfjs-dist` (web).
- Canvia el protocol ([PROTOCOL.md](../PROTOCOL.md) i `web/src/lib/protocol.ts`): les rutes `/api/attachments`, el camp `attachments` de `turn.start`, `meta.attachments` de la pregunta, `pdf_in_revisions` als `RuntimeSettings` i els estats `415` i `507`.
- Canvien els contractes interns: `Attachment` i `GenerationRequest.attachments` (`providers/base.py`); `get_attachments`, `link_attachments`, `discard_conversation` i `NewMessage.attachments` (la pregunta i els seus enllaços en una transacció) del `Store`; `TurnRequest.attachments` i `pdf_in_revisions`; i la versió de la clau de la memòria cau de torns (6).
- L'esquema de la base de dades passa a la versió 4. El volum de dades, i les seves còpies, creixen amb els adjunts enviats.
- Codex rep els PDF com a text sense contrastar fins a P7b.
- Seguretat: cap fitxer pujat no es serveix com a HTML; `nosniff` i una política `sandbox` a tots; els PDF només els llegeix un procés limitat; el cos gran només l'admet una ruta amb sessió; el text dels fitxers no pot passar per part del prompt; i cap registre no desa els noms dels fitxers.
- Caddy (`deploy/Caddyfile`) té un límit propi per a la pujada: cal reiniciar-lo en actualitzar (`docker compose restart caddy`, ja als passos d'«Actualitzar» de [DESPLEGAMENT.md](../DESPLEGAMENT.md)).
- Aquesta decisió és una proposta fins que el propietari l'accepti.
