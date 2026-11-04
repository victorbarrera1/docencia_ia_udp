-- ==============================================================================
-- Tests de RLS por rol ([IA-01]). Datos 100 % ficticios; todo corre en una transacción
-- que se revierte al final (no deja rastro). Ejecutar con supabase/tests/run.sh.
-- Cada aserción que falla aborta con "FALLA: ..." (psql -v ON_ERROR_STOP=1).
-- ==============================================================================
\set ON_ERROR_STOP 1

BEGIN;

-- ------------------------------------------------------------------------------
-- Utilidades de aserción (esquema propio, desaparece con el ROLLBACK)
-- ------------------------------------------------------------------------------
CREATE SCHEMA rls_test;
GRANT USAGE ON SCHEMA rls_test TO anon, authenticated, service_role;

CREATE FUNCTION rls_test.expect(actual BIGINT, expected BIGINT, label TEXT) RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'FALLA: % (esperado %, obtenido %)', label, expected, actual;
    END IF;
    RAISE NOTICE 'ok: %', label;
END $$;

-- La sentencia debe fallar por permisos (sin privilegio o violación de política RLS: 42501)
CREATE FUNCTION rls_test.expect_denied(stmt TEXT, label TEXT) RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE stmt;
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'ok (denegado): %', label;
        RETURN;
    END;
    RAISE EXCEPTION 'FALLA: % (se permitió: %)', label, stmt;
END $$;

-- La sentencia debe ejecutarse y afectar exactamente N filas (0 = RLS la filtró en silencio)
CREATE FUNCTION rls_test.expect_rows(stmt TEXT, expected INT, label TEXT) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE n INT;
BEGIN
    EXECUTE stmt;
    GET DIAGNOSTICS n = ROW_COUNT;
    PERFORM rls_test.expect(n, expected, label);
END $$;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA rls_test TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- Fixtures (como superusuario)
-- ------------------------------------------------------------------------------
\set u_teacher   'aaaaaaaa-0000-4000-a000-000000000001'
\set u_assistant 'aaaaaaaa-0000-4000-a000-000000000002'
\set u_observer  'aaaaaaaa-0000-4000-a000-000000000003'
\set u_student1  'aaaaaaaa-0000-4000-a000-000000000004'
\set u_student2  'aaaaaaaa-0000-4000-a000-000000000005'
\set u_outsider  'aaaaaaaa-0000-4000-a000-000000000006'
\set u_inactive  'aaaaaaaa-0000-4000-a000-000000000007'

\set course1 'bbbbbbbb-0000-4000-a000-000000000001'
\set sec1    'bbbbbbbb-0000-4000-a000-000000000011'
\set sec2    'bbbbbbbb-0000-4000-a000-000000000012'
\set st1     'cccccccc-0000-4000-a000-000000000001'
\set st2     'cccccccc-0000-4000-a000-000000000002'
\set st3     'cccccccc-0000-4000-a000-000000000003'
\set cs1     'dddddddd-0000-4000-a000-000000000001'
\set cs2     'dddddddd-0000-4000-a000-000000000002'
\set act1    'eeeeeeee-0000-4000-a000-000000000001'
\set act2    'eeeeeeee-0000-4000-a000-000000000002'
\set act3    'eeeeeeee-0000-4000-a000-000000000003'

INSERT INTO public.users (id, email, name, role_global, canvas_user_id, is_active) VALUES
    (:'u_teacher',   'docente@example.test',   'Docente Ficticio',    'docente', NULL,   true),
    (:'u_assistant', 'ayudante@example.test',  'Ayudante Ficticio',   'docente', NULL,   true),
    (:'u_observer',  'observador@example.test','Observador Ficticio', 'docente', NULL,   true),
    (:'u_student1',  'alumno1@example.test',   'Alumno Uno',          'docente', 990101, true),
    (:'u_student2',  'alumno2@example.test',   'Alumno Dos',          'docente', 990102, true),
    (:'u_outsider',  'externo@example.test',   'Usuario Sin Curso',   'docente', NULL,   true),
    (:'u_inactive',  'inactivo@example.test',  'Docente Inactivo',    'docente', NULL,   false);

INSERT INTO public.courses (id, canvas_course_id, code, name) VALUES (:'course1', 990001, 'TEST1000', 'Curso de Prueba RLS');
INSERT INTO public.sections (id, course_id, code, name, pin_activo) VALUES
    (:'sec1', :'course1', 'TEST1000_S1', 'Sección 1', '1111'),
    (:'sec2', :'course1', 'TEST1000_S2', 'Sección 2', '2222');

