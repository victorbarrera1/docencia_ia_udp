-- IA-05: bucket privado y RPC create_submission_version con datos ficticios.
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
CREATE FUNCTION ia_test.expect_text(actual TEXT, expected TEXT, label TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
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
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ia_test TO anon, authenticated, service_role;
\ir modelo_ia_seed.sql

-- Bucket privado, solo PDF, 20 MiB.
SELECT ia_test.expect((SELECT count(*) FROM storage.buckets WHERE id = 'entregas' AND NOT public
    AND file_size_limit = 20 * 1024 * 1024 AND allowed_mime_types = ARRAY['application/pdf']), 1, 'bucket entregas privado, PDF, 20 MiB');

-- Solo service_role ejecuta la RPC.
SET LOCAL ROLE authenticated;
SELECT ia_test.expect_error(format($q$SELECT public.create_submission_version(%L, %L, 'Texto.', '[]')$q$, :'sub1', :'student1'),
    '42501', 'authenticated no puede invocar la RPC');
RESET ROLE;

UPDATE public.activities SET due_date = now() + interval '1 day' WHERE id = :'act1';
SET LOCAL ROLE service_role;

-- sub1 ya tiene v1 y v2 en el seed, así que la primera versión nueva es la 3.
SELECT public.create_submission_version(:'sub1', :'student1', 'Texto de ejemplo ficticio uno.',
    '[{"idx":0,"texto":"Texto de ejemplo","char_start":0,"char_end":16},{"idx":1,"texto":"ficticio uno.","char_start":17,"char_end":30}]'::jsonb) AS r1 \gset
SELECT ia_test.expect((:'r1'::jsonb ->> 'version_n')::bigint, 3, 'la versión continúa tras las existentes');
SELECT ia_test.expect_text(:'r1'::jsonb ->> 'storage_path',
    format('section/%s/activity/%s/submission/%s/v3.pdf', :'sec1', :'act1', :'sub1'), 'ruta section/activity/submission/vN.pdf');
SELECT ia_test.expect_text(:'r1'::jsonb ->> 'atrasada', 'false', 'antes del plazo no es atrasada');
SELECT ia_test.expect((:'r1'::jsonb ->> 'chunks')::bigint, 2, 'inserta los chunks');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks WHERE owner_id = (:'r1'::jsonb ->> 'id')::uuid
    AND embedding IS NULL AND owner_type = 'version'), 2, 'chunks de la versión sin embedding');

SELECT (public.create_submission_version(:'sub1', :'student1', 'Otra versión ficticia.', '[]'::jsonb) ->> 'version_n')::bigint AS n2 \gset
SELECT ia_test.expect(:n2, 4, 'cada versión incrementa version_n');

RESET ROLE;
UPDATE public.activities SET due_date = now() - interval '1 hour' WHERE id = :'act1';
SET LOCAL ROLE service_role;
SELECT ia_test.expect_text(public.create_submission_version(:'sub1', :'student1', 'Entrega tardía ficticia.', '[]'::jsonb) ->> 'atrasada',
    'true', 'después del plazo es atrasada');
RESET ROLE;
UPDATE public.activities SET due_date = NULL WHERE id = :'act1';
SET LOCAL ROLE service_role;
SELECT ia_test.expect_text(public.create_submission_version(:'sub1', :'student1', 'Sin plazo ficticio.', '[]'::jsonb) ->> 'atrasada',
    'false', 'sin due_date no es atrasada');

SELECT ia_test.expect_error(format($q$SELECT public.create_submission_version(%L, %L, 'Texto.', '[]')$q$,
    'ffffffff-ffff-4fff-afff-ffffffffffff', :'student1'), 'P0002', 'entrega inexistente');
SELECT ia_test.expect_error(format($q$SELECT public.create_submission_version(%L, %L, 'Texto.', '[]')$q$,
    :'sub1', 'ffffffff-ffff-4fff-afff-ffffffffffff'), '23503', 'usuario que sube inexistente');
SELECT ia_test.expect_error(format($q$SELECT public.create_submission_version(%L, %L, '   ', '[]')$q$,
    :'sub1', :'student1'), '22023', 'texto vacío rechazado');
SELECT ia_test.expect_error(format($q$SELECT public.create_submission_version(%L, %L, 'Texto original.',
    '[{"idx":0,"texto":"Texto falso","char_start":0,"char_end":11}]')$q$, :'sub1', :'student1'), '23514', 'chunk que no coincide con el texto');
-- Atomicidad: el chunk inválido revierte también la inserción de la versión.
SELECT ia_test.expect((SELECT count(*) FROM public.submission_versions WHERE texto_extraido = 'Texto original.'), 0, 'rechazo no deja la versión insertada');
RESET ROLE;
ROLLBACK;
\echo 'IA-05: todos los tests pasaron.'
