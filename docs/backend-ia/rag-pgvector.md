# IA-06 — RAG con pgvector

Embeddings reales sobre `rag_chunks` (IA-04), con los chunks que deja la ingesta (IA-05) y el cliente (IA-03).

## Flujo

1. **Cliente.** `OpenRouterClient.embeddings(texts)` llama a `POST /embeddings` con `EMBEDDING_MODEL`; valida cantidad,
   orden y `EMBEDDING_DIM`; reutiliza reintentos y dispara el hook de auditoría (IA-07): un lote = una fila en `ai_audit_log`.
   Con `LLM_FAKE=1` devuelve vectores deterministas (bolsa de palabras con hashing), sin red.
2. **Privacidad.** `rag/embedder.py` (`PrivateEmbedder`) es el único camino al cliente de embeddings. Seudonimiza cada
   texto con `core/privacy.py` y la lista de estudiantes de la sección (ordenada por `students.id`) y comprueba que el
   resultado sea estable. Si no se puede cargar la lista, no se envía nada. `rag_chunks.texto` conserva el original
   (los offsets de las citas dependen de él); solo el texto enviado al proveedor está seudonimizado.
3. **Indexado.** `rag/indexer.py` escribe `embedding` y `embedding_model` con la RPC `set_rag_chunk_embeddings`, por lotes.
   La RPC solo actualiza el chunk si su texto sigue siendo el embebido.
4. **Recuperación.** `RagRetriever.retrieve_for_criterion(criterio, version_id, k)` busca en la versión actual y en la
   inmediatamente anterior de la misma entrega con `match_rag_chunks`. `CourseKnowledgeRetriever.retrieve_context`
   conserva la interfaz histórica y busca en el material del curso (devuelve `[]` si no hay material o configuración).

## Idempotencia y modelo

- Identidad de un chunk: `(owner_type, owner_id, idx)`. Reindexar no crea filas nuevas.
- Sin `force`: solo se embeben los chunks con `embedding IS NULL`. Los ya embebidos con **otro** modelo no se tocan, pero se
  cuentan en `omitidos_modelo_distinto` (aviso de mezcla de espacios vectoriales).
- `force=true`: además re-embebe los chunks cuyo `embedding_model` no coincide con `EMBEDDING_MODEL`
  (o es NULL con embedding presente). Con `LLM_FAKE=1` la etiqueta es `fake/<modelo>`, así que nunca se confunde con datos reales.
- Rúbrica: un chunk por criterio de `activities.rubric_json` (`Criterio (máx. N pts): descriptor; ...`). `replace_rag_chunks`
  conserva el embedding de los criterios sin cambios, anula el de los modificados y borra los que desaparecen.
- Material de curso: reemplaza el corpus completo del curso (aún no hay catálogo de documentos, ver IA-04).

## Endpoints (llave de servicio)

| Endpoint | Qué hace |
|---|---|
| `POST /api/v1/rag/versions/{id}/index?force=` | Embebe los chunks pendientes de una versión |
| `POST /api/v1/rag/activities/{id}/rubric/index?force=` | Indexa la rúbrica de una actividad |
| `POST /api/v1/rag/courses/{id}/material?force=` | Indexa material de cátedra (`{"texto": "..."}`) |
| `POST /api/v1/rag/backfill?limit=&force=` | Embebe hasta `limit` (máx. 1000) chunks pendientes de cualquier propietario |

`POST /api/v1/submissions/{id}/versions` agenda el indexado de la versión nueva en segundo plano si `RAG_AUTO_INDEX` está
activo (por defecto sí). Si falla, los chunks quedan con `embedding NULL` y el backfill los recoge.

## Límites conocidos

- `core/privacy.py` solo detecta nombres que estén en la lista de la sección: un nombre mal escrito o de una persona no
  matriculada saldría hacia el proveedor. RUT, correos y teléfonos se detectan siempre.
- `retrieve_for_criterion` devuelve el texto original. IA-08 debe volver a seudonimizar lo recuperado antes de enviarlo a un LLM.
- `match_rag_chunks` no filtra por `embedding_model`; durante una migración de modelo usar `force` hasta que
  `omitidos_modelo_distinto` sea 0 antes de buscar.
- Dos indexados simultáneos del mismo propietario pueden embeber dos veces (se paga dos veces; el resultado es el mismo).

## Verificación

```bash
cd agent-services && python -m pytest && python evals/run_evals.py
DATABASE_URL=postgresql://localhost/<base_vacia_desechable> bash supabase/tests/run.sh
```
