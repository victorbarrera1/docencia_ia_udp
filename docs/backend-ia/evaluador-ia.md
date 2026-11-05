# IA-08 — Evaluador real con citas verificadas

El evaluador propone; el docente decide. La IA escribe `ai_reviews` en estado `propuesta` y `ai_observations` en
estado `pendiente`. Nunca escribe `nota_final`: solo lo hace `POST /api/v1/reviews/{id}/confirm`.

## Flujo de `POST /api/v1/agent/evaluate`

1. Si el endpoint del evaluador no está configurado (ni `LLM_FAKE`), responde **503 `IA_NO_CONFIGURADA`** antes de
   leer datos. Ya no existe la simulación del 85 %.
2. Carga la versión (`texto_extraido`), la versión anterior, la actividad, la rúbrica (`activities.rubric_json`)
   y la lista de estudiantes de la sección. La rúbrica de la base manda; `rubric` del body solo se usa si la
   actividad no tiene rúbrica.
3. Arma el contexto: la versión actual completa si cabe en `EVALUATOR_MAX_TEXT_KCHARS` (48 mil caracteres por
   defecto); si no, los fragmentos que recupera el RAG de IA-06 por criterio. Si el RAG no responde, usa el
   comienzo del texto y lo marca como truncado. Para la versión anterior usa fragmentos RAG, o el texto completo
   si es corto.
4. **Seudonimiza una sola vez** el mensaje completo (rúbrica y fragmentos de ambas versiones) con `core/privacy.py`.
   Un único mapping evita que un RUT desconocido de la versión 1 y otro de la versión 2 compartan token.
   Si seudonimizar de nuevo el resultado lo cambia, no se envía nada (`SEUDONIMIZACION_FALLIDA`).
5. Llama a `complete_json` (IA-03) con el prompt versionado `agents/prompts/evaluador.md` (hoy `evaluador-v1`),
   las directivas del nodo y sus `PedagogicalSettings`. La llamada queda en `ai_audit_log` (IA-07).
6. Restaura los nombres en resumen, comentarios y observaciones.
7. **Valida cada cita** (`agents/specialists/citations.py`): normaliza espacios, la busca literal en los segmentos
   de la versión indicada (`actual`/`anterior`), lleva el rango al texto original con `map_offsets` y lo compara
   con el `texto_extraido` original. Si no calza, o si tiene menos de `EVALUATOR_MIN_CITA_CHARS` caracteres reales,
   descarta la observación y la cuenta en `citas_descartadas`.
8. **La nota la calcula el sistema** a partir del puntaje de cada criterio (acotado a [0, máx]). Usa la escala
   1,0–7,0 con exigencia `EVALUATOR_EXIGENCIA` (60 %: el 60 % del puntaje equivale a 4,0) y redondeo a un decimal
   (mitades hacia arriba). Las décimas se topan en `min(max_decimas del body, activities.max_decimas, politica_decimas del nodo)`.
9. La RPC `create_ai_review` guarda la revisión y sus observaciones en una sola transacción. El trigger de IA-04
   vuelve a verificar que cada cita sea literal.

### Request

```json
{ "version_id": "uuid", "actor": "uuid|null", "course_id": "course:cit3203", "settings": null,
  "max_decimas": 0.6, "assignment_title": "opcional", "rubric": "solo si la actividad no tiene", "student_solution": "ignorado" }
```

### Response (compatible: los campos de IA-08 son opcionales)

Mantiene `nota_sugerida` (preliminar + décimas), `decimas_sugeridas`, `resumen_feedback`, `criterios_evaluados`
(`cita_textual` = primera cita válida del criterio, o `""`), `sugerencias_mejora` y
`profesor_revisor_pendiente=true`. Agrega `review_id`, `nota_preliminar`,
`observaciones[{id, criterio, tipo, severidad, texto, cita{version, version_id, texto, char_start, char_end}, estado_docente}]`,
`citas_descartadas` y `modelo`.

### Errores (`{"error": mensaje genérico, "code": ...}`)

