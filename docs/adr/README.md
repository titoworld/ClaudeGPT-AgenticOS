# Registre de decisions d'arquitectura (ADR)

Cada decisió important es documenta en un fitxer numerat `NNNN-titol-curt.md`. Així qualsevol sessió (humana o de Claude) sap què es va decidir, per què i quines alternatives es van descartar, sense reobrir el debat.

Un ADR acceptat no s'edita: si la decisió canvia, s'escriu un ADR nou que el substitueix i l'antic passa a l'estat "Substituït per NNNN".

## Índex

| Núm. | Decisió                                                                                   | Estat    |
| ---- | ----------------------------------------------------------------------------------------- | -------- |
| 0001 | [Python amb uv com a base del projecte](0001-python-with-uv.md) (en castellà)              | Acceptat |
| 0002 | [Subscripcions via les CLI oficials, amb claus d'API com a alternativa](0002-subscriptions-via-official-clis.md) | Acceptat |
| 0003 | [Mode Consell i estratègia d'estalvi de tokens](0003-council-and-token-savings.md)      | Acceptat |
| 0004 | [Interfície web i desplegament](0004-web-and-deployment.md)                               | Acceptat |
| 0005 | [Integritat de les respostes: truncades, negatives i pressupost de sortida](0005-answer-integrity.md) | Proposat |
| 0006 | [Revisió de la configuració i preus per defecte a la taula de preus](0006-settings-revisions.md) | Proposat |
| 0007 | [Resultat del torn desat a la pregunta](0007-turn-outcome.md)                        | Proposat |
| 0008 | [Recompte de tokens i intents declinats](0008-token-accounting.md)                      | Proposat |
| 0009 | [Adjunts: imatges, PDF i fitxers de text al xat](0009-attachments.md)                         | Proposat |
| 0010 | [Mode «Perfecciona»: un document que les dues IA milloren fins que l'aturis](0010-refine-mode.md) | Proposat |
| 0011 | [Internationalization: the repository in English, the interface in English, Spanish and Catalan](0011-internationalization.md) | Proposat |

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
