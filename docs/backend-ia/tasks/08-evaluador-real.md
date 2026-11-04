# [IA-08] Evaluador real con citas verificadas
**Depende de:** 02, 03, 06, 07 · **Ola:** 5 · **Modelo:** Opus

## Contexto
Hoy `submission_evaluator.py` es una simulación. La rama `cursor/feat-agentai-9e9d` tiene un `_evaluate_with_llm`: revísalo y reutiliza lo útil.

## Entregables
- Flujo: cargar versión + rúbrica → recuperar contexto (IA-06) → seudonimizar (IA-02) → `complete_json` con el contrato del SPEC (IA-03) → restaurar nombres → **validar citas** → persistir `ai_reviews` (`propuesta`) y `ai_observations` (`pendiente`).
- `agents/specialists/citations.py`: valida que `cita.texto` exista literal (normalizando espacios) en la versión indicada; calcula offsets en el texto original; descarta y cuenta las inválidas.
- Prompt de sistema versionado en `agents/prompts/evaluador.md`: solo observaciones fundadas en la rúbrica, cita literal obligatoria, escala 1.0–7.0, respeta `PedagogicalSettings` del nodo del árbol.
- `POST /api/v1/agent/evaluate` sigue respondiendo `SubmissionEvaluationResponse` (compatibilidad), con campos nuevos opcionales (`review_id`, `observaciones`, `citas_descartadas`).
- Nuevo `POST /api/v1/reviews/{id}/confirm` (solo docente): exige que no queden observaciones `pendiente`; único camino que escribe `nota_final`.
- Elimina la simulación del 85 %; si no hay API key, responde error claro (no notas inventadas).

## Tests
Con LLM fake: cita válida se guarda, cita inventada se descarta, nunca se escribe `nota_final` sin confirmación, texto enviado al LLM no contiene RUT ni nombres.
