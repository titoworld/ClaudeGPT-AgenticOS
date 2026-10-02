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

Es fa en dues etapes: **P7a**, els adjunts a tots els modes, i **P7b**, el contrast del PDF per a ChatGPT amb la subscripció.

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
- El text d'un fitxer, i el d'un PDF quan es passa com a text, es tracta com la resta de text que no és del propietari (les respostes dels models, l'historial): passa per `neutralize_tags`, de manera que no pot obrir ni tancar cap secció del prompt (`<user_message>`, `<current_message>`...), i va entre una línia d'obertura que acaba amb un codi, `[Fitxer: informe.txt · 1f0c…]`, i la línia `[Fi del fitxer 1f0c…]`, amb el mateix codi. La llista d'adjunts del prompt explica que tot el que hi ha entre les dues línies és el contingut del fitxer. A Codex, un PDF obre amb `[PDF «informe.pdf», 12 pàgines: text extret pel servidor, sense contrastar · 9b2d…]`, amb un codi propi des de P7b (punt 4 de P7b).
- El codi són 16 xifres hexadecimals del SHA-256 del `sha256` del contingut i del nom. Ni el fitxer (ni el text que se n'extreu) ni el nom no el poden contenir, perquè haurien de contenir el seu propi hash: un final de fitxer falsificat té un altre codi. El mateix fitxer amb el mateix nom sempre té el mateix codi, així que els prompts no canvien d'una fase a l'altra i la memòria cau de prompts continua encertant.
- La clau de la memòria cau de torns inclou el contingut (`sha256`) i el nom de cada adjunt, en ordre, i `pdf_in_revisions` quan canvia el que reben els models (un debat amb revisions i algun PDF amb text). P7b hi afegeix la versió del contrast i el que el lector ha fet de cada PDF (vegeu-ne les conseqüències).
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

**Limitació de P7a.** Codex rep el text del PDF extret pel servidor, sense contrastar i marcat així: d'un PDF escanejat no en rep res. Ho resol P7b, a continuació.

### P7b. El PDF per a ChatGPT amb la subscripció, contrastat per Claude

Només quan ChatGPT funciona amb Codex (mode `cli`): no pot obrir cap PDF i en llegeix el text que el servidor n'ha extret. Claude (CLI o API) i l'API d'OpenAI llegeixen els PDF ells mateixos, i no canvien. Aquest text pot enganyar ChatGPT de tres maneres: una pàgina escanejada no en té, una font sense mapa de caràcters el fa il·legible, i un PDF pot portar text que no es veu a la pàgina (en un mode de renderitzat invisible, minúscul o fora de la pàgina), que ChatGPT llegiria com si hi fos, una via per colar-li instruccions.

**1. Anàlisi de les pàgines, en pujar el PDF** (gratuïta, a tots els modes). El lector de P7a, en el mateix procés i en la mateixa passada que el text, analitza cada pàgina (`src/agentic_os/attachments.py`, amb els visitants d'operadors de l'extracció de text de pypdf):

- On és el text de la pàgina dins del text desat. També s'analitzen les pàgines que el límit del text deixa fora: les dades surten del seu propi text, que després es descarta.
- Les lletres i els caràcters trencats del seu text: U+FFFD, caràcters d'ús privat, substituts solitaris i caràcters de control (el que dona una font sense mapa de caràcters).
- Si dibuixa alguna imatge: a les seves `Resources` o a les dels seus formularis (fins a 5 nivells), o una imatge en línia. Una imatge de les `Resources` compta encara que la pàgina no la dibuixi.
- El text que mostra sense que es vegi: en un mode de renderitzat que no pinta res (3 o 7); més petit d'1 punt en alguna direcció, comptant les escales amb què es dibuixa (la mida de la lletra, l'escala horitzontal `Tz`, la matriu de text, la de transformació i la del formulari que el dibuixa: un text aixafat en una sola direcció tampoc no es llegeix); o amb l'origen, comptant-hi el desplaçament vertical `Ts`, a més d'1 punt fora de la part visible de la pàgina (la `CropBox` dins de la `MediaBox`). pypdf no segueix el mode de renderitzat, la mida, l'escala horitzontal ni el desplaçament vertical, així que l'anàlisi els desa amb `q` i els restaura amb `Q`. Un formulari es dibuixa com entre `q` i `Q`: hi comença amb l'estat de qui el dibuixa, el seu no en surt, i no pot restaurar més estats dels que ha desat (una manera de fer passar per visible el text invisible de la pàgina).

D'aquí surten els avisos de cada PDF (`pdf_notes` a l'`Attachment`): les pàgines sense text (menys de 25 lletres), les il·legibles i les que poden amagar text. El text invisible només és sospitós en una pàgina sense imatges: sobre un escaneig és el text reconegut, i és legítim. Són avisos, no veredictes: una pàgina pot amagar text d'altres maneres que l'anàlisi no veu (blanc sobre blanc, sota una imatge, o tallat per un camí de retall que no en deixa veure res), i el text invisible d'una pàgina que té una imatge a les `Resources` sense dibuixar-la no és sospitós. Per això el contrast de Claude mira la pàgina tal com es veu. Si l'anàlisi no encaixa amb el text, el PDF es desa igualment, sense analitzar, i es llegeix com a P7a. Cap operand estrany no fa perdre el text d'una pàgina: l'anàlisi se'l salta.

