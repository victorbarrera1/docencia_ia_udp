-- IA-06: embedding_model, set_rag_chunk_embeddings y replace_rag_chunks con datos ficticios.
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
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ia_test TO anon, authenticated, service_role;
\ir modelo_ia_seed.sql

SELECT ia_test.expect((SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'
    AND table_name = 'rag_chunks' AND column_name = 'embedding_model'), 1, 'rag_chunks.embedding_model existe');

-- Solo service_role ejecuta las RPCs de escritura.
SET LOCAL ROLE authenticated;
SELECT ia_test.expect_error(format($q$SELECT public.set_rag_chunk_embeddings('m', '[]')$q$), '42501', 'authenticated no puede set_rag_chunk_embeddings');
SELECT ia_test.expect_error(format($q$SELECT public.replace_rag_chunks('rubric', %L, '[]')$q$, :'act1'), '42501', 'authenticated no puede replace_rag_chunks');
RESET ROLE;
SET LOCAL ROLE anon;
SELECT ia_test.expect_error(format($q$SELECT public.replace_rag_chunks('rubric', %L, '[]')$q$, :'act1'), '42501', 'anon no puede replace_rag_chunks');
RESET ROLE;

SET LOCAL ROLE service_role;

-- Las versiones son inmutables: no se reemplazan por esta vía.
SELECT ia_test.expect_error(format($q$SELECT public.replace_rag_chunks('version', %L, '[]')$q$, :'v1b'), '22023', 'replace rechaza owner_type version');

-- Rúbrica: idx 0 igual (conserva embedding del seed), idx 1 nuevo.
SELECT public.replace_rag_chunks('rubric', :'act1',
    '[{"idx":0,"texto":"Diseño","char_start":0,"char_end":6},{"idx":1,"texto":"Pruebas","char_start":8,"char_end":15}]'::jsonb) AS r1 \gset
SELECT ia_test.expect((:'r1'::jsonb ->> 'insertados')::bigint, 1, 'replace inserta el idx nuevo');
SELECT ia_test.expect((:'r1'::jsonb ->> 'sin_cambios')::bigint, 1, 'replace deja intacto el idx igual');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks WHERE owner_id = :'act1' AND idx = 0 AND embedding IS NOT NULL), 1,
    'el chunk sin cambios conserva su embedding');

-- Idempotente: repetir no cambia nada.
SELECT public.replace_rag_chunks('rubric', :'act1',
    '[{"idx":0,"texto":"Diseño","char_start":0,"char_end":6},{"idx":1,"texto":"Pruebas","char_start":8,"char_end":15}]'::jsonb) AS r2 \gset
SELECT ia_test.expect((:'r2'::jsonb ->> 'sin_cambios')::bigint, 2, 'replace repetido no cambia nada');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks WHERE owner_type = 'rubric' AND owner_id = :'act1'), 2, 'sin duplicados');

-- Cambia el texto del idx 0 y desaparece el idx 1: el embedding se pierde y el sobrante se borra.
SELECT public.replace_rag_chunks('rubric', :'act1',
    '[{"idx":0,"texto":"Diseño nuevo","char_start":0,"char_end":12}]'::jsonb) AS r3 \gset
SELECT ia_test.expect((:'r3'::jsonb ->> 'actualizados')::bigint, 1, 'replace actualiza el idx cambiado');
SELECT ia_test.expect((:'r3'::jsonb ->> 'borrados')::bigint, 1, 'replace borra el idx sobrante');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks WHERE owner_id = :'act1' AND embedding IS NULL AND embedding_model IS NULL), 1,
    'el chunk cambiado queda sin embedding ni modelo');
SELECT ia_test.expect_error(format($q$SELECT public.replace_rag_chunks('rubric', %L, '[{"idx":0,"texto":"Corto","char_start":0,"char_end":99}]')$q$, :'act1'),
    '23514', 'replace respeta los CHECK de offsets');

-- Vaciar la rúbrica borra todos sus chunks.
SELECT ia_test.expect(((public.replace_rag_chunks('rubric', :'draft', '[]'::jsonb)) ->> 'borrados')::bigint, 1, 'replace con lista vacía borra todo');

-- Escritura de embeddings.
SELECT id AS chunk_id FROM public.rag_chunks WHERE owner_id = :'act1' AND idx = 0 \gset
SELECT ia_test.expect_error($q$SELECT public.set_rag_chunk_embeddings('  ', '[]')$q$, '22023', 'modelo vacío rechazado');
SELECT ia_test.expect_error(format($q$SELECT public.set_rag_chunk_embeddings('m/x', '[{"id":"%s","texto":"Diseño nuevo","embedding":[1,2,3]}]')$q$, :'chunk_id'),
    '22000', 'dimensión distinta de 1536 rechazada');
-- Texto distinto al embebido (el chunk cambió mientras tanto): no se escribe.
SELECT public.set_rag_chunk_embeddings('m/x', jsonb_build_array(jsonb_build_object('id', :'chunk_id'::uuid, 'texto', 'Texto viejo', 'embedding', :'embedding_y'::jsonb))) AS n_stale \gset
SELECT ia_test.expect(:n_stale, 0, 'no escribe si el texto cambió');
SELECT public.set_rag_chunk_embeddings('m/x', jsonb_build_array(jsonb_build_object('id', :'chunk_id'::uuid, 'texto', 'Diseño nuevo', 'embedding', :'embedding_y'::jsonb))) AS n_ok \gset
SELECT ia_test.expect(:n_ok, 1, 'escribe el embedding');
SELECT ia_test.expect((SELECT count(*) FROM public.rag_chunks WHERE id = :'chunk_id' AND embedding_model = 'm/x' AND embedding IS NOT NULL), 1,
    'guarda el embedding y el modelo');
SELECT ia_test.expect((SELECT count(*) FROM public.match_rag_chunks(:'embedding_y', ARRAY[:'act1'::uuid], 3) WHERE id = :'chunk_id' AND similarity > 0.99), 1,
    'match_rag_chunks encuentra el chunk recién embebido');
RESET ROLE;

ROLLBACK;
