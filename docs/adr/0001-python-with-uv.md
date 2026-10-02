# 0001. Python con uv como base del proyecto

- Estado: Aceptado
- Fecha: 2026-09-27

## Contexto

El proyecto necesita un lenguaje principal antes de definir la arquitectura, para disponer desde el principio de tests, lint y CI. Se desarrollará sobre todo con Claude Code en la web, así que cada sesión tiene que poder instalar dependencias y validar cambios sin intervención manual.

## Decisión

- Python >= 3.12 (3.13 para desarrollo, fijado en `.python-version`), con layout `src/` y paquete `agentic_os`.
- uv para entorno, dependencias y lockfile (`uv.lock`).
- pytest para tests, ruff para lint y formato, mypy en modo estricto para tipos.
- GitHub Actions ejecuta las cuatro comprobaciones con Python 3.12 y 3.13.

## Alternativas consideradas

- **TypeScript:** buen encaje si la interfaz principal fuera web, pero el ecosistema de agentes e IA es más amplio en Python.
- **pip + venv / Poetry:** uv es más rápido, gestiona también la versión de Python y produce un lockfile reproducible.

## Consecuencias

- Los SDK oficiales de Anthropic y OpenAI para Python estarán disponibles cuando se implemente la capa de proveedores.
- El tipado estricto exige anotar todo el código, a cambio de detectar errores antes, algo especialmente útil cuando buena parte del código lo escribe un agente.
