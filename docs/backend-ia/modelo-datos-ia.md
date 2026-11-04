# IA-04 — Contrato del modelo de datos

Implementación aditiva de las cinco tablas del SPEC. No modifica `src/`, las tablas de
asistencia ni datos de cursos reales. La migración `20261105_modelo_ia.sql` usa el siguiente
prefijo disponible para ejecutarse después de IA-01; el mismo bloque cierra `schema.sql`.

## Embeddings aprobados

- Proveedor: OpenRouter, `POST https://openrouter.ai/api/v1/embeddings`.
- Modelo: `openai/text-embedding-3-small`; dimensión: **1536**.
- Configuración: `EMBEDDING_MODEL` y `EMBEDDING_DIM`, expuestas como
  `settings.embedding_model` y `settings.embedding_dim` en `agent-services/config.py`.
- Precio consultado el 2026-11-04: **US$0,02 / millón de tokens** de entrada.
  Un corpus de 10 millones de tokens cuesta aproximadamente US$0,20 por pasada,
  sin incluir almacenamiento, consultas adicionales ni generación de respuestas.
- Referencias: [endpoint oficial](https://openrouter.ai/docs/api/api-reference/embeddings/submit-an-embedding-request)
  y [modelo y precio](https://openrouter.ai/openai/text-embedding-3-small).

IA-04 fija el contrato SQL; no hace llamadas ni consume créditos. La integración del cliente
de embeddings, la seudonimización previa y el reindexado corresponden a IA-06. Cambiar modelo
requiere migración de la columna, regeneración de embeddings y reindexación, incluso cuando
el nuevo modelo también produce 1536 dimensiones: no mezclar espacios vectoriales.

## Tablas, propietarios y offsets

| Tabla | Contrato |
|---|---|
| `submission_versions` | Una versión por `(submission_id, version_n)`. Archivo y texto extraído se insertan juntos; `service_role` no puede actualizar una versión existente. |
| `rag_chunks` | Identidad canónica `(owner_type, owner_id, idx)`. FKs generadas aseguran existencia y borrado en cascada de los tres tipos de propietario, sin duplicar identidades editables. |
| `ai_reviews` | Propuesta IA con nota 1–7 y décimas no negativas; confirmación docente separada. La versión de una revisión es inmutable. |
| `ai_observations` | Cita obligatoria y literal, perteneciente a la misma entrega y a su versión actual o una anterior. |
| `ai_audit_log` | Solo metadatos: actor, sección, versión, modelo, tokens, costo, latencia y resultado; sin campos de prompt ni respuesta. Inserción solamente para el servicio. |

`owner_type='version'` referencia `submission_versions.id`; `rubric` referencia
`activities.id` (`rubric_json` sigue siendo la rúbrica oficial); `course_material` referencia
`courses.id`. Para materiales, `idx` identifica chunks a nivel de curso: un catálogo de
documentos individuales queda pendiente. No interpretar `owner_id` como ID de Storage.

Los offsets cuentan caracteres Unicode, con base cero e intervalo `[char_start, char_end)`.
El rango tiene que tener la longitud del texto. Para chunks de versiones y observaciones,
el fragmento debe coincidir literalmente con el texto persistido; IA-05 debe normalizar
antes de persistir y calcular los offsets sobre ese mismo texto. Una versión citada no se
puede borrar dejando observaciones huérfanas: esa FK se verifica al cerrar la transacción,
permitiendo las cascadas completas de eliminación de una entrega o curso.

El error de auditoría es un código sanitizado (`LLM_TIMEOUT`, por ejemplo), nunca el mensaje
crudo del proveedor. `costo_usd=NULL` significa costo desconocido; cero significa costo cero.
Al borrar la fuente, los metadatos de auditoría permanecen con sus FKs anuladas. El servicio
no tiene permisos de `UPDATE` ni `DELETE` en el log. IA-07 implementará el listener persistente.

## Permisos

Todas las tablas tienen RLS y revocación explícita para `anon`. Las políticas usan únicamente
los helpers de identidad/rol de IA-01 y las relaciones académicas existentes; no se crean
helpers alternativos de autorización ni se cambia el modelo multiinstitución de IA-01.

| Rol | Lectura | Escritura directa nueva |
|---|---|---|
| Docente | Versiones, chunks, revisiones, observaciones y auditoría de su sección; materiales de sus cursos. | Confirmar/corregir revisiones y decidir/editar observaciones de su sección. |
| Ayudante | Mismo alcance de lectura de staff. | Ninguna. |
| Estudiante | Sus versiones/chunks, rúbricas publicadas y materiales de sus cursos. | Ninguna. |
| Observador | Rúbricas publicadas y materiales de sus cursos. | Ninguna. |
| Sin matrícula / inactivo / `anon` | Ningún dato IA. | Ninguna. |
| `service_role` | Procesamiento backend de las cinco tablas. | Versiones nuevas, chunks, propuestas y observaciones; agregar auditoría. |

Los estudiantes **no leen filas de `ai_reviews` ni de `ai_observations`**, tampoco cuando la
revisión está confirmada o la observación aceptada. Una vista/API filtrada para estudiantes
es un pendiente explícito; no se implementa en IA-04.

La confirmación requiere `estado='confirmada'`, nota final y docente identificado. El trigger
exige `is_section_teacher` y que `confirmada_por=current_app_user_id()`, y fija la fecha en el
servidor. También exige identidad docente cuando el servicio intenta cambiar una confirmación.
Un proceso IA con `service_role` sin identidad docente no puede confirmar. Un futuro puente
backend debe autenticar al docente y conservar su identidad; la llave del servicio sola no
representa una aprobación humana. Las credenciales administrativas siguen siendo de confianza.

## RPCs

`next_version_n(submission_id uuid)` es `SECURITY INVOKER`, solo para `service_role`. Bloquea
la entrega y calcula el siguiente entero. Llamar e insertar **dentro de una misma transacción
READ COMMITTED**, antes de `COMMIT`; dos llamadas HTTP separadas no reservan un número. La
restricción única es la última defensa. IA-05 deberá usar una transacción de base de datos
para esta operación, no dos peticiones independientes a PostgREST.

`match_rag_chunks(query_embedding vector, owner_ids uuid[], k int)` es `SECURITY INVOKER`
y está concedida a usuarios autenticados y al servicio. Acepta dimensión 1536 y `k` de 1 a 100;
propietarios nulos/vacíos producen cero resultados. Devuelve identidad, texto, offsets y
similitud coseno, ordenados por distancia. Excluye chunks aún sin embedding. El filtro de
propietarios no reemplaza RLS: un UUID conocido no permite leer entregas ajenas. El servicio,
que ignora RLS por diseño de Supabase, debe autorizar y acotar propietarios en el backend.

## Verificación local y CI

```bash
brew install pgvector
brew services restart postgresql@17
createdb docencia_ia04_test
DATABASE_URL=postgresql://localhost/docencia_ia04_test bash supabase/tests/run.sh
cd agent-services
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest
```

Usar exclusivamente una base local vacía y desechable. El harness mantiene ambos escenarios:
A, esquema + migraciones + reaplicación idempotente; B, instalación solo desde `schema.sql`.
Cada uno ejecuta los tests IA-01, los asserts IA-04 y dos conexiones que compiten por la
misma entrega. Las fixtures son ficticias; el test principal revierte su transacción y el
harness limpia `public`, incluidas las fixtures compartidas del test concurrente, al salir.
CI ejecuta el mismo harness sobre `pgvector/pgvector:pg17`. pgvector se instala en `extensions`,
fuera de las funciones de aplicación de `public`; una instalación previa en otro esquema
debe revisarse antes de aplicar la migración en un destino compartido.

Pytest fija `LLM_FAKE=0` antes de importar la configuración, como ya hace con las llaves de
proveedores. Esto evita que un `.env` local con `LLM_FAKE=` impida cargar la suite. No cambia
la configuración de ejecución de la aplicación ni habilita llamadas externas en tests.

## Propuesta pendiente: equipos frente a estudiantes

La API `src/app/api/canvas/courses/[courseId]/groups/route.ts` ya entrega `canvasGroupId` e
integrantes identificados por `canvas_id`; `src/types/groups.ts` define `CourseGroup`. El
workspace de grupos sincroniza con Canvas y persiste mediante `src/services/groupsStore.ts`
en `localStorage`. Su mapeo actual no conserva explícitamente `canvasGroupId`, aunque la API
y el tipo lo incluyen. No hay tablas de grupos en `supabase/schema.sql`.

Propuesta para otra tarea: persistir grupos por institución/curso con un UUID interno e ID
Canvas, enlazar una versión al grupo mediante una relación explícita y guardar una fotografía
de integrantes al momento de la entrega. Mantener `activity_submissions` por estudiante para
trazabilidad y aprobación de cada destinatario; compartir archivo/revisión grupal sin asumir
que el grupo actual coincide con el de una versión histórica. La publicación de notas por
integrante seguirá requiriendo aprobación docente. Definir ese contrato y preservar
`canvasGroupId` requiere trabajo coordinado con el frontend; no se implementa aquí.
