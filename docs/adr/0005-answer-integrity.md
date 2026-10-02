# 0005. Integritat de les respostes

- Estat: Proposat
- Data: 2026-09-28

## Context

La validació de l'auditoria del 28 de setembre de 2026 (punts 10 i 17, i problemes nous N1, N2, N3, N4 i N14) va mostrar que el sistema no sabia distingir una resposta completa d'una de tallada o d'una negativa. La causa comuna era triple.

**El contracte dels proveïdors no ho podia dir.** `GenerationResult` només portava text i ús. Per això:

- Una resposta tallada pel límit de sortida es desava com a completa i entrava a la memòria cau de torns durant 7 dies. Passava amb `response.incomplete` d'OpenAI, amb `stop_reason: "max_tokens"` d'Anthropic i amb un flux de l'API de Claude que acabava sense `message_stop`.
- Una negativa d'OpenAI es mostrava com a «resposta buida». Si abans hi havia text parcial, aquest fragment es desava com a resposta.
- Un tall de connexió a mig flux de l'API de Claude sortia com a error intern sense reintent.
- Codex, quan reintenta un flux caigut després d'haver emès text, torna a generar tota la resposta en un element nou. El resultat era la resposta duplicada.

**El pressupost de sortida no significava el mateix a tot arreu.**

- A l'API d'OpenAI, `max_output_tokens=8000` inclou el raonament. Si el raonament l'esgotava, sortia una «resposta buida» amb 8.000 tokens facturats.
- L'API de Claude l'apujava en silenci a 16.000 quan hi havia pensament.
- Les CLI no el rebien.

**L'analitzador de revisions prenia per estructura les etiquetes escrites com a contingut.** Una resposta que explica `<answer>` en codi o en XML quedava tallada a mitja frase. A més, «UNCHANGED (la meva resposta ja ho cobreix)» substituïa tota la resposta per la nota.

La revisió de la primera versió d'aquesta decisió hi va afegir tres coses:

- La CLI de Claude 2.1.283, provada contra una API simulada en local, envia la represa de després de `max_tokens` uns 10 ms després del final de la resposta. El proveïdor la matava massa tard i amb SIGTERM: la represa sortia sempre (6 de 6 intents) i no se'n comptava el cost.
- La mateixa CLI, després d'una negativa, torna a preguntar pel seu compte. El proveïdor ajuntava el fragment negat amb la resposta nova i ho desava com una resposta completa, que a més entrava a la memòria cau.
- La primera regla de l'analitzador per a les etiquetes de tancament desava etiquetes i text de més quan el model escrivia un encapçalament entre seccions («Here is my revised answer:») o un comiat al final, i perdia la resposta nova en dos casos que abans funcionaven.

## Decisió

### Contracte (`providers/base.py`)

- `GenerationResult` té `truncated` (bool) i `finish_reason`. Els valors de `finish_reason` són `"max_tokens"`, `"content_filter"`, `"incomplete"`, `"interrupted"` o un valor propi del proveïdor. Una resposta tallada és una resposta parcial útil, mai una de completa.
- `RefusalError(ProviderError)` és compartit per tots els proveïdors: tipus `invalid`, no reintentable. Porta l'ús facturat (`usage`), el model, la categoria si n'hi ha i el text de la negativa, netejat i escurçat. El text emès abans d'una negativa no es converteix mai en resposta.
- Qualsevol `ProviderError` pot portar `usage` i `model`. Així, una crida que falla però que s'ha facturat conserva el seu cost; per exemple, una que esgota el límit de sortida sense escriure res.
- `GenerationRequest.max_output_tokens` és el màxim de tokens de sortida **facturats** de la crida, amb el raonament inclòs. Els adaptadors l'envien tal com és i no l'apugen mai.
- `GenerationRequest.reasoning` (`"default"` o `"off"`) és la política de raonament i va a part del pressupost.
- Pressupostos explícits al motor (`EngineConfig`):
  - respostes, revisions i síntesis: 16.000 tokens facturats, amb el raonament per defecte;
  - resums de compactació: 2.000, amb el raonament `"off"`.

