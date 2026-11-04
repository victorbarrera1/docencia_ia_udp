-- IA-04: esquema, integridad y RLS con datos ficticios. El harness usa ON_ERROR_STOP.
\set ON_ERROR_STOP 1
BEGIN;
CREATE SCHEMA ia_test;
GRANT USAGE ON SCHEMA ia_test TO anon, authenticated, service_role;
CREATE FUNCTION ia_test.expect(actual BIGINT, expected BIGINT, label TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
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
CREATE FUNCTION ia_test.expect_rows(stmt TEXT, expected BIGINT, label TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE n BIGINT;
BEGIN
    EXECUTE stmt;
    GET DIAGNOSTICS n = ROW_COUNT;
    PERFORM ia_test.expect(n, expected, label);
END $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ia_test TO anon, authenticated, service_role;
\ir modelo_ia_seed.sql

-- Esquema e índices reales, no mocks.
SELECT ia_test.expect((SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND rowsecurity
    AND tablename IN ('submission_versions', 'rag_chunks', 'ai_reviews', 'ai_observations', 'ai_audit_log')), 5, 'las cinco tablas IA tienen RLS');
SELECT ia_test.expect((SELECT count(*) FROM pg_extension WHERE extname = 'vector' AND extnamespace = 'extensions'::regnamespace), 1,
    'pgvector instalado fuera de public');
SELECT ia_test.expect((SELECT count(*) FROM pg_attribute WHERE attrelid = 'public.rag_chunks'::regclass
    AND attname = 'embedding' AND format_type(atttypid, atttypmod) = 'extensions.vector(1536)'), 1, 'columna vector de 1536 dimensiones');
SELECT ia_test.expect((SELECT count(*) FROM pg_class c JOIN pg_am a ON a.oid = c.relam
    JOIN pg_index i ON i.indexrelid = c.oid WHERE c.relname = 'idx_rag_chunks_embedding'
    AND a.amname = 'hnsw' AND i.indisvalid), 1, 'índice HNSW válido');
SELECT ia_test.expect(public.next_version_n(:'sub1'), 3, 'siguiente versión tras las dos existentes');
SELECT ia_test.expect_error(format('SELECT public.next_version_n(%L)', '00000000-0000-4000-a000-000000000000'), '23503', 'entrega inexistente no recibe versión');
SELECT ia_test.expect_error(format($q$INSERT INTO public.submission_versions (submission_id, version_n, storage_path, subido_por)
    VALUES (%L, 1, 'test/duplicada.pdf', %L)$q$, :'sub1', :'teacher'), '23505', 'número de versión único por entrega');
SELECT ia_test.expect_error(format($q$INSERT INTO public.submission_versions (submission_id, version_n, storage_path, subido_por)
    VALUES (%L, 0, 'test/cero.pdf', %L)$q$, :'sub1', :'teacher'), '23514', 'numeración de versiones positiva');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET nota_preliminar = 7.1 WHERE id = %L$q$, :'r1'), '23514', 'escala preliminar 1 a 7');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET estado = 'publicada' WHERE id = %L$q$, :'r1'), '23514', 'estados de revisión restringidos');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET version_id = %L WHERE id = %L$q$, :'v2', :'r1'), '23514', 'revisión no puede trasladarse a otra entrega');
SELECT ia_test.expect_error(format($q$UPDATE public.rag_chunks SET embedding = '[1,2,3]' WHERE owner_id = %L$q$, :'v1b'), '22000', 'rechaza embeddings de otra dimensión');
SELECT ia_test.expect_error(format($q$UPDATE public.rag_chunks SET char_start = -1 WHERE owner_id = %L$q$, :'act1'), '23514', 'offsets no negativos');
SELECT ia_test.expect_error(format($q$UPDATE public.rag_chunks SET owner_id = %L WHERE owner_id = %L$q$,
    '00000000-0000-4000-a000-000000000000', :'act1'), '23514', 'propietario polimórfico debe existir');
