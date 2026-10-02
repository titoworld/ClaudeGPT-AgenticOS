# Protocol client ↔ servidor

Contracte entre el frontend (`web/`) i el backend (`src/agentic_os/server/`). Tot és JSON en UTF-8. Els noms de camps són en anglès i en `snake_case`. Els tipus TypeScript equivalents són a `web/src/lib/protocol.ts`.

## Autenticació i seguretat comuna

- Sessió amb una cookie `__Host-aos_session` (HttpOnly, Secure, SameSite=Strict, Path=/). En desenvolupament sense HTTPS (`AOS_SECURE_COOKIES=false`) la cookie es diu `aos_session` i no és `Secure`. La sessió caduca després de `AOS_SESSION_IDLE_HOURS` sense activitat (72 h) i als `AOS_SESSION_MAX_DAYS` (30 dies).
- Activitat: només les accions del propietari allarguen la caducitat per inactivitat (iniciar sessió, obrir una conversa, desar, esborrar, canviar un nom, `turn.start`, `turn.cancel`...). Les peticions REST que el client fa pel seu compte, sense cap acció del propietari (els refrescos després d'un `hello` o d'una reconnexió, els refrescos periòdics i els reintents), porten la capçalera `X-AOS-Background: 1`. Per a aquestes, el servidor comprova la sessió només en lectura, com un `ping`: respon igual (`401` si ja no és vàlida), però no allarga la caducitat. Així, una pestanya oberta sense ús no manté la sessió viva. El client també posa la capçalera a tots els `POST /api/auth/logout`, perquè un logout que falla no allargui la sessió (si funciona, la tanca igualment). Sense la capçalera, o amb un altre valor, la petició compta com a activitat. L'*handshake* del WebSocket també és només de lectura (vegeu [WebSocket](#websocket-apiws)).
- Dispositiu conegut: cada inici de sessió correcte posa també una cookie `__Host-aos_device` (HttpOnly, Secure, SameSite=Strict, Path=/; sense HTTPS es diu `aos_device` i no és `Secure`) amb un testimoni aleatori que dura 1 any i se substitueix per un de nou a cada inici de sessió. El logout la conserva; `agentic-os init` i `agentic-os reset-sessions` obliden tots els dispositius. Un intent d'inici de sessió que la porta només es limita pel comptador d'errors d'aquell dispositiu (no pel de l'adreça ni pel global), de manera que ningú no pot bloquejar el propietari des d'un navegador on ja ha entrat.
- Totes les rutes sota `/api/` requereixen sessió, excepte `GET /api/health`, `GET /api/auth/state` i `POST /api/auth/login`.
- Les peticions que canvien estat (`POST`, `PUT`, `PATCH`, `DELETE`) i l'*handshake* del WebSocket han de portar una capçalera `Origin` present a `Settings.allowed_origins`: la d'`AOS_PUBLIC_ORIGIN` o una d'`AOS_EXTRA_ORIGINS`. Si no, la petició rep `403`. El WebSocket, en canvi, s'accepta i es tanca de seguida amb el codi `4403`, perquè el navegador en vegi el motiu: d'un *handshake* rebutjat no en veu cap.
- Errors HTTP: cos `{"detail": "missatge en català"}`. `400` si el client talla la connexió abans d'enviar tot el cos, `401` sense sessió, `403` origen no permès, `404`, `408` si el cos no arriba sencer en 15 s (des que el servidor el comença a llegir; 120 s a `PUT /api/attachments`), `409` si la configuració ha canviat des que el client la va llegir (`PUT /api/settings`, amb `settings` al cos) o si l'adjunt que s'esborra ja s'ha enviat (`DELETE /api/attachments/{id}`), `413` cos massa gran: com a molt 1 MiB, o 4 KiB a `POST /api/auth/login` (l'única ruta que es llegeix sense sessió), o 20 MB a `PUT /api/attachments` (cada tipus de fitxer en té un de més baix: vegeu «Adjunts»), pel `Content-Length` o comptat mentre arriba; el `detail` diu el límit que s'ha aplicat (`La petició és massa gran (màxim 1 MiB).`, `La petició és massa gran (màxim 4 KiB).` o `La petició és massa gran (màxim 20 MB).`), `415` tipus de fitxer no admès (només els adjunts), `422` validació (també els nombres fora de rang, per grans que siguin; vegeu «Validació de l'entrada»), `429` massa intents (amb capçalera `Retry-After` i camp `retry_after` en segons), `507` el servidor no té espai al disc per desar un adjunt.
- Validació de l'entrada: els identificadors de conversa i d'adjunt (a la ruta, a `before`, i al `conversation_id` i els `attachments` del WebSocket) han de ser enters d'1 a 2^63 − 1, el màxim de SQLite; si no, `422` (`Dades no vàlides: «conversation_id».`, `«attachment_id»` o `«before»`). El text dels cossos JSON (claus i valors) s'ha de poder codificar en UTF-8: un substitut solitari, que en JSON s'escriu `"\ud800"` i és JSON vàlid, dona `422` amb `La petició conté text que no és UTF-8 vàlid.` i no es desa res. Les parelles de substituts, com `"\ud83d\ude00"` (😀), són text vàlid. L'única excepció és `POST /api/auth/login`: una contrasenya o un codi amb aquest text són credencials incorrectes (`401`), i l'intent compta per al bloqueig per intents fallits. Els missatges d'error no inclouen mai els missatges interns de Python.
- Una resposta que surt abans que el servidor hagi rebut tot el cos de la petició (`403`, `401`, `413` pel `Content-Length`, `429`, `408`, o un cos enviat a una ruta que no el llegeix) porta `Connection: close` i el servidor tanca la connexió: el client no la pot reutilitzar. Les peticions sense cos o amb el cos llegit sencer mantenen la connexió.

## REST

| Mètode i ruta | Cos / paràmetres | Resposta |
| --- | --- | --- |
| `GET /api/health` | – | `{"status": "ok"}` |
| `GET /api/auth/state` | – | `{"authenticated": bool, "setup_required": bool}` (`setup_required`: encara no s'ha executat `agentic-os init`) |
| `POST /api/auth/login` | `{"password": str, "totp": str}` | `204` + cookies de sessió i de dispositiu; `401`; `429`. L'inici de sessió acaba en una sola transacció, condicionada al propietari amb què s'han comprovat les credencials: si mentrestant `agentic-os init` l'ha canviat, `401` i no es desa res. La mateixa transacció tanca la sessió que presentava la cookie, si n'hi havia; després se'n tanquen els WebSockets |
| `POST /api/auth/logout` | – | `204` (esborra la cookie de sessió i tanca els WebSockets d'aquesta sessió; la de dispositiu es conserva); `401` sense sessió. Per al client, només `204` i `401` volen dir que la sessió s'ha acabat. Amb qualsevol altra resposta, o si no n'arriba cap, bloqueja la pàgina localment (al servidor, la sessió continua oberta) i, fins que el servidor confirma el logout o el propietari torna a iniciar sessió, cada càrrega de la pàgina torna a provar el logout abans de consultar `GET /api/auth/state` |
| `GET /api/providers` | – | `[ProviderStatus]` |
| `GET /api/models` | `?refresh=1` opcional (ignora la memòria cau) | `ModelCatalog` |
| `GET /api/pricing` | – | `Pricing` |
| `GET /api/spend` | – | `MonthSpend` (mes en curs, per a les barres de pressupost) |
| `GET /api/settings` | – | `RuntimeSettings`, amb la `revision` actual |
| `PUT /api/settings` | `RuntimeSettings` amb la `revision` en què es basa el canvi (obligatòria; les altres claus que falten prenen el valor per defecte) | `RuntimeSettings` desats (`revision` + 1); `409` o `422` (vegeu «Desament de la configuració») |
| `GET /api/conversations` | `?limit=50&before=<id>&q=<text>` (vegeu «Llista i cerca de converses») | `[ConversationSummary]`, les més recents primer |
| `GET /api/conversations/{id}` | – | `ConversationDetail` |
| `PATCH /api/conversations/{id}` | `{"title": str}` | `ConversationSummary` |
| `DELETE /api/conversations/{id}` | – | `204`. Els torns en curs de la conversa es cancel·len, i el servidor n'oblida tots els torns: un `turn.subscribe` posterior rep `turn.unknown` |
| `GET /api/stats` | `?days=30` (1–365) | `Stats` |
| `PUT /api/attachments` | `?name=<nom del fitxer>`; el cos és el fitxer tal com és, no multipart (vegeu «Adjunts») | `201` + `Attachment`; `413`, `415`, `422`, `507` |
| `GET /api/attachments/{id}` | – | `Attachment` |
| `GET /api/attachments/{id}/content` | – | El fitxer, amb el tipus detectat en pujar-lo (vegeu «Adjunts») |
| `PUT /api/attachments/{id}/thumbnail` | El cos és la miniatura: PNG o WebP, com a molt 100 kB i 512 px per costat | `204`; `404`, `413`, `415`, `422` |
| `GET /api/attachments/{id}/thumbnail` | – | La miniatura (`image/png` o `image/webp`), o `404` si no en té |
| `DELETE /api/attachments/{id}` | – | `204` si no s'ha enviat mai; `409` si ja és a una pregunta (s'esborra amb la conversa) |
| `GET /api/ws` | WebSocket | vegeu més avall |

### Tipus

```ts
type Agent = "claude" | "chatgpt";
type TurnMode = "solo" | "duel" | "debate";
type MessageKind = "question" | "answer" | "revision" | "synthesis";

interface Usage {
  input_tokens: number;        // entrada no servida des de memòria cau
  output_tokens: number;       // inclou el raonament
  cache_read_tokens: number; cache_write_tokens: number;
  reasoning_tokens: number;    // part de output_tokens: no s'hi torna a sumar
  cost_usd: number | null;     // cost estimat (preus d'API); null si el model no té preu
}
// Tokens processats (ADR 0008): input_tokens + cache_read_tokens + cache_write_tokens +
// output_tokens. És la definició de tots els recomptes de tokens: el total d'un torn, els
// estalvis (cache i early_stop) i la ràtio del tauler, amb la mateixa definició al
// numerador i al denominador. El client la calcula (processedTokens a web/src/lib/costs.ts).
// Un Usage és sempre d'un sol model, llevat dels totals (d'un torn, d'un agent, d'un dia).

interface ProviderStatus {
  agent: Agent; mode: "cli" | "api" | "fake";
  available: boolean; model: string; detail: string;   // detail en català
  limits: { window: string;            // "5h", "7d"...
            used_percent: number | null;   // 0–100 (pot passar de 100)
            resets_at: string | null;  // ISO 8601
            status: string }[];        // "allowed" | "warning" | "rejected"
}

interface ModelInfo {
  id: string;                  // valor que s'envia al proveïdor (id d'API, àlies de la CLI...)
  label: string; description: string;
  is_default: boolean; context_window: number | null;
}

interface ModelCatalog {
  claude: AgentModels; chatgpt: AgentModels;
}
interface AgentModels {
  mode: "cli" | "api" | "fake";
  default_model: string;       // el que es fa servir si no se'n tria cap
  fast_model: string;          // el de les crides internes (resums)
  models: ModelInfo[];         // llista en directe del proveïdor, o una de reserva
  live: boolean;               // false si la llista és la de reserva
}
// Qualsevol id que compleixi ^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,99}$ és vàlid:
// així es poden fer servir models nous encara que no surtin a la llista.

interface FxRate {
  eur_per_usd: number;
  as_of: string | null;        // data (AAAA-MM-DD) del tipus del BCE
  source: "ecb" | "manual";
}

interface ModelPrice {         // USD per milió de tokens, com els publiquen els proveïdors
  input: number; output: number; cache_read: number; cache_write: number;
}

interface Pricing {
  fx: FxRate;
  prices: (ModelPrice & { model: string; key: string; source: "default" | "custom";
                          default: ModelPrice | null })[];
}
// prices: exactament la taula amb què es calculen els costos, ordenada per model: els
// preus per defecte amb els del propietari al damunt (un preu propi substitueix el per
// defecte del mateix model normalitzat, sense prefix de proveïdor, data ni context).
// key: l'id normalitzat amb què el servidor compara els models (normalize_model: sense
// espais als extrems i en minúscules, sense el que hi ha fins a l'última "/" ni el prefix
// "anthropic.", sense un context "[…]" al final i després sense una data "-AAAAMMDD",
// "@AAAAMMDD" o "-latest" al final). Dues files no tenen mai la mateixa key. Un model
// la key del qual no té fila paga el preu de la fila amb la key més llarga que sigui un
// prefix de la seva.
// Els vectors de tests/fixtures/model_ids.json fixen la normalització per al servidor
// i per al web.
// default: a una fila "custom" que substitueix un preu per defecte (la mateixa key),
// aquell preu per defecte; null a les altres.

interface RuntimeSettings {
  revision: number;                       // desaments: 0 fins al primer, +1 a cada un
                                          // (vegeu «Desament de la configuració»)
  default_mode: TurnMode;                 // per defecte "debate"
  default_target: Agent;                  // agent del mode solo
  debate: { rounds: number;               // 0–4, per defecte 2
            consensus_threshold: number;  // 50–100, per defecte 85
            synthesizer: Agent };         // per defecte "claude"
  use_cache: boolean;                     // per defecte true
  compaction_threshold_tokens: number;    // 1000–100000, per defecte 6000
  models: Record<Agent, string | null>;       // model per defecte; null = el del proveïdor
  fast_models: Record<Agent, string | null>;  // model per als resums; null = el del proveïdor
  prices: Record<string, ModelPrice>;         // preus propis (substitueixen o afegeixen models);
                                              // 422 si una clau no identifica cap model un cop
                                              // normalitzada ("openai/") o si dues són el mateix
  fx: { mode: "auto" | "manual";              // auto: BCE diari, amb el manual de reserva
        eur_per_usd: number };                // 0,2–5, per defecte 0,86
  budgets_eur: Record<Agent, number | null>;  // pressupost mensual de l'ús per API
  plans_eur: Record<Agent, number | null>;    // preu mensual de la subscripció
  pdf_in_revisions: "full" | "text";          // PDF a les revisions d'un debat: "text" (per
                                              // defecte) el text extret; "full" el document.
                                              // Un PDF sense text hi va sempre sencer
}

interface AgentSpend {
  api_usd: number;             // cost real de les crides en mode api
  equivalent_usd: number;      // valor de les crides en mode cli a preus d'API
  unpriced_calls: number;      // crides de models sense preu conegut
  budget_eur: number | null;  budget_used: number | null;   // 0–1+ (api_usd en € / pressupost); null sense pressupost
  plan_eur: number | null;    plan_value: number | null;    // 0–1+ (equivalent en € / preu del pla); null sense preu
}
// equivalent_usd inclou tota crida que no és d'API i té preu (també les de demostració si els poses preu).

interface MonthSpend {
  month: string;               // "AAAA-MM" (UTC)
  fx: FxRate;
  by_agent: Record<Agent, AgentSpend>;
}

interface ConversationSummary {
  id: number; title: string;
  created_at: string; updated_at: string;   // ISO 8601 UTC
  last_mode: TurnMode | null; message_count: number;
}

interface Message {
  id: number; turn_id: number; kind: MessageKind; content: string;
  agent: Agent | null; round: number; final: boolean;
  meta: Record<string, unknown>;   // vegeu "Metadades de missatge"
  created_at: string;
}

interface ConversationDetail extends ConversationSummary {
  summary: string | null;          // resum de la compactació, si n'hi ha
  messages: Message[];             // tots, els més antics primer
}

interface Attachment {             // un fitxer adjunt (vegeu «Adjunts»)
  id: number;
  name: string;                    // nom que es mostra, net (sense camí ni caràcters de control)
  kind: "image" | "pdf" | "text";
  mime: string;                    // "image/png" | "image/jpeg" | "image/gif" | "image/webp" |
                                   // "application/pdf" | "text/plain"
  size: number;                    // bytes
  pages: number | null;            // PDF
  width: number | null; height: number | null;   // imatges, en píxels
  sha256: string;                  // del contingut
  created_at: string;              // ISO 8601 UTC, quan es va pujar
  has_thumbnail: boolean;          // el navegador n'ha pujat la miniatura
  text_available: boolean;         // text: sempre; PDF: se n'ha pogut extreure el text
  estimated_tokens: number;        // tokens d'entrada aproximats per crida
  pdf_notes: {                     // avisos de les pàgines d'un PDF analitzat (vegeu «Adjunts»);
    no_text: number[];             // null en els altres. Números de pàgina, des de l'1:
    garbled: number[];             // sense text (escanejades), amb el text il·legible
    hidden: number[];              // i amb possible text que no es veu
  } | null;
}

interface Stats {
  days: number;
  totals: { calls: number; errors: number; cost_usd: number;
            by_agent: Record<Agent, Usage & { calls: number }> };
            // errors: crides amb ok = false (fallides, i cada intent que un model va
            // declinar abans d'un fallback)
  savings: { cache: number; compaction: number; early_stop: number;
             unchanged: number; total: number; cost_usd: number | null };
             // cost_usd: valor dels estalvis de la finestra; com els tokens, es conserva
             // encara que s'esborri la conversa (null si cap estalvi no té preu).
             // Tokens processats; les files desades abans de l'ADR 0008 conserven la
             // definició antiga (input + output a cache i early_stop).
  daily: { date: string; agent: Agent; input_tokens: number; output_tokens: number;
           cache_read_tokens: number; cache_write_tokens: number;
           cost_usd: number }[];   // date: dia natural UTC; els quatre tipus de tokens
                                   // processats, per sumar-los com Usage
  savings_daily: { date: string; kind: "cache" | "compaction" | "early_stop" | "unchanged";
                   tokens: number }[];
  latency: Record<Agent, { p50_ms: number | null; p95_ms: number | null;
                           ttft_p50_ms: number | null }>;
                           // només les crides que escriuen un missatge (respostes,
                           // revisions i síntesis): no els resums de l'historial ni el
                           // contrast dels PDF, que compten als tokens i als costos
  turns: { solo: number; duel: number; debate: number };
  consensus: { debates: number; reached: number; avg_rounds: number | null };
  costs: { fx: FxRate;
           by_agent: Record<Agent, { api_usd: number; equivalent_usd: number;
                                     unpriced_calls: number }> };
  month: MonthSpend;
}
```

### Desament de la configuració

`revision` compta els desaments de la configuració: val 0 on no s'ha desat mai i augmenta en 1 a cada desament correcte. Una configuració desada per una versió anterior, sense revisió, compta com a revisió 1. Així, només la configuració integrada, que un client té mentre encara no ha llegit la del servidor, és a la revisió 0, i un desament basat en aquesta no pot substituir mai una configuració desada. Es desa amb la configuració, de manera que es conserva en reiniciar, i no torna mai enrere. El client edita a partir de la configuració que ha llegit i envia a `PUT /api/settings` tota la configuració, amb la `revision` que tenia la que va llegir ([ADR 0006](adr/0006-revisio-de-la-configuracio.md)):

- `200`: la revisió és l'actual. La resposta és la configuració desada, amb `revision` + 1.
- `409`: la revisió no és l'actual, normalment perquè s'ha desat des d'una altra pestanya o dispositiu. No es desa res. El cos és `{"detail": "La configuració ha canviat en una altra pestanya o dispositiu. Revisa-la i torna-la a desar.", "settings": RuntimeSettings}`, amb la configuració actual tal com la dona `GET /api/settings`: el client la mostra i el propietari la revisa i la torna a desar.
- `422`: falta `revision`, no és un enter ≥ 0 o algun altre camp no és vàlid. L'ordre és: el cos ha de ser un objecte JSON, després `revision` i després la resta de camps. La revisió es compara al final, de manera que una edició no vàlida dona `422` encara que es basi en una revisió antiga.
- La comparació i l'escriptura són atòmiques (una sola transacció d'escriptura de SQLite): de dues peticions basades en la mateixa revisió, una rep `200` i l'altra `409`, encara que vinguin de processos diferents.

### Llista i cerca de converses

`GET /api/conversations` dona les converses per activitat, les més recents primer (per `updated_at` i, si coincideix, per `id`), a pàgines:

- `limit`: d'1 a 200 converses per pàgina (per defecte, 50).
- `before`: l'`id` de l'última conversa de la pàgina anterior. En dona les que la segueixen en aquest ordre, o cap si aquella conversa ja no existeix.
- `q` (opcional): només les converses el títol de les quals conté aquest text, sense distingir majúscules ni accents. El servidor transforma igual el títol i el text: descomposició de compatibilitat (NFKD), `casefold` i sense marques combinants. Així, `cafe` troba «Cafè», `strasse` troba «Straße» i `fi` troba «ﬁnances». La coincidència és literal: `%`, `_` i `\` són caràcters com els altres. Al text se li treuen els espais dels extrems, i els espais seguits compten com un de sol, com als títols. Un `q` buit, o només amb espais, és com no posar-n'hi.
- El text de la cerca pot tenir com a molt 200 caràcters, un cop tret els espais dels extrems. Un de més llarg dona `422` amb `La cerca no pot tenir més de 200 caràcters.`
- Una cerca pagina com la llista: `before` és l'`id` de l'última conversa de la pàgina anterior de la mateixa cerca. La resposta té la mateixa forma, `[ConversationSummary]`.

### Adjunts

Els fitxers que el propietari adjunta a una pregunta ([ADR 0009](adr/0009-adjunts.md)). Els límits són les constants de `src/agentic_os/attachments.py`.

- **Tipus.** Surt sempre del contingut, mai del nom ni del `Content-Type`:
  - imatges PNG, JPEG, GIF i WebP, pels primers bytes; les dimensions, de les capçaleres (el servidor no descodifica mai cap imatge);
  - PDF, pel `%PDF-` del començament;
  - text: UTF-8 vàlid sense cap caràcter NUL i amb una d'aquestes extensions: `txt`, `md`, `markdown`, `csv`, `tsv`, `json`, `yaml`, `yml`, `xml`, `html`, `htm`, `log`, `ini`, `toml`, `cfg`, `py`, `js`, `ts`, `jsx`, `tsx`, `svelte`, `css`, `scss`, `sql`, `sh`, `bash`, `rs`, `go`, `java`, `kt`, `c`, `h`, `cpp`, `hpp`, `cs`, `rb`, `php`, `swift`, `lua`, `r`, `pl`. Sempre és text pla (`text/plain`), també un `.html`;
  - qualsevol altra cosa, també l'SVG i l'HEIC, dona `415`.
- **Límits:**
  - com a molt 5 adjunts per missatge, i 20 MB entre tots;
  - imatge: 7 MB i 8.000 píxels per costat. Abans de pujar-la, el navegador redueix tota imatge de més de 2.576 píxels al costat llarg i torna a codificar a la mateixa mida una de més de 7 MB; també torna a codificar dreta una foto que es mostra girada per la seva orientació EXIF (com les del mòbil), perquè els models en reben els píxels tal com estan desats, sense les metadades. Els GIF es pugen tal com són;
  - PDF: 20 MB i 100 pàgines, sense xifrar;
  - text: 200 kB.
  - Massa gran: `413`, amb el límit del tipus (`El fitxer és massa gran: una imatge pot tenir com a molt 7 MB.`). No vàlid (buit, sense nom, una imatge il·legible, massa píxels o pàgines, un PDF xifrat o malmès): `422`.
- **Pujada:** `PUT /api/attachments?name=<nom>`, amb el fitxer com a cos. Necessita la sessió i l'`Origin`, com totes les escriptures. El servidor escriu el cos en un fitxer temporal a mesura que arriba i el talla al límit del seu tipus, que decideixen els primers 16 bytes: si el `Content-Length` ja el passa, respon `413` de seguida, sense llegir-ne més. `name` és el nom que es mostra (i el que dona l'extensió d'un fitxer de text): se'n queda l'última part d'un camí, en NFC, sense caràcters de control ni de format invisibles (com els que capgiren el sentit del text), amb els espais seguits com un de sol i com a molt 200 caràcters (un de més llarg es talla i conserva l'extensió). Un adjunt que no s'envia en cap torn s'esborra al cap de 24 h.
- **Text d'un PDF:** el servidor el llegeix amb pypdf en un procés a part, com a molt 60 s. Té un bloc per pàgina, introduït per la línia `--- Pàgina N ---`, i els models el reben quan no reben el document (vegeu `pdf_in_revisions` i l'ADR). `text_available` és `false` si no se n'ha pogut extreure cap text: un PDF escanejat, o un error o el temps esgotat mentre se n'extreia (si ni tan sols se'n poden comptar les pàgines, la pujada dona `422`). Si passa d'1.000.000 caràcters, es talla i acaba amb l'avís `[Text retallat: el text extret del PDF passava de 1.000.000 caràcters.]`.
- **Pàgines d'un PDF** (`pdf_notes`): en la mateixa passada, el lector analitza cada pàgina, també les que el límit del text deixa fora: on és el seu text dins del text desat, quantes lletres i quants caràcters trencats té (U+FFFD, caràcters d'ús privat o de control: una font sense mapa de caràcters), si dibuixa alguna imatge i quant text mostra que no es veu: en un mode de renderitzat invisible (3 o 7), més petit d'1 punt en alguna direcció (comptant la mida de la lletra, l'escala horitzontal `Tz`, la matriu de text, la de transformació i la del formulari que el dibuixa) o amb l'origen a més d'1 punt fora de la part visible de la pàgina (la `CropBox` dins de la `MediaBox`), comptant-hi el desplaçament vertical del text (`Ts`). `pdf_notes` en dona els avisos, per pàgina:
  - `no_text`: menys de 25 lletres (una pàgina escanejada, o text dibuixat com a imatge);
  - `garbled`: 5 caràcters trencats o més, i almenys el 5 % del text (una pàgina sense text no hi surt);
  - `hidden`: 10 caràcters o més que no es veuen. El text invisible només hi compta en una pàgina sense imatges: sobre un escaneig és el text reconegut, legítim.

  Són avisos, no veredictes: l'anàlisi no veu el text que amaga un camí de retall ni el que té el color del que hi ha a sota, i una imatge que la pàgina té a les seves `Resources` compta encara que no la dibuixi. Un PDF que no s'ha pogut analitzar té `pdf_notes: null`, com les imatges, els fitxers de text i els PDF pujats abans que existís l'anàlisi. Els `meta.attachments` desats abans no porten la clau.
- **Contrast de Claude** ([ADR 0009](adr/0009-adjunts.md)): ChatGPT amb la subscripció (Codex, mode `cli`) no pot obrir cap PDF i en llegeix el text extret, pàgina per pàgina. En un torn on participa i la pregunta porta PDF analitzats, Claude contrasta aquest text amb el document (en un duel o un debat, mentre respon): a les pàgines on el text extret hi falta o no es pot llegir, ChatGPT llegeix el que hi llegeix Claude, marcat, i d'una pàgina on Claude troba text que no es veu només en rep el text visible i l'avís. ChatGPT espera el contrast com a molt 5 minuts; després llegeix el text sense contrastar. Són com a molt 3 crides per PDF: les pàgines que no hi caben queden sense contrastar, i ho diuen. El Claude de demostració (`AOS_CLAUDE_MODE=fake`) no contrasta res, perquè respon que totes les pàgines són correctes sense llegir-ne cap: amb ell el PDF es llegeix sense contrastar, com sense Claude. Les pàgines que ningú no ha contrastat (`unchecked_pages`) arriben a ChatGPT tal com s'han extret, també el text que no es veu que puguin tenir: diuen que no s'han contrastat i, si l'anàlisi les troba sospitoses, que poden tenir text que no es veu, un avís que també reben tots els models a l'etiqueta del PDF. L'anàlisi és una heurística, no una garantia. El contrast es desa pel contingut del fitxer, i el reaprofiten els torns posteriors i les altres converses; s'esborra quan cap adjunt no fa servir el fitxer. Un torn on ChatGPT ha llegit un PDF que un altre torn llegiria d'una altra manera no entra mai a la memòria cau de torns: quan el torn següent el tornaria a contrastar (la comprovació ha fallat, Claude no l'ha volgut fer, una resposta no ha avançat o ha trigat massa) o quan ningú no el podia contrastar (sense Claude, o amb el de demostració). Es veu amb l'esdeveniment `pdf.check` i amb `meta.pdf_reading` dels missatges de ChatGPT. Les crides es facturen amb el propòsit `check` i compten al total del torn.
- **`estimated_tokens`** (aproximats, per a cada crida que rep l'adjunt): imatge `ceil(w'/28) · ceil(h'/28)`, com a molt 4.784, amb `(w', h')` la imatge reduïda a 2.576 píxels al costat llarg (mai ampliada); PDF, 3.600 per pàgina; text, `ceil(caràcters / 4)`.
- **Contingut** (`GET /api/attachments/{id}/content`): el fitxer tal com es va pujar, amb el tipus detectat (`text/plain; charset=utf-8` per al text), `X-Content-Type-Options: nosniff` i `Content-Security-Policy: default-src 'none'; sandbox`. Les imatges porten `Content-Disposition: inline`; els PDF i el text, `attachment` (es descarreguen). En tots dos casos, amb el nom: `filename` en ASCII i `filename*` en UTF-8. Res del que es puja no es serveix com a HTML. Admet `Range`.
- **Miniatures:** el navegador en fa una en adjuntar el fitxer i la puja a `PUT /api/attachments/{id}/thumbnail`: una imatge PNG o WebP (pel contingut) de com a molt 100 kB i 512 píxels per costat; si no, `415`, `413` o `422`. Una miniatura nova substitueix l'anterior. `GET` la retorna, amb les mateixes capçaleres que una imatge, o `404` si no en té.
- **Esborrar:** `DELETE /api/attachments/{id}` esborra un adjunt que no s'ha enviat mai (el propietari l'ha tret del compositor): `204`. Si ja és a una pregunta, `409`, i s'esborra amb la seva conversa: esborrar una conversa esborra els adjunts que només feia servir ella. Dues pujades del mateix fitxer en comparteixen la còpia, que s'esborra quan cap adjunt no la fa servir, i el contrast de Claude, amb ella.

### Resultat del torn (`meta.outcome` de la pregunta)

Com va acabar un torn es decideix una sola vegada i es desa a la pregunta abans de l'esdeveniment final ([ADR 0007](adr/0007-resultat-del-torn.md)):

```ts
interface TurnOutcome {
  status: "completed" | "failed" | "cancelled";
  error?: { kind: string; message: string };   // només si status és "failed" (el de turn.failed)
  failures: { agent: Agent; kind: string; message: string; round: number }[];
                                   // les fallades de crida del torn (stream.failed), en ordre
  usage: Usage;                    // total del torn: totes les crides facturades (resums de
                                   // compactació, contrastos de PDF, crides fallides, intents
                                   // declinats i crides sense missatge incloses); el mateix
                                   // usage de l'esdeveniment final
  savings: object;                 // com el savings de turn.completed (vegeu més avall);
                                   // zeros en un torn fallit o cancel·lat (no registra estalvis),
                                   // tret d'un torn cancel·lat quan ja desava els seus
                                   // estalvis: les files s'escriuen senceres i els porta
  consensus: object | null;        // com el consensus de turn.completed: el d'un debat completat
  final_message_ids: number[];     // missatges finals desats (també en un torn cancel·lat)
  cached: boolean;                 // servit des de la memòria cau de torns
}
```

- La pregunta es crea amb `outcome: null`. Si el torn no acaba mai (una caiguda o un reinici del servidor), es queda `null`: el client el mostra com a no completat. Els torns desats abans de l'ADR 0007 no tenen la clau.
- Un torn cancel·lat també el desa, abans del `turn.cancelled`. Un torn es cancel·la una sola vegada: un `turn.cancel` repetit, o l'aturada del servidor, mentre el torn s'atura no l'interromp, i el `turn.cancelled` arriba quan el torn s'ha aturat i ha desat el resultat, amb el mateix `usage`. Si l'escriptura falla, el torn no falla i la pregunta es queda amb `null`.

### Metadades de missatge (`meta`)

- Pregunta (`question`): `mode`, `target`, `options`, `models` (models triats per a aquest torn, si n'hi ha), `attachments` (els adjunts de la pregunta, en ordre: `Attachment[]` tal com eren en començar el torn; només hi és si en porta), `compaction_usage` (`Usage` de totes les crides de resum del torn, si n'hi ha hagut) i `outcome` (vegeu «Resultat del torn»).
- Respostes (`answer`, `revision`, `synthesis`): `model`, `usage` (amb `cost_usd`; només el de l'intent que ha respost), `cost_basis` (`"api"`: cost real; `"equivalent"`: mode subscripció, valor a preus d'API), `latency_ms`, `ttft_ms`, `cached` (si ve de la memòria cau).
- Resposta servida després d'un fallback (API de Claude): `declined`, `[{model, usage}]`, els intents facturats que altres models van declinar abans, cadascun amb el seu model i el seu cost. Són crides facturades a part (una fila d'ús per intent, amb `ok = false`) i compten al total del torn, però no a l'`usage` del missatge: els tokens de models diferents no se sumen mai ([ADR 0008](adr/0008-recompte-de-tokens.md)).
- Resposta tallada: `truncated: true` i, si se sap, `finish_reason` (`"max_tokens"`: límit de sortida; `"content_filter"`: filtre de contingut; `"incomplete"` o `"interrupted"`; o un valor propi del proveïdor). Només hi són quan la resposta del model es va tallar abans del final: el contingut és una resposta parcial útil, mai completa. En una revisió que conserva la resposta anterior, el que es va tallar és la crítica o la resposta nova. Un torn amb algun missatge tallat no entra mai a la memòria cau de torns. Una síntesi degradada que reutilitza una resposta tallada també porta la marca ([ADR 0005](adr/0005-integritat-de-les-respostes.md)).
- Revisió (`revision`): a més, `critique` (text), `agreement` (0–100 o `null`) i `unchanged` (bool). Si `unchanged` és cert, `content` conté la resposta anterior que es conserva, i `unchanged_note` (opcional) és la nota curta que el model va escriure després d'`UNCHANGED`, a la mateixa línia (com a molt 200 caràcters). `content` també conté la resposta anterior (amb `unchanged: false`) quan la revisió es va tallar abans de la resposta.
- Síntesi (`synthesis`): a més, `consensus` (`{reached, round, scores}`) i `degraded: true` si s'ha desat sense cridar cap model.
- Missatges de ChatGPT (`answer`, `revision`, `synthesis`) d'una pregunta amb PDF quan no els pot obrir (la subscripció, vegeu «Contrast de Claude» a «Adjunts»): `pdf_reading`, com ha llegit cada PDF, en l'ordre dels adjunts:

  ```ts
  interface PdfReading {
    attachment_id: number; name: string;
    checked: boolean;              // Claude n'ha contrastat alguna pàgina
    claude_pages: number[];        // pàgines que ChatGPT ha llegit, senceres o en part, tal com
                                   // les ha llegit Claude (sense text, il·legibles, incompletes
                                   // o amb text que no es veu) o amb la descripció de Claude
                                   // del que mostren les figures
    hidden_pages: number[];        // pàgines amb text que no es veu: ChatGPT no l'ha rebut
    unchecked_pages: number[];     // pàgines que ningú no ha contrastat: el text extret tal com és
    reason: string | null;         // per què queden pàgines sense contrastar (null si no en queda cap)
  }
  ```

  Una resposta servida des de la memòria cau de torns conserva el `meta` desat.
- Missatges finals del torn (els de `final_message_ids`): `savings` (el mateix objecte que `turn.completed`). Cada missatge final porta també `unstored_usage` (`Usage`) si fins llavors hi ha hagut crides facturades que no han deixat cap missatge (errors, respostes buides, negatives, límit de sortida esgotat sense text, intents declinats).
- Total d'un torn recarregat: `outcome.usage` de la pregunta, tal com és (no s'hi tornen a sumar `compaction_usage` ni `unstored_usage`). És igual al `usage` de l'esdeveniment final i a la suma de les files d'ús del torn més les dels seus resums de compactació, que es desen sense `turn_id` perquè es fan abans que existeixi la pregunta. Per als torns sense `outcome` (desats abans de l'ADR 0007), la suma dels `usage` dels seus missatges, més `compaction_usage` i l'`unstored_usage` de l'últim missatge final, que no inclou una crida fallida que acabés després que l'altre agent d'un duel hagués desat la seva resposta.

## WebSocket `/api/ws`

Una sola connexió persistent per pestanya. El servidor tanca amb el codi:

- `4401` si no hi ha sessió en connectar i també quan la sessió s'acaba amb el socket obert: al moment si és un logout d'aquest servidor, i com a molt en 30 s si l'ha revocada `agentic-os reset-sessions` o ha caducat (el servidor la torna a comprovar cada 30 s encara que el client no enviï res).
- `4403` si l'origen no és vàlid (vegeu «Autenticació i seguretat comuna»).
- `1013` si el client no rep prou ràpid: té 4096 missatges pendents d'enviar, o 200.000 esdeveniments o més (cada esdeveniment d'un reenviament de `turn.subscribe` compta; un sol reenviament pot ser més llarg, així que qualsevol torn es pot recuperar), quan n'arriba un altre. Ha de reconnectar i fer `turn.subscribe` des de l'últim `seq` que té.
- `1011` si hi ha un error intern.

L'*handshake* i cada missatge del client (amb `type`) comproven la sessió. Només `turn.start` i `turn.cancel`, que són accions del propietari, compten com a activitat. L'*handshake*, el `ping` i el `turn.subscribe` (que el client envia sol després de reconnectar) la comproven només en lectura: no allarguen la caducitat per inactivitat. Així, una pestanya oberta sense ús no manté la sessió viva, encara que es reconnecti.

### Client → servidor

```jsonc
{"type": "turn.start", "request_id": "uuid", "text": "…", "mode": "debate",
 "target": "claude", "conversation_id": null,
 "options": {"debate": {"rounds": 2, "consensus_threshold": 85, "synthesizer": "claude"},
             "use_cache": true},
 "models": {"claude": "opus", "chatgpt": "gpt-6-sol"},
 "attachments": [12, 13]}
{"type": "turn.cancel", "request_id": "uuid"}
{"type": "turn.subscribe", "request_id": "uuid", "after_seq": 12}   // després d'una reconnexió
{"type": "ping", "t": 1727450000000}
```

`mode`, `target`, `options` (també parcials) i `models` són opcionals: s'apliquen els `RuntimeSettings`. A `models` (i als `RuntimeSettings`), `null` o `""` vol dir el model per defecte; els identificadors es netegen d'espais. Límit: 3 torns simultanis i un de sol per conversa.

`attachments` (opcional) són els `id` dels adjunts pujats, en l'ordre en què van a la pregunta: com a molt 5, cadascun un sol cop, enters d'1 a 2^63 − 1. Si no, `error` amb `code: "invalid"` i el `request_id`, i el torn no comença. Un adjunt que no existeix, o uns adjunts que sumen més de 20 MB, donen `turn.failed` amb `kind: "invalid"` (`L'adjunt 12 no existeix.`), sense `turn.started`, i no es desa res. El mateix passa si un adjunt s'esborra mentre el torn es prepara (des d'una altra pestanya, o l'escombrada d'un adjunt no enviat de fa més de 24 h): la pregunta es desa amb els seus adjunts en una sola transacció, abans de `turn.started`, i una conversa nova que el torn acabava de crear s'esborra. Les respostes i la síntesi reben els adjunts sencers; les revisions d'un debat reben els PDF com diu `pdf_in_revisions` dels `RuntimeSettings` (el valor de quan comença el torn), excepte un PDF del qual no s'ha pogut extreure cap text (un d'escanejat), que hi va sencer.

La pregunta (`text`) pot tenir com a molt 100.000 caràcters. Una de buida o de més llarga dona `turn.failed` amb `kind: "invalid"`, sense `turn.started`, i no es desa res.

Cap missatge del client no pot passar de 524.288 caràcters (512 × 1024). El servidor no llegeix un missatge més llarg: respon `{"type": "error", "code": "too_large", "message": "El missatge és massa gran."}` sense `request_id`, perquè no sap de quin torn és, i el torn no comença. Un client no n'ha d'enviar cap: la resposta no li diria quin torn s'ha quedat sense començar. Una pregunta dins del seu límit només el pot superar si la major part són caràcters de control, que el JSON escriu amb 6 caràcters cadascun.

### Servidor → client

En connectar: `{"type": "hello", "version": string, "providers": [ProviderStatus], "fx": FxRate, "active_turns": [{"request_id", "conversation_id" (null fins al turn.started d'una conversa nova), "last_seq"}]}`. `version` és la versió del paquet `agentic-os`, la mateixa que mostra `agentic-os --version`. `active_turns` només inclou els torns en curs, i no els d'una conversa esborrada.

Cada esdeveniment d'un torn porta `request_id` i `seq` (enter creixent dins del torn, començant per 1). El servidor guarda els esdeveniments dels torns en curs i dels acabats fa menys de 5 minuts: `turn.subscribe` reenvia els que tenen `seq > after_seq` i després continua en directe; si el torn no existeix, o la seva conversa s'ha esborrat, respon `{"type": "turn.unknown", "request_id"}`. Un `turn.cancel` d'un torn que el servidor no té (no ha existit mai, va acabar fa més de 5 minuts o era d'una conversa esborrada i ja ha acabat) també rep `turn.unknown`; el d'un torn que ja ha acabat, però que encara es guarda, no rep cap resposta. Una connexió rep cada torn una sola vegada: un `turn.subscribe` d'un torn que la connexió ja rep (perquè l'ha començat o ja s'hi ha subscrit) s'ignora, sense resposta, ja que ja té tots els esdeveniments des del primer `after_seq`. **Un torn continua encara que es talli la connexió**; només `turn.cancel` l'atura.

| `type` | Camps | Significat |
| --- | --- | --- |
| `turn.started` | `conversation_id`, `turn_id`, `mode`, `new_conversation` | Pregunta desada |
| `phase` | `phase` (`answer`, `revision`, `synthesis`, `compaction`), `round` | Canvi de fase (`compaction` pot arribar abans de `turn.started`) |
| `stream.started` | `stream_id`, `agent`, `kind`, `round`, `model` | Un model comença a respondre |
| `stream.delta` | `stream_id`, `section` (`text`, `critique`, `answer`), `text` | Fragment de text |
| `pdf.check` | `attachment_id`, `name`, `state` (`checking`, `checked`, `unchecked`), `claude_pages`, `hidden_pages`, `unchecked_pages` (números de pàgina, com a `PdfReading`), `reused`, `usage` (`Usage` o `null`), `reason` (text o `null`) | Claude contrasta un PDF de la pregunta per a ChatGPT amb la subscripció (vegeu «Contrast de Claude» a «Adjunts»). `checking` quan Claude comença a contrastar-lo, abans de la primera crida de ChatGPT, que l'espera (no n'hi ha si el contrast ja estava desat, si no es pot fer o si el temps s'acaba abans que comenci: aleshores només arriba el final); després `checked` (almenys una pàgina contrastada) o `unchecked` (cap: la comprovació ha fallat, Claude no l'ha volgut fer, ha trigat massa, el PDF no s'ha pogut analitzar, no hi ha Claude o és el de demostració). `reused`: el contrast d'un torn anterior, sense cap crida en aquest. `usage`: el que han facturat les crides d'aquest torn per a aquest PDF, amb el cost (`null` mentre contrasta i si és reutilitzat). `reason`: per què queden pàgines sense contrastar, en català (`null` si no en queda cap). Si el torn es cancel·la o falla mentre Claude contrasta un PDF, no arriba cap `pdf.check` final per a aquell PDF: el client el dona per interromput. Forma part dels esdeveniments del torn (amb `seq` i reenviat per `turn.subscribe`) |
| `stream.completed` | `stream_id`, `message_id`, `usage`, `latency_ms`, `ttft_ms`, `agreement`, `unchanged`, `cost_basis`; opcionals: `truncated` (només quan és `true`), `finish_reason`, `unchanged_note` i `pdf_reading` (només quan hi són) | Resposta acabada i desada. Amb `truncated: true` és una resposta tallada, i `finish_reason` en diu el motiu. `unchanged_note` és la nota curta d'una revisió `UNCHANGED`. `pdf_reading` (`PdfReading[]`) diu com ha llegit ChatGPT els PDF quan no els pot obrir. Tots valen el mateix que als camps de `meta` del missatge desat, així que la vista en directe i la recarregada coincideixen |
| `stream.failed` | `stream_id`, `error: {kind, message}`; opcional: `usage` | Aquell model ha fallat (el torn pot continuar amb l'altre). Una negativa del model arriba com a `kind: "invalid"` amb el seu propi missatge; el text que s'hagués emès abans no es desa. `usage` és el que va facturar la crida fallida, amb el cost (una negativa, una resposta buida, el límit de sortida esgotat sense text); només hi és quan se'n sap una facturació |
| `turn.completed` | `conversation_id`, `turn_id`, `final_message_ids`, `usage`, `savings`, `consensus`, `cached` | Torn acabat |
| `turn.failed` | `error: {kind, message}`, `usage` | Torn avortat. `usage` és el total del torn fins aleshores, el mateix d'`outcome.usage` (zero si ha fallat abans de cap crida) |
| `turn.cancelled` | `usage` | Cancel·lat per l'usuari (o perquè el servidor s'atura). `usage` és el que el torn havia gastat, el mateix d'`outcome.usage`. Arriba quan el torn s'ha aturat i ha desat el resultat; un `turn.cancel` repetit mentrestant no fa res |

Altres: `{"type": "pong", "t"}` (retorna el mateix `t`) i `{"type": "error", "code", "message", "request_id"?}` per a missatges invàlids o límits (`code`: `invalid`, `busy`, `duplicate`, `unavailable`, `too_large`, `internal`; `request_id` quan es rebutja un `turn.start`). Un `conversation_id` fora de l'interval 1 – 2^63 − 1 dona `invalid`. Un missatge amb text que no es pot codificar en UTF-8 (un substitut solitari, `\ud800`, en qualsevol clau o valor) dona `invalid` amb `El missatge conté text que no és UTF-8 vàlid.` i el `request_id` si aquest és vàlid; no se n'usa res.

`savings` = `{"cache", "compaction", "early_stop", "unchanged", "total", "cost_usd"}` (tokens processats estimats estalviats i el seu valor aproximat: les respostes conservades al preu de sortida del seu model, la compactació al preu d'entrada de les crides que portaven el context, les rondes omeses al cost mitjà de les revisions del torn i un encert de memòria cau al cost del torn original sencer, cada crida i cada intent declinat abans d'un fallback a les tarifes actuals del seu model; `null` si no se'n pot posar preu a cap). `consensus` = `{"reached": bool, "round": int, "scores": {"claude": int, "chatgpt": int}}` o `null` fora del mode debat.

### Ordre típic d'un debat

1. `turn.started` → `phase(answer, 0)` → dos `stream.started` (Claude i ChatGPT en paral·lel) amb els seus `stream.delta` (`section: "text"`) i `stream.completed`. Si ChatGPT funciona amb la subscripció i la pregunta porta PDF, abans del `stream.started` de ChatGPT arriben els `pdf.check` de cada PDF (`checking` i, en acabar el contrast, `checked` o `unchecked`) mentre Claude ja respon.
2. Per a cada ronda `r`: `phase(revision, r)` → dos fluxos amb `section` `critique` i després `answer`; `stream.completed` porta `agreement`.
3. Si tots dos arriben al llindar de consens, s'aturen les rondes (estalvi `early_stop`).
4. `phase(synthesis, r)` → un flux de l'agent sintetitzador → `turn.completed`.