### Motor (`orchestrator/`)

- Una resposta tallada es desa amb `meta.truncated: true` i `meta.finish_reason`, i `stream.completed` porta `truncated: true` i el mateix `finish_reason`. Una revisió `UNCHANGED` amb nota porta `unchanged_note` a `stream.completed` i a `meta`. Així la vista en directe i la recarregada diuen el mateix. Un torn amb algun missatge tallat **no entra mai** a la memòria cau de torns.
- En un debat, una resposta tallada continua servint per a les revisions i la síntesi, però el prompt la marca com a incompleta. A l'agent que la va escriure se li demana que la completi en lloc de respondre `UNCHANGED`. Una síntesi degradada conserva la marca de la resposta que reutilitza.
- Una resposta tallada sense cap text falla amb un missatge que diu que s'ha esgotat el límit de sortida. L'ús facturat es registra igualment.
- Un resum de compactació tallat no es fa servir, perquè substituiria els missatges antics per sempre. Se'n registra el cost i es prova l'altre proveïdor.
- Una negativa es comunica amb el seu propi missatge (no «resposta buida») i el seu ús facturat es registra com fins ara (`failed_call_usage`).
- `CACHE_KEY_VERSION` passa a 3. Les entrades anteriors poden contenir respostes tallades, duplicades o malmeses.

### Proveïdors

- **API d'OpenAI:**
  - `response.refusal.delta`/`done` o una part `refusal` de `response.output` llancen `RefusalError`, també si abans hi ha hagut text.
  - `response.incomplete` dona un resultat tallat amb el motiu d'`incomplete_details` (`max_output_tokens` passa a ser `"max_tokens"`). Sense text, és un error amb l'ús facturat.
  - Amb el raonament `"off"`, s'usa l'esforç més baix que accepta el model: `none` a GPT-6 Sol i Luna, `minimal` als primers GPT-5 i `low` a la resta.
- **API de Claude:**
  - `max_tokens` és el pressupost exacte. S'elimina el mínim de 16.000.
  - Si el pressupost no dona per al pressupost mínim de pensament (1.024), el model respon sense pensar.
  - Amb el raonament `"off"`, el pensament queda desactivat.
  - `stop_reason: "max_tokens"` dona un resultat tallat.
  - Un error de transport d'httpx2 a mig flux, o un flux sense l'esdeveniment final, és un error `unavailable` reintentable: «La resposta de Claude s'ha interromput».
- **CLI de Claude:**
  - El pressupost arriba a la CLI com a `CLAUDE_CODE_MAX_OUTPUT_TOKENS`. És un valor que calcula el proveïdor, com `DISABLE_AUTOUPDATER`, i mai s'hereta de l'entorn de l'aplicació: la llista tancada de variables no s'amplia.
  - El pressupost i el raonament formen part de la clau del grup de processos calents.
  - Amb el raonament `"off"`, s'afegeix `--thinking disabled`.
  - La CLI 2.1.283 no accepta dues menes de resposta i, uns 10 ms després del final, envia una petició nova amb un missatge seu, que torna a facturar tot el context:
    - després de `stop_reason: "max_tokens"`, una represa («Output token limit hit. Resume directly…»), fins a 3 vegades; el `result` només porta el text de l'última;
    - després de `stop_reason: "refusal"`, un segon intent («Your response above was stopped by a safety classifier…»).
  - Per això, tan bon punt el proveïdor llegeix un `message_delta` amb un d'aquests dos motius, mata el grup de processos amb SIGKILL, abans de llegir res més i abans de lliurar res al motor. SIGTERM no serveix: la CLI s'atura ordenadament i, mentrestant, envia la petició.
  - Amb `max_tokens`, la crida retorna el text com a resultat tallat, amb l'ús d'aquella petició.
  - Amb una negativa, la crida llança `RefusalError` amb l'ús d'aquella petició, el model i la categoria de `stop_details`. Un `result` amb `is_error` i `stop_reason: "refusal"` també és una negativa, amb l'ús de tot el torn de la CLI.
