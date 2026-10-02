# 0008. Recompte de tokens i intents declinats

- Estat: Proposat
- Data: 2026-09-28

## Context

La validació de l'auditoria del 28 de setembre de 2026 (punts 7 i 8) va mostrar dos errors de comptabilitat de naturalesa diferent.

**Punt 7. Els recomptes de tokens deixaven fora la memòria cau.** Els imports en diners eren correctes (es calculen per categories), però els comptes i les ràtios no.

- `Usage.total_tokens` era `input + output`, i `input_tokens` és només l'entrada que no ve de la memòria cau: els quatre adaptadors la normalitzen així.
- Els consumidors el feien servir com a «tokens del torn»:
  - l'estalvi d'un encert de la memòria cau de torns (`CachedTurn.tokens`);
  - la parada per consens;
  - el total d'un torn a la interfície;
  - la ràtio d'estalvi del tauler.
- En una crida amb context (3 tokens d'entrada, 100 de sortida, 10.000 de lectures de memòria cau i 20.000 d'escriptures), comptava 103 tokens en lloc de 30.103. La parada per consens comptava 618 tokens en lloc de 180.618.
- Les escriptures de memòria cau, facturades a 1,25 vegades l'entrada, no sortien enlloc: `stats.daily` no les portava.
- Els tokens estalviats no quadraven amb el seu valor (103 tokens valorats a 0,132515 USD), i la ràtio barrejava definicions: mostrava un 84 % quan el real era un 1,8 %.

**Punt 8. Els fallbacks d'Anthropic es cobraven al preu del model final.** Els imports eren erronis.

- Amb els fallbacks del costat del servidor, un model que declina passa la petició a un altre, i la resposta arriba en una sola crida.
- `billed_usage` sumava els intents declinats al mateix `Usage`, i el motor el preuava amb el model que responia.
- D'un Fable 5.1 (10/50 USD per milió de tokens) a un Opus 4.8 (5/25), es registraven 0,25075 USD en lloc de 0,370625 (−32 %), a nom d'Opus 4.8. Una negativa després d'un fallback quedava en 0,055 USD en lloc de 0,11 (−50 %).
- La regla d'Anthropic (guia «Refusals and fallback», secció «Billing and rate limits», consultada el 28 de setembre de 2026): cada intent es factura a les tarifes del model que l'ha executat; `usage.iterations` és el registre del que es factura per intent; l'`usage` de primer nivell només descriu l'intent que ha produït el missatge, i els tokens de models diferents no se sumen mai en un mateix camp.

## Decisió

### Tokens processats

- `Usage.processed_tokens = input + cache_read + cache_write + output`: tot el que la crida ha processat i s'ha facturat. El raonament (`reasoning_tokens`) ja forma part de la sortida a Anthropic, OpenAI i Codex, i no s'hi torna a sumar.
- `Usage.total_tokens` desapareix. Cap consumidor no necessitava «entrada sense memòria cau més sortida».
- Els tokens processats es fan servir a:
  - `CachedTurn.tokens` i l'estalvi de la memòria cau de torns (vegeu «Valor d'un encert de la memòria cau»);
  - la parada per consens: la parella mitjana de revisions del torn, en tokens processats;
  - `is_billed`, que decideix si una crida fallida es va facturar;
  - al client (`web/`): el total d'un torn, `consumed()` i la ràtio del tauler, amb la mateixa definició al numerador i al denominador, calculats amb `processedTokens(usage)` de `web/src/lib/costs.ts`.
- `Usage` no canvia al protocol: el client calcula els tokens processats.
- Cada fila de `stats.daily` porta `cache_write_tokens`. Els totals per agent ja el portaven.
- La compactació i `UNCHANGED` ja eren estimacions de tokens d'entrada i de sortida a partir del text. No canvien.
- `CACHE_KEY_VERSION` passa a 4. Les entrades anteriors comptaven `input + output` i amagaven els intents declinats dins l'ús del missatge servit, de manera que un encert hauria donat l'estalvi amb la xifra antiga i el fallback valorat al preu del model final.
- Les files d'estalvi desades abans d'aquest canvi conserven la definició antiga (`input + output`, a la memòria cau de torns i a la parada per consens). No es migren, perquè les files no diuen quanta memòria cau hi havia.

### Intents declinats d'un fallback

- Contracte dels proveïdors (`providers/base.py`):
  - `DeclinedAttempt(model, usage)` és un intent que el seu model va declinar abans que un altre model agafés la petició.
  - `GenerationResult.declined` són els intents facturats que altres models van declinar abans que `model` servís la resposta, en ordre. Sense fallback és buida.
  - `ProviderError`, i per tant `RefusalError`, també porta `declined`: una negativa, o un límit de sortida esgotat sense text, després d'un fallback.
  - `usage` és sempre l'ús de l'intent que ha produït el resultat o l'error, a les tarifes del seu `model`. Els tokens de models diferents no se sumen mai.
