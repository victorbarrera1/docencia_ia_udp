-- ==============================================================================
-- MIGRACIÓN 2026-11-04 — [IA-01] RLS por rol y protección de datos de estudiantes
-- ------------------------------------------------------------------------------
-- Requiere: 20261016_fase3c_bloqueo_rls.sql (deja la base cerrada) y public.users.
--
-- Estado anterior: anon y authenticated sin privilegios ni políticas; todo pasa por el
-- servidor con service_role (que ignora RLS). Esta migración NO cambia eso para la app actual:
--   * anon sigue sin acceso a nada.
--   * authenticated (JWT de Supabase cuyo "sub" = public.users.id) obtiene acceso mínimo por
--     rol en cada sección: docente, ayudante, observador o estudiante.
--   * Las escrituras masivas (nómina, matrícula, sesiones, asistencia) siguen solo en service_role.
-- No modifica datos, RPCs ni triggers de Asistencia. Idempotente.
-- ==============================================================================

-- 1. Columnas creadas hoy por scripts/normalize_canvas_schema.js (ya existen en producción) ---
ALTER TABLE public.enrollments ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE public.enrollments ADD COLUMN IF NOT EXISTS enrollment_type VARCHAR(50) DEFAULT 'StudentEnrollment';
ALTER TABLE public.enrollments ADD COLUMN IF NOT EXISTS canvas_enrollment_id BIGINT NULL;
ALTER TABLE public.enrollments ALTER COLUMN student_id DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_enrollments_user_id ON public.enrollments(user_id);

-- 2. Helpers de identidad y rol (SECURITY DEFINER: leen users/enrollments sin depender de RLS) ---

-- Usuario de la app asociado al JWT (null si no existe o está inactivo)
CREATE OR REPLACE FUNCTION public.current_app_user_id()
RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT u.id FROM public.users u WHERE u.id = auth.uid() AND u.is_active IS TRUE
$$;

-- Fila de students del usuario (vínculo por ID de Canvas)
CREATE OR REPLACE FUNCTION public.current_student_id()
RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT s.id
    FROM public.students s
    JOIN public.users u ON u.canvas_user_id = s.canvas_id
    WHERE u.id = public.current_app_user_id() AND s.is_active IS TRUE
    LIMIT 1
$$;

-- Rol más alto del usuario en la sección: teacher > assistant > observer > student (o null).
-- Docentes y ayudantes se matriculan con user_id + enrollment_type (role queda en 'student' por
-- defecto), por eso enrollment_type manda cuando es de staff.
CREATE OR REPLACE FUNCTION public.current_role_in_section(p_section_id UUID)
RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT r.role_name
    FROM (
        SELECT CASE e.enrollment_type
                   WHEN 'TeacherEnrollment'  THEN 'teacher'
                   WHEN 'TaEnrollment'       THEN 'assistant'
                   WHEN 'ObserverEnrollment' THEN 'observer'
                   ELSE e.role
               END AS role_name
        FROM public.enrollments e
        WHERE e.section_id = p_section_id
          AND COALESCE(e.status, 'active') = 'active'
          AND (
              (e.user_id IS NOT NULL AND e.user_id = public.current_app_user_id())
              OR (e.student_id IS NOT NULL AND e.student_id = public.current_student_id())
          )
    ) r
    ORDER BY CASE r.role_name WHEN 'teacher' THEN 1 WHEN 'assistant' THEN 2 WHEN 'observer' THEN 3 ELSE 4 END
    LIMIT 1
$$;

