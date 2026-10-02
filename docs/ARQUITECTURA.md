# Arquitectura

> Consell d'IA personal: Claude i ChatGPT responen, es critiquen i sintetitzen una resposta millor, gastant els mínims tokens. Un sol usuari, autoallotjat en un VPS.

## Visió general

```
Navegador (Svelte 5 + three.js)
   │  HTTPS / HTTP/3 · una connexió WebSocket persistent
   ▼
Caddy (TLS automàtic, capçaleres de seguretat, compressió)
   │  xarxa interna de Docker
   ▼
Aplicació Python (FastAPI + uvicorn/uvloop)
   ├── server/       API REST, WebSocket, sessió, capçaleres de seguretat (CSP), fitxers estàtics del frontend
   ├── security/     contrasenya argon2id, TOTP, sessions, dispositius coneguts, límits d'intents
   ├── orchestrator/ motor de torns: solo · duel · debat · perfecciona, compactació,
   │                 memòria cau, comptabilitat
   ├── providers/    Claude i ChatGPT, cadascun en mode cli · api · fake
   ├── storage/      SQLite (WAL): converses, missatges, adjunts, ús, estalvis, memòria cau,
   │                 sessions; els fitxers adjunts, al costat, adreçats pel contingut
   ├── attachments   adjunts: límits, tipus pel contingut, text i pàgines dels PDF (en un procés a part)
   └── pricing · fx  preus per model (USD/MTok) i canvi USD→EUR del BCE
        │
        ├── CLI oficial de Claude Code  (subscripció Pro/Max, OAuth)   ─┐ mode "cli"
        ├── CLI oficial de Codex        (subscripció ChatGPT, OAuth)   ─┘
        └── SDK d'Anthropic / OpenAI    (claus d'API)                     mode "api"
```

## Mòduls i contractes

| Mòdul | Contracte | Responsabilitat |
| --- | --- | --- |
| `domain.py` | tipus compartits | `AgentName`, `TurnMode`, `Usage`, opcions de debat i de «Perfecciona» |
| `providers/base.py` | `Provider`, `Attachment` | Converteix una `GenerationRequest` (amb els seus adjunts) en un flux de `TextDelta` + un `GenerationResult` |
| `orchestrator/store.py` | `Store` | Persistència que necessita el motor (implementada per `storage`), també el resultat de cada torn i els adjunts de la pregunta |
| `orchestrator/events.py` | esdeveniments, `TurnOutcome` | Missatges servidor → client d'un torn ([PROTOCOL.md](PROTOCOL.md)) i com va acabar |
| `orchestrator/types.py` | `TurnRequest`, `EngineConfig` | Entrada del motor |
| `config.py` | `Settings` | Configuració del procés (variables `AOS_*`) |
| `pricing.py` | `ModelPrice`, `estimate_cost_usd` | Preus per defecte i propis; cost de cada crida |
| `fx.py` | `FxRate` | Tipus de canvi diari del BCE amb valor manual de reserva |
| `attachments.py` | límits, `PdfReader` | Límits dels adjunts, tipus d'un fitxer pel contingut, dimensions d'una imatge, nom net, tokens estimats i lectura dels PDF en un procés a part: el text i l'anàlisi de cada pàgina |
| `pdf_facts.py` | `PdfPage`, `PdfCheck` | Què ha trobat el lector a cada pàgina d'un PDF i els avisos que en surten; el contrast de Claude, llegit estrictament |
| `orchestrator/pdf_check.py` | `check_pdf` | Contrast de Claude del text d'un PDF que llegeix ChatGPT amb la subscripció |
| `orchestrator/refine.py` | `parse_review`, `parse_edit` | El mode «Perfecciona»: llegeix estrictament les revisions i les versions de l'editor, també mentre arriben |

## Modes de torn