SELECT ia_test.expect_error(format($q$UPDATE public.rag_chunks SET texto = 'Cita falsaxxx' WHERE owner_id = %L$q$, :'v1b'), '23514', 'chunk de versión debe coincidir con el texto fuente');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_observations SET cita_texto = 'Cita inventada' WHERE review_id = %L$q$, :'r1'), '23514', 'cita inventada rechazada');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_observations SET cita_version_id = %L WHERE review_id = %L$q$, :'v2', :'r1'), '23514', 'cita no puede venir de otro estudiante');
SELECT ia_test.expect_error(format($q$INSERT INTO public.ai_reviews (version_id, agente, modelo)
    VALUES (%L, 'test/historia', 'fake/test');$q$, '00000000-0000-4000-a000-000000000000'), '23503', 'revisión requiere versión existente');
INSERT INTO public.ai_reviews (version_id, agente, modelo) VALUES (:'v1', 'test/historia', 'fake/test') RETURNING id AS historical_review \gset
SELECT ia_test.expect_error(format($q$INSERT INTO public.ai_observations
    (review_id, tipo, severidad, texto, cita_texto, cita_version_id, char_start, char_end)
    VALUES (%L, 'criterio', 'baja', 'Ficticia', 'Diseño seguro', %L, 0, 13)$q$, :'historical_review', :'v1b'), '23514', 'revisión histórica no cita versiones futuras');
DELETE FROM public.ai_reviews WHERE id = :'historical_review';
SELECT ia_test.expect_error($q$INSERT INTO public.ai_audit_log (agente, modelo, ok, error)
    VALUES ('test', 'fake/test', false, 'error con texto privado')$q$, '23514', 'auditoría solo acepta códigos de error sanitizados');
SELECT ia_test.expect_error($q$INSERT INTO public.ai_audit_log (agente, modelo, ok, tokens_in)
    VALUES ('test', 'fake/test', true, -1)$q$, '23514', 'auditoría no acepta tokens negativos');
SELECT ia_test.expect_error($q$INSERT INTO public.ai_audit_log (agente, modelo, ok, costo_usd)
    VALUES ('test', 'fake/test', true, -0.1)$q$, '23514', 'auditoría no acepta costo negativo');

-- anon no lee tablas ni ejecuta RPCs IA.
SET ROLE anon;
SELECT ia_test.expect_error('SELECT * FROM public.submission_versions', '42501', 'anon sin versiones');
SELECT ia_test.expect_error('SELECT * FROM public.rag_chunks', '42501', 'anon sin chunks');
SELECT ia_test.expect_error('SELECT * FROM public.ai_reviews', '42501', 'anon sin revisiones');
SELECT ia_test.expect_error('SELECT * FROM public.ai_observations', '42501', 'anon sin observaciones');
SELECT ia_test.expect_error('SELECT * FROM public.ai_audit_log', '42501', 'anon sin auditoría');
SELECT ia_test.expect_error(format('SELECT public.next_version_n(%L)', :'sub1'), '42501', 'anon sin RPC de versiones');
RESET ROLE;
SELECT ia_test.expect(has_function_privilege('anon', 'public.match_rag_chunks(extensions.vector,uuid[],integer)', 'EXECUTE')::int, 0,
    'anon sin RPC de búsqueda');

