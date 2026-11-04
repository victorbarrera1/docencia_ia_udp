-- [IA-06] RAG con pgvector. Depende de 20261105_modelo_ia.sql (rag_chunks, match_rag_chunks).
BEGIN;

-- [IA-06] RAG con pgvector: modelo de cada embedding y RPCs de escritura para el servicio.
-- embedding_model se llena al embeber; NULL con embedding presente = origen desconocido (se re-embebe con force).
ALTER TABLE public.rag_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT;

-- Guarda varios embeddings en una llamada (una transacción). Solo actualiza el chunk si su texto sigue
-- siendo el que se embebió (p_items[].texto): evita que un reemplazo concurrente de la rúbrica quede con
-- el vector del texto anterior. Devuelve cuántos chunks se actualizaron.
CREATE OR REPLACE FUNCTION public.set_rag_chunk_embeddings(p_model TEXT, p_items JSONB)
RETURNS INTEGER LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, extensions, pg_temp AS $$
DECLARE v_updated INTEGER;
BEGIN
    IF p_model IS NULL OR btrim(p_model) = '' THEN
        RAISE EXCEPTION 'Modelo de embedding requerido' USING ERRCODE = '22023';
    END IF;
    UPDATE public.rag_chunks c
    SET embedding = i.embedding::text::extensions.vector, embedding_model = p_model
    FROM jsonb_to_recordset(COALESCE(p_items, '[]'::jsonb)) AS i(id UUID, texto TEXT, embedding JSONB)
    WHERE c.id = i.id AND c.texto = i.texto;
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END $$;
REVOKE ALL ON FUNCTION public.set_rag_chunk_embeddings(TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.set_rag_chunk_embeddings(TEXT, JSONB) TO service_role;

-- Reemplaza atómicamente los chunks de una rúbrica o de un material de curso (las versiones son inmutables
-- y las crea create_submission_version). Los idx con el mismo texto y offsets conservan su embedding; los
-- que cambian lo pierden (quedan NULL para re-embeber); los idx que ya no vienen se borran.
-- Devuelve {insertados, actualizados, sin_cambios, borrados}.
CREATE OR REPLACE FUNCTION public.replace_rag_chunks(p_owner_type TEXT, p_owner_id UUID, p_chunks JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, extensions, pg_temp AS $$
DECLARE v_in JSONB; v_total INTEGER; v_inserted INTEGER; v_updated INTEGER; v_deleted INTEGER;
BEGIN
    IF p_owner_type NOT IN ('rubric', 'course_material') OR p_owner_id IS NULL THEN
        RAISE EXCEPTION 'Solo se reemplazan chunks de rúbrica o material de curso' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_owner_type || ':' || p_owner_id::text, 0));
    v_in := COALESCE(p_chunks, '[]'::jsonb);
    SELECT count(*) INTO v_total FROM jsonb_to_recordset(v_in) AS n(idx INTEGER, texto TEXT, char_start INTEGER, char_end INTEGER);

    DELETE FROM public.rag_chunks c
    WHERE c.owner_type = p_owner_type AND c.owner_id = p_owner_id
      AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset(v_in) AS n(idx INTEGER, texto TEXT, char_start INTEGER, char_end INTEGER)
          WHERE n.idx = c.idx);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    UPDATE public.rag_chunks c
    SET texto = n.texto, char_start = n.char_start, char_end = n.char_end, embedding = NULL, embedding_model = NULL
    FROM jsonb_to_recordset(v_in) AS n(idx INTEGER, texto TEXT, char_start INTEGER, char_end INTEGER)
    WHERE c.owner_type = p_owner_type AND c.owner_id = p_owner_id AND c.idx = n.idx
      AND (c.texto, c.char_start, c.char_end) IS DISTINCT FROM (n.texto, n.char_start, n.char_end);
    GET DIAGNOSTICS v_updated = ROW_COUNT;

    INSERT INTO public.rag_chunks (owner_type, owner_id, idx, texto, char_start, char_end)
    SELECT p_owner_type, p_owner_id, n.idx, n.texto, n.char_start, n.char_end
    FROM jsonb_to_recordset(v_in) AS n(idx INTEGER, texto TEXT, char_start INTEGER, char_end INTEGER)
    WHERE NOT EXISTS (SELECT 1 FROM public.rag_chunks c
        WHERE c.owner_type = p_owner_type AND c.owner_id = p_owner_id AND c.idx = n.idx);
    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object('insertados', v_inserted, 'actualizados', v_updated,
        'sin_cambios', v_total - v_inserted - v_updated, 'borrados', v_deleted);
END $$;
REVOKE ALL ON FUNCTION public.replace_rag_chunks(TEXT, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.replace_rag_chunks(TEXT, UUID, JSONB) TO service_role;

COMMIT;
