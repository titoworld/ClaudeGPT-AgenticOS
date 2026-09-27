# CLAUDE.md

Instrucciones para las sesiones de Claude Code en este repositorio.

## Idioma

- Habla con el usuario en español.
- Documentación (README, `docs/`) en español.
- Código, identificadores y comentarios en inglés.
- Mensajes de commit en inglés, con [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).

## Estado del proyecto

Fase 0/1: la arquitectura **todavía no está decidida**. Antes de trabajar, lee `docs/VISION.md`, `docs/ROADMAP.md` y `docs/adr/`.

- No introduzcas decisiones de arquitectura por tu cuenta: propónlas, confírmalas con el usuario y regístralas como ADR en `docs/adr/`.
- Al completar un hito, marca su casilla en `docs/ROADMAP.md`.

## Comandos

```bash
uv sync                 # instalar dependencias (el hook de inicio de sesión ya lo hace en la web)
uv run pytest           # tests
uv run ruff check .     # lint
uv run ruff format .    # formato
uv run mypy             # tipos (modo estricto)
```

Antes de cada commit deben pasar `ruff check`, `ruff format --check`, `mypy` y `pytest`: es lo mismo que ejecuta la CI.

## Convenciones de código

- Python >= 3.12, layout `src/` (paquete `agentic_os`), tests en `tests/`.
- Tipado completo: mypy en modo estricto sobre `src` y `tests`.
- Dependencias siempre con `uv add` / `uv add --dev`; nunca edites `uv.lock` a mano. Justifica cada dependencia nueva.
- Los tests no llaman a APIs reales de modelos (cuestan dinero y requieren red): usa dobles de prueba.
- Todo cambio de comportamiento lleva su test.

## Secretos

- Nunca escribas claves ni tokens en el repositorio. Se leen de variables de entorno (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
- `.env` es solo para desarrollo local y está en `.gitignore`; `.env.example` documenta las variables.

## Git

- `main` es la rama por defecto y debe estar siempre en verde.
- Trabaja en una rama y lleva los cambios a `main` mediante pull request; no hagas push directo a `main`.
- Si cambias comandos, estructura o convenciones, actualiza este fichero y el README en el mismo cambio.