-- JWT sin identidad, externo e inactivo: no heredan acceso de staff.
SELECT set_config('request.jwt.claims', '{"role":"authenticated"}', true);
SET ROLE authenticated;
SELECT ia_test.expect((SELECT count(*) FROM public.submission_versions), 0, 'sin sub no hay versiones');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews), 0, 'sin sub no hay revisiones');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks), 0, 'sin sub no hay chunks');
RESET ROLE;
SELECT set_config('request.jwt.claims', json_build_object('sub', :'outsider')::text, true);
SET ROLE authenticated;
SELECT ia_test.expect((SELECT count(*) FROM public.submission_versions), 0, 'externo sin versiones');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews), 0, 'externo sin revisiones');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_observations), 0, 'externo sin observaciones');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_audit_log), 0, 'externo sin auditoría');
SELECT ia_test.expect((SELECT count(*) FROM public.match_rag_chunks(:'embedding_x', ARRAY[:'v1b', :'v3']::uuid[], 10)), 0, 'UUID conocido no da acceso al externo');
RESET ROLE;
SELECT set_config('request.jwt.claims', json_build_object('sub', :'inactive')::text, true);
SET ROLE authenticated;
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews), 0, 'docente inactivo sin revisiones');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks), 0, 'docente inactivo sin chunks');
SELECT ia_test.expect_rows('UPDATE public.ai_reviews SET estado = ''corregida''', 0, 'docente inactivo no cambia revisiones');
RESET ROLE;

-- Ayudante puede leer las propuestas, nunca confirmar ni revisar observaciones.
SELECT set_config('request.jwt.claims', json_build_object('sub', :'assistant')::text, true);
SET ROLE authenticated;
SELECT ia_test.expect((SELECT count(*) FROM public.submission_versions), 3, 'ayudante ve versiones de su sección');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews WHERE nota_preliminar IS NOT NULL), 2, 'ayudante ve preliminares de su sección');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_observations), 4, 'ayudante ve observaciones de su sección');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_audit_log), 1, 'ayudante ve metadatos de su sección');
SELECT ia_test.expect_rows(format($q$UPDATE public.ai_reviews SET estado = 'confirmada', nota_final = 6, confirmada_por = %L$q$, :'assistant'), 0, 'ayudante no confirma notas');
SELECT ia_test.expect_rows('UPDATE public.ai_observations SET estado_docente = ''aceptada''', 0, 'ayudante no decide observaciones');
SELECT ia_test.expect_error($q$INSERT INTO public.ai_reviews (version_id, agente, modelo)
    VALUES (NULL, 'test', 'fake/test')$q$, '42501', 'authenticated no inserta propuestas como IA');
RESET ROLE;

-- Docente: confirmación de su sección, identidad no suplantable y restricciones de nota.
SELECT set_config('request.jwt.claims', json_build_object('sub', :'teacher')::text, true);
SET ROLE authenticated;
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews), 2, 'docente ve revisiones solo de su sección');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks), 5, 'docente ve chunks solo de su curso');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET nota_final = 6 WHERE id = %L$q$, :'r1'), '23514', 'nota final prohibida sin estado confirmada');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET estado = 'confirmada', nota_final = 6, confirmada_por = %L
    WHERE id = %L$q$, :'assistant', :'r1'), '42501', 'docente no suplanta confirmada_por');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET estado = 'confirmada', nota_final = 7.1, confirmada_por = %L
    WHERE id = %L$q$, :'teacher', :'r1'), '23514', 'nota final fuera de escala rechazada');
SELECT ia_test.expect_rows(format($q$UPDATE public.ai_reviews SET estado = 'confirmada', nota_final = 5.8, confirmada_por = %L,
    confirmada_en = '1900-01-01' WHERE id = %L$q$, :'teacher', :'r1'), 1, 'docente confirma nota de su sección');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews WHERE id = :'r1' AND nota_final = 5.8
    AND confirmada_por = :'teacher' AND confirmada_en = now()), 1, 'confirmación conserva docente y fecha fijada por servidor');
SELECT ia_test.expect_rows(format($q$UPDATE public.ai_reviews SET estado = 'confirmada', nota_final = 6, confirmada_por = %L
    WHERE id = %L$q$, :'teacher', :'r3'), 0, 'docente no confirma revisión de otro curso');
SELECT ia_test.expect_rows(format($q$UPDATE public.ai_observations SET estado_docente = 'aceptada'
    WHERE review_id = %L AND estado_docente = 'pendiente'$q$, :'r1'), 1, 'docente acepta observación propia');
