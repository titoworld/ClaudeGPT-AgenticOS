# ClaudeGPT · Agentic OS

Un **sistema operativo agéntico**: una capa que orquesta agentes de IA (Claude, GPT…) como un sistema operativo orquesta procesos, con herramientas, memoria, permisos y planificación bien definidos.

> **Estado: fase 0.** El repositorio está inicializado y la base de Python funciona. La visión y la arquitectura están por definir: consulta [docs/VISION.md](docs/VISION.md) y el [roadmap](docs/ROADMAP.md).

## Cómo trabajar en este repo

### Con Claude Code en la web

1. Abre [claude.ai/code](https://claude.ai/code) y elige el repositorio `titoworld/ClaudeGPT-AgenticOS`.
2. Describe la tarea. Cada sesión trabaja en su propia rama `claude/...` y los cambios llegan a `main` mediante pull request.
3. Al arrancar la sesión, el hook [`.claude/hooks/session-start.sh`](.claude/hooks/session-start.sh) instala las dependencias (`uv sync`), así Claude puede ejecutar tests y linters desde el principio.

Las instrucciones que siguen las sesiones de Claude están en [CLAUDE.md](CLAUDE.md).

### En local

Requisito: [uv](https://docs.astral.sh/uv/getting-started/installation/) (descarga la versión de Python necesaria si no la tienes).

```bash
git clone https://github.com/titoworld/ClaudeGPT-AgenticOS.git
cd ClaudeGPT-AgenticOS
uv sync                  # crea .venv e instala dependencias
uv run agentic-os        # comprueba que todo funciona
```

## Comandos de desarrollo

| Tarea    | Comando                   |
| -------- | ------------------------- |
| Tests    | `uv run pytest`           |
| Lint     | `uv run ruff check .`     |
| Formato  | `uv run ruff format .`    |
| Tipos    | `uv run mypy`             |

La CI de GitHub Actions ejecuta estas comprobaciones en cada push a `main` y en cada pull request.

## Estructura

```
.
├── src/agentic_os/      # paquete Python (por ahora, solo el CLI mínimo)
├── tests/               # tests con pytest
├── docs/
│   ├── VISION.md        # qué queremos construir y preguntas abiertas
│   ├── ROADMAP.md       # fases del proyecto
│   └── adr/             # registro de decisiones de arquitectura
├── .claude/             # configuración de Claude Code (hook de inicio de sesión)
├── .github/workflows/   # CI
└── CLAUDE.md            # instrucciones para las sesiones de Claude
```

## Claves de API

Nunca subas claves al repositorio. En local, copia `.env.example` a `.env` (está en `.gitignore`); en Claude Code en la web, defínelas como variables de entorno en la configuración del entorno.