- **Codex (app-server 0.157.1):**
  - El text es guarda per element, i el text final només inclou els elements que no són comentari i que han rebut `item/completed`.
  - Un `error` amb `willRetry` després d'haver emès text atura la crida amb «La resposta de ChatGPT s'ha interromput». Aquest error és reintentable: el motor només el reintenta si encara no ha mostrat res.
  - El protocol no té cap camp per al límit de sortida. La crida atura el torn (`turn/interrupt`) quan el text visible estimat (caràcters / 4) supera el pressupost i retorna un resultat tallat amb l'ús que informa Codex. Aquesta aturada es distingeix de l'estat `interrupted` que no ha demanat la crida.
- **Fake:** respecta el pressupost (talla i marca la resposta) i, per a les proves, pot tallar o negar-se per propòsit.
- **CLI falsa de les proves de la CLI de Claude:** reprodueix l'ordre d'esdeveniments de la CLI real davant de `max_tokens` i d'una negativa, s'atura ordenadament amb SIGTERM com la real i anota si arriba a continuar pel seu compte. Les proves comproven que no hi arriba mai.

### Analitzador de revisions (`orchestrator/sections.py`)

- Una etiqueta dins de codi és text: blocs amb tanques ```` ``` ```` o `~~~`, amb qualsevol sagnat, i fragments de codi en línia tancats a la mateixa línia.
- Un bloc de codi que no es tanca mai no era codi. Des de la primera etiqueta que hi apareix, el text es reté fins que el bloc es tanca (era codi) o s'acaba la resposta (es torna a llegir com a text normal). El codi sense etiquetes es mostra de seguida.
- L'etiqueta d'obertura de la secció que ja és oberta és text. Una etiqueta de tancament d'una secció que no és oberta, també.
- Una etiqueta de tancament de la secció oberta la tanca si després, sense comptar els espais, ve l'obertura d'una secció o el final de la resposta. Després de `</answer>` també la tanca una línia d'acord solta, com «Agreement: 80».
- Quan després de l'etiqueta de tancament ve un altre text, la decisió espera fins a la següent etiqueta:
  - la mateixa etiqueta de tancament: la primera era text (una crítica o una resposta que l'esmenta) i la secció continua;
  - l'obertura d'una secció o el final de la resposta: era el tancament real, i el text del mig es descarta. És un encapçalament com «Here is my revised answer:» o un comiat. Si la resposta no té cap `<answer>`, aquest text és la resposta sense etiquetes, com abans.
  - Després de `</answer>`, l'espera dura com a molt 300 caràcters (sense comptar els espais). Un text més llarg és part de la resposta, i l'etiqueta també.
- Una resposta és `UNCHANGED` quan la primera línia no buida és el marcador, que pot anar entre `*`, `_`, accents greus o cometes i acabar en `.` o `!`. Sol, s'accepta en qualsevol combinació de majúscules i minúscules. Per portar una nota ha d'estar escrit en majúscules, com demana el prompt, i la nota ha d'anar a la mateixa línia, després d'un guió, dos punts, un parèntesi o un claudàtor, o bé d'un punt o un signe d'exclamació i un espai, i tenir com a molt 200 caràcters. La nota es desa a `meta.unchanged_note` i el contingut és la resposta anterior. Qualsevol altra cosa és una resposta normal: «Unchanged: el BCE manté el tipus al 2 %» és una resposta, i «UNCHANGED» seguit d'un paràgraf també.
- L'analitzador només reté el text que encara no sap on col·locar. Qualsevol manera de trossejar el flux dona el mateix resultat, i el que es mostra en directe és el que es desa. Ho comproven proves amb talls aleatoris.

## Alternatives considerades

- **`max(límit, 16.000)` quan hi ha raonament:** era la correcció que proposava l'auditoria. El propietari la va descartar perquè barreja el pressupost facturat amb el que el model necessita per pensar. El pressupost facturat, la política de raonament i el text visible són conceptes separats, i comptar fragments de text no és mai facturació.
- **Tractar tota resposta tallada com un error:** es llençaria text útil que ja s'ha pagat. Una resposta parcial marcada és més útil que cap.
- **Reintentar automàticament una resposta tallada:** podria costar el doble sense cap garantia de cabre-hi. El propietari pot tornar a preguntar.
- **Codex: deixar que reintenti i quedar-se amb l'últim element:** el text parcial ja s'ha enviat al navegador i, en una revisió, ja l'ha llegit l'analitzador. No es pot retirar.
- **Codex: només documentar que no hi ha límit:** amb la subscripció, una resposta desbocada consumeix quota de 5 hores. Una aturada aproximada és millor que cap.
- **Analitzador: la resposta arriba fins a l'últim `</answer>`:** caldria retenir text sense límit després de cada menció en prosa, i el text en directe s'aturaria. La regla adoptada espera com a molt 300 caràcters.
- **Analitzador: un tancament seguit de text és sempre text (la primera versió d'aquesta decisió):** un encapçalament entre seccions o un comiat al final quedaven desats dins de la crítica o de la resposta, amb l'etiqueta i tot.
- **Analitzador: una nota d'`UNCHANGED` en un paràgraf a part:** «UNCHANGED» seguit d'una correcció en un altre paràgraf perdia la correcció, que quedava com a nota.
- **CLI de Claude: SIGTERM i esperar que la CLI plegui:** la CLI s'atura ordenadament en uns 20 ms i, mentrestant, envia la petició de represa. Passava en totes les proves.
- **CLI de Claude: desactivar les represes:** la versió 2.1.283 no té cap opció ni variable per fer-ho. El màxim de 3 represes és fix.

## Conseqüències

- Una resposta tallada es veu com a incompleta, amb el motiu, en directe i en recarregar, i no es torna a servir des de la memòria cau.
- Les negatives tenen un missatge propi i el seu cost queda registrat. El text parcial d'abans de la negativa no es desa mai.
- Els pressupostos són els mateixos per a tots els proveïdors i es respecten exactament a les dues API. A la CLI de Claude, el límit s'aplica a cada petició que fa la CLI.
- Limitacions conegudes:
  - L'aturada de Codex és aproximada: no compta el raonament i el model pot generar uns quants tokens més abans que arribi la interrupció. L'ús d'una petició interrompuda pot quedar sense informar.
  - Aturar la CLI de Claude és una cursa: la petició nova surt uns 10 ms després del final de la resposta. El SIGKILL arriba abans en totes les proves fetes amb la CLI real i una API simulada en local (cap represa en 15 respostes tallades ni cap segon intent en 24 negatives; abans sortien sempre). Si una versió nova de la CLI fos més ràpida, una petició podria sortir igualment, i el seu cost no es comptaria.
  - Una etiqueta escrita en prosa i seguida del tancament de la secció continua sent ambigua. Una crítica que esmenta `</critique>` i continua fins a `<answer>` sense tornar a tancar-se perd el text de després de l'esment. Una resposta tallada que esmenta `</answer>` en els seus últims 300 caràcters perd el text de després de l'esment.
  - Una etiqueta dins d'un bloc de codi atura el text en directe fins que el bloc es tanca.
- `unchanged_note` i `truncated`/`finish_reason` formen part del protocol, a `meta` i a `stream.completed` ([PROTOCOL.md](../PROTOCOL.md)). La interfície els mostra.
- Aquesta decisió és una proposta fins que el propietari l'accepti.
