# [IA-05] Ingesta de PDF y versionado de entregas
**Depende de:** 04 · **Ola:** 4 · **Modelo:** Sonnet

## Entregables
- `rag/pdf_processor.py`: extracción con `pypdf`, normalización de espacios, offsets de caracteres por chunk (~800 tokens, solape 100), error claro si el PDF no tiene texto (escaneado).
- Bucket privado `entregas` en Supabase Storage, ruta `section/{id}/activity/{id}/submission/{id}/v{n}.pdf`.
- Endpoint `POST /api/v1/submissions/{submission_id}/versions` (protegido con `require_service_key` como los existentes): guarda PDF, crea `submission_versions` con `next_version_n`, marca `atrasada` según `activities.due_date`.
- Límite 20 MB y solo `application/pdf`.

## Tests
Fixture PDF de texto, PDF vacío, entrega atrasada, segunda versión que incrementa `version_n`.