| HTTP | code |
|---|---|
| 404 | `VERSION_NO_ENCONTRADA` |
| 422 | `RUBRICA_VACIA`, `ENTREGA_SIN_TEXTO`, validación del body |
| 500 | `SEUDONIMIZACION_FALLIDA`, `ERROR_INTERNO` |
| 502 | `IA_NO_DISPONIBLE`, `IA_RESPUESTA_INVALIDA`, `ALMACENAMIENTO_NO_DISPONIBLE` |
| 503 | `IA_NO_CONFIGURADA`, `ALMACENAMIENTO_NO_CONFIGURADO` |

## Revisión docente

| Endpoint | Body | Qué hace |
|---|---|---|
| `GET /api/v1/reviews/{id}` | — | Revisión, observaciones (las de la versión actual primero) y número de `pendientes`. |
| `PATCH /api/v1/observations/{id}` | `{actor, estado_docente: aceptada\|rechazada\|editada, texto?}` | RPC `decide_ai_observation`. `editada` exige texto; la cita no cambia. Devuelve la revisión. |
| `POST /api/v1/reviews/{id}/confirm` | `{actor, nota_final?}` | RPC `confirm_ai_review`. Exige cero pendientes; sin `nota_final`, confirma preliminar + décimas (tope 7,0). |

Errores: 403 `NO_AUTORIZADO` (el actor no es docente activo de la sección), 404 `NO_ENCONTRADO`,
409 `OBSERVACIONES_PENDIENTES` / `REVISION_CONFIRMADA`, 422 `DATOS_INVALIDOS`.

**Confianza (importante para IA-09).** El frontend usa sesión propia (HMAC), no Supabase Auth: no hay un JWT del
docente que reenviar. Las RPCs (solo `service_role`) reciben `actor` y lo fijan como `sub` únicamente en las claims
de su transacción. Así, `is_section_teacher` y el trigger de IA-04 (`confirmada_por = current_app_user_id()`)
verifican el rol real del usuario en la base. El servicio **confía en el `actor` que le entrega el puente**:
IA-09 debe tomarlo de la sesión validada en el servidor de Next, nunca del navegador, y no exponer estas rutas sin
verificar el rol. La llave de servicio sin un docente activo de la sección no basta para confirmar.
`GET /reviews` es servicio a servicio, como `/audit`: devuelve notas preliminares y citas, así que el puente debe
limitarlo al staff de la sección. Los estudiantes no deben verlo.

## Modelo y endpoint

- `EVALUATOR_MODEL` (por defecto `qwen/qwen3.8-27b` en OpenRouter, mismo modelo abierto que se hospedará en la UDP).
- `EVALUATOR_BASE_URL` / `EVALUATOR_API_KEY`: endpoint propio compatible con OpenAI (vLLM). Si van vacíos, se usa
  OpenRouter con `EVALUATOR_API_KEY` u `OPENROUTER_API_KEY`. Con un endpoint propio solo se envía
  `EVALUATOR_API_KEY` (puede ir vacía): la llave de OpenRouter nunca viaja a otro host, y tampoco los extras propios
  de OpenRouter (`usage.include`, cabeceras de atribución). Los embeddings siguen en OpenRouter.
- `LLM_JSON_SCHEMA_MODELS` acepta prefijos de modelo (`qwen/`) y prefijos de endpoint (`https://llm.udp.example/v1`).
  vLLM soporta `response_format: json_schema` con decodificación guiada. Si nada coincide, el schema se pide en el prompt.
- Un servidor propio no informa costo: `ai_audit_log.costo_usd` queda NULL y los tokens se registran igual.
- `extract_json` ignora bloques `<think>…</think>` (modelos con razonamiento visible servidos por vLLM).
- El prompt y el schema no usan rasgos de ningún proveedor (sin tool use ni restricciones numéricas en el schema).

Costo estimado en OpenRouter (precio del 2026-11-04: US$0,15 / M tokens de entrada y US$1,875 / M de salida),
con ~18 mil tokens de entrada y ~2,5 mil de salida: **≈ US$0,007 por entrega** (unos US$0,90 por sección de
40 estudiantes con 3 versiones). Un reintento por JSON inválido duplica el costo de esa entrega. Si el modelo
genera razonamiento oculto, esos tokens de salida también se cobran.

## Verificación

```bash
cd agent-services && .venv/bin/python -m pytest
DATABASE_URL=postgresql://localhost/<base_desechable> bash supabase/tests/run.sh   # incluye evaluador_real_test.sql
```