CREATE OR REPLACE FUNCTION public.is_section_teacher(p_section_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT COALESCE(public.current_role_in_section(p_section_id) = 'teacher', false) $$;

-- Docente o ayudante (ven datos de estudiantes de la sección)
CREATE OR REPLACE FUNCTION public.is_section_staff(p_section_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT COALESCE(public.current_role_in_section(p_section_id) IN ('teacher', 'assistant'), false) $$;

CREATE OR REPLACE FUNCTION public.is_section_member(p_section_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT public.current_role_in_section(p_section_id) IS NOT NULL $$;

-- ¿El estudiante está matriculado en alguna sección donde el usuario es staff?
CREATE OR REPLACE FUNCTION public.is_staff_of_student(p_student_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.enrollments e
        WHERE e.student_id = p_student_id AND public.is_section_staff(e.section_id)
    )
$$;

CREATE OR REPLACE FUNCTION public.section_of_session(p_session_id UUID)
RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT cs.section_id FROM public.class_sessions cs WHERE cs.id = p_session_id $$;

CREATE OR REPLACE FUNCTION public.section_of_activity(p_activity_id UUID)
RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT a.section_id FROM public.activities a WHERE a.id = p_activity_id $$;

DO $$
DECLARE fn TEXT;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.current_app_user_id()', 'public.current_student_id()',
        'public.current_role_in_section(uuid)', 'public.is_section_teacher(uuid)',
        'public.is_section_staff(uuid)', 'public.is_section_member(uuid)',
        'public.is_staff_of_student(uuid)', 'public.section_of_session(uuid)',
        'public.section_of_activity(uuid)'
    ] LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon', fn);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated, service_role', fn);
    END LOOP;
END $$;

-- 3. Privilegios: se parte de cero para anon/authenticated y se concede lo mínimo ------------
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;

-- Los DEFAULT de id llaman a uuid_generate_v4(): quien inserta necesita poder ejecutarla
DO $$
BEGIN
    IF to_regprocedure('uuid_generate_v4()') IS NOT NULL THEN
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated', to_regprocedure('uuid_generate_v4()'));
    END IF;
END $$;

GRANT SELECT ON public.courses TO authenticated;

-- sections: sin pin_activo (el PIN del día solo lo entrega el servidor)
GRANT SELECT (
    id, course_id, canvas_section_id, code, name, teacher_name, teacher_email, assistant_name, assistant_email,
    horario_catedra_dias, horario_catedra_inicio, horario_catedra_fin, horario_catedra_sala,
    horario_ayudantia_dias, horario_ayudantia_inicio, horario_ayudantia_fin, horario_ayudantia_sala,
    horario_ayudantia2, requiere_pin, requiere_geo, incluir_ayudantias_en_final, created_at, updated_at
) ON public.sections TO authenticated;
GRANT UPDATE (
    horario_catedra_dias, horario_catedra_inicio, horario_catedra_fin, horario_catedra_sala,
    horario_ayudantia_dias, horario_ayudantia_inicio, horario_ayudantia_fin, horario_ayudantia_sala,
    horario_ayudantia2, pin_activo, requiere_pin, requiere_geo, incluir_ayudantias_en_final
) ON public.sections TO authenticated;

GRANT SELECT ON public.students TO authenticated;
GRANT SELECT ON public.enrollments TO authenticated;

-- class_sessions: sin pin
GRANT SELECT (
    id, section_id, session_code, date, dia_semana, type, modality, status, motivo_cancelacion,
    start_time, end_time, room, created_at, updated_at
) ON public.class_sessions TO authenticated;
GRANT UPDATE (status, motivo_cancelacion, modality, start_time, end_time, room) ON public.class_sessions TO authenticated;

-- attendance_records: sin IP ni geolocalización; escritura solo vía RPCs del servidor
GRANT SELECT (id, session_id, student_id, value, marked_by, marked_at) ON public.attendance_records TO authenticated;

GRANT SELECT, INSERT, UPDATE ON public.student_work_records TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.activities TO authenticated;
GRANT SELECT, INSERT ON public.activity_submissions TO authenticated;
GRANT UPDATE (status, decimas_obtained, feedback_text, evaluated_by, evaluated_at) ON public.activity_submissions TO authenticated;

-- La vista corre con los permisos de quien consulta (no salta RLS) y sigue cerrada
ALTER VIEW IF EXISTS public.v_student_attendance_summary SET (security_invoker = true);
REVOKE ALL ON public.v_student_attendance_summary FROM anon, authenticated;

-- 4. Políticas (reemplazan cualquier política abierta previa) ------------------------------
ALTER TABLE public.courses              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sections             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.students             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.enrollments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.class_sessions       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attendance_records   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.student_work_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activities           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activity_submissions ENABLE ROW LEVEL SECURITY;

-- Políticas abiertas históricas (schema.sql y la migración de asistencia del 2026-10-11)
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['courses', 'sections', 'students', 'enrollments', 'class_sessions',
                             'attendance_records', 'student_work_records', 'activities', 'activity_submissions'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS "lectura" ON public.%I', t);
        EXECUTE format('DROP POLICY IF EXISTS "creacion" ON public.%I', t);
        EXECUTE format('DROP POLICY IF EXISTS "actualizacion" ON public.%I', t);
    END LOOP;
END $$;
DROP POLICY IF EXISTS "Permitir lectura general de cursos" ON public.courses;
DROP POLICY IF EXISTS "Permitir edición general de cursos" ON public.courses;
DROP POLICY IF EXISTS "Permitir lectura de secciones" ON public.sections;
DROP POLICY IF EXISTS "Permitir edición de secciones" ON public.sections;
DROP POLICY IF EXISTS "Permitir lectura de estudiantes" ON public.students;
DROP POLICY IF EXISTS "Permitir edición de estudiantes" ON public.students;
DROP POLICY IF EXISTS "Permitir lectura de matricula" ON public.enrollments;
DROP POLICY IF EXISTS "Permitir edición de matricula" ON public.enrollments;
DROP POLICY IF EXISTS "Permitir lectura de sesiones" ON public.class_sessions;
DROP POLICY IF EXISTS "Permitir edición de sesiones" ON public.class_sessions;
DROP POLICY IF EXISTS "Permitir lectura de asistencia" ON public.attendance_records;
DROP POLICY IF EXISTS "Permitir edición de asistencia" ON public.attendance_records;
DROP POLICY IF EXISTS "Permitir lectura de work records" ON public.student_work_records;
DROP POLICY IF EXISTS "Permitir edición de work records" ON public.student_work_records;

-- courses: miembros de alguna sección del curso
DROP POLICY IF EXISTS rls_courses_select ON public.courses;
CREATE POLICY rls_courses_select ON public.courses FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM public.sections s WHERE s.course_id = courses.id AND public.is_section_member(s.id)));