INSERT INTO public.students (id, canvas_id, rut, nombres, apellidos, email) VALUES
    (:'st1', 990101, 'RUT-FICTICIO-1', 'Alumno', 'Uno',  'alumno1@example.test'),
    (:'st2', 990102, 'RUT-FICTICIO-2', 'Alumno', 'Dos',  'alumno2@example.test'),
    (:'st3', 990103, 'RUT-FICTICIO-3', 'Alumno', 'Tres', 'alumno3@example.test');

-- Staff con user_id + enrollment_type (role queda 'student' por defecto, como en producción)
INSERT INTO public.enrollments (section_id, user_id, enrollment_type) VALUES
    (:'sec1', :'u_teacher',   'TeacherEnrollment'),
    (:'sec1', :'u_assistant', 'TaEnrollment'),
    (:'sec1', :'u_observer',  'ObserverEnrollment'),
    (:'sec1', :'u_inactive',  'TeacherEnrollment');
INSERT INTO public.enrollments (section_id, student_id) VALUES
    (:'sec1', :'st1'), (:'sec1', :'st2'), (:'sec2', :'st3');

INSERT INTO public.class_sessions (id, section_id, session_code, date, dia_semana, type, pin) VALUES
    (:'cs1', :'sec1', 'test_s1_cat_1', '2026-11-04', 'Miércoles', 'catedra', '9991'),
    (:'cs2', :'sec2', 'test_s2_cat_1', '2026-11-04', 'Miércoles', 'catedra', '9992');

INSERT INTO public.attendance_records (session_id, student_id, value, ip_address) VALUES
    (:'cs1', :'st1', 1, '192.0.2.1'), (:'cs1', :'st2', 0, '192.0.2.2'), (:'cs2', :'st3', 1, '192.0.2.3');

INSERT INTO public.student_work_records (section_id, student_id, decimas_acumuladas) VALUES
    (:'sec1', :'st1', 0.2), (:'sec1', :'st2', 0.1), (:'sec2', :'st3', 0.3);

INSERT INTO public.activities (id, section_id, title, is_published) VALUES
    (:'act1', :'sec1', 'Taller publicado',     true),
    (:'act2', :'sec1', 'Taller borrador',      false),
    (:'act3', :'sec2', 'Taller otra sección',  true);

INSERT INTO public.activity_submissions (activity_id, student_id) VALUES
    (:'act1', :'st1'), (:'act1', :'st2'), (:'act3', :'st3');

-- Para usar los IDs dentro de format() en las sentencias de prueba
SELECT set_config('t.sec1', :'sec1', false), set_config('t.sec2', :'sec2', false),
       set_config('t.st1', :'st1', false), set_config('t.st2', :'st2', false),
       set_config('t.cs1', :'cs1', false), set_config('t.act1', :'act1', false),
       set_config('t.act3', :'act3', false), set_config('t.course1', :'course1', false);

-- ------------------------------------------------------------------------------
-- 1. Privilegios globales (catálogo)
-- ------------------------------------------------------------------------------
SELECT rls_test.expect(
    (SELECT count(*) FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND grantee = 'anon'), 0,
    'anon no tiene privilegios sobre ninguna tabla/vista de public');

SELECT rls_test.expect(
    (SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND NOT rowsecurity), 0,
    'todas las tablas de public tienen RLS activado');

SELECT rls_test.expect(
    (SELECT count(*) FROM pg_policies WHERE schemaname = 'public'
     AND (qual = 'true' OR with_check = 'true')), 0,
    'no queda ninguna política abierta USING (true)');

SELECT rls_test.expect(
    (SELECT count(DISTINCT table_name) FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND grantee = 'authenticated'
       AND table_name NOT IN ('courses', 'sections', 'students', 'enrollments', 'class_sessions',
                              'attendance_records', 'student_work_records', 'activities', 'activity_submissions')), 0,
    'authenticated sin privilegios fuera de las 9 tablas académicas (users, auth_*, vista, etc.)');

SELECT rls_test.expect(
    (SELECT count(*) FROM information_schema.column_privileges
     WHERE table_schema = 'public' AND grantee = 'authenticated' AND privilege_type = 'SELECT'
       AND (table_name, column_name) IN (('sections', 'pin_activo'), ('class_sessions', 'pin'),
                                         ('attendance_records', 'ip_address'), ('attendance_records', 'latitude'),
                                         ('attendance_records', 'longitude'))), 0,
    'authenticated no puede leer PIN, IP ni geolocalización');