SELECT ia_test.expect_rows(format($q$UPDATE public.ai_observations SET estado_docente = 'aceptada'
    WHERE review_id = %L$q$, :'r3'), 0, 'docente no acepta observación de otro curso');
SELECT ia_test.expect_error('UPDATE public.ai_reviews SET nota_preliminar = 7', '42501', 'preliminar generada no editable por cliente');
SELECT ia_test.expect_error(format('SELECT public.next_version_n(%L)', :'sub1'), '42501', 'asignación de versiones solo servidor');
SELECT ia_test.expect_error('UPDATE public.ai_audit_log SET tokens_in = 0', '42501', 'docente no altera auditoría');
SELECT ia_test.expect_error('DELETE FROM public.ai_audit_log', '42501', 'docente no borra auditoría');
RESET ROLE;

-- Estudiante: historial y chunks propios, jamás notas preliminares ni observaciones (ni confirmadas).
SELECT set_config('request.jwt.claims', json_build_object('sub', :'student1')::text, true);
SET ROLE authenticated;
SELECT ia_test.expect((SELECT count(*) FROM public.submission_versions), 2, 'estudiante ve sus dos versiones');
SELECT ia_test.expect((SELECT count(*) FROM public.submission_versions WHERE id = :'v2'), 0, 'estudiante no ve versión de compañero');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews), 0, 'estudiante sin revisiones incluso confirmadas');
SELECT ia_test.expect((SELECT count(nota_preliminar) FROM public.ai_reviews), 0, 'estudiante nunca obtiene nota preliminar');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_observations), 0, 'estudiante sin observaciones de ningún estado');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_audit_log), 0, 'estudiante sin auditoría');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks), 3, 'estudiante ve chunk propio, rúbrica publicada y material de su curso');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks WHERE owner_id = :'draft'), 0, 'estudiante no ve rúbrica borrador');
SELECT ia_test.expect((SELECT count(*) FROM public.match_rag_chunks(:'embedding_x', ARRAY[:'v1b', :'v2', :'v3']::uuid[], 10)), 1, 'búsqueda RAG respeta propiedad incluso con UUID ajenos');
SELECT ia_test.expect((SELECT count(*) FROM public.match_rag_chunks(:'embedding_x', ARRAY[:'v1b']::uuid[], 1) WHERE similarity > 0.999), 1,
    'búsqueda calcula similitud coseno');
SELECT ia_test.expect((SELECT count(*) FROM public.match_rag_chunks(:'embedding_x', '{}'::uuid[], 10)), 0, 'lista de propietarios vacía no busca globalmente');
SELECT ia_test.expect((SELECT count(*) FROM public.match_rag_chunks(:'embedding_x', NULL, 10)), 0, 'lista nula no busca globalmente');
SELECT ia_test.expect_error(format('SELECT * FROM public.match_rag_chunks(''[1,2,3]'', ARRAY[%L]::uuid[], 1)', :'v1b'), '22023', 'RPC rechaza dimensión incorrecta');
SELECT ia_test.expect_error(format('SELECT * FROM public.match_rag_chunks(%L, ARRAY[%L]::uuid[], 0)', :'embedding_x', :'v1b'), '22023', 'RPC rechaza k cero');
SELECT ia_test.expect_error(format('SELECT * FROM public.match_rag_chunks(%L, ARRAY[%L]::uuid[], 101)', :'embedding_x', :'v1b'), '22023', 'RPC limita k a 100');
SELECT ia_test.expect_rows('UPDATE public.ai_reviews SET nota_final = 7', 0, 'estudiante no se confirma nota');
SELECT ia_test.expect_rows('UPDATE public.ai_observations SET estado_docente = ''aceptada''', 0, 'estudiante no acepta observaciones');
SELECT ia_test.expect_error(format($q$INSERT INTO public.submission_versions (submission_id, version_n, storage_path, subido_por)
    VALUES (%L, 3, 'test/cliente.pdf', %L)$q$, :'sub1', :'student1'), '42501', 'subida directa de versiones deshabilitada');
