# Registro de decisiones de arquitectura (ADR)

Cada decisión importante se documenta en un fichero numerado `NNNN-titulo-corto.md`. Así cualquier sesión (humana o de Claude) sabe qué se decidió, por qué y qué alternativas se descartaron, sin reabrir el debate.

Un ADR aceptado no se edita: si la decisión cambia, se escribe un ADR nuevo que lo sustituye y el antiguo pasa a estado "Sustituido por NNNN".

## Índice

| Nº   | Decisión                                               | Estado   |
| ---- | ------------------------------------------------------ | -------- |
| 0001 | [Python con uv como base del proyecto](0001-python-con-uv.md) | Aceptado |

## Plantilla

```markdown
# NNNN. Título

- Estado: Propuesto | Aceptado | Sustituido por NNNN
- Fecha: AAAA-MM-DD

## Contexto

Qué problema hay que resolver y qué condiciona la decisión.

## Decisión

Qué se decide.

## Alternativas consideradas

Qué otras opciones había y por qué se descartaron.

## Consecuencias

Qué implica la decisión, tanto lo bueno como lo malo.
```
