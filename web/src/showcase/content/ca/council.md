<!-- @question -->
Migració de PostgreSQL 14 a 17 amb el mínim temps d'aturada

Tinc un PostgreSQL 14 amb 40 GB de dades en un VPS (Debian 12, 4 vCPU, 8 GB de RAM i 160 GB de disc). L'aplicació pot estar aturada com a molt 10 minuts. Quin pla em proposeu?
<!-- @claude-answer -->
## `pg_upgrade --link`: 3–5 minuts d'aturada

Amb 40 GB i una finestra de 10 minuts, l'opció més segura és **`pg_upgrade` amb enllaços durs**: no copia les dades, només reescriu el catàleg del sistema, i amb aquest volum sol trigar menys d'un minut.

1. **Prepara el 17 al costat del 14.** Instal·la `postgresql-17` i comprova la compatibilitat amb `pg_upgrade --check` (extensions, tipus de dades, codificació).
2. **Fes una còpia verificada.** Un `pg_basebackup` la nit abans, i prova de restaurar-lo: amb `--link`, un cop arrenques el 17, el clúster del 14 ja no és fiable.
3. **Atura l'aplicació i actualitza:**
   ```bash
   sudo pg_dropcluster 17 main --stop   # el clúster buit que crea el paquet
   sudo pg_upgradecluster -m upgrade --link 14 main
   ```
4. **Arrenca i refresca les estadístiques** amb `vacuumdb --all --analyze-in-stages`: sense estadístiques, les primeres consultes poden anar lentes.

**Aturada prevista:** 3–5 minuts, la major part per aturar i arrencar els serveis.
<!-- @chatgpt-answer -->
**Resposta curta:** amb 10 minuts de marge tens dues opcions vàlides. Jo triaria la **replicació lògica**: l'aturada es redueix a segons i pots tornar enrere en qualsevol moment.

| Opció | Aturada | Tornar enrere | Risc |
| --- | --- | --- | --- |
| `pg_upgrade --link` | 3–5 min | Només des d'una còpia | Baix |
| Replicació lògica | < 1 min | Sí: el 14 continua viu | Mitjà |
| `pg_dump` + `pg_restore` | 30–60 min | Sí | Baix |

**Passos amb replicació lògica**

- Crea un clúster 17 al port `5433` i copia-hi l'esquema amb `pg_dump --schema-only`.
- `CREATE PUBLICATION` al 14 i `CREATE SUBSCRIPTION` al 17; espera que acabi la còpia inicial.
- **El canvi:** atura l'aplicació, sincronitza les seqüències (`setval`) i apunta-la al port `5433`.

> La replicació lògica no copia les seqüències ni els canvis d'esquema (DDL): congela les migracions de l'aplicació mentre duri.
<!-- @claude-critique-1 -->
- La replicació lògica és l'opció quan cal una aturada de segons, però aquí el límit és de 10 minuts: `--link` hi cap amb marge i té menys peces que poden fallar.
- Necessita el doble de disc (40 GB més: n'hi ha, però just) i les taules sense clau primària no admeten `UPDATE` ni `DELETE` un cop publicades: caldria revisar-les una per una.
- Encerta en un punt que jo no tenia: poder tornar enrere. L'incorporo amb un assaig previ i una còpia verificada.
<!-- @claude-revision-1 -->
## `pg_upgrade --link`, assajat abans

Mantinc la recomanació: amb una finestra de 10 minuts, **`pg_upgrade --link`** hi cap amb marge i té menys peces que poden fallar que la replicació lògica.

1. **Assaig complet** en una instantània del VPS: mesura el temps real i detecta extensions incompatibles amb `pg_upgrade --check`.
2. **Còpia verificada** la nit abans (`pg_basebackup` i una prova de restauració): és el teu camí de tornada.
3. **Finestra:** `pg_dropcluster 17 main --stop` i `pg_upgradecluster -m upgrade --link 14 main`.
4. **Després:** arrenca l'aplicació i executa `vacuumdb --all --analyze-in-stages`.

**Pla B:** si algun dia necessites una aturada de segons, la replicació lògica que proposa ChatGPT és el camí, però abans cal revisar les taules sense clau primària.
<!-- @chatgpt-critique-1 -->
- El pla és correcte i, per a una finestra de 10 minuts, més simple que el meu.
- Cal dir clarament que, amb `--link`, un cop arrencat el 17 ja no es pot tornar al 14: l'única tornada és la còpia.
- Hi falta un assaig en una còpia del servidor per saber el temps real abans de la finestra.
<!-- @chatgpt-revision-1 -->
**Resposta revisada:** amb 10 minuts de marge, **`pg_upgrade --link`** és suficient i més simple; la replicació lògica només compensa si necessites una aturada de segons.

| | `pg_upgrade --link` | Replicació lògica |
| --- | --- | --- |
| Aturada | 3–5 min | < 1 min |
| Disc addicional | Gairebé cap | +40 GB |
| Tornar enrere | Des de la còpia | Immediat |
| Risc | Baix | Mitjà (DDL, seqüències) |

Abans de la finestra, assaja-ho en una instantània del VPS i guarda una còpia verificada: és l'única manera de tornar enrere.
<!-- @claude-critique-2 -->
- Cap error rellevant: la resposta revisada de ChatGPT coincideix amb la meva, i la taula hi afegeix el cost en disc de cada opció.
<!-- @chatgpt-critique-2 -->
- Cap objecció: el pla és complet i inclou l'assaig, la còpia verificada i el pla B.
<!-- @synthesis -->
## Pla final: `pg_upgrade --link`, assajat abans

Claude i ChatGPT coincidim: amb una finestra de 10 minuts, **`pg_upgrade --link`** és l'opció més simple i hi cap amb marge. La replicació lògica queda com a pla B per si mai necessites una aturada de segons.

**La setmana abans**

1. Instantània del VPS i assaig complet: mesura el temps real.
2. `pg_upgrade --check` per detectar extensions o tipus incompatibles.
3. Còpia verificada amb `pg_basebackup` la nit abans, i una prova de restauració.

**La finestra (uns 5 minuts)**

```bash
sudo systemctl stop app
sudo pg_dropcluster 17 main --stop
sudo pg_upgradecluster -m upgrade --link 14 main
sudo systemctl start app
sudo -u postgres vacuumdb --all --analyze-in-stages   # amb l'aplicació ja en marxa
```

**Si alguna cosa falla:** no tornis a arrencar el 14 després d'haver arrencat el 17; restaura la còpia del pas 3.

| | `pg_upgrade --link` | Replicació lògica (pla B) |
| --- | --- | --- |
| Aturada | 3–5 min | < 1 min |
| Disc addicional | Gairebé cap | +40 GB |
| Complexitat | Baixa | Mitjana |
