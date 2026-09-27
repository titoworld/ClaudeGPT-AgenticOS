# Visión (borrador)

> Documento vivo. Recoge la idea de partida y las preguntas que hay que responder en la **fase 1** para decidir la arquitectura. Nada de lo que aparece aquí está decidido hasta que tenga su ADR en [`adr/`](adr/).

## La idea

Un *Agentic OS* aplica las ideas de un sistema operativo a los agentes de IA: en lugar de procesos que compiten por CPU y memoria, hay agentes que compiten por contexto, herramientas y presupuesto, y un núcleo que los coordina de forma segura y observable.

| Sistema operativo      | Equivalente agéntico                                                            |
| ---------------------- | ------------------------------------------------------------------------------- |
| Kernel                 | Orquestador: decide qué agente trabaja, con qué contexto y con qué permisos     |
| Proceso                | Agente en ejecución: tarea, estado e historial                                  |
| Llamadas al sistema    | Herramientas que un agente puede invocar                                        |
| Drivers                | Proveedores de modelos (Claude, GPT…) detrás de una interfaz común               |
| Memoria RAM            | Ventana de contexto del modelo                                                  |
| Disco y ficheros       | Memoria persistente: ficheros, base de datos, búsqueda semántica                |
| Planificador           | Cola de tareas, prioridades y presupuestos de tokens y coste                    |
| Comunicación (IPC)     | Mensajes y delegación entre agentes                                             |
| Usuarios y permisos    | Qué puede hacer cada agente y qué requiere aprobación humana                    |
| Shell                  | Interfaz con el usuario: CLI, chat, web…                                        |
| Logs                   | Trazas y auditoría de cada acción                                               |

## Enfoques posibles

- **A. Runtime propio en Python.** Construimos el núcleo (orquestador, agentes, herramientas, memoria, planificador) usando los SDK oficiales de Anthropic y OpenAI. Máximo control y aprendizaje; más código que mantener.
- **B. OS personal sobre Claude Code.** El "sistema operativo" es un conjunto de `CLAUDE.md`, skills, subagentes, comandos y memoria en Markdown, y Claude Code actúa de motor. Muy rápido de poner en marcha; menos control sobre el bucle y centrado en Claude.
- **C. Híbrido.** Un núcleo propio en Python que expone sus capacidades como herramientas (por ejemplo, un servidor MCP) para usarlas desde Claude Code u otros clientes.

## Preguntas para la fase 1

1. **Para qué:** ¿uso personal, para un negocio o como producto? Tres casos de uso concretos que el sistema debería resolver.
2. **Enfoque:** A, B o C.
3. **Modelos:** ¿qué papel tienen Claude y GPT? Intercambiables, especializados por tarea, uno revisa al otro…
4. **Bucle agéntico:** ¿lo escribimos nosotros sobre los SDK (Anthropic SDK con su *tool runner*, OpenAI SDK) o nos apoyamos en un framework (Claude Agent SDK, OpenAI Agents SDK)?
5. **Interfaz:** CLI, chat web, bot de mensajería…
6. **Memoria:** qué debe recordar el sistema y dónde (ficheros, SQLite, base vectorial).
7. **Autonomía y seguridad:** qué puede hacer un agente sin preguntar, cómo se aísla la ejecución y qué límite de gasto hay.
8. **Ejecución:** local, servidor propio o nube; ¿tareas programadas o solo bajo demanda?

Cuando haya respuestas, cada decisión se registra como ADR y el roadmap se concreta.