**2. Quan es contrasta: quan un torn el necessita, no en pujar el PDF.**

- Un adjunt que el propietari treu del compositor, o que només envia a Claude, no costa res.
- La resposta de ChatGPT espera el contrast mentre Claude respon: el comença la primera crida de ChatGPT del torn que el necessita, i totes les crides de ChatGPT del torn (respostes, revisions i síntesi) esperen la mateixa tasca.
- Només en un torn on participa ChatGPT (solo amb ChatGPT, duel o debat) i hi ha un Claude que el pot fer. Mai en una resposta servida des de la memòria cau de torns.
- El Claude de demostració (`AOS_CLAUDE_MODE=fake`) no en fa cap: respon que totes les pàgines són correctes sense llegir-ne cap, així que amb un ChatGPT real donaria per buida una pàgina escanejada i per visible el text amagat. Amb ell, el PDF es llegeix sense contrastar, com sense Claude, i no es desa ni es reaprofita cap contrast. La versió 2 del contrast no reaprofita cap dels que desava la 1, que també desava el del Claude de demostració.
- El contrast es desa pel contingut del fitxer (`sha256`) i la versió del contrast (no pel model: el d'un altre model de Claude també serveix), i el reaprofiten els torns posteriors i les altres converses. Dos torns alhora poden contrastar el mateix PDF dues vegades: s'accepta, perquè és rar i només costa les crides de més.

**3. Com.** Claude rep el PDF com a document **i** el text extret de cada pàgina, entre dues línies amb un codi diferent del que veu ChatGPT, i els avisos de l'anàlisi. Només escriu les pàgines que difereixen, una línia JSON per pàgina: `missing` (sense text: la transcriu sencera), `garbled` (il·legible: també), `partial` (només la part que falta), `hidden` (el text visible i una cita curta del que no es veu) o `ok` amb una descripció `visual` del que mostren les figures; al final, `{"end": true}`. L'anàlisi és estricta: una línia mal formada, fora d'ordre o tallada pel pressupost acaba la lectura, i només compten les pàgines d'abans.

- El model és el de Claude del torn: qualitat abans que cost, com va triar el propietari. Sense raonament, i amb un pressupost de sortida segons les pàgines (més per a les que Claude haurà de transcriure senceres: les que no tenen text, les il·legibles i les que poden amagar text, de les quals escriu tot el text que es veu).
- Com a molt 3 crides per PDF, cadascuna des de la pàgina on s'ha quedat l'anterior: unes 60 pàgines escanejades. La resta queden sense contrastar, i ho diuen.
- Com a molt 2 PDF alhora, i 5 minuts per a tot el contrast del torn: després, ChatGPT llegeix el text sense contrastar.
- Es desa quan és complet o quan s'han acabat les crides. Mai després d'un error, d'una negativa, d'una crida que no avança, del temps esgotat o d'una cancel·lació: el torn següent ho torna a provar. Per això un torn on ChatGPT ha llegit un PDF així no entra a la memòria cau de torns: la mateixa pregunta en repetiria la lectura sense contrastar en lloc de tornar-ho a provar (punt 6).

**4. El que llegeix ChatGPT, pàgina per pàgina.** El text extret exacte on és correcte; la lectura de Claude, marcada, on hi falta o no es pot llegir, i el seu complement on n'hi falta una part; les descripcions de les figures, marcades. **El text que Claude troba que no es veu no li arriba:** de la pàgina, rep el text visible i l'avís que en té. **Una pàgina que ningú no ha contrastat li arriba tal com s'ha extret, amb el text que no es veu que pugui tenir:** sense Claude o amb el de demostració, després d'un error, d'una negativa o del temps esgotat, les pàgines que queden fora de les crides (que no es tornen a contrastar, perquè el contrast parcial es desa) i els PDF sense analitzar. Diu que no s'ha contrastat i, si l'anàlisi la troba sospitosa, que pot tenir text que no es veu, un avís que també reben tots els models (punt 5). L'anàlisi és una heurística (punt 1): una pàgina que no la desperta arriba a ChatGPT sense cap avís. La vista té un codi propi, diferent del del fitxer (que Claude veu a les revisions, quan rep el PDF com a text) i del del contrast: només el veu ChatGPT, així que ni el PDF ni Claude no en poden falsificar cap línia de pàgina ni cap nota.

**5. Avisos a tots els models.** L'etiqueta d'un PDF amb pàgines sospitoses els diu que les tractin amb recel, i les revisions i la síntesi saben quines pàgines ha llegit ChatGPT a través de Claude, pel text que hi ha llegit Claude o per la seva descripció de les figures (encara que el text extret fos correcte): on hi coincideixen, és una sola lectura, no dues.

**6. Quan no es pot contrastar** (sense cap proveïdor de Claude, amb el de demostració, un error, una negativa, el temps esgotat o un PDF sense analitzar): ChatGPT rep el text extret, amb les pàgines sense contrastar marcades, i el torn diu per què. Un torn així no entra mai a la memòria cau de torns quan un altre torn llegiria el PDF d'una altra manera: quan el torn següent el tornaria a contrastar (un error, una negativa, una crida que no avança, el temps esgotat) o quan no hi havia cap Claude que el pogués contrastar (cap, o el de demostració), perquè un de configurat després ho faria. Un PDF sense analitzar es llegeix sempre igual, i el torn sí que hi entra: la clau distingeix una altra pujada del mateix fitxer que sí que s'ha analitzat.

**7. Interfície i protocol** ([PROTOCOL.md](../PROTOCOL.md)): la targeta de l'adjunt avisa de les pàgines sense text, il·legibles o amb possible text amagat; el torn mostra l'estat del contrast (`pdf.check`: contrastant, contrastat, amb el seu cost o «ja contrastat abans», o sense contrastar i per què); i les respostes de ChatGPT porten un distintiu amb les pàgines que ha llegit a través de Claude, les amagades i les que no s'han contrastat (`meta.pdf_reading`).

**8. Comptabilitat.** Cada crida de contrast és una crida facturada, amb el propòsit `check`: surt a les estadístiques i compta al total del torn que la fa.

**9. Emmagatzematge** (migració 5): la columna `attachments.pdf_pages`, amb les dades de cada pàgina, i la taula `pdf_checks`, amb el contrast per contingut i versió. Un contrast s'esborra quan cap adjunt no fa servir el fitxer: en esborrar l'últim adjunt que el té (sense enviar o amb la seva conversa) o, si en queda algun d'orfe, amb l'escombrada de cada hora.

**10. Límits del contrast.** És el judici d'un model. Troba bé el text que falta, el text il·legible, el text amagat i les pàgines escanejades, però una diferència petita (una xifra en una taula llarga) se li pot escapar. On la capa de text és correcta, ChatGPT continua rebent el text exacte.

## Alternatives considerades

- **Pujada multipart/form-data:** caldria `python-multipart`, una dependència nova i més superfície d'anàlisi. Un cos cru amb el nom a la consulta n'hi ha prou per a un fitxer per petició.
- **El nom del fitxer en una capçalera (`X-AOS-File-Name`) en lloc de la consulta:** no arribaria als registres d'accés sense haver-los de filtrar, però canviaria el protocol. S'ha preferit que cap registre no desi la consulta de cap URL (uvicorn i Caddy), que també protegeix el text de les cerques (`?q=`).
- **Fiar-se del `Content-Type` o de l'extensió:** un HTML o un SVG etiquetat com a imatge es podria servir a l'origen de l'aplicació (XSS). El tipus surt del contingut i l'SVG es rebutja.
- **Desar els fitxers a SQLite:** faria créixer la base de dades i el WAL, i Codex necessita un camí per a `localImage`. Els fitxers adreçats pel contingut, al costat de la base de dades, són al mateix volum i a les mateixes còpies.
- **Descodificar les imatges al servidor (Pillow) per validar-les o reduir-les:** una dependència nativa amb un historial de vulnerabilitats als descodificadors. El navegador ja les redueix, i el servidor només necessita les dimensions, que llegeix de les capçaleres.
- **Llegir els PDF al procés del servidor:** un PDF hostil podria encallar o esgotar la memòria del procés que ho serveix tot. Per això es llegeixen en un procés a part amb límits.
- **Una altra biblioteca de PDF:** PyMuPDF és AGPL i nativa; pdfminer.six és més lenta i porta més dependències. pypdf (6.19.0) és Python pur, BSD-3, sense dependències obligatòries. Al navegador, `pdfjs-dist` (Mozilla, Apache-2.0) fa les miniatures i la vista prèvia.
- **Enviar els PDF sencers a totes les fases:** és el més simple i el més fidel, però multiplica el cost. La configuració deixa triar al propietari.
- **Renderitzar les pàgines del PDF com a imatges per a Codex (PDFium o poppler):** més fidel i independent de la capa de text, però amb dependències natives i més tokens a cada crida de ChatGPT. Es guarda per més endavant, com a alternativa a P7b.
- **Contrastar el PDF en pujar-lo:** el contrast estaria llest abans del torn, però cada PDF pujat costaria una crida de Claude, també els que el propietari treu del compositor o només envia a Claude.
- **Contrastar amb un model barat de Claude (el ràpid):** costaria menys, però transcriure pàgines escanejades i trobar text amagat és on més compta la qualitat. El propietari va triar el model del torn.
- **Un interruptor per desactivar el contrast:** de moment no; s'hi pot afegir més endavant si el propietari el vol.
- **Detectar el text amagat només per la capa de text (sense Claude):** l'anàlisi en troba els indicis, però no veu el text blanc sobre blanc ni el que queda sota una imatge, i no pot llegir una pàgina escanejada. Per això és un avís i el contrast el fa Claude, que veu la pàgina.
- **Que Codex llegeixi el PDF amb les seves eines:** l'aplicació li desactiva les eines i el fa córrer en mode només lectura, i l'app-server 0.157.1 no té cap entrada de document.
- **Fer les miniatures al servidor:** caldria renderitzar imatges i PDF al servidor (dependències natives). El navegador les fa un sol cop i les puja.

## Conseqüències

- Dues dependències noves: `pypdf` (Python) i `pdfjs-dist` (web).
- Canvia el protocol ([PROTOCOL.md](../PROTOCOL.md) i `web/src/lib/protocol.ts`): les rutes `/api/attachments`, el camp `attachments` de `turn.start`, `meta.attachments` de la pregunta, `pdf_in_revisions` als `RuntimeSettings` i els estats `415` i `507`.
- Canvien els contractes interns: `Attachment` i `GenerationRequest.attachments` (`providers/base.py`); `get_attachments`, `link_attachments`, `discard_conversation` i `NewMessage.attachments` (la pregunta i els seus enllaços en una transacció) del `Store`; `TurnRequest.attachments` i `pdf_in_revisions`; i la versió de la clau de la memòria cau de torns (6).
- L'esquema de la base de dades passa a la versió 4. El volum de dades, i les seves còpies, creixen amb els adjunts enviats.
- Amb Codex, cada PDF d'un torn costa crides de Claude la primera vegada que es fa servir (el contrast), i la resposta de ChatGPT l'espera; els torns següents el reaprofiten.
- L'esquema de la base de dades passa a la versió 5 (P7b): `attachments.pdf_pages` i la taula `pdf_checks`. Els PDF pujats abans no tenen anàlisi i es llegeixen com a P7a.
- Canvien el protocol (`Attachment.pdf_notes`, l'esdeveniment `pdf.check`, `meta.pdf_reading` i el `pdf_reading` opcional de `stream.completed`) i els contractes interns: `Attachment.pdf_pages` i `pdf_check`, el propòsit `check`, i `get_pdf_check` i `put_pdf_check` del `Store`.
- La clau de la memòria cau de torns passa a la versió 7 (P7b): inclou la versió del contrast quan el torn porta algun PDF i, de cada PDF, si en té text i els avisos de les seves pàgines (o que no s'ha analitzat), perquè dues pujades del mateix fitxer poden diferir-hi (una d'abans de l'anàlisi, o una lectura que ha esgotat el temps). Un torn on ChatGPT ha llegit un PDF que un altre torn llegiria d'una altra manera no hi entra mai (punt 6).
- Seguretat: cap fitxer pujat no es serveix com a HTML; `nosniff` i una política `sandbox` a tots; els PDF només els llegeix un procés limitat; el cos gran només l'admet una ruta amb sessió; el text dels fitxers no pot passar per part del prompt; el text d'un PDF que Claude troba que no es veu no arriba a ChatGPT amb la subscripció (el de les pàgines que ningú no ha contrastat, sí), i tots els models reben l'avís de les pàgines on l'anàlisi en sospita, que és una heurística; i cap registre no desa els noms dels fitxers.
- Caddy (`deploy/Caddyfile`) té un límit propi per a la pujada: cal reiniciar-lo en actualitzar (`docker compose restart caddy`, ja als passos d'«Actualitzar» de [DEPLOYMENT.md](../DEPLOYMENT.md)).
- Aquesta decisió és una proposta fins que el propietari l'accepti.
