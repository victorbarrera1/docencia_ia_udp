# Backend IA — Spec (Act-IA / Docencia IA UDP)

Responsable: Víctor Barrera (backend: integración de APIs de IA, RAG, seguridad y privacidad).
Fuente: postulación al Fondo Estudiantil Talento IA-UDP 2026 + código actual de `staging`.

## Punto de partida (estado real de `staging`)

| Pieza | Estado | Qué falta |
|---|---|---|
| `agent-services/` FastAPI, árbol jerárquico, A2A | Funciona | Tests (no hay carpeta `tests/`) |
| `core/openrouter_client.py` | Llama a OpenRouter | Salida JSON forzada, reintentos, modo fake, hook de auditoría |
| `agents/specialists/submission_evaluator.py` | **Simulación**: 85 % fijo y cita inventada | Evaluación real, validación de citas, persistencia |
| `rag/retriever.py`, `memory/vector_store.py` | **En memoria**, coincidencia léxica | pgvector real, embeddings, chunks con offsets |
| `rag/pdf_processor.py` | Básico | Extracción con offsets, PDFs escaneados |
| Seudonimización | Solo en la rama `cursor/feat-agentai-9e9d` | Llevarla a `staging`, cubrir RUT, correos y nombres |
| `supabase/schema.sql` RLS | **Abierta**: `FOR ALL USING (true)` en courses, students, enrollments, etc. | Políticas por rol; proteger RUT y correos |
| Entregas | `activity_submissions` (una por estudiante, sin archivo ni texto) | Versiones, texto extraído, revisiones de IA |
| Auditoría de IA | No existe | `ai_audit_log` |

## Principios (comprometidos en el fondo)

1. La IA nunca pone notas: produce `nota_preliminar`; existe nota final solo cuando el docente confirma.
2. Cada observación cita el fragmento exacto; observación sin cita verificable se descarta.
3. Seudonimización antes de cualquier llamada externa (nombres, RUT, correos, teléfonos).
4. Registro auditable de cada uso de IA (quién, cuándo, modelo, tokens, costo), sin guardar el texto enviado.
5. Proveedor vía OpenRouter (ya elegido por el equipo), con modelo configurable por agente.

## Modelo de datos nuevo (se agrega, no reemplaza)

Sobre las tablas existentes `courses → sections → activities → activity_submissions` y `students`:

- `submission_versions` (id, submission_id → activity_submissions, version_n, storage_path, texto_extraido, subido_por, subido_en, atrasada bool) — único (submission_id, version_n).
- `rag_chunks` (id, owner_type `version|rubric|course_material`, owner_id, idx, texto, char_start, char_end, embedding vector(N)).
- `ai_reviews` (id, version_id, agente, modelo, estado `propuesta|confirmada|corregida`, nota_preliminar, decimas_preliminares, resumen, confirmada_por, confirmada_en, nota_final).
- `ai_observations` (id, review_id, criterio, tipo `criterio|feedback_no_abordado|retroceso|inconsistencia|atraso`, severidad `baja|media|alta`, texto, cita_texto, cita_version_id, char_start, char_end, estado_docente `pendiente|aceptada|rechazada|editada`).
- `ai_audit_log` (id, ts, actor, section_id, version_id, agente, modelo, tokens_in, tokens_out, costo_usd, latencia_ms, ok, error).

La rúbrica sigue viviendo en `activities.rubric_json` (formato `RubricItem` de `core/models.py`).
Dimensión del embedding `N`: se fija en la tarea IA-04 según el modelo elegido (decisión del equipo).

## Contrato de salida del evaluador (JSON)

```json
{
  "resumen": "string",
  "nota_preliminar": 5.4,
  "decimas_preliminares": 0.2,
  "observaciones": [
    {
      "criterio": "string | null",
      "tipo": "criterio | feedback_no_abordado | retroceso | inconsistencia | atraso",
      "severidad": "baja | media | alta",
      "texto": "Qué se observa y por qué",
      "cita": { "version": "actual | anterior", "texto": "fragmento literal" }
    }
  ]
}
```

Validación obligatoria tras el LLM: `cita.texto` debe aparecer literalmente (normalizando espacios) en la versión indicada; si no, la observación se descarta y se cuenta en el log. Escala 1.0–7.0.
La respuesta actual `SubmissionEvaluationResponse` se mantiene compatible (el frontend ya la usa) y se extiende.

## Tareas y olas

Ver `docs/backend-ia/tasks/README.md`.
