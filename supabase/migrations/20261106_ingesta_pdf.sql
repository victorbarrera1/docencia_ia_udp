-- [IA-05] Ingesta de PDF: bucket privado "entregas" y RPC transaccional de versiones.
-- Depende de 20261105_modelo_ia.sql (next_version_n, submission_versions, rag_chunks).
BEGIN;

-- Bucket privado de entregas: sin políticas en storage.objects, solo service_role (que ignora RLS)
-- lee y escribe. La descarga para docentes/estudiantes será con URL firmada emitida por el backend.
-- El bloque se omite en un Postgres sin Storage (p.ej. el stub mínimo de otros entornos).
DO $$
BEGIN
    IF to_regclass('storage.buckets') IS NOT NULL THEN
        INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
        VALUES ('entregas', 'entregas', false, 20 * 1024 * 1024, ARRAY['application/pdf'])
        ON CONFLICT (id) DO UPDATE SET public = false, file_size_limit = 20 * 1024 * 1024,
            allowed_mime_types = ARRAY['application/pdf'];
    ELSE
        RAISE NOTICE 'storage.buckets no existe: crear el bucket privado "entregas" manualmente';
    END IF;
END $$;

-- Reserva el número, calcula atrasada, arma la ruta e inserta versión + chunks en UNA transacción
-- (una llamada RPC de PostgREST = una transacción, requisito de next_version_n). El archivo se sube
-- después a la ruta devuelta; si falla, el backend borra la versión. embedding queda NULL (IA-06).
CREATE OR REPLACE FUNCTION public.create_submission_version(
    p_submission_id UUID, p_subido_por UUID, p_texto TEXT, p_chunks JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE
    v_activity UUID; v_section UUID; v_due TIMESTAMPTZ; v_n INTEGER; v_now TIMESTAMPTZ;
    v_late BOOLEAN; v_path TEXT; v_id UUID; v_chunks INTEGER;
BEGIN
    IF p_texto IS NULL OR btrim(p_texto) = '' THEN
        RAISE EXCEPTION 'La versión requiere texto extraído' USING ERRCODE = '22023';
    END IF;
    SELECT a.id, a.section_id, a.due_date INTO v_activity, v_section, v_due
    FROM public.activity_submissions s JOIN public.activities a ON a.id = s.activity_id
    WHERE s.id = p_submission_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Entrega inexistente' USING ERRCODE = 'P0002';
    END IF;
    v_n := public.next_version_n(p_submission_id);
    v_now := clock_timestamp();
    v_late := v_due IS NOT NULL AND v_now > v_due;
    v_path := format('section/%s/activity/%s/submission/%s/v%s.pdf', v_section, v_activity, p_submission_id, v_n);
    INSERT INTO public.submission_versions (submission_id, version_n, storage_path, texto_extraido, subido_por, subido_en, atrasada)
    VALUES (p_submission_id, v_n, v_path, p_texto, p_subido_por, v_now, v_late)
    RETURNING id INTO v_id;
    INSERT INTO public.rag_chunks (owner_type, owner_id, idx, texto, char_start, char_end)
    SELECT 'version', v_id, c.idx, c.texto, c.char_start, c.char_end
    FROM jsonb_to_recordset(COALESCE(p_chunks, '[]'::jsonb)) AS c(idx INTEGER, texto TEXT, char_start INTEGER, char_end INTEGER);
    GET DIAGNOSTICS v_chunks = ROW_COUNT;
    RETURN jsonb_build_object('id', v_id, 'version_n', v_n, 'storage_path', v_path, 'atrasada', v_late, 'chunks', v_chunks);
END $$;
REVOKE ALL ON FUNCTION public.create_submission_version(UUID, UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_submission_version(UUID, UUID, TEXT, JSONB) TO service_role;

COMMIT;
