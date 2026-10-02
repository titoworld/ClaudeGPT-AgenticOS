<!-- @question -->
Política de còpies de seguretat del servidor

Redacta la política de còpies de seguretat del nostre servidor, en una pàgina: què es copia, cada quan, on es guarda, quant de temps i com comprovem que es pot restaurar. Som tres persones i cap no és tècnic de sistemes a temps complet.
<!-- @claude-answer -->
# Política de còpies de seguretat

## Abast

El servidor `vps-01`: l'aplicació, la base de dades PostgreSQL i els fitxers que pugen els usuaris.

## Còpies

- **Base de dades:** `pg_dump` complet cada nit a les 02:00 i arxivat continu del registre de transaccions (WAL) cada 15 minuts.
- **Fitxers:** còpia incremental cada nit amb `restic`, xifrada.
- **Configuració:** ja és al repositori d'infraestructura; el `.env` s'hi guarda xifrat.

## On (regla 3-2-1)

Tres còpies, en dos suports diferents, una de fora de línia:

1. L'original, al servidor.
2. Emmagatzematge d'objectes d'un altre proveïdor.
3. Un disc extern setmanal, desconnectat.

El RAID 1 del servidor protegeix d'una avaria de disc, però no d'un esborrat accidental: no substitueix cap d'aquestes còpies.

## Objectius

Pèrdua màxima de 24 hores i recuperació en 4 hores.
<!-- @chatgpt-answer -->
**Proposta de política (resum d'una pàgina)**

| Còpia | Freqüència | Retenció | On |
| --- | --- | --- | --- |
| Diària | Cada nit | 14 dies | Núvol (un altre proveïdor) |
| Setmanal | Diumenge | 8 setmanes | Núvol i disc extern |
| Mensual | Primer dia del mes | Les últimes | Núvol |

**Comprovacions**

- Alerta automàtica si una còpia falla.
- Prova de restauració mensual en un servidor de proves.
- Simulacre anual de recuperació completa.

**Responsabilitats:** una persona n'és la responsable i revisa les alertes cada matí.

**Seguretat:** totes les còpies xifrades; la clau, al gestor de contrasenyes de l'equip.
<!-- @v1 -->
# Política de còpies de seguretat

**Abast:** el servidor `vps-01` (aplicació, base de dades i fitxers dels usuaris).

## Què es copia i cada quan

| Què | Com | Freqüència |
| --- | --- | --- |
| Base de dades | `pg_dump` complet i registre de transaccions | Cada nit (02:00) i cada 15 min |
| Fitxers dels usuaris | Còpia incremental amb `restic` | Cada nit |
| Configuració | Repositori d'infraestructura i `.env` xifrat | A cada canvi |

## On es guarda (regla 3-2-1)

1. L'original, al servidor.
2. Una còpia xifrada a l'emmagatzematge d'objectes d'un altre proveïdor.
3. Una còpia setmanal fora de línia, en un disc extern que custodia el responsable.

El servidor ja té els discos en RAID 1, que el protegeix d'una avaria d'un disc. Tot i així, cal mantenir les còpies externes, perquè el RAID no protegeix d'un esborrat accidental ni d'un atac.

## Quant de temps

| Còpies | Es guarden |
| --- | --- |
| Diàries | 14 dies |
| Setmanals | 8 setmanes |
| Mensuals | Les últimes |

## Com es comprova

- **Cada dia:** una alerta si la còpia de la nit no ha acabat bé.
- **Cada mes:** es restaura la base de dades en un servidor de proves.
- **Cada any:** simulacre complet, recuperant tot el servidor des de zero.

## Objectius

- **Pèrdua màxima de dades (RPO):** 24 hores.
- **Temps màxim de recuperació (RTO):** 4 hores.

## Claus

La contrasenya del repositori `restic` és al gestor de contrasenyes de l'equip i, en paper, en un sobre tancat a l'oficina. Sense ella, les còpies no es poden llegir.
<!-- @v2 -->
# Política de còpies de seguretat

**Abast:** el servidor `vps-01` (aplicació, base de dades i fitxers dels usuaris).

## Què es copia i cada quan

| Què | Com | Freqüència |
| --- | --- | --- |
| Base de dades | `pg_dump` complet i registre de transaccions | Cada nit (02:00) i cada 15 min |
| Fitxers dels usuaris | Còpia incremental amb `restic` | Cada nit |
| Configuració | Repositori d'infraestructura i `.env` xifrat | A cada canvi |

## On es guarda (regla 3-2-1)

1. L'original, al servidor.
2. Una còpia xifrada a l'emmagatzematge d'objectes d'un altre proveïdor.
3. Una còpia setmanal fora de línia, en un disc extern que custodia el responsable.

## Quant de temps

| Còpies | Es guarden |
| --- | --- |
| Diàries | 14 dies |
| Setmanals | 8 setmanes |
| Mensuals | 12 mesos |

## Com es comprova

- **Cada dia:** una alerta si la còpia de la nit no ha acabat bé.
- **Cada mes:** es restaura la base de dades en un servidor de proves.
- **Cada any:** simulacre complet, recuperant tot el servidor des de zero.

## Objectius

- **Pèrdua màxima de dades (RPO):** 15 minuts per a la base de dades i 24 hores per als fitxers.
- **Temps màxim de recuperació (RTO):** 4 hores.

## Claus

La contrasenya del repositori `restic` és al gestor de contrasenyes de l'equip i, en paper, en un sobre tancat a l'oficina. Sense ella, les còpies no es poden llegir.
<!-- @v3 -->
# Política de còpies de seguretat

**Abast:** el servidor `vps-01` (aplicació, base de dades i fitxers dels usuaris).
**Responsable:** Marta. **Suplent:** Jordi.

## Què es copia i cada quan

| Què | Com | Freqüència |
| --- | --- | --- |
| Base de dades | `pg_dump` complet i registre de transaccions | Cada nit (02:00) i cada 15 min |
| Fitxers dels usuaris | Còpia incremental amb `restic` | Cada nit |
| Configuració | Repositori d'infraestructura i `.env` xifrat | A cada canvi |

## On es guarda (regla 3-2-1)

1. L'original, al servidor.
2. Una còpia xifrada a l'emmagatzematge d'objectes d'un altre proveïdor.
3. Una còpia setmanal fora de línia, en un disc extern que custodia el responsable.

## Quant de temps

Es guarden les còpies **diàries durant 14 dies**, les **setmanals durant 8 setmanes** i les **mensuals durant 12 mesos**; `restic forget --prune` esborra les que sobren.

## Com es comprova

- **Cada dia:** una alerta al xat de l'equip si la còpia de la nit no ha acabat bé.
- **El primer dilluns de cada mes:** el responsable restaura la base de dades en un servidor de proves i comprova que l'aplicació hi arrenca. Anota el resultat i el temps que hi ha dedicat.
- **Cada any:** simulacre complet, recuperant tot el servidor des de zero.

## Objectius

- **Pèrdua màxima de dades (RPO):** 15 minuts per a la base de dades i 24 hores per als fitxers.
- **Temps màxim de recuperació (RTO):** 4 hores.

## Claus

La contrasenya del repositori `restic` és al gestor de contrasenyes de l'equip i, en paper, en un sobre tancat a l'oficina. Sense ella, les còpies no es poden llegir.
