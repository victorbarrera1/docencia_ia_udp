-- IA-08: RPCs del evaluador (propuesta, decisión docente y confirmación) con datos ficticios.
-- Todo corre en una transacción que se revierte. El harness usa ON_ERROR_STOP.
\set ON_ERROR_STOP 1
BEGIN;
CREATE SCHEMA ia_test;
GRANT USAGE ON SCHEMA ia_test TO anon, authenticated, service_role;
CREATE FUNCTION ia_test.expect(actual TEXT, expected TEXT, label TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'FALLA: % (esperado %, obtenido %)', label, expected, actual;
    END IF;
    RAISE NOTICE 'ok: %', label;
END $$;
CREATE FUNCTION ia_test.expect_error(stmt TEXT, expected_state TEXT, label TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE stmt;
    EXCEPTION WHEN OTHERS THEN
        IF SQLSTATE <> expected_state THEN RAISE; END IF;
        RAISE NOTICE 'ok (rechazado %): %', expected_state, label;
        RETURN;
    END;
    RAISE EXCEPTION 'FALLA: % (se permitió la sentencia)', label;
END $$;
-- Vuelve a la identidad del servicio (sin docente) entre pasos.
CREATE FUNCTION ia_test.as_service() RETURNS VOID LANGUAGE sql AS $$
    SELECT set_config('request.jwt.claim.sub', '', true), set_config('request.jwt.claims', '{"role":"service_role"}', true);
$$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ia_test TO anon, authenticated, service_role;
\ir modelo_ia_seed.sql

-- Texto de v1b: 'Diseño seguro con rúbrica y revisión docente.'; 'rúbrica y revisión' ocupa [18, 36).
\set obs_ok '[{"criterio":"Diseño","tipo":"criterio","severidad":"media","texto":"Cita válida ficticia","cita_texto":"rúbrica y revisión","cita_version_id":"f1040000-0000-4000-a000-000000000002","char_start":18,"char_end":36}]'
\set obs_bad '[{"criterio":"Diseño","tipo":"criterio","severidad":"media","texto":"Cita inventada","cita_texto":"texto que no existe","cita_version_id":"f1040000-0000-4000-a000-000000000002","char_start":0,"char_end":19}]'

-- Clientes sin la llave de servicio no pueden invocar las RPCs.
SELECT set_config('request.jwt.claims', json_build_object('sub', :'teacher', 'role', 'authenticated')::text, true);
SET ROLE authenticated;
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L)$q$, :'r1', :'teacher'), '42501',
    'un usuario autenticado no llama a confirm_ai_review directo');
SELECT ia_test.expect_error(format($q$SELECT public.create_ai_review(%L, 'x', 'x', 5, 0, '', '[]')$q$, :'v1b'), '42501',
    'un usuario autenticado no crea propuestas');
RESET ROLE;
SET ROLE anon;
SELECT ia_test.expect_error(format($q$SELECT public.decide_ai_observation(%L, %L, 'aceptada')$q$, :'r1', :'teacher'), '42501',
    'anon no decide observaciones');
RESET ROLE;

SELECT ia_test.as_service();
SET ROLE service_role;

-- Propuesta: revisión + observaciones en una transacción, sin nota_final.
SELECT (public.create_ai_review(:'v1b', 'submission_evaluator/evaluador-v1', 'fake/test', 5.0, 0.3, 'Resumen ficticio',
    :'obs_ok'::jsonb) ->> 'review_id') AS rnew \gset
SELECT ia_test.expect((SELECT estado || '|' || COALESCE(nota_final::text, 'null') FROM public.ai_reviews WHERE id = :'rnew'),
    'propuesta|null', 'propuesta sin nota final');
SELECT ia_test.expect((SELECT string_agg(estado_docente || ':' || cita_texto, ',') FROM public.ai_observations WHERE review_id = :'rnew'),
    'pendiente:rúbrica y revisión', 'observación queda pendiente con su cita literal');
SELECT ia_test.expect((SELECT count(*)::text FROM public.ai_reviews WHERE version_id = :'v1b'), '2', 'una revisión nueva');
SELECT ia_test.expect_error(format($q$SELECT public.create_ai_review(%L, 'x', 'fake/test', 5, 0, '', %L)$q$, :'v1b', :'obs_bad'),
    '23514', 'cita inventada rechazada por el trigger');
SELECT ia_test.expect((SELECT count(*)::text FROM public.ai_reviews WHERE version_id = :'v1b'), '2',
    'una cita inválida no deja revisión huérfana (todo o nada)');
SELECT ia_test.expect_error(format($q$SELECT public.create_ai_review(%L, 'x', 'fake/test', 5, 0, '', '{}')$q$, :'v1b'),
    '22023', 'observaciones deben ser arreglo');
