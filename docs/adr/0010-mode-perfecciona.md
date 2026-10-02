# 0010. Mode «Perfecciona»: un document que les dues IA milloren fins que l'aturis

- Estat: Proposat
- Data: 2026-10-02

## Context

El propietari va demanar «una opció perquè vagin debatint per perfeccionar un projecte però sense sobredimensionar el resultat, és a dir un debat "infinit" fins que cliqui al botó de parar i aturi ja a la següent o a l'altra ronda amb el resultat final», per a desenvolupaments que busquen la perfecció.

El consell (`debate`) no hi serveix tal com és:

- Té com a molt 4 rondes i s'atura sol quan els dos models coincideixen: està pensat per respondre una pregunta, no per polir un lliurable.
- Cada model reescriu la seva pròpia resposta. No hi ha un sol document que millori ronda rere ronda.
- Res no frena el creixement. Quan dos models es revisen l'un a l'altre sense límits, el text s'allarga: cadascun hi afegeix el que creu que hi falta, i el resultat acaba sobredimensionat.

## Decisió

Un quart mode de torn, `refine` («Perfecciona»). Les dues IA treballen sobre **un sol document**.

1. **Ronda 0:** totes dues responen l'encàrrec, com en un consell.
2. **Ronda 1:** l'editor (Claude per defecte) fusiona les dues respostes en la versió 1.
3. **Ronda 2 i següents:** totes dues revisen la versió vigent, i l'editor n'escriu la següent aplicant només els canvis justificats.

El torn s'acaba quan el propietari l'atura, quan cap de les dues IA no hi troba res a canviar o quan s'arriba a un límit. L'última versió és la resposta final del torn i la que veuen els torns següents.

### Contra el sobredimensionament

- **Encàrrec tancat:** cada prompt torna a citar l'encàrrec. Un canvi ha de dir quin defecte corregeix o quin requisit de l'encàrrec compleix, i els prompts rebutgen els afegits que l'encàrrec no demana.
- **Límit de paraules:** el que fixi el propietari o, si no en fixa cap, 1,2 vegades les paraules de la versió 1 (300 com a mínim).
  - Cada prompt diu la llargada actual i el límit.
  - El motor comprova el límit sense cap model: una versió que el passa té un intent per escurçar-se, i si encara el passa, la ronda es descarta i es manté la versió vigent.
  - La versió 1 no té cap versió anterior per mantenir. Si la fusió passa del límit del propietari, també té un intent per escurçar-se, però si encara el passa (o si és la còpia d'una resposta, perquè ningú no ha pogut fusionar-les), es queda igualment com a versió 1, i són les edicions de les rondes següents les que l'han de fer cabre.
  - Cada versió s'escriu sencera en una sola resposta del model, que té com a molt 16.000 tokens de sortida, el raonament inclòs: una versió que no hi cap es talla i no s'accepta. A la pràctica, el document no pot passar d'unes 10.000 paraules de prosa en anglès, i en català o en codi en són menys.
- **Com a molt 5 canvis per ronda:** 5 propostes per revisió i 5 canvis per edició. Cada revisió ha de buscar primer defectes i després alguna cosa per treure o simplificar.
- **Contra l'oscil·lació:** cada prompt porta el registre de canvis de les rondes anteriors (les 30 últimes línies), i desfer un canvi s'ha de justificar.
- **Visibilitat:** el propietari veu cada versió, el que ha canviat respecte de l'anterior, les paraules i el cost de cada ronda.

### Aturar-lo

- **«Atura en acabar la ronda»** (`turn.stop`): la ronda en curs acaba (revisions i edició) i el torn es tanca amb l'última versió.
- **«Atura ara»** (`turn.cancel`): les crides en curs es cancel·len. L'última versió completa es desa igualment com a resposta final, sense cap crida, perquè no es perdi res del que ja s'ha pagat.
- **S'atura sol:**
  - quan cap de les dues IA no troba res a canviar dues rondes seguides (sempre);
  - quan totes dues li donen el llindar (90 per defecte) o més, sense proposar cap defecte, dues rondes seguides (es pot desactivar per fer-lo «infinit»).
- **Límits de seguretat, sempre:** un màxim de rondes (12 per defecte, fins a 50) i un pressupost en euros (3 € per defecte; en mode subscripció compta el valor a preus d'API, per no esgotar la quota).

Quan una de les dues IA falla, l'altra continua sola. Si totes dues fallen quan ja hi ha una versió, aquesta és la resposta final. Un torn «Perfecciona» no es desa mai a la memòria cau de torns.

## Alternatives considerades

- **Editors alterns** (cada ronda escriu una IA diferent): més independent, però cada editor desfà part del que ha fet l'altre i el document oscil·la. Es manté un sol editor, i l'altra IA hi aporta les seves revisions.
- **Rondes lliures, sense límit de paraules:** el document creix ronda rere ronda, que és el que el propietari vol evitar.
- **Una sola IA que es revisa a si mateixa:** no té una segona opinió, i perd el sentit del consell.
- **Ampliar el consell actual amb més rondes:** cada IA continuaria reescrivint la seva resposta en lloc de millorar un sol document, i la síntesi arribaria només al final.

## Conseqüències

- El protocol té un mode nou (`refine`) amb les seves opcions, el missatge `turn.stop` i els esdeveniments `turn.stopping` i `refine.round` ([PROTOCOL.md](../PROTOCOL.md)). Els missatges fan servir els tipus que ja existeixen (`answer`, `revision`, `synthesis`), amb `meta.refine`, així que la taula de missatges no canvia. Només un torn «Perfecciona» té rondes per acabar: un `turn.stop` d'un altre mode es rebutja, i aquell torn s'atura amb `turn.cancel`.
- «Perfecciona» no pot ser el mode per defecte de la configuració: un torn que dura fins que l'atures només comença quan el propietari el tria.
- El pressupost es dona en euros, però el motor compta en dòlars. El servidor el converteix amb el tipus amb què l'aplicació mostra els euros (el del BCE o el manual), de manera que el torn s'atura quan el que la interfície mostra que ha gastat arriba al pressupost.
- La taula de converses guarda el mode «Perfecciona» com a últim mode en una columna nova (`last_turn_mode`, migració 6), amb els valors de l'antiga, que es conserva sense fer-se servir. La columna antiga té una restricció `CHECK` que no es pot modificar, i refer la taula arrossegaria els missatges.
- El cost d'un torn «Perfecciona» depèn de les rondes: una ronda són tres crides (dues revisions i una edició, i una quarta si la versió nova s'ha d'escurçar). Els límits de rondes i d'euros i l'aturada automàtica el fiten. La interfície en mostra el cost ronda a ronda.