SELECT rls_test.expect(
    (SELECT count(*) FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace
     AND has_function_privilege('anon', p.oid, 'EXECUTE')), 0,
    'anon no puede ejecutar ninguna función de public');

SELECT rls_test.expect(
    (SELECT count(*) FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace
     AND has_function_privilege('authenticated', p.oid, 'EXECUTE')
     AND p.proname NOT IN ('current_app_user_id', 'current_student_id', 'current_role_in_section',
                           'is_section_teacher', 'is_section_staff', 'is_section_member',
                           'is_staff_of_student', 'section_of_session', 'section_of_activity',
                           'uuid_generate_v4')), 0,
    'authenticated no puede ejecutar RPCs del servidor (asistencia, décimas, apelaciones)');

-- ------------------------------------------------------------------------------
-- 2. anon (clave pública sin sesión)
-- ------------------------------------------------------------------------------
SET ROLE anon;
SELECT rls_test.expect_denied('SELECT * FROM public.students', 'anon no lee estudiantes');
SELECT rls_test.expect_denied('SELECT * FROM public.sections', 'anon no lee secciones');
SELECT rls_test.expect_denied('UPDATE public.attendance_records SET value = 1', 'anon no edita asistencia');
SELECT rls_test.expect_denied('SELECT * FROM public.users', 'anon no lee usuarios');
SELECT rls_test.expect_denied($q$SELECT public.upsert_attendance_batch_by_codes('[]'::jsonb)$q$, 'anon no ejecuta RPC de asistencia masiva');
RESET ROLE;

-- ------------------------------------------------------------------------------
-- 3. authenticated sin usuario de la app (JWT sin sub, sub desconocido o usuario inactivo)
-- ------------------------------------------------------------------------------
SELECT set_config('request.jwt.claims', '{"role":"authenticated"}', false);
SET ROLE authenticated;
SELECT rls_test.expect((SELECT count(*) FROM public.students), 0, 'JWT sin sub no ve estudiantes');
SELECT rls_test.expect((SELECT count(*) FROM public.sections), 0, 'JWT sin sub no ve secciones');
RESET ROLE;

SELECT set_config('request.jwt.claims', json_build_object('sub', :'u_outsider')::text, false);
SET ROLE authenticated;
SELECT rls_test.expect((SELECT count(*) FROM public.students), 0, 'usuario sin matrícula no ve estudiantes');
SELECT rls_test.expect((SELECT count(*) FROM public.courses), 0, 'usuario sin matrícula no ve cursos');
SELECT rls_test.expect((SELECT count(*) FROM public.attendance_records), 0, 'usuario sin matrícula no ve asistencia');
SELECT rls_test.expect_denied('SELECT * FROM public.users', 'authenticated no lee la tabla users (hashes)');
SELECT rls_test.expect_denied('SELECT * FROM public.v_student_attendance_summary', 'authenticated no lee la vista resumen');
RESET ROLE;

SELECT set_config('request.jwt.claims', json_build_object('sub', :'u_inactive')::text, false);
SET ROLE authenticated;
SELECT rls_test.expect((SELECT count(*) FROM public.students), 0, 'docente inactivo no ve estudiantes');
SELECT rls_test.expect_rows(format($q$UPDATE public.sections SET horario_catedra_sala = 'X' WHERE id = %L$q$, current_setting('t.sec1')), 0,
    'docente inactivo no edita su sección');
RESET ROLE;

-- ------------------------------------------------------------------------------
-- 4. Docente de la sección 1
-- ------------------------------------------------------------------------------
SELECT set_config('request.jwt.claims', json_build_object('sub', :'u_teacher')::text, false);
SET ROLE authenticated;
SELECT rls_test.expect((SELECT count(*) FROM public.courses), 1, 'docente ve su curso');
SELECT rls_test.expect((SELECT count(*) FROM public.sections), 1, 'docente ve solo su sección');
SELECT rls_test.expect((SELECT count(*) FROM public.students), 2, 'docente ve los 2 estudiantes de su sección');
SELECT rls_test.expect((SELECT count(*) FROM public.students WHERE rut IS NOT NULL), 2, 'docente lee RUT de sus estudiantes');
SELECT rls_test.expect((SELECT count(*) FROM public.enrollments), 6, 'docente ve la matrícula completa de su sección');
SELECT rls_test.expect((SELECT count(*) FROM public.attendance_records), 2, 'docente ve asistencia de su sección');
SELECT rls_test.expect((SELECT count(*) FROM public.activities), 2, 'docente ve actividades publicadas y borradores');
SELECT rls_test.expect((SELECT count(*) FROM public.activity_submissions), 2, 'docente ve entregas de su sección');
SELECT rls_test.expect_denied('SELECT pin_activo FROM public.sections', 'docente no lee pin_activo por esta vía');
SELECT rls_test.expect_denied('SELECT ip_address FROM public.attendance_records', 'docente no lee IP de marcajes');