- L'adaptador de l'API de Claude llegeix `usage.iterations`:
  - Cada entrada `message` és un intent declinat, aparellat en ordre amb el bloc `fallback` que dona la categoria i el model que va declinar. L'entrada `fallback_message` és l'intent que va servir.
  - Només compten els intents facturats: amb sortida, o declinats abans de la sortida en una categoria que Anthropic factura igualment.
  - El model de cada intent és el de la seva entrada. Si no hi és, el `from` del seu bloc; si tampoc, el model al qual havia passat l'intent anterior; i si no, el model que es va demanar.
- El motor registra cada intent declinat com una crida facturada sense missatge: una fila d'ús amb el seu model i el seu cost, `ok = False` i l'error «<model> ha declinat la petició i l'ha passada a un altre model.».
  - Compta al total del torn (`outcome.usage`, [ADR 0007](0007-turn-outcome.md)) i a `unstored_usage`.
  - Si portava el context compactat, compta també per a l'estalvi de la compactació.
  - Els resums de compactació fan el mateix.
- El missatge servit conserva l'ús del seu intent (`meta.usage` i `stream.completed.usage`) i guarda els intents declinats a `meta.declined`, `[{model, usage}]`.
- En una negativa després d'un fallback, `RefusalError.usage` és el de l'intent que ha refusat, que pot no facturar res, i `declined`, els d'abans. `stream.failed.usage` és el cost de la crida fallida; els intents declinats compten al total del torn.

### Valor d'un encert de la memòria cau

- Un encert val el torn original sencer: les crides de cada missatge i els intents declinats abans de cadascuna (`meta.declined`), cadascun a les tarifes actuals del seu model.
- Els tokens estalviats són els tokens processats d'aquestes mateixes crides. Així, l'estalvi i el seu valor surten de les mateixes dades i quadren.
- Les crides fallides del torn original que es van reintentar no hi compten, com fins ara: no formen part de la resposta que es reprodueix.

## Alternatives considerades

- **Mantenir `total_tokens` al costat de `processed_tokens`:** cap consumidor no el necessitava, i tenir dues definicions semblants convidava a tornar-les a barrejar.
- **Afegir `processed_tokens` a l'`Usage` del protocol:** és una dada derivada dels altres camps. El client la calcula amb una funció.
- **Migrar les files d'estalvi antigues:** les files no diuen quanta memòria cau hi havia, així que qualsevol xifra seria inventada.
- **No pujar `CACHE_KEY_VERSION` i documentar que les entrades antigues conserven la xifra antiga:** durant els 7 dies de vida de les entrades, els encerts sortirien amb 103 tokens i els fallbacks valorats al preu del model final.
- **Sumar els tokens dels intents declinats a l'`usage` i posar-hi el cost correcte a part:** un `Usage` amb tokens de dos models no es pot tornar a preuar (el valor d'un encert es calcula als preus actuals) i contradiu la regla d'Anthropic.
- **Una sola fila d'ús per crida, amb el cost de tots els intents:** trencaria la regla que una fila és una crida d'un sol model, de la qual depenen les estadístiques per model.
- **Valorar un encert només amb el missatge servit:** l'estalvi d'un torn amb fallback valdria menys del que va costar el torn.

## Conseqüències

- Els tokens del torn, del tauler i dels estalvis són els tokens processats: 30.103 en lloc de 103 en el cas de l'auditoria. La ràtio d'estalvi és coherent.
- Els imports d'un torn amb fallback són els que factura Anthropic: 0,370625 USD en el cas de l'auditoria, amb una fila de Fable 5.1 (0,240125, `ok = False`) i una d'Opus 4.8 (0,1305). Una negativa després d'un fallback val 0,11 USD, a les tarifes de Fable 5.1.
- `stats.totals.errors` compta també els intents declinats.
- Les entrades de la memòria cau de torns d'abans de la versió 4 deixen de fer-se servir i caduquen soles en 7 dies. La primera vegada que es repeteixi una pregunta, es tornarà a cridar el model.
- Les files d'estalvi d'abans d'aquest canvi conserven la definició antiga. Una finestra del tauler que en barregi de totes dues les suma tal com són.
- Canvien els contractes interns i el protocol: `Usage.processed_tokens` (`domain.py`), `DeclinedAttempt` i `declined` (`providers/base.py`), `CachedTurn.tokens` (`orchestrator/store.py`), `meta.declined` i `cache_write_tokens` a `stats.daily` ([PROTOCOL.md](../PROTOCOL.md) i `web/src/lib/protocol.ts`).
- Aquesta decisió és una proposta fins que el propietari l'accepti.