SELECT ia_test.expect_error(format($q$SELECT public.create_ai_review(%L, 'x', 'fake/test', 7.5, 0, '', '[]')$q$, :'v1b'),
    '23514', 'nota preliminar fuera de escala');

SELECT (public.create_ai_review(:'v1b', 'x', 'fake/test', 6.9, 0.4, '', '[]') ->> 'review_id') AS rcap \gset
SELECT (public.create_ai_review(:'v1b', 'x', 'fake/test', 4.0, 0, '', '[]') ->> 'review_id') AS rplain \gset
-- La IA (service_role sin docente) sigue sin poder escribir nota_final directo.
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET estado = 'confirmada', nota_final = 6, confirmada_por = %L
    WHERE id = %L$q$, :'teacher', :'rnew'), '42501', 'servicio no confirma con UPDATE directo');

-- Confirmación: solo docente activo de la sección y sin observaciones pendientes.
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, NULL)$q$, :'rplain'), '42501', 'sin actor no se confirma');
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L)$q$, :'rplain', :'assistant'), '42501', 'ayudante no confirma');
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L)$q$, :'rplain', :'outsider'), '42501', 'externo no confirma');
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L)$q$, :'rplain', :'inactive'), '42501', 'docente inactivo no confirma');
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L)$q$, :'r3', :'teacher'), '42501', 'docente no confirma otra sección');
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L)$q$, 'a1040000-0000-4000-b000-0000000000ff', :'teacher'),
    'P0002', 'revisión inexistente');
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L)$q$, :'rnew', :'teacher'), 'IA001', 'pendientes bloquean la confirmación');
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L, 7.2)$q$, :'rplain', :'teacher'), '22023', 'nota final fuera de escala');
SELECT ia_test.expect((SELECT estado FROM public.ai_reviews WHERE id = :'rnew'), 'propuesta', 'los rechazos no cambian la revisión');

-- Decisiones del docente.
SELECT id AS obsnew FROM public.ai_observations WHERE review_id = :'rnew' \gset
SELECT ia_test.expect_error(format($q$SELECT public.decide_ai_observation(%L, %L, 'aceptada')$q$, :'obsnew', :'assistant'), '42501', 'ayudante no decide');
SELECT ia_test.expect_error(format($q$SELECT public.decide_ai_observation(%L, %L, 'pendiente')$q$, :'obsnew', :'teacher'), '22023', 'estado inválido');
SELECT ia_test.expect_error(format($q$SELECT public.decide_ai_observation(%L, %L, 'editada', '  ')$q$, :'obsnew', :'teacher'), '22023', 'editada exige texto');
SELECT ia_test.expect_error(format($q$SELECT public.decide_ai_observation(%L, %L, 'aceptada')$q$, 'a1040000-0000-4000-b000-0000000000ee', :'teacher'),
    'P0002', 'observación inexistente');
SELECT ia_test.expect((public.decide_ai_observation(:'obsnew', :'teacher', 'editada', ' Texto docente ficticio ') ->> 'texto'),
    'Texto docente ficticio', 'docente edita la observación');
SELECT ia_test.expect((SELECT estado_docente || '|' || cita_texto FROM public.ai_observations WHERE id = :'obsnew'),
    'editada|rúbrica y revisión', 'editar no altera la cita');
SELECT ia_test.as_service();

-- Confirmación válida: sin nota explícita, la propuesta (5,0 + 0,3).
SELECT ia_test.expect((public.confirm_ai_review(:'rnew', :'teacher') ->> 'nota_final'), '5.30', 'docente confirma la propuesta');
SELECT ia_test.expect((SELECT estado || '|' || confirmada_por || '|' || (confirmada_en = now())::text FROM public.ai_reviews WHERE id = :'rnew'),
    'confirmada|' || :'teacher' || '|true', 'confirmación conserva docente y fecha del servidor');
SELECT ia_test.as_service();
SELECT ia_test.expect_error(format($q$SELECT public.confirm_ai_review(%L, %L, 6)$q$, :'rnew', :'teacher'), 'IA002', 'no se reconfirma');
SELECT ia_test.expect_error(format($q$SELECT public.decide_ai_observation(%L, %L, 'rechazada')$q$, :'obsnew', :'teacher'), 'IA002',
    'no se cambian observaciones de una revisión confirmada');
SELECT ia_test.expect((public.confirm_ai_review(:'rcap', :'teacher') ->> 'nota_final'), '7.00', 'nota + décimas con tope 7,0');
SELECT ia_test.as_service();
SELECT ia_test.expect((public.confirm_ai_review(:'rplain', :'teacher', 5.84) ->> 'nota_final'), '5.80', 'nota explícita del docente, a un decimal');
SELECT ia_test.as_service();
RESET ROLE;

SET CONSTRAINTS ALL IMMEDIATE;
ROLLBACK;
\echo 'IA-08: todos los tests pasaron.'