-- sections: miembros leen; solo el docente actualiza la configuración
DROP POLICY IF EXISTS rls_sections_select ON public.sections;
CREATE POLICY rls_sections_select ON public.sections FOR SELECT TO authenticated
    USING (public.is_section_member(id));
DROP POLICY IF EXISTS rls_sections_update_teacher ON public.sections;
CREATE POLICY rls_sections_update_teacher ON public.sections FOR UPDATE TO authenticated
    USING (public.is_section_teacher(id)) WITH CHECK (public.is_section_teacher(id));

-- students: cada estudiante solo su fila; docente/ayudante, los de sus secciones (RUT y correo ajenos protegidos)
DROP POLICY IF EXISTS rls_students_select ON public.students;
CREATE POLICY rls_students_select ON public.students FOR SELECT TO authenticated
    USING (id = public.current_student_id() OR public.is_staff_of_student(id));

-- enrollments: staff ve la matrícula de su sección; cada uno, la propia
DROP POLICY IF EXISTS rls_enrollments_select ON public.enrollments;
CREATE POLICY rls_enrollments_select ON public.enrollments FOR SELECT TO authenticated
    USING (
        public.is_section_staff(section_id)
        OR (student_id IS NOT NULL AND student_id = public.current_student_id())
        OR (user_id IS NOT NULL AND user_id = public.current_app_user_id())
    );

-- class_sessions: miembros leen; el docente cambia estado/horario (crear sesiones: solo servidor)
DROP POLICY IF EXISTS rls_class_sessions_select ON public.class_sessions;
CREATE POLICY rls_class_sessions_select ON public.class_sessions FOR SELECT TO authenticated
    USING (public.is_section_member(section_id));
