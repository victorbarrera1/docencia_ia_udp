# [IA-03] Cliente LLM robusto (OpenRouter)
**Depende de:** 00 · **Ola:** 2 · **Modelo:** Sonnet

## Objetivo
Mejorar `core/openrouter_client.py` sin romper a quien ya lo usa (`engine.py`, `activity_designer.py`).

## Entregables
- `complete_json(system, user, schema: type[BaseModel], model=None) -> (BaseModel, meta)`: usa `response_format` JSON schema de OpenRouter cuando el modelo lo soporta; si no, valida con Pydantic y reintenta una vez con el error.
- Reintentos con backoff en 429/5xx, timeout configurable.
- `meta`: modelo usado, tokens in/out, costo (usa el campo de uso que devuelve OpenRouter), latencia.
- Hook de auditoría: `register_audit_listener(fn)` que recibe `meta` + contexto (`agente`, `section_id`, `version_id`). La persistencia va en IA-07.
- Modo **fake** determinista (`LLM_FAKE=1` o sin API key en tests) para CI.
- Mantén la firma actual de `chat_completion` funcionando.

## Tests
Todo con modo fake o `httpx.MockTransport`: JSON válido, JSON inválido que se reintenta, 429 con reintento, hook llamado con tokens.
