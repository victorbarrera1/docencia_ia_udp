# IA-07 — Registro auditable del uso de IA

Cada llamada a un LLM deja una fila de **metadatos** en `ai_audit_log` (tabla de IA-04). Nunca se guarda el
texto enviado ni la respuesta, ni el mensaje de error del proveedor.

## Cómo se escribe

- `core/audit.py` registra `audit_listener` en el hook de `core/openrouter_client.py` (`setup_audit()` al arrancar
  `main.py`). Cubre `chat_completion` y `complete_json`, llamadas exitosas, fallidas y en modo fake.
- Escritura **en línea** vía PostgREST con `SUPABASE_SERVICE_ROLE_KEY` y tope `AI_AUDIT_TIMEOUT_S` (3 s). Sin
  Supabase configurado no escribe y avisa una vez en el log; nunca cae a la anon key.
- Un fallo al escribir **no rompe la llamada de IA**: queda un `ERROR` en el logger `ai_audit` con los metadatos de
  la fila (modelo, tokens, costo, IDs), suficiente para reconstruirla. Si el fallo es una FK inexistente
  (`section_id`/`version_id`/`actor`), reintenta una vez sin esas referencias para no perder tokens ni costo.
- `error` es un código (`LLM_HTTP_429`, `LLM_TIMEOUT`, `LLM_INVALID_RESPONSE`, `LLM_ERROR`), como exige el CHECK de la tabla.
- `LLMCallContext` ahora admite `actor` (users.id). Hoy nadie lo pasa: lo aportará el puente de IA-09.
  `section_id`/`version_id`/`actor` que no sean UUID válidos se registran como NULL.
- `costo_usd = NULL` es costo desconocido; `0` es costo cero (el modo fake registra `0`).

## Consulta: `GET /api/v1/audit`

Protegido con `X-Agent-Service-Key`. Parámetros opcionales: `section_id` (UUID), `desde` y `hasta` (fechas UTC,
**ambas inclusivas**), `agente` (exacto), `limit` (1–500, por defecto 100).

```json
{
  "total": {"llamadas": 4, "llamadas_ok": 3, "llamadas_error": 1, "tokens_in": 357, "tokens_out": 58,
            "costo_usd": "0.0000000007", "llamadas_sin_costo": 1},
  "por_modelo": [{"modelo": "openai/gpt-4o", "llamadas": 3, "...": "mismos campos que total"}],
  "items": [{"id": "…", "ts": "…", "actor": "…|null", "section_id": "…|null", "version_id": "…|null", "agente": "…",
             "modelo": "…", "tokens_in": 10, "tokens_out": 2, "costo_usd": "0.0000000004|null",
             "latencia_ms": 5, "ok": true, "error": null}],
  "truncated": false
}
```

- Los totales los calcula la RPC SQL `ai_audit_totals(p_section_id, p_desde, p_hasta, p_agente)`
  (migración `20261110_ia07_audit_totals.sql`, solo `service_role`) sobre **todo** el filtro, con `numeric`
  exacto. `costo_usd` viaja como texto decimal para no perder precisión. El costo desconocido no se suma
  como cero: se cuenta en `llamadas_sin_costo`.
- `items` son las últimas `limit` filas; `truncated` indica que hay más.
- Errores genéricos: 503 sin Supabase configurado, 502 si la consulta falla.
- El endpoint es servicio a servicio y ve todas las secciones; el filtrado por docente corresponde al puente de IA-09.

## Chat (`core/engine.py`)

El chat jerárquico ya no llama a Gemini directo: usa `openrouter_client.chat_completion` con `CHAT_MODEL`
(por defecto `google/gemini-2.5-flash`), por lo que queda auditado (`agente = "chat"` o `"chat:<worker_role>"`).
`GEMINI_API_KEY`, `DEFAULT_FRONTIER_MODEL`, `DEFAULT_WORKER_MODEL` y `google-genai` quedan **deprecados**
(nada los lee) y se retirarán al coordinar con el frontend.

## Verificación

`DATABASE_URL=… bash supabase/tests/run.sh` (incluye `supabase/tests/audit_totals_test.sql`) y
`cd agent-services && .venv/bin/python -m pytest`.
