# [IA-01] RLS real y protección de datos de estudiantes
**Depende de:** 00 · **Ola:** 2 · **Modelo:** Opus · **Afecta al frontend: SÍ (avisar a Benjamín antes de llevar a upstream)**

## Problema
`schema.sql` tiene políticas `FOR ALL USING (true)` en courses, sections, students, enrollments, class_sessions, attendance_records, student_work_records (y revisar activities y activity_submissions). Con la anon key cualquiera puede leer RUT y correos y editar notas y asistencia.

## Pasos
1. **Auditoría primero (sin cambiar nada):** busca en `src/` cómo se accede a Supabase (anon key en el cliente vs service role en `src/app/api/**`). Entrega en el plan una tabla: tabla → quién la lee/escribe hoy → desde dónde.
2. Con eso, nueva migración `supabase/migrations/YYYYMMDD_rls_por_rol.sql`:
   - Helpers `security definer`: `current_role_in_section(section_id)` basado en `enrollments.role` y el usuario autenticado (revisa cómo se vincula hoy el usuario de auth con `students`/docentes en las migraciones `auth_*`).
   - Docente: todo en sus secciones. Ayudante: lectura de la sección y escritura de correcciones. Estudiante: solo sus filas.
   - `students.rut` y `students.email` no legibles por estudiantes de otros.
   - Escrituras masivas que hoy hace el frontend vía API → solo service role.
3. Reemplaza las políticas abiertas con `DROP POLICY IF EXISTS` + nuevas.
4. Actualiza `supabase/schema.sql` para que refleje el estado final.

## Tests
Script `supabase/tests/rls_test.sql` (o pytest con usuarios del seed) que pruebe por rol al menos: lectura permitida, lectura denegada, escritura denegada.

## Criterio de listo
`supabase db reset` sin errores, tests de RLS verdes, y el PR lista qué pantallas del frontend podrían romperse.