- **Solo:** respon un sol agent. El més barat.
- **Duel:** tots dos responen en paral·lel i es mostren costat a costat.
- **Debat:**
  1. *Respostes inicials* en paral·lel.
  2. *Rondes de revisió* (per defecte fins a 2): cada agent rep la pregunta, la seva resposta i la de l'altre, i retorna una crítica breu, la seva resposta millorada (o `UNCHANGED`, amb una nota curta opcional, si no cal canviar-la) i un grau d'acord 0–100.
  3. *Parada per consens:* si tots dos superen el llindar (per defecte 85), no es fan més rondes.
  4. *Síntesi:* l'agent sintetitzador combina les dues respostes finals i els punts de desacord en la resposta definitiva.
- **Perfecciona** (`refine`, [ADR 0010](adr/0010-mode-perfecciona.md)): les dues IA milloren **un sol document** (un pla, un text, un disseny, codi) ronda rere ronda, fins que el propietari l'atura. Només comença quan el propietari el tria: no pot ser el mode per defecte.
  1. *Respostes inicials* (ronda 0) en paral·lel, com en un debat.
  2. *Fusió* (ronda 1): l'editor (Claude per defecte) fusiona les dues respostes en la versió 1.
  3. *Rondes de millora* (de la 2 endavant): totes dues revisen la versió vigent. Cada revisió proposa com a molt 5 canvis (corregir un defecte, guanyar claredat, simplificar o complir un requisit de l'encàrrec) i puntua de 0 a 100 com respon la versió a l'encàrrec; o bé diu `UNCHANGED`, si no hi canviaria res. Una revisió que no segueix aquest format (no hi proposa cap canvi amb el seu tipus ni hi diu `UNCHANGED`) falla, i aquell agent no compta en la ronda. Després l'editor escriu la versió següent, amb com a molt 5 canvis i una línia de registre per canvi. Si cap revisió no hi proposa res, no hi ha edició.
  4. *Sense sobredimensionar-lo:* cada prompt torna a citar l'encàrrec, i un canvi ha de dir quin defecte corregeix o quin requisit compleix: els afegits que l'encàrrec no demana es rebutgen, i cada revisió ha de buscar també què es pot treure o simplificar. Els prompts porten la llargada de la versió, el límit de paraules i les 30 últimes línies del registre de canvis, perquè desfer un canvi anterior s'ha de justificar. El límit és el del propietari o, si no en fixa cap, 1,2 vegades les paraules de la versió 1 (300 com a mínim). El motor el comprova sense cap model: una versió que el passa té un intent per escurçar-se i, si encara el passa, la ronda es descarta i es manté la versió vigent. La versió 1, que no en té cap d'anterior, també té un intent per escurçar-se quan la fusió passa del límit del propietari, però si encara el passa (o si és la còpia d'una resposta) es queda igualment, i són les edicions següents les que l'han de fer cabre. Tampoc no s'accepta una resposta sense la versió completa ni una versió igual a la vigent. Totes es desen igualment, perquè el propietari les vegi. L'editor escriu cada versió sencera en una sola resposta, que té com a molt 16.000 tokens de sortida, el raonament inclòs: una versió que no hi cap es talla i no s'accepta. Per això el document no pot passar d'unes 10.000 paraules de prosa en anglès (en català o en codi, menys), encara que el límit de paraules n'admeti més.
  5. *Aturada:* el torn s'acaba amb la versió vigent, que és la resposta final i la que veuen els torns següents:
     - quan el propietari l'atura, en acabar la ronda (`turn.stop`) o de seguida (`turn.cancel`: les crides en curs es cancel·len, però la versió vigent es desa igualment com a resposta final, sense cap crida);
     - quan cap de les dues no hi troba res a canviar 2 rondes seguides (sempre);
     - quan totes dues li donen el llindar (90 per defecte) o més, sense proposar cap defecte, 2 rondes seguides (es pot desactivar, per fer-lo «infinit»);
     - al màxim de rondes (12 per defecte, de 2 a 50), comptant-hi la fusió, o quan ha gastat el pressupost (3 € per defecte, de 0,1 a 100 €; en mode subscripció compta el valor a preus d'API, per no esgotar la quota). El motor compta en dòlars, així que el servidor li passa el pressupost convertit amb el tipus amb què l'aplicació mostra els euros. Les crides dels models sense preu no hi compten;
     - quan les dues fallen en una ronda. Si només en falla una, l'altra continua sola.

  Un torn «Perfecciona» no fa servir mai la memòria cau de torns.

## Estalvi de tokens

Tots els recomptes de tokens fan servir els **tokens processats** d'una crida: entrada, lectures i escriptures de memòria cau i sortida (el raonament ja és part de la sortida). Així, els tokens estalviats i el seu valor en diners quadren, i la ràtio del tauler té la mateixa definició al numerador i al denominador ([ADR 0008](adr/0008-recompte-de-tokens.md)). Les files d'estalvi desades abans d'aquest canvi conserven la definició antiga: entrada sense memòria cau més sortida, a la memòria cau de torns i a la parada per consens.

| Tècnica | Com funciona | Com es mesura |
| --- | --- | --- |
| Prompt de sistema propi | Les CLI s'executen amb un prompt de sistema curt en lloc del d'agent de programació: la de Claude sense cap eina i la de Codex sense les que es poden desactivar ([Seguretat](#seguretat-un-sol-usuari)) | – |
| Context mínim als debats | Les revisions només veuen la pregunta i les dues últimes respostes, no tota la transcripció | – |
| Historial canònic | A l'historial de la conversa només hi van la pregunta i la resposta final (la síntesi), no les rondes intermèdies | – |
| Compactació | Quan l'historial supera el llindar, els missatges antics es resumeixen amb el model ràpid i es conserven els últims | (tokens de l'historial original − tokens del context compactat) × crides facturades del torn que porten el context (les respostes i la síntesi) |
| Parada per consens | S'ometen les rondes que queden | tokens processats mitjans d'una ronda × rondes omeses |
| `UNCHANGED` | Un agent d'acord no reescriu la resposta (com a molt hi afegeix una nota curta) | longitud de la resposta no reescrita |
| Memòria cau de respostes | Una pregunta idèntica (mateix mode, agents, models i context) es respon sense cridar cap model. Si no se sap quin model respondrà (l'estat del proveïdor tarda, falla o diu que no està disponible), el torn no llegeix ni desa la memòria cau. Un torn «Perfecciona» no la fa servir mai | tokens processats del torn original, amb els intents declinats abans d'un fallback; el valor, cada crida a les tarifes actuals del seu model |
| Memòria cau del proveïdor | Prefixos estables (prompt de sistema primer) perquè Anthropic i OpenAI reaprofitin el càlcul | `cache_read_tokens` |

## Proveïdors

Cada agent té tres modes, escollits amb `AOS_CLAUDE_MODE` i `AOS_CHATGPT_MODE`:

- **`cli`** (per defecte): executa la CLI oficial (`claude`, `codex`) amb el prompt per l'entrada estàndard, en un directori buit i amb un entorn mínim: Claude sense cap eina i Codex en mode només lectura, sense les eines que es poden desactivar ([Seguretat](#seguretat-un-sol-usuari)). Autenticada amb la teva subscripció (OAuth) un sol cop al VPS.
- **`api`**: SDK oficial amb clau d'API (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) i *prompt caching*.
- **`fake`**: respostes deterministes per a proves i per provar la interfície sense gastar res.

## Adjunts

El propietari pot adjuntar a una pregunta imatges (PNG, JPEG, GIF, WebP), PDF i fitxers de text, com a molt 5 i 20 MB entre tots ([ADR 0009](adr/0009-adjunts.md), límits i rutes a [PROTOCOL.md](PROTOCOL.md#adjunts)).

- **Pujada:** cada fitxer es puja sol (`PUT /api/attachments`), abans d'enviar la pregunta, i `turn.start` en porta els `id`. El tipus surt del contingut, mai del nom: les imatges i els PDF per la seva signatura, el text per ser UTF-8 amb una extensió permesa. Tota la resta, també l'SVG, es rebutja. El servidor llegeix les dimensions d'una imatge de les capçaleres, sense descodificar-la, i el navegador ja redueix les imatges grans abans de pujar-les.
- **PDF:** el llegeix pypdf en un procés a part (Python aïllat, sense l'entorn del servidor, 60 s, 512 MiB d'espai d'adreces, sense escriure fitxers ni crear processos), que en compta les pàgines i n'extreu el text, amb un bloc per pàgina. En la mateixa passada n'analitza cada pàgina amb el visitant d'operadors de pypdf: on és el seu text, les lletres i els caràcters trencats, si dibuixa imatges i el text que mostra sense que es vegi (invisible, més petit d'1 punt o fora de la part visible). D'aquí surten els avisos de la targeta (`pdf_notes`: pàgines sense text, il·legibles o amb possible text amagat). El procés del servidor no analitza mai cap PDF, i si el contenidor es queda sense memòria, el nucli mata primer el lector (la pujada falla), no una CLI a mig torn ni el servidor.
- **Emmagatzematge:** cada fitxer es desa un sol cop, adreçat pel seu `sha256`, a `<data_dir>/attachments` (directoris 0700, fitxers 0600), al costat de la base de dades i dins de les mateixes còpies de seguretat. La base de dades en desa la descripció i el text (el contingut d'un fitxer de text, el text extret d'un PDF i l'anàlisi de les seves pàgines), quines preguntes els porten i el contrast de Claude de cada PDF, pel seu contingut. Un adjunt que no s'envia s'esborra al cap de 24 h; esborrar una conversa esborra els que només feia servir ella; cada hora, una escombrada esborra els fitxers que cap fila no fa servir. El contrast d'un PDF s'esborra quan cap adjunt no en fa servir el fitxer.
- **Lliurament:** el motor els carrega en començar el torn (un que no existeix fa fallar el torn) i desa la pregunta lligada a ells, en una sola transacció, amb la descripció a `meta.attachments`. Les respostes i la síntesi reben tots els adjunts sencers; les revisions, les imatges i el text sencers i els PDF com diu `pdf_in_revisions` (per defecte, el text extret, que costa molts menys tokens; un PDF sense text, com un d'escanejat, hi va sencer). Els torns posteriors només en veuen una referència. Cada proveïdor els envia amb els seus blocs (imatges i documents a Claude; `input_image` i `input_file` a l'API d'OpenAI); Codex rep les imatges pel camí (el d'un enllaç amb l'extensió del tipus, que és d'on Codex el dedueix) i els PDF com a text extret, pàgina per pàgina i contrastat per Claude (vegeu el punt següent). Els models han de tractar el contingut dels adjunts com a dades, mai com a instruccions: el text d'un fitxer passa per `neutralize_tags` i va entre dues línies amb un codi que el fitxer no pot contenir (`[Fitxer: nom · codi]` i `[Fi del fitxer codi]`), així que no es pot fer passar per part del prompt.
- **PDF per a ChatGPT amb la subscripció:** Codex no pot obrir cap PDF i en llegeix el text extret. Perquè aquest text no l'enganyi (una pàgina escanejada no en té, una font sense mapa de caràcters el fa il·legible, i un PDF pot portar text que no es veu), Claude el contrasta amb el document ([ADR 0009](adr/0009-adjunts.md)):
  1. Quan la primera crida de ChatGPT d'un torn necessita un PDF analitzat, el motor comença el contrast (`orchestrator/pdf_check.py`), com a molt 2 PDF alhora (en un duel o un debat, mentre Claude respon); cada crida de ChatGPT del torn (respostes, revisions i síntesi) espera la mateixa tasca, com a molt 5 minuts. El Claude de demostració (`AOS_CLAUDE_MODE=fake`) no contrasta res: respon que totes les pàgines són correctes sense llegir-ne cap, així que amb ell el PDF es llegeix sense contrastar, com sense Claude.
  2. Si el contrast ja està desat (pel contingut del fitxer i la versió del contrast), es reaprofita sense cap crida. Si no, Claude rep el PDF sencer, el text extret de cada pàgina entre dues línies amb un codi que ChatGPT no veu mai i els avisos de l'anàlisi, i només escriu les pàgines que difereixen, una línia JSON per pàgina: sense text, il·legible, incompleta, amb text que no es veu, o una descripció del que mostren les figures. Són com a molt 3 crides per PDF, cadascuna des de la pàgina on s'ha quedat l'anterior; una línia mal formada o tallada acaba la lectura, i només compten les pàgines d'abans.
  3. ChatGPT llegeix el PDF pàgina per pàgina (`prompt_format.pdf_view`): el text extret on és correcte i la lectura de Claude, marcada, on no ho és; d'una pàgina on Claude troba text que no es veu, només en rep el text visible i l'avís. Les pàgines que ningú no ha contrastat (sense Claude, un error, una negativa, el temps esgotat, les que queden fora de les crides o un PDF sense analitzar) li arriben tal com s'han extret, amb el text que no es veu que puguin tenir: diuen que no s'han contrastat i, si l'anàlisi les troba sospitoses, que poden tenir text que no es veu. Cada línia de la vista porta un codi propi que només veu ChatGPT (ni el del fitxer, que Claude veu a les revisions, ni el del contrast), així que ni el PDF ni Claude no en poden falsificar cap.
  4. Tots dos models reben l'avís de les pàgines amb possible text amagat, i les revisions i la síntesi saben quines pàgines ha llegit ChatGPT a través de Claude (el text que hi ha llegit Claude o la seva descripció de les figures): si hi coincideixen, és una sola lectura.
  5. El contrast es desa quan és complet o quan s'han acabat les crides; mai després d'un error, d'una negativa, d'una crida que no avança, del temps esgotat o d'una cancel·lació (el torn següent ho torna a provar). Un torn on ChatGPT ha llegit un PDF que el torn següent tornaria a contrastar, o que ningú no podia contrastar (sense Claude o amb el de demostració), no entra a la memòria cau de torns. Les crides es facturen amb el propòsit `check` i compten al total del torn. La interfície en mostra l'estat (`pdf.check`) i el distintiu de les respostes de ChatGPT (`meta.pdf_reading`).
- **Miniatures:** les fa el navegador (les imatges amb un `canvas`, la primera pàgina d'un PDF amb PDF.js) i les puja, perquè es vegin a tots els dispositius.

## Models, costos i límits

- **Models:** cada agent té un model per defecte i un de ràpid (per als resums), configurables des de la interfície. La llista es demana en directe al proveïdor (API de models d'Anthropic i d'OpenAI, `model/list` de Codex; a la CLI de Claude, els àlies `opus`, `sonnet`, `haiku` i `fable`, que sempre apunten a l'última versió). També s'accepta qualsevol identificador, per fer servir un model nou el mateix dia que surt.
- **Cost:** el motor calcula el cost de cada crida amb la taula de preus (USD per milió de tokens, editable). En mode API és el cost real; en mode subscripció és el *valor equivalent* a preus d'API. La interfície ho mostra en euros amb el tipus del BCE.
- **Fallbacks de l'API de Claude:** quan un model declina i un altre respon, cada intent es factura a les tarifes del model que l'ha executat, com fa Anthropic. L'intent declinat és una crida facturada a part, amb el seu model i el seu cost, que compta al total del torn; la resposta conserva només l'ús de l'intent que ha respost. Els tokens de models diferents no se sumen mai ([ADR 0008](adr/0008-recompte-de-tokens.md)).
- **Percentatge usat:** en mode subscripció, les finestres de 5 hores i setmanal que informen Anthropic i OpenAI; en mode API, el pressupost mensual en euros; i, si indiques el preu del pla, quant valor n'has tret aquest mes.

## Resultat del torn

Com acaba cada torn (completat, fallit o cancel·lat) es decideix una sola vegada i es desa a la pregunta (`meta.outcome`) abans de l'esdeveniment final ([ADR 0007](adr/0007-resultat-del-torn.md)). Porta l'estat, l'error, les fallades de cada crida, el total del torn (totes les crides facturades, també la compactació, els contrastos de PDF, les fallides i els intents declinats), els estalvis, el consens i els missatges finals. Així, un torn recarregat mostra el mateix que en directe; les estadístiques globals ja eren correctes, perquè surten de la taula d'ús.

- La pregunta es crea amb el resultat obert (`null`). Si el servidor cau o es reinicia a mig torn, queda així, i la interfície el mostra com a no completat.
- Un torn cancel·lat desa el resultat abans que la cancel·lació continuï, en una tasca pròpia protegida amb `asyncio.shield`. Un torn es cancel·la una sola vegada: un segon «Atura», o l'aturada del servidor, no interromp un torn que ja s'està aturant, i `turn.cancelled` arriba després del resultat desat, amb el mateix total. En aturar-se, el servidor espera que aquests torns acabin abans de tancar la base de dades.
- Un torn cancel·lat just quan desava els estalvis (ja amb tots els missatges) els acaba d'escriure i el resultat els porta, com les files que compta el tauler. Qualsevol altre torn cancel·lat o fallit no en registra.
- `turn.failed` i `turn.cancelled` porten el total del torn, com `turn.completed`, i `stream.failed` porta el cost de la crida fallida quan se sap.
- Un torn «Perfecciona» hi afegeix per què s'ha acabat (`stop_reason`: l'ha aturat el propietari, cap dels dos no hi troba res a canviar, ha convergit, s'han acabat les rondes o el pressupost, o els dos models han fallat). Si es cancel·la quan ja té una versió, la desa abans com a resposta final, sense cap crida i amb la mateixa protecció, perquè no es perdi res del que ja s'ha pagat.

## Integritat de les respostes

Una resposta pot ser completa, pot estar tallada o pot ser una negativa, i el sistema no les confon mai ([ADR 0005](adr/0005-integritat-de-les-respostes.md)).

- **Resposta tallada:** és una resposta parcial útil, però mai completa. Es desa amb `truncated` i el motiu (`finish_reason`) i la interfície la mostra com a incompleta. El torn no entra a la memòria cau. En un debat continua alimentant les revisions i la síntesi, però el prompt la marca com a incompleta. Si no hi ha cap text, la crida falla dient que s'ha esgotat el límit de sortida, i el cost es registra igualment. Un resum de compactació tallat no es fa servir mai.
- **Negativa:** és un error propi (`RefusalError`), no reintentable, amb el seu missatge i el seu cost. El text emès abans de la negativa no es desa mai. La CLI de Claude, davant d'una negativa, torna a preguntar pel seu compte una vegada; el proveïdor l'atura abans (vegeu la taula de sota).
- **Flux interromput:** és un error reintentable, mai una resposta completa. Passa quan la connexió cau a mig flux, quan el flux acaba sense l'esdeveniment final o quan Codex reintenta una resposta que ja s'estava mostrant. El motor només el reintenta si encara no ha mostrat res.
- **Revisions:** l'analitzador tracta com a text les etiquetes escrites dins de codi i les etiquetes d'obertura de la mateixa secció. Un bloc de codi que no es tanca mai no era codi. Si una etiqueta de tancament va seguida de text, es decideix amb la següent etiqueta: si torna a aparèixer la mateixa, la primera era text; si comença una altra secció o s'acaba la resposta, tancava la secció, i el text del mig (un encapçalament, una salutació) es descarta. `UNCHANGED` sol conserva la resposta anterior; en majúscules, a més, pot anar seguit d'una nota curta a la mateixa línia, que es desa a part.

### Pressupost de sortida

`max_output_tokens` és el màxim de tokens de sortida **facturats** d'una crida, amb el raonament inclòs. Cap adaptador no l'apuja. El raonament es tria a part, amb `reasoning`:

- respostes, revisions i síntesis: 16.000 tokens, amb el raonament per defecte;
- resums: 2.000 tokens, amb el raonament `off`;
- contrastos de PDF: 2.000 tokens, més 1.200 per cada pàgina de la crida sense text, il·legible o amb possible text amagat (Claude la transcriu sencera; de l'última, tot el text que es veu) i 100 per cada altra pàgina, com a molt 32.000, amb el raonament `off`.

Cada proveïdor ho aplica així:

| Proveïdor | Límit de sortida | Raonament `off` |
| --- | --- | --- |
| API de Claude | `max_tokens` exacte. Si no hi cap el pressupost mínim de pensament (1.024), no pensa. `stop_reason: "max_tokens"` dona una resposta tallada. | Pensament desactivat |
| API d'OpenAI | `max_output_tokens` exacte. `response.incomplete` dona una resposta tallada. | L'esforç més baix que accepta el model: `none` a GPT-6 Sol i Luna, `minimal` als primers GPT-5 i `low` a la resta |
| CLI de Claude | `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, calculat pel proveïdor, forma part de la clau dels processos calents. La CLI l'aplica a cada petició que fa. Quan una resposta s'atura per `max_tokens`, la CLI 2.1.283 la reprèn pel seu compte (fins a 3 vegades) amb una petició nova uns 10 ms després; després d'una negativa, torna a preguntar una vegada. Cada petició d'aquestes tornaria a facturar tot el context. Per això el proveïdor mata el grup de processos amb SIGKILL tan bon punt llegeix aquest `stop_reason` i acaba la crida amb l'ús d'aquella petició: una resposta tallada o una negativa. Amb SIGTERM no n'hi ha prou, perquè la CLI s'atura ordenadament i envia la petició igualment. Comprovat amb la CLI real contra una API simulada en local. | `--thinking disabled` |
| Codex (app-server 0.157.1) | El protocol no té cap camp per al límit. La crida atura el torn (`turn/interrupt`) quan el text visible estimat (caràcters / 4) supera el pressupost. És aproximat: no compta el raonament, i l'ús és el que informa Codex. | `low`, el nivell més baix del catàleg |

## Seguretat (un sol usuari)

- Només Caddy és accessible des de fora (80/443); l'aplicació escolta a la xarxa interna. El cos de les peticions té un màxim d'1 MiB (4 KiB per a l'inici de sessió, l'única ruta que es llegeix sense sessió): Caddy el passa a l'aplicació a mesura que arriba, sense acumular-lo en memòria, i l'aplicació respon 408 i tanca la connexió si no ha arribat sencer en 15 s (Caddy talla als 30 s), així que una pujada lenta no ocupa cap connexió gaire estona; els WebSockets no passen per aquest límit. La pujada d'un adjunt, que només es llegeix amb sessió, té un límit propi: 20 MB i 120 s (Caddy talla als 150 s).
- Inici de sessió amb contrasenya (argon2id) **i** codi TOTP; bloqueig exponencial després d'intents fallits. Un navegador on ja s'ha entrat (cookie de dispositiu conegut) només es bloqueja pels seus propis errors; `agentic-os reset-throttle` aixeca tots els bloquejos. L'inici de sessió acaba en una sola transacció d'escriptura, condicionada al propietari amb què s'han comprovat les credencials: si `agentic-os init` el canvia mentrestant, l'intent falla, no en queda cap sessió ni dispositiu i la contrasenya antiga no pot substituir mai la nova.
- Sessions al servidor (només se'n desa el hash), cookie `__Host-` HttpOnly, Secure, SameSite=Strict, caducitat per inactivitat i absoluta. Només les accions del propietari compten com a activitat: les peticions que el client fa pel seu compte (amb `X-AOS-Background: 1`), les reconnexions del WebSocket, els *pings* i les resubscripcions comproven la sessió sense allargar-la, de manera que una pestanya oberta sense ús no la manté viva. Com que la cookie és HttpOnly, només el servidor pot tancar la sessió: el client només dona el logout per fet quan el servidor el confirma. Fins aleshores, la pàgina queda bloquejada localment, sense cap dada de la sessió en memòria, i en tornar-la a carregar es torna a provar el logout abans de res més.
- L'entrada es valida a les fronteres: els identificadors han de cabre a SQLite i el text ha de ser UTF-8 vàlid. Si no, la resposta és un `422` (o un error `invalid` al WebSocket) en català, mai un error intern ni un missatge intern de Python.
- Comprovació d'`Origin` a totes les peticions que canvien estat i al WebSocket.
- CSP estricta (`script-src 'self'`), HSTS, `frame-ancestors 'none'`; el markdown dels models es neteja amb DOMPurify.
- Adjunts: el tipus surt del contingut i l'SVG es rebutja. Cap fitxer pujat no es serveix com a HTML: les imatges es mostren amb el seu tipus, els PDF i el text es descarreguen (`Content-Disposition: attachment`), i tots porten `nosniff` i una CSP `default-src 'none'; sandbox`. Els PDF només els llegeix un procés limitat, i els fitxers es desen amb un nom que surt del seu hash, mai del nom que dona el navegador. El text d'un PDF que no es veu no arriba a ChatGPT amb la subscripció a les pàgines que Claude ha contrastat; les que ningú no ha contrastat li arriben tal com s'han extret. Tots dos models reben l'avís de les pàgines on l'anàlisi del servidor en sospita, que és una heurística, no una garantia. El nom del fitxer viatja a la consulta de l'URL de la pujada, però cap registre (ni el d'accés de l'aplicació ni els de Caddy) no desa la consulta de cap URL, tampoc el text de les cerques.
- Les CLI s'executen sense *shell* ni accés als secrets de l'aplicació, amb temps màxim i matant tot el grup de processos en cancel·lar. La de Claude no té cap eina. Codex 0.157.1 encara ofereix a ChatGPT una eina de codi en un procés fill (entorn aïllat V8, sense fitxers ni xarxa) i eines de subagents: l'aplicació només en deixa córrer un alhora (`agents.max_threads=1`), interromp de seguida els torns que no són de cap crida en curs, atura la crida que en fa servir més de 3 vegades i, quan ja no hi ha cap crida en curs, reinicia el procés de Codex que n'hagi obert algun ([ADR 0002](adr/0002-subscripcions-via-cli-oficials.md)). L'estat i els registres de Codex, que contenen els prompts, viuen en un tmpfs privat, i els registres s'esborren cada vegada que s'engega Codex. Per això `agentic-os doctor` engega el seu propi Codex amb un directori d'estat temporal, que esborra en acabar: mai no comparteix el de l'aplicació en marxa. També `claude --version` i `codex --version` s'executen amb la llista tancada de variables d'entorn de cada CLI.
- Contenidors sense root (l'aplicació amb l'usuari 10001 i Caddy amb el 10002; només `caddy-init` corre uns segons com a root, sense xarxa i amb només les *capabilities* que necessita `chown`, per donar els volums de Caddy al seu usuari), `no-new-privileges`, sense *capabilities* efectives i amb límits de memòria, CPU i processos.

## Latència i connexió

- Una sola connexió WebSocket persistent (sense *handshakes* per petició), amb *ping/pong* i reconnexió automàtica.
- Els torns continuen al servidor si es talla la connexió; en reconnectar, el client recupera els esdeveniments pendents (`turn.subscribe`). Quan s'esborra una conversa, el servidor n'oblida també els torns: ja no se'n pot recuperar res.
- El procés de Codex es recupera sol. Cada petició té un temps màxim que inclou escriure-la, perquè un procés encallat que deixa de llegir l'entrada no pugui bloquejar les altres. Quan s'allibera una crida, un procés que no respon es reinicia, i després de fallades seguides els reinicis s'espacien com a molt 30 s. Si una crida no rep resposta en començar, una petició barata distingeix un procés encallat, que es reinicia de seguida, d'un d'ocupat: aquest continua servint les altres crides i es reinicia quan queda lliure.
- Les dues IA treballen en paral·lel; el text arriba en *streaming*.
- uvloop + httptools, HTTP/3 a Caddy, fitxers estàtics amb hash i memòria cau llarga, three.js carregat de manera diferida perquè la interfície aparegui a l'instant.
