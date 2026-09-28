# Registre de decisions d'arquitectura (ADR)

Cada decisió important es documenta en un fitxer numerat `NNNN-titol-curt.md`. Així qualsevol sessió (humana o de Claude) sap què es va decidir, per què i quines alternatives es van descartar, sense reobrir el debat.

Un ADR acceptat no s'edita: si la decisió canvia, s'escriu un ADR nou que el substitueix i l'antic passa a l'estat "Substituït per NNNN".

## Índex

| Núm. | Decisió                                                                                   | Estat    |
| ---- | ----------------------------------------------------------------------------------------- | -------- |
| 0001 | [Python amb uv com a base del projecte](0001-python-con-uv.md) (en castellà)              | Acceptat |
| 0002 | [Subscripcions via les CLI oficials, amb claus d'API com a alternativa](0002-subscripcions-via-cli-oficials.md) | Acceptat |
| 0003 | [Mode Consell i estratègia d'estalvi de tokens](0003-consell-i-estalvi-de-tokens.md)      | Acceptat |
| 0004 | [Interfície web i desplegament](0004-web-i-desplegament.md)                               | Acceptat |
| 0005 | [Integritat de les respostes: truncades, negatives i pressupost de sortida](0005-integritat-de-les-respostes.md) | Proposat |

## Plantilla

```markdown
# NNNN. Títol

- Estat: Proposat | Acceptat | Substituït per NNNN
- Data: AAAA-MM-DD

## Context

Quin problema cal resoldre i què condiciona la decisió.

## Decisió

Què es decideix.

## Alternatives considerades

Quines altres opcions hi havia i per què es van descartar.

## Conseqüències

Què implica la decisió, tant el que és bo com el que no.
```
