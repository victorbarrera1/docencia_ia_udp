# [IA-00] Base de tests y CI para agent-services
**Depende de:** nada · **Ola:** 1 · **Modelo:** Sonnet

## Objetivo
Hoy `agent-services/` no tiene tests. Dejar la base que todas las demás tareas usan.

## Entregables
- `agent-services/requirements-dev.txt`: pytest, pytest-asyncio, ruff.
- `agent-services/tests/` con `conftest.py`: `TestClient` de FastAPI y fixture que fuerza el cliente LLM en modo sin llave (sin llamadas reales).
- Tests mínimos de lo existente: `/health`, `/.well-known/agent-card.json`, `/api/v1/tree/hierarchy`, `/api/v1/agent/evaluate` (que responda con el schema actual y `profesor_revisor_pendiente = true`).
- `agent-services/pytest.ini` o sección equivalente.
- GitHub Action `.github/workflows/agent-services.yml`: corre solo si cambia `agent-services/**`; Python 3.12, `pip install -r requirements.txt -r requirements-dev.txt`, `ruff check` (solo errores, sin reformatear el código de Benjamín), `pytest`, `python evals/run_evals.py`.
- Si ya existe un workflow en `.github/`, no lo modifiques: agrega uno nuevo.

## Criterio de listo
`cd agent-services && python -m pytest` verde localmente y en CI.