DROP POLICY IF EXISTS rls_class_sessions_update_teacher ON public.class_sessions;
CREATE POLICY rls_class_sessions_update_teacher ON public.class_sessions FOR UPDATE TO authenticated
    USING (public.is_section_teacher(section_id)) WITH CHECK (public.is_section_teacher(section_id));

-- attendance_records: staff lee su sección; estudiante, solo lo suyo. Sin escritura directa.
DROP POLICY IF EXISTS rls_attendance_records_select ON public.attendance_records;
CREATE POLICY rls_attendance_records_select ON public.attendance_records FOR SELECT TO authenticated
    USING (public.is_section_staff(public.section_of_session(session_id)) OR student_id = public.current_student_id());

-- student_work_records: staff lee; docente escribe; estudiante lee lo suyo
DROP POLICY IF EXISTS rls_work_records_select ON public.student_work_records;
CREATE POLICY rls_work_records_select ON public.student_work_records FOR SELECT TO authenticated
    USING (public.is_section_staff(section_id) OR student_id = public.current_student_id());
DROP POLICY IF EXISTS rls_work_records_insert_teacher ON public.student_work_records;
CREATE POLICY rls_work_records_insert_teacher ON public.student_work_records FOR INSERT TO authenticated
    WITH CHECK (public.is_section_teacher(section_id));
DROP POLICY IF EXISTS rls_work_records_update_teacher ON public.student_work_records;
CREATE POLICY rls_work_records_update_teacher ON public.student_work_records FOR UPDATE TO authenticated
    USING (public.is_section_teacher(section_id)) WITH CHECK (public.is_section_teacher(section_id));

-- activities: staff lee; docente gestiona; estudiante ve las publicadas de su sección
DROP POLICY IF EXISTS rls_activities_select ON public.activities;
CREATE POLICY rls_activities_select ON public.activities FOR SELECT TO authenticated
    USING (public.is_section_staff(section_id) OR (is_published AND public.is_section_member(section_id)));
DROP POLICY IF EXISTS rls_activities_insert_teacher ON public.activities;
CREATE POLICY rls_activities_insert_teacher ON public.activities FOR INSERT TO authenticated
    WITH CHECK (public.is_section_teacher(section_id));
DROP POLICY IF EXISTS rls_activities_update_teacher ON public.activities;
CREATE POLICY rls_activities_update_teacher ON public.activities FOR UPDATE TO authenticated
    USING (public.is_section_teacher(section_id)) WITH CHECK (public.is_section_teacher(section_id));
DROP POLICY IF EXISTS rls_activities_delete_teacher ON public.activities;
CREATE POLICY rls_activities_delete_teacher ON public.activities FOR DELETE TO authenticated
    USING (public.is_section_teacher(section_id));

-- activity_submissions: staff lee; docente crea; docente y ayudante corrigen; estudiante lee lo suyo
DROP POLICY IF EXISTS rls_submissions_select ON public.activity_submissions;
CREATE POLICY rls_submissions_select ON public.activity_submissions FOR SELECT TO authenticated
    USING (public.is_section_staff(public.section_of_activity(activity_id)) OR student_id = public.current_student_id());
DROP POLICY IF EXISTS rls_submissions_insert_teacher ON public.activity_submissions;
CREATE POLICY rls_submissions_insert_teacher ON public.activity_submissions FOR INSERT TO authenticated
    WITH CHECK (public.is_section_teacher(public.section_of_activity(activity_id)));
DROP POLICY IF EXISTS rls_submissions_update_staff ON public.activity_submissions;
CREATE POLICY rls_submissions_update_staff ON public.activity_submissions FOR UPDATE TO authenticated
    USING (public.is_section_staff(public.section_of_activity(activity_id)))
    WITH CHECK (public.is_section_staff(public.section_of_activity(activity_id)));
