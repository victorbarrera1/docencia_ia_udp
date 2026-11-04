# [IA-04] Modelo de datos para IA
**Depende de:** 01 · **Ola:** 3 · **Modelo:** Opus

## Entregables
- Migración `supabase/migrations/YYYYMMDD_modelo_ia.sql` con las tablas de la sección "Modelo de datos nuevo" del SPEC: `submission_versions`, `rag_chunks`, `ai_reviews`, `ai_observations`, `ai_audit_log`.
- Extensión `vector`; índice HNSW en `rag_chunks.embedding`.
- **Decisión de dimensión:** propón en el plan el modelo de embeddings (disponible vía OpenRouter u otro proveedor) y su dimensión; espera aprobación antes de fijar `vector(N)`.
- Función `next_version_n(submission_id)` y función `match_rag_chunks(query_embedding, owner_ids uuid[], k int)`.
- RLS coherente con IA-01: estudiantes nunca ven `nota_preliminar` ni observaciones `pendiente`; solo docente confirma (`nota_final`, `confirmada_por`).
- Restricción a nivel BD: `nota_final` solo puede escribirse si `estado = 'confirmada'`.
- Seed con datos ficticios para tests.
- Actualiza `supabase/schema.sql`.

## Pregunta abierta para el PR
Los proyectos del fondo son por **equipos**, pero `activity_submissions` es por estudiante. Revisa si el frontend ya maneja grupos (hay `src/app/api/canvas/courses/[courseId]/groups`) y propone en el PR cómo enlazar versión ↔ grupo, sin implementarlo si requiere cambiar el frontend.