SELECT rls_test.expect_rows(format($q$UPDATE public.sections SET horario_catedra_sala = 'SALA T' WHERE id = %L$q$, current_setting('t.sec1')), 1,
    'docente actualiza configuración de su sección');
SELECT rls_test.expect_rows(format($q$UPDATE public.sections SET horario_catedra_sala = 'SALA T' WHERE id = %L$q$, current_setting('t.sec2')), 0,
    'docente no actualiza una sección ajena');
SELECT rls_test.expect_denied(format($q$UPDATE public.sections SET code = 'OTRO' WHERE id = %L$q$, current_setting('t.sec1')),
    'docente no cambia el código de la sección');
SELECT rls_test.expect_rows(format($q$UPDATE public.class_sessions SET status = 'cancelada' WHERE id = %L$q$, current_setting('t.cs1')), 1,
    'docente cancela una sesión de su sección');
SELECT rls_test.expect_rows(format($q$UPDATE public.student_work_records SET decimas_acumuladas = 0.4 WHERE section_id = %L$q$, current_setting('t.sec1')), 2,
    'docente edita décimas de su sección');
SELECT rls_test.expect_rows(format($q$UPDATE public.student_work_records SET decimas_acumuladas = 0.4 WHERE section_id = %L$q$, current_setting('t.sec2')), 0,
    'docente no edita décimas de otra sección');
SELECT rls_test.expect_rows(format($q$INSERT INTO public.activities (section_id, title) VALUES (%L, 'Nueva')$q$, current_setting('t.sec1')), 1,
    'docente crea actividad en su sección');
SELECT rls_test.expect_denied(format($q$INSERT INTO public.activities (section_id, title) VALUES (%L, 'Intrusa')$q$, current_setting('t.sec2')),
    'docente no crea actividad en sección ajena');
SELECT rls_test.expect_denied(format($q$INSERT INTO public.attendance_records (session_id, student_id, value) VALUES (%L, %L, 1)$q$,
    current_setting('t.cs1'), current_setting('t.st1')), 'escritura directa de asistencia solo vía servidor');
SELECT rls_test.expect_denied($q$INSERT INTO public.students (canvas_id, rut, nombres, apellidos, email) VALUES (1, 'R', 'N', 'A', 'e@example.test')$q$,
    'nómina de estudiantes solo vía servidor');
SELECT rls_test.expect_denied(format($q$INSERT INTO public.enrollments (section_id, student_id) VALUES (%L, %L)$q$,
    current_setting('t.sec1'), current_setting('t.st1')), 'matrícula solo vía servidor');
SELECT rls_test.expect_denied(format($q$INSERT INTO public.class_sessions (section_id, date, dia_semana, type) VALUES (%L, '2026-12-01', 'Martes', 'catedra')$q$,
    current_setting('t.sec1')), 'creación de sesiones solo vía servidor');
SELECT rls_test.expect_denied('DELETE FROM public.students', 'docente no borra estudiantes');
RESET ROLE;

-- ------------------------------------------------------------------------------
-- 5. Ayudante de la sección 1
-- ------------------------------------------------------------------------------
SELECT set_config('request.jwt.claims', json_build_object('sub', :'u_assistant')::text, false);
SET ROLE authenticated;
SELECT rls_test.expect((SELECT count(*) FROM public.students), 2, 'ayudante ve estudiantes de su sección');
SELECT rls_test.expect((SELECT count(*) FROM public.attendance_records), 2, 'ayudante ve asistencia de su sección');
SELECT rls_test.expect_rows(format($q$UPDATE public.activity_submissions SET feedback_text = 'Bien', status = 'corregido' WHERE activity_id = %L$q$,
    current_setting('t.act1')), 2, 'ayudante corrige entregas de su sección');
SELECT rls_test.expect_rows(format($q$UPDATE public.activity_submissions SET feedback_text = 'X' WHERE activity_id = %L$q$,
    current_setting('t.act3')), 0, 'ayudante no corrige entregas de otra sección');
