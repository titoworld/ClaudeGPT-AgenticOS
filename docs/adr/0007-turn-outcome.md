# 0007. Resultat del torn

- Estat: Proposat
- Data: 2026-09-28

## Context

La validació de l'auditoria del 28 de setembre de 2026 (punts 9 i 14, i problema nou N10) va mostrar que l'estat final d'un torn només existia com a esdeveniment efímer del WebSocket. No es desava enlloc, i en recarregar la conversa el client l'havia d'endevinar a partir dels missatges desats.

- **Punt 9. El cost d'una fallada tardana es perdia en recarregar.** En un duel, una crida facturada que fallava després que l'altre agent hagués desat la resposta no quedava a cap missatge: una negativa de l'API de Claude amb ús, o una resposta buida facturada de qualsevol proveïdor. L'ús de les crides sense missatge (`unstored_usage`) s'escrivia als missatges finals en desar-los, i els missatges no es tornen a escriure. En directe, el torn valia 0,029316 USD; recarregat, 0,003116. Només afectava el torn recarregat: les estadístiques globals surten de la taula d'ús i eren correctes. Ja constava com a limitació a PROTOCOL.md.
- **Punt 14. Un duel cancel·lat es reconstruïa com a completat.** Si un agent havia desat la resposta i després arribava una cancel·lació, una aturada o un reinici, les dades desades eren idèntiques a les d'un duel completat amb un agent fallit. També es perdia el motiu de la fallada d'un agent.
- **N10. El cost d'un torn fallit no sortia al torn.** Ni en directe (`turn.failed` no portava ús) ni en recarregar, tot i que comptava a les estadístiques.

## Decisió

### El resultat es desa a la pregunta

- La pregunta és el registre del torn (el seu id és el `turn_id`). El motor la crea amb `meta.outcome = null`.
- Quan el torn acaba, el motor decideix com ha acabat, una sola vegada, i ho escriu a la pregunta **abans** d'emetre l'esdeveniment final. El resultat és:
  - `status`: `completed`, `failed` o `cancelled`.
  - `error` (`{kind, message}`): només si és `failed`, el mateix que porta `turn.failed`.
  - `failures`: les fallades de crida del torn (les de `stream.failed`), en l'ordre en què van passar, amb `{agent, kind, message, round}`. Pot ser buida.
  - `usage`: el total del torn. Inclou totes les crides facturades: els resums de compactació, les crides fallides, els intents declinats abans d'un fallback ([ADR 0008](0008-token-accounting.md)) i les crides que no han desat cap missatge. És el mateix valor que porta l'esdeveniment final.
  - `savings`: el mateix de `turn.completed`. Un torn fallit o cancel·lat no registra estalvis, com fins ara, i hi porta zeros. L'excepció és un torn que es cancel·la quan ja estava desant els estalvis, just abans d'acabar, amb tots els missatges desats: les files d'estalvi s'escriuen senceres (en una tasca pròpia, com el resultat), i el resultat les porta i s'escriu després. Així coincideix amb el que compta el tauler.
  - `consensus`: el d'un debat completat; `null` en els altres casos.
  - `final_message_ids`: els missatges finals desats. En un duel cancel·lat, la resposta que ja s'havia desat.
  - `cached`: si el torn s'ha servit des de la memòria cau de torns.
- El contracte del magatzem (`orchestrator/store.py`) té `set_turn_outcome(question_message_id, outcome)`:
  - SQLite l'escriu amb `json_set(meta, '$.outcome', json(?))` (JSON1, que ja es fa servir a les estadístiques). Queda com a objecte JSON, sense tocar les altres claus ni l'`updated_at` de la conversa. No cal cap migració d'esquema.
  - Un id que no és una pregunta, o que ja no existeix perquè s'ha esborrat la conversa, no canvia res.
  - `InMemoryStore` fa el mateix.
