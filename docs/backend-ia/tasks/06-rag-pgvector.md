# [IA-06] RAG real con pgvector
**Depende de:** 03, 04, 05 · **Ola:** 4 · **Modelo:** Sonnet

## Entregables
- `memory/vector_store.py`: reemplaza el adaptador en memoria por uno sobre `rag_chunks` + `match_rag_chunks`. Mantén un modo en memoria solo para tests.
- Embeddings vía el modelo fijado en IA-04, a través del cliente de IA-03; texto **seudonimizado** (IA-02) antes de embeber.
- Indexar: versiones de entrega, criterios de `activities.rubric_json` y material de curso.
- `rag/retriever.py`: dado un criterio, top-k de la versión actual y de la anterior. Conserva la interfaz `retrieve_context` para no romper `engine.py`.
- Reindexado idempotente por versión.

## Tests
Con embeddings fake deterministas: indexar, recuperar, reindexar sin duplicar.