SELECT rls_test.expect_rows(format($q$UPDATE public.sections SET horario_catedra_sala = 'X' WHERE id = %L$q$, current_setting('t.sec1')), 0,
    'ayudante no edita configuración de la sección');
SELECT rls_test.expect_rows(format($q$UPDATE public.student_work_records SET decimas_acumuladas = 0.6 WHERE section_id = %L$q$, current_setting('t.sec1')), 0,
    'ayudante no edita décimas acumuladas');
SELECT rls_test.expect_denied(format($q$INSERT INTO public.activities (section_id, title) VALUES (%L, 'X')$q$, current_setting('t.sec1')),
    'ayudante no crea actividades');
RESET ROLE;

-- ------------------------------------------------------------------------------
-- 6. Observador de la sección 1
-- ------------------------------------------------------------------------------
SELECT set_config('request.jwt.claims', json_build_object('sub', :'u_observer')::text, false);
SET ROLE authenticated;
SELECT rls_test.expect((SELECT count(*) FROM public.sections), 1, 'observador ve la sección');
SELECT rls_test.expect((SELECT count(*) FROM public.students), 0, 'observador no ve datos de estudiantes');
SELECT rls_test.expect((SELECT count(*) FROM public.attendance_records), 0, 'observador no ve asistencia');
RESET ROLE;

-- ------------------------------------------------------------------------------
-- 7. Estudiante 1 (sección 1)
-- ------------------------------------------------------------------------------
SELECT set_config('request.jwt.claims', json_build_object('sub', :'u_student1')::text, false);
SET ROLE authenticated;
SELECT rls_test.expect((SELECT count(*) FROM public.students), 1, 'estudiante ve solo su propia fila');
SELECT rls_test.expect((SELECT count(*) FROM public.students WHERE id = current_setting('t.st2')::uuid), 0,
    'estudiante no ve RUT ni correo de un compañero');
SELECT rls_test.expect((SELECT count(*) FROM public.enrollments), 1, 'estudiante ve solo su matrícula');
SELECT rls_test.expect((SELECT count(*) FROM public.sections), 1, 'estudiante ve su sección');
SELECT rls_test.expect((SELECT count(*) FROM public.class_sessions), 1, 'estudiante ve las sesiones de su sección');
SELECT rls_test.expect((SELECT count(*) FROM public.attendance_records), 1, 'estudiante ve solo su asistencia');
SELECT rls_test.expect((SELECT count(*) FROM public.student_work_records), 1, 'estudiante ve solo sus décimas');
SELECT rls_test.expect((SELECT count(*) FROM public.activities), 1, 'estudiante ve solo actividades publicadas de su sección');
SELECT rls_test.expect((SELECT count(*) FROM public.activity_submissions), 1, 'estudiante ve solo su entrega');
SELECT rls_test.expect_denied('SELECT pin_activo FROM public.sections', 'estudiante no lee el PIN de la sección');
SELECT rls_test.expect_denied('SELECT pin FROM public.class_sessions', 'estudiante no lee el PIN de la sesión');
SELECT rls_test.expect_rows($q$UPDATE public.student_work_records SET decimas_acumuladas = 0.6$q$, 0, 'estudiante no se sube décimas');
SELECT rls_test.expect_rows($q$UPDATE public.activity_submissions SET feedback_text = 'auto', decimas_obtained = 0.6$q$, 0,
    'estudiante no se corrige su entrega');
SELECT rls_test.expect_rows(format($q$UPDATE public.class_sessions SET status = 'cancelada' WHERE id = %L$q$, current_setting('t.cs1')), 0,
    'estudiante no cancela sesiones');
SELECT rls_test.expect_denied($q$UPDATE public.students SET email = 'otro@example.test'$q$, 'estudiante no edita datos personales');
SELECT rls_test.expect_denied(format($q$INSERT INTO public.attendance_records (session_id, student_id, value) VALUES (%L, %L, 1)$q$,
    current_setting('t.cs1'), current_setting('t.st1')), 'estudiante no se marca asistencia directo en la tabla');
RESET ROLE;

-- ------------------------------------------------------------------------------
-- 8. service_role (servidor) ignora RLS: la app actual sigue funcionando igual
-- ------------------------------------------------------------------------------
SET ROLE service_role;
SELECT rls_test.expect((SELECT count(*) FROM public.students), 3, 'service_role ve todos los estudiantes');
SELECT rls_test.expect((SELECT count(*) FROM public.sections WHERE pin_activo IS NOT NULL), 2, 'service_role lee pin_activo');
RESET ROLE;

ROLLBACK;
\echo 'RLS: todos los tests pasaron.'