- Camins del motor:
  - `completed`: en acabar el torn, també quan se serveix des de la memòria cau.
  - `failed`: a tots els camins que emeten `turn.failed`, també un error intern. Un `CancelledError` que no ve de cap cancel·lació (el llança una crida pel seu compte, per un error d'un proveïdor o d'una biblioteca) és un error intern: en directe arriba `turn.failed`, i el resultat desat diu el mateix.
  - `cancelled`: quan es cancel·la el torn (el propietari l'atura, s'esborra la conversa o el servidor s'atura).
- Cancel·lació:
  - El motor primer atura les crides del torn. Després decideix el resultat i l'escriu en una tasca pròpia, que espera amb `asyncio.shield`. Finalment torna a llançar el `CancelledError`, que sempre es propaga.
  - Un torn es cancel·la una sola vegada. La capa web no torna a cancel·lar un torn que ja s'està aturant: ni amb un segon `turn.cancel` (el propietari que torna a prémer «Atura») ni en aturar-se el servidor. A més, si el consumidor d'`Engine.run` es torna a cancel·lar mentre el torn s'atura, el motor no l'interromp: espera que el torn acabi (que les crides s'aturin, cosa que en una CLI pot trigar uns segons, i que el resultat quedi escrit) i després deixa continuar el `CancelledError`. Així, `turn.cancelled` arriba sempre després del resultat desat, amb el mateix `usage`, i cap escriptura del torn no arriba a una base de dades que el servidor ja ha tancat.
  - L'escriptura segueix protegida amb `asyncio.shield`: si la tasca del torn es tornés a cancel·lar mentre s'escriu el resultat, deixaria d'esperar, però l'escriptura acabaria igualment.
  - Si la cancel·lació arriba mentre s'escriu un resultat ja decidit (`completed` o `failed`), es conserva aquest: un resultat no se sobreescriu mai.
- Si l'escriptura falla, queda registrat al log i el torn no falla. La pregunta es queda amb `null`.

### Esdeveniments

- `stream.failed` porta `usage` opcional: el que va facturar la crida fallida, amb el cost (una negativa, una resposta buida, el límit de sortida esgotat sense text). No hi és si no se sap que s'hagi facturat res.
- `turn.failed` i `turn.cancelled` porten `usage`: el total del torn, el mateix valor que `outcome.usage`. Val zero si el torn falla abans de cap crida.
- `turn.cancelled` l'emet la capa web quan s'acaba la tasca del torn, que no acaba fins que el resultat s'ha desat (vegeu «Cancel·lació»). El motor li fa arribar el resultat amb una funció, `on_outcome` d'`Engine.run`, que crida tan bon punt el decideix.

### Reconstrucció al client (`web/`)

- Si `outcome` hi és, s'usa: l'estat, el total (sense tornar a sumar `compaction_usage` ni `unstored_usage`), les fallades a les targetes dels agents amb el motiu, i un avís si es va cancel·lar.
- Si `outcome` és `null`, el torn no va acabar (una caiguda o un reinici): «Aquest torn no es va completar».
- Si la clau no hi és (torns desats abans d'aquesta decisió), es manté la reconstrucció d'abans.
- Els missatges finals continuen portant `unstored_usage` per a les reconstruccions sense `outcome`. El total d'un torn nou, però, és `outcome.usage`.

## Alternatives considerades

- **Tornar a escriure `unstored_usage` a l'últim missatge final quan arriba una fallada tardana:** resol el punt 9, però no el 14 (un duel cancel·lat i un de completat continuarien sent iguals) ni N10 (un torn fallit no té cap missatge final on escriure-ho).
- **Una taula `turns` nova:** seria més neta per consultar, però caldria una migració d'esquema i un canvi a l'API de converses. La pregunta ja és el registre del torn, viatja amb la conversa i les seves metadades ja es llegeixen amb JSON1.
- **Deduir l'estat de la taula `usage`:** les files no diuen si el torn es va cancel·lar ni per què va fallar, i es conserven encara que s'esborri la conversa.
- **Escriure el resultat després de l'esdeveniment final:** un client que recarregués la conversa de seguida podria llegir `null` d'un torn acabat. S'escriu abans, i el cost és una escriptura curta abans de `turn.completed`.
- **Que el motor emeti `turn.cancelled`:** un cop cancel·lat, el motor no pot emetre res més sense empassar-se la cancel·lació. Per això el resultat arriba a la capa web amb `on_outcome`.
- **Que un segon «Atura», o l'aturada del servidor, interrompin un torn que ja s'atura:** el servidor respondria uns segons abans, però `turn.cancelled` sortiria sense el cost i abans que el resultat estigués desat (una recàrrega just després mostraria el torn com a no completat), i l'escriptura del resultat podria trobar la base de dades ja tancada.

## Conseqüències

- Un torn recarregat mostra el mateix que en directe: l'estat, el total i les fallades. En el cas de l'auditoria, el duel val 0,029316 USD en directe, recarregat i a la taula d'ús.
- Un torn que no va acabar (una caiguda, un reinici) es distingeix d'un d'antic: `null` en lloc d'absent.
- `turn.failed` i `turn.cancelled` mostren en directe el cost del torn.
- Cada torn fa una escriptura més a SQLite (un `UPDATE` petit) abans de l'esdeveniment final.
- En la finestra de pocs mil·lisegons en què s'escriu el resultat, una cancel·lació pot fer que en directe es vegi `turn.cancelled` i que el resultat desat sigui `completed`. Tots els missatges del torn ja s'havien desat.
- Un segon «Atura», o l'aturada del servidor mentre un torn s'atura, no avancen `turn.cancelled`: arriba quan el torn s'ha acabat d'aturar. En aturar-se, el servidor espera els torns que s'estan aturant, tant com trigui cada proveïdor a aturar una crida (com a molt uns segons en una CLI, que primer rep `SIGTERM` i després `SIGKILL`).
- Canvien els contractes interns i el protocol: `Store.set_turn_outcome`, `TurnOutcome` i `TurnFailure` (`orchestrator/events.py`), `on_outcome` d'`Engine.run` i de `TurnRunner`, i `usage` a `stream.failed`, `turn.failed` i `turn.cancelled` ([PROTOCOL.md](../PROTOCOL.md) i `web/src/lib/protocol.ts`).
- Aquesta decisió és una proposta fins que el propietari l'accepti.
