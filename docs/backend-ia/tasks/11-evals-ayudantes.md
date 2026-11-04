# [IA-11] Evaluación contra ayudantes humanos
**Depende de:** 08 · **Ola:** 6 · **Modelo:** Sonnet

## Entregables
- Extiende `agent-services/evals/` (no lo reemplaces): formato de casos con rúbrica, entrega y corrección humana (nota + observaciones). Solo datos ficticios o anonimizados.
- Corre el evaluador con 2–3 modelos de OpenRouter configurables.
- Métricas: diferencia absoluta de nota, correlación, recall de observaciones humanas, % de citas descartadas, costo y latencia por modelo.
- Reporte Markdown en `agent-services/evals/reports/`.
- `run_evals.py` existente sigue pasando.
