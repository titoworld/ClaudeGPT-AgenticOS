# Roadmap

Las fases 2 en adelante son un borrador y se concretarán al cerrar la fase 1.

## Fase 0 · Inicialización ✅

- [x] Repositorio en GitHub con `main` como rama por defecto
- [x] Base de Python: uv, pytest, ruff, mypy estricto y CLI mínimo (`agentic-os`)
- [x] CI en GitHub Actions
- [x] Hook de inicio de sesión para Claude Code en la web
- [x] `CLAUDE.md`, visión inicial y registro de decisiones (ADR)

## Fase 1 · Visión y arquitectura

- [ ] Responder las preguntas de [VISION.md](VISION.md)
- [ ] ADR: enfoque (runtime propio, OS sobre Claude Code o híbrido)
- [ ] ADR: papel de cada modelo y cómo se abstraen los proveedores
- [ ] ADR: memoria, autonomía y seguridad
- [ ] Diagrama de arquitectura y concreción de las fases siguientes

## Fase 2 · Núcleo mínimo (borrador)

- [ ] Un agente que resuelve una tarea usando una herramienta y un proveedor
- [ ] Trazas de cada paso
- [ ] Tests con dobles de prueba (sin llamadas reales a las APIs)

## Fase 3 · Multi-proveedor y memoria (borrador)

- [ ] Claude y GPT detrás de una interfaz común
- [ ] Memoria persistente

## Fase 4 · Multi-agente y planificación (borrador)

- [ ] Delegación entre agentes
- [ ] Cola de tareas, prioridades y presupuestos

## Fase 5 · Interfaz y despliegue (borrador)

- [ ] Interfaz de usuario
- [ ] Despliegue
