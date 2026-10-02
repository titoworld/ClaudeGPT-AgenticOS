# 0006. Revisió de la configuració

- Estat: Proposat
- Data: 2026-09-28

## Context

La validació de l'auditoria del 28 de setembre de 2026 (punts 11 i 21, i problemes nous N5, N11 i N13) va mostrar dues coses. La configuració del tauler es podia perdre sense cap avís, i la taula de preus no sabia quin era el preu base d'un model amb un preu propi.

**El client no sabia quina versió de la configuració editava.**

- `PUT /api/settings` substitueix tota la configuració, i les claus que falten prenen el valor per defecte.
- L'SPA llegia la configuració una sola vegada, en entrar. Si la càrrega fallava, s'empassava l'error i continuava amb la configuració integrada. El calaix de configuració la copiava en obrir-se i no la tornava a llegir.
- Per això, desar després d'una càrrega fallida, o amb el calaix obert abans que arribés la configuració, substituïa preus, pressupostos, models i tipus de canvi pels valors integrats (punt 11). La primera pregunta també podia sortir amb el mode integrat.
- Un calaix obert en una pestanya o en un dispositiu desfeia en silenci el que s'havia desat en un altre (N5).
- Desar qualsevol ajust tornava el compositor als valors per defecte, encara que no haguessin canviat (N11).

**La taula de preus perdia el preu base.** `GET /api/pricing` retorna la taula efectiva, on un preu propi substitueix la fila per defecte del mateix model. La interfície només buscava el preu base entre les files `default`. Després de desar un preu propi, el model sortia com a «afegit», «Restaura» es convertia en «Elimina» i «Afegeix» omplia zeros (punt 21).

**La interfície i el servidor no normalitzaven els ids de la mateixa manera.** La taula comparava els ids amb `trim` i minúscules, i el servidor amb `normalize_model`, que també treu el prefix del proveïdor, el context i la data. Afegir `anthropic/claude-opus-5` creava una fila nova «afegida» a zeros que el servidor aplicava a tota la família (N13).

## Decisió

### Revisió de la configuració

- `RuntimeSettings` té `revision`, un enter ≥ 0 que compta els desaments. Val 0 en una base de dades on no s'ha desat mai, i cada desament correcte la puja en 1.
- Una configuració desada val sempre 1 o més. Una que va desar una versió anterior, sense revisió, compta com a revisió 1. Així, la revisió 0 és només la de la configuració integrada, la que té un client que encara no ha llegit la del servidor. Un desament basat en aquesta configuració rep `409` si ja hi ha una configuració desada, també abans del primer desament amb aquesta versió.
- La revisió es desa dins del mateix JSON de la configuració (la fila `runtime` de la taula `settings`). No cal cap migració d'esquema: la fila es llegeix i s'escriu sencera, i un JSON antic sense `revision` es llegeix com a revisió 1. Es conserva en reiniciar.
- La revisió no torna mai enrere:
  - Qualsevol escriptura la puja, també les que no comparen (`put_runtime_settings` sense `base_revision`, que fan servir les eines i les proves). La revisió desada és sempre l'anterior + 1, sigui quina sigui la que porti la configuració que s'escriu.
  - Si la configuració desada ja no és vàlida i es llegeix com la integrada, conserva la revisió desada. Una revisió desada que no és vàlida, o que és 0, només la pot deixar una edició a mà i compta com a 1.
  - Així, un client basat en una revisió antiga no pot tornar a coincidir mai amb l'actual.
- `GET /api/settings` retorna la revisió amb la resta de la configuració.

### Desament amb comparació (`PUT /api/settings`)

- El cos porta tota la configuració i `revision`, que és la revisió de la configuració que el client va llegir i ha editat. És obligatòria.
- Ordre de les comprovacions:
  1. El cos ha de ser un objecte JSON; si no, `422`.
  2. Si falta `revision`: `422` amb «Cal indicar «revision» (la revisió de la configuració en què es basa el canvi). Torna a carregar la pàgina.». És el que rep una pestanya que encara té una versió antiga de l'SPA.
  3. Si `revision` no és un enter ≥ 0: `422` amb ««revision» ha de ser un enter igual o més gran que 0.».
  4. La resta de camps es validen com fins ara (`422`).
  5. Si la revisió no és l'actual: `409` amb `{"detail": "La configuració ha canviat en una altra pestanya o dispositiu. Revisa-la i torna-la a desar.", "settings": <la configuració actual>}`. No es desa res.
  6. Si és l'actual, es desa amb la revisió + 1, i la resposta (`200`) és la configuració desada.
- La comparació i l'escriptura són un sol *compare-and-swap*. Es llegeix, es compara i s'escriu dins de la mateixa transacció d'escriptura (`BEGIN IMMEDIATE`). El bloqueig de la connexió exclou les altres tasques del procés, i el bloqueig d'escriptura de SQLite exclou els altres processos. De dues peticions basades en la mateixa revisió, una guanya i l'altra rep `409`.
- Les claus que falten, fora de `revision`, continuen prenent el valor per defecte. El client ha d'enviar sempre la configuració sencera.

### Taula de preus (`GET /api/pricing`)

- Cada fila porta `key`: l'id normalitzat (`normalize_model`) amb què el servidor compara els models. Dues files no tenen mai la mateixa `key`.
- Cada fila porta `default`:
  - a una fila `custom` que substitueix un preu per defecte amb la mateixa `key`, aquell preu per defecte (`{input, output, cache_read, cache_write}`);
  - `null` a totes les altres, també a un preu propi d'un id més llarg que només comparteix el prefix amb un preu per defecte.
