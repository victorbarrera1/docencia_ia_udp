-- Datos 100% ficticios. Solo incluir desde el harness en una base vacía y desechable.
\set teacher   'a1040000-0000-4000-a000-000000000001'
\set assistant 'a1040000-0000-4000-a000-000000000002'
\set observer  'a1040000-0000-4000-a000-000000000003'
\set student1  'a1040000-0000-4000-a000-000000000004'
\set student2  'a1040000-0000-4000-a000-000000000005'
\set outsider  'a1040000-0000-4000-a000-000000000006'
\set inactive  'a1040000-0000-4000-a000-000000000007'
\set course1 'b1040000-0000-4000-a000-000000000001'
\set course2 'b1040000-0000-4000-a000-000000000002'
\set sec1 'b1040000-0000-4000-a000-000000000011'
\set sec2 'b1040000-0000-4000-a000-000000000012'
\set st1 'c1040000-0000-4000-a000-000000000001'
\set st2 'c1040000-0000-4000-a000-000000000002'
\set st3 'c1040000-0000-4000-a000-000000000003'
\set act1 'd1040000-0000-4000-a000-000000000001'
\set act2 'd1040000-0000-4000-a000-000000000002'
\set draft 'd1040000-0000-4000-a000-000000000003'
\set sub1 'e1040000-0000-4000-a000-000000000001'
\set sub2 'e1040000-0000-4000-a000-000000000002'
\set sub3 'e1040000-0000-4000-a000-000000000003'
\set v1  'f1040000-0000-4000-a000-000000000001'
\set v1b 'f1040000-0000-4000-a000-000000000002'
\set v2  'f1040000-0000-4000-a000-000000000003'
\set v3  'f1040000-0000-4000-a000-000000000004'
\set r1 'a1040000-0000-4000-b000-000000000001'
\set r2 'a1040000-0000-4000-b000-000000000002'
\set r3 'a1040000-0000-4000-b000-000000000003'

INSERT INTO public.users (id, email, name, canvas_user_id, is_active) VALUES
    (:'teacher', 'ia-docente@example.test', 'Docente IA Ficticio', NULL, true),
    (:'assistant', 'ia-ayudante@example.test', 'Ayudante IA Ficticio', NULL, true),
    (:'observer', 'ia-observador@example.test', 'Observador IA Ficticio', NULL, true),
    (:'student1', 'ia-alumno1@example.test', 'Estudiante IA Uno', 990401, true),
    (:'student2', 'ia-alumno2@example.test', 'Estudiante IA Dos', 990402, true),
    (:'outsider', 'ia-externo@example.test', 'Externo IA Ficticio', NULL, true),
    (:'inactive', 'ia-inactivo@example.test', 'Inactivo IA Ficticio', NULL, false);
INSERT INTO public.courses (id, canvas_course_id, code, name) VALUES
    (:'course1', 990410, 'TEST_IA_1', 'Sandbox IA ficticio'),
    (:'course2', 990420, 'TEST_IA_2', 'Otro sandbox IA ficticio');
INSERT INTO public.sections (id, course_id, code, name) VALUES
    (:'sec1', :'course1', 'TEST_IA_S1', 'Sandbox IA sección 1'),
    (:'sec2', :'course2', 'TEST_IA_S2', 'Sandbox IA sección 2');
INSERT INTO public.students (id, canvas_id, rut, nombres, apellidos, email) VALUES
    (:'st1', 990401, 'IA-FICTICIO-1', 'Estudiante', 'Uno', 'ia-alumno1@example.test'),
    (:'st2', 990402, 'IA-FICTICIO-2', 'Estudiante', 'Dos', 'ia-alumno2@example.test'),
    (:'st3', 990403, 'IA-FICTICIO-3', 'Estudiante', 'Tres', 'ia-alumno3@example.test');
INSERT INTO public.enrollments (section_id, user_id, enrollment_type) VALUES
    (:'sec1', :'teacher', 'TeacherEnrollment'), (:'sec1', :'assistant', 'TaEnrollment'),
    (:'sec1', :'observer', 'ObserverEnrollment'), (:'sec1', :'inactive', 'TeacherEnrollment');
INSERT INTO public.enrollments (section_id, student_id) VALUES
    (:'sec1', :'st1'), (:'sec1', :'st2'), (:'sec2', :'st3');