RESET ROLE;
SELECT set_config('request.jwt.claims', json_build_object('sub', :'student2')::text, true);
SET ROLE authenticated;
SELECT ia_test.expect((SELECT count(*) FROM public.submission_versions), 1, 'otro estudiante solo ve su propia versión');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews), 0, 'otro estudiante sin revisiones');
RESET ROLE;
SELECT set_config('request.jwt.claims', json_build_object('sub', :'observer')::text, true);
SET ROLE authenticated;
SELECT ia_test.expect((SELECT count(*) FROM public.submission_versions), 0, 'observador sin entregas');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews), 0, 'observador sin revisiones');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_observations), 0, 'observador sin observaciones');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_audit_log), 0, 'observador sin auditoría');
RESET ROLE;

-- La IA usa service_role sin identidad docente: RLS no basta, el trigger también debe impedirlo.
SELECT set_config('request.jwt.claims', '{"role":"service_role"}', true);
SET ROLE service_role;
SELECT ia_test.expect((SELECT count(*) FROM public.ai_reviews), 3, 'servidor ve revisiones para procesamiento');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_observations), 5, 'servidor ve observaciones para procesamiento');
SELECT ia_test.expect(public.next_version_n(:'sub1'), 3, 'servidor puede asignar siguiente versión');
SELECT ia_test.expect_error('UPDATE public.submission_versions SET texto_extraido = ''Otro texto''', '42501',
    'servidor no altera versiones que respaldan citas y offsets');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET estado = 'confirmada', nota_final = 6,
    confirmada_por = %L WHERE id = %L$q$, :'teacher', :'r2'), '42501', 'IA no confirma aunque use service_role');
SELECT ia_test.expect_error(format($q$INSERT INTO public.ai_reviews (version_id, agente, modelo, estado, nota_final, confirmada_por)
    VALUES (%L, 'test', 'fake/test', 'confirmada', 6, %L)$q$, :'v1', :'teacher'), '42501', 'IA tampoco inserta una nota confirmada');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET estado = 'corregida', nota_final = NULL,
    confirmada_por = NULL, confirmada_en = NULL WHERE id = %L$q$, :'r1'), '42501', 'IA no revoca una confirmación docente');
SELECT ia_test.expect_error(format($q$UPDATE public.ai_reviews SET nota_final = 7 WHERE id = %L$q$, :'r1'), '42501',
    'IA no cambia una nota ya confirmada');
SELECT ia_test.expect_error('UPDATE public.ai_audit_log SET tokens_in = 0', '42501', 'servidor tampoco altera auditoría');
SELECT ia_test.expect_error('DELETE FROM public.ai_audit_log', '42501', 'servidor tampoco borra auditoría');
SELECT ia_test.expect_rows(format($q$INSERT INTO public.ai_audit_log (section_id, agente, modelo, ok, error)
    VALUES (%L, 'test', 'fake/test', false, 'LLM_TIMEOUT')$q$, :'sec1'), 1, 'servidor agrega metadatos de auditoría');
RESET ROLE;

-- Limpieza de propietarios polimórficos en cascada.
DELETE FROM public.activities WHERE id = :'draft';
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks WHERE owner_id = :'draft'), 0, 'eliminar rúbrica limpia sus chunks');
DELETE FROM public.courses WHERE id = :'course2';
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks WHERE owner_id IN (:'course2', :'v3', :'act2')), 0, 'eliminar curso limpia chunks de todos sus propietarios');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_audit_log WHERE section_id IS NULL AND version_id IS NULL), 1,
    'auditoría conserva metadatos tras eliminar fuente');
SET CONSTRAINTS ALL IMMEDIATE;
ROLLBACK;
\echo 'IA-04: todos los tests pasaron.'