- Així, la interfície pot mostrar el preu base, restaurar-lo i omplir una fila nova amb el preu conegut, sense haver de conèixer la taula per defecte.
- La taula continua sent exactament la que fa servir el motor per calcular els costos.

### Normalització compartida

- `tests/fixtures/model_ids.json` conté ids en brut i la `key` que els dona `normalize_model`. Pytest comprova `normalize_model` amb aquests vectors, i vitest comprova amb els mateixos vectors el port a TypeScript que fa servir la interfície. Si una de les dues bandes canvia, les proves fallen.
- Els vectors nous s'hi afegeixen. Els que ja hi són no es canvien si no es canvien alhora les dues implementacions.

### Client (`web/`)

- L'app sap si la configuració s'està carregant, és a punt o no s'ha pogut carregar. No s'envia cap pregunta ni es desa res fins que és a punt. Si la càrrega falla, l'error es veu, es pot tornar a provar i es reintenta sol. Una petició de la configuració o dels preus que no respon en 15 segons compta com a fallada, perquè una connexió encallada no deixi l'app esperant fins que es torni a carregar la pàgina.
- Els valors per defecte del compositor només s'apliquen als camps que el propietari no ha tocat. Després de desar, només s'apliquen els que han canviat.
- El calaix de configuració torna a llegir la configuració i els preus cada vegada que s'obre, i envia la revisió en desar. Davant d'un `409`, torna a omplir el formulari amb la configuració del servidor i avisa que ha canviat en un altre lloc.
- La taula de preus fa servir `key` i `default`:
  - mostra el preu base;
  - «Restaura el preu per defecte» torna a posar la fila per defecte;
  - «Afegeix» omple el preu conegut per a la `key`;
  - un id amb la mateixa `key` que una fila existent edita aquella fila, i la taula diu per què (per exemple, que el servidor tracta `anthropic/claude-opus-5` com a `claude-opus-5`).

## Alternatives considerades

- **`ETag` i `If-Match` (412 Precondition Failed):** és el mecanisme estàndard d'HTTP, però la resposta 412 no porta la configuració actual, i caldria una altra petició per recuperar-la. El client també hauria de gestionar capçaleres a més del cos JSON. Un camp al cos és més senzill i es veu a la mateixa configuració.
- **Desar només els camps canviats (un `PATCH` que fusiona):** no esborraria el que no s'ha tocat, però de dues edicions del mateix camp se'n continuaria perdent una sense avís. A més, hi ha camps que depenen els uns dels altres (dues claus de preus no poden ser el mateix model normalitzat), i una fusió automàtica podria desar una configuració que cap client no ha vist.
- **Una marca de temps (`updated_at`) en lloc d'un comptador:** dos desaments dins del mateix mil·lisegon, o un rellotge que torna enrere, la farien ambigua. Un comptador és exacte.
- **Una columna o una taula noves per a la revisió:** caldria una migració d'esquema sense cap avantatge, perquè la configuració ja es llegeix i s'escriu sencera dins d'una sola transacció.
- **Una revisió opcional (sense revisió, el `PUT` substitueix com abans):** una pestanya amb una versió antiga de l'SPA, o un client que se n'oblidés, continuaria desfent canvis en silenci. Amb la revisió obligatòria, una SPA antiga rep un `422` que li demana que torni a carregar la pàgina.
- **Només tornar a llegir la configuració en obrir el calaix, sense comprovar res al servidor:** no protegeix dos calaixos oberts alhora.
- **Preus: una ruta a part amb els preus per defecte, o que el client els conegui:** duplicaria al client la taula i la regla de substitució. `default` a la fila és el mínim que necessita la interfície.
- **Preus: retornar també les files per defecte substituïdes:** la taula ja no seria exactament la que fa servir el motor, i es perdria la garantia que el preu que es veu és el que es cobra.

## Conseqüències

- Una pestanya o un dispositiu ja no pot desfer en silenci el que s'ha desat en un altre. Rep un `409`, veu la configuració actual i ha de tornar a desar. No hi ha fusió automàtica: el propietari torna a fer els canvis que calgui.
- Una SPA antiga, en memòria cau d'abans de l'actualització, no pot desar fins que es torna a carregar (`422` amb un missatge que ho diu).
- Els desaments interns sense comparació (eines i proves) també invaliden les edicions obertes.
- La revisió compta desaments, però no és un historial: no permet recuperar versions anteriors.
- Si es torna a una versió anterior de l'aplicació i s'hi desa la configuració, la revisió es perd, perquè aquella versió no la coneix. En tornar a actualitzar, comença de nou per 1, i una pestanya que hagués quedat oberta des d'abans podria tornar a coincidir amb una revisió nova. Cal que passin totes tres coses alhora, així que és molt improbable.
- La normalització dels ids existeix en dos llocs, en Python i en TypeScript. Els vectors compartits els mantenen iguals, però un canvi a `normalize_model` s'ha de fer als dos llocs i als vectors.
- `revision`, `key`, `default` i el `409` formen part del protocol ([PROTOCOL.md](../PROTOCOL.md)). Els tipus del client són a `web/src/lib/protocol.ts`.
- Aquesta decisió és una proposta fins que el propietari l'accepti.