INSERT INTO public.activities (id, section_id, title, is_published, rubric_json) VALUES
    (:'act1', :'sec1', 'Actividad IA ficticia', true, '[{"criterio":"Diseño","puntaje_max":1,"descriptores":[]}]'),
    (:'act2', :'sec2', 'Actividad IA ajena', true, '[]'),
    (:'draft', :'sec1', 'Rúbrica IA borrador', false, '[]');
INSERT INTO public.activity_submissions (id, activity_id, student_id) VALUES
    (:'sub1', :'act1', :'st1'), (:'sub2', :'act1', :'st2'), (:'sub3', :'act2', :'st3');
INSERT INTO public.submission_versions (id, submission_id, version_n, storage_path, texto_extraido, subido_por) VALUES
    (:'v1', :'sub1', 1, 'test-ia/v1.pdf', 'Texto inicial ficticio.', :'student1'),
    (:'v1b', :'sub1', 2, 'test-ia/v2.pdf', 'Diseño seguro con rúbrica y revisión docente.', :'student1'),
    (:'v2', :'sub2', 1, 'test-ia/otra.pdf', 'Trabajo de otra persona.', :'student2'),
    (:'v3', :'sub3', 1, 'test-ia/ajena.pdf', 'Entrega de otro curso.', :'teacher');

-- Vectores unitarios artificiales; nunca se llama a un proveedor ni se usan créditos.
SELECT ('[' || array_to_string(ARRAY[1.0] || array_fill(0.0, ARRAY[1535]), ',') || ']') AS embedding_x,
       ('[' || array_to_string(ARRAY[0.0, 1.0] || array_fill(0.0, ARRAY[1534]), ',') || ']') AS embedding_y \gset
INSERT INTO public.rag_chunks (owner_type, owner_id, idx, texto, char_start, char_end, embedding) VALUES
    ('version', :'v1b', 0, 'Diseño seguro', 0, 13, :'embedding_x'),
    ('version', :'v2', 0, 'Trabajo', 0, 7, :'embedding_y'),
    ('version', :'v3', 0, 'Entrega', 0, 7, :'embedding_x'),
    ('rubric', :'act1', 0, 'Diseño', 0, 6, :'embedding_x'),
    ('rubric', :'draft', 0, 'Borrador', 0, 8, :'embedding_x'),
    ('course_material', :'course1', 0, 'Material ficticio', 0, 17, :'embedding_x'),
    ('course_material', :'course2', 0, 'Material ajeno', 0, 14, :'embedding_x');
INSERT INTO public.ai_reviews (id, version_id, agente, modelo, nota_preliminar, decimas_preliminares, resumen) VALUES
    (:'r1', :'v1b', 'test/evaluador', 'fake/test', 5.4, 0.2, 'Resumen ficticio'),
    (:'r2', :'v2', 'test/evaluador', 'fake/test', 4.0, 0.1, 'Otro resumen ficticio'),
    (:'r3', :'v3', 'test/evaluador', 'fake/test', 6.0, 0.3, 'Resumen ajeno ficticio');
INSERT INTO public.ai_observations (review_id, tipo, severidad, texto, cita_texto, cita_version_id, char_start, char_end, estado_docente) VALUES
    (:'r1', 'criterio', 'baja', 'Observación ficticia', 'Diseño seguro', :'v1b', 0, 13, 'pendiente'),
    (:'r1', 'retroceso', 'media', 'Referencia histórica ficticia', 'Texto inicial', :'v1', 0, 13, 'aceptada'),
    (:'r1', 'inconsistencia', 'alta', 'Observación rechazada ficticia', 'Diseño seguro', :'v1b', 0, 13, 'rechazada'),
    (:'r2', 'feedback_no_abordado', 'baja', 'Observación de compañero ficticia', 'Trabajo', :'v2', 0, 7, 'editada'),
    (:'r3', 'atraso', 'baja', 'Observación ajena ficticia', 'Entrega', :'v3', 0, 7, 'pendiente');
INSERT INTO public.ai_audit_log (actor, section_id, version_id, agente, modelo, tokens_in, tokens_out, costo_usd, latencia_ms, ok, error) VALUES
    (:'teacher', :'sec1', :'v1b', 'test/evaluador', 'fake/test', 100, 20, 0.000002, 25, true, NULL),
    (:'teacher', :'sec2', :'v3', 'test/evaluador', 'fake/test', 0, 0, NULL, 30, false, 'LLM_TIMEOUT');
