-- [IA-04] Modelo aditivo de IA. Depende de 20261104_rls_por_rol.sql.
-- 20261105 reserva el siguiente prefijo para respetar el orden lexicográfico del harness.
-- Embeddings aprobados: OpenRouter / openai/text-embedding-3-small, 1536 dimensiones.
-- Cambiar de modelo exige migrar la columna embedding, regenerar los embeddings y reindexar;
-- cambiar solamente EMBEDDING_MODEL/EMBEDDING_DIM no convierte los vectores existentes.
BEGIN;

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
GRANT USAGE ON SCHEMA extensions TO authenticated, service_role;

CREATE TABLE IF NOT EXISTS public.submission_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    submission_id UUID NOT NULL REFERENCES public.activity_submissions(id) ON DELETE CASCADE,
    version_n INTEGER NOT NULL CHECK (version_n > 0),
    storage_path TEXT NOT NULL CHECK (btrim(storage_path) <> ''),
    texto_extraido TEXT NOT NULL DEFAULT '',
    subido_por UUID NOT NULL REFERENCES public.users(id),
    subido_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    atrasada BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (submission_id, version_n)
);

CREATE TABLE IF NOT EXISTS public.rag_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_type TEXT NOT NULL CHECK (owner_type IN ('version', 'rubric', 'course_material')),
    -- version -> submission_versions.id; rubric -> activities.id; course_material -> courses.id.
    -- No existe aún un catálogo persistido de materiales individuales.
    owner_id UUID NOT NULL,
    -- FKs derivadas de la identidad canónica: evitan huérfanos incluso con borrados concurrentes.
    version_owner_id UUID GENERATED ALWAYS AS (CASE WHEN owner_type = 'version' THEN owner_id END) STORED
        REFERENCES public.submission_versions(id) ON DELETE CASCADE,
    rubric_owner_id UUID GENERATED ALWAYS AS (CASE WHEN owner_type = 'rubric' THEN owner_id END) STORED
        REFERENCES public.activities(id) ON DELETE CASCADE,
    course_owner_id UUID GENERATED ALWAYS AS (CASE WHEN owner_type = 'course_material' THEN owner_id END) STORED
        REFERENCES public.courses(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL CHECK (idx >= 0),
    texto TEXT NOT NULL CHECK (btrim(texto) <> ''),
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL,
    embedding extensions.vector(1536),
    CHECK (char_end > char_start AND char_end - char_start = char_length(texto)),
    UNIQUE (owner_type, owner_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding ON public.rag_chunks
    USING hnsw (embedding extensions.vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_version_owner ON public.rag_chunks(version_owner_id) WHERE version_owner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rag_chunks_rubric_owner ON public.rag_chunks(rubric_owner_id) WHERE rubric_owner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rag_chunks_course_owner ON public.rag_chunks(course_owner_id) WHERE course_owner_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.ai_reviews (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version_id UUID NOT NULL REFERENCES public.submission_versions(id) ON DELETE CASCADE,
    agente TEXT NOT NULL CHECK (btrim(agente) <> ''),
    modelo TEXT NOT NULL CHECK (btrim(modelo) <> ''),
    estado TEXT NOT NULL DEFAULT 'propuesta' CHECK (estado IN ('propuesta', 'confirmada', 'corregida')),
    nota_preliminar NUMERIC(3,2) CHECK (nota_preliminar BETWEEN 1 AND 7),
    decimas_preliminares NUMERIC(4,2) CHECK (decimas_preliminares >= 0),
    resumen TEXT NOT NULL DEFAULT '',
    confirmada_por UUID REFERENCES public.users(id),
    confirmada_en TIMESTAMPTZ,
    nota_final NUMERIC(3,2) CHECK (nota_final BETWEEN 1 AND 7),
    CONSTRAINT ai_reviews_confirmation CHECK (
        (estado = 'confirmada' AND nota_final IS NOT NULL
            AND confirmada_por IS NOT NULL AND confirmada_en IS NOT NULL)
        OR (estado <> 'confirmada' AND nota_final IS NULL
            AND confirmada_por IS NULL AND confirmada_en IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_ai_reviews_version ON public.ai_reviews(version_id);

CREATE TABLE IF NOT EXISTS public.ai_observations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    review_id UUID NOT NULL REFERENCES public.ai_reviews(id) ON DELETE CASCADE,
    criterio TEXT,
    tipo TEXT NOT NULL CHECK (tipo IN ('criterio', 'feedback_no_abordado', 'retroceso', 'inconsistencia', 'atraso')),
    severidad TEXT NOT NULL CHECK (severidad IN ('baja', 'media', 'alta')),
    texto TEXT NOT NULL CHECK (btrim(texto) <> ''),
    cita_texto TEXT NOT NULL CHECK (btrim(cita_texto) <> ''),
    cita_version_id UUID NOT NULL REFERENCES public.submission_versions(id) DEFERRABLE INITIALLY DEFERRED,
    -- Offsets de caracteres Unicode: base cero, intervalo [char_start, char_end).
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL,
    estado_docente TEXT NOT NULL DEFAULT 'pendiente'
        CHECK (estado_docente IN ('pendiente', 'aceptada', 'rechazada', 'editada')),
    CHECK (char_end > char_start AND char_end - char_start = char_length(cita_texto))
);
CREATE INDEX IF NOT EXISTS idx_ai_observations_review ON public.ai_observations(review_id);
CREATE INDEX IF NOT EXISTS idx_ai_observations_cita ON public.ai_observations(cita_version_id);

CREATE TABLE IF NOT EXISTS public.ai_audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor UUID REFERENCES public.users(id) ON DELETE SET NULL,
    section_id UUID REFERENCES public.sections(id) ON DELETE SET NULL,
    version_id UUID REFERENCES public.submission_versions(id) ON DELETE SET NULL,
    agente TEXT NOT NULL,
    modelo TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
    tokens_out INTEGER NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
    costo_usd NUMERIC(16,10) CHECK (costo_usd >= 0),
    latencia_ms INTEGER NOT NULL DEFAULT 0 CHECK (latencia_ms >= 0),
    ok BOOLEAN NOT NULL,
    -- Solo código sanitizado; nunca prompts, respuestas ni errores crudos del proveedor.
    error TEXT CHECK (error ~ '^[A-Z][A-Z0-9_]{0,79}$'),
    CHECK (NOT ok OR error IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_ai_audit_section_ts ON public.ai_audit_log(section_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ai_audit_version ON public.ai_audit_log(version_id);

-- Reglas de integridad de datos, no helpers nuevos de autorización.
CREATE OR REPLACE FUNCTION public.validate_ai_review_confirmation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE section_id UUID;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.version_id IS DISTINCT FROM OLD.version_id THEN
            RAISE EXCEPTION 'La versión de una revisión es inmutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.estado = 'confirmada' OR NEW.nota_final IS NOT NULL
        OR NEW.confirmada_por IS NOT NULL OR NEW.confirmada_en IS NOT NULL
        OR (TG_OP = 'UPDATE' AND OLD.estado = 'confirmada') THEN
        SELECT public.section_of_activity(s.activity_id) INTO section_id
        FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE v.id = NEW.version_id;
        IF NOT public.is_section_teacher(section_id) THEN
            RAISE EXCEPTION 'Solo el docente de la sección puede confirmar' USING ERRCODE = '42501';
        END IF;
        IF NEW.estado = 'confirmada' THEN
            IF NEW.confirmada_por IS DISTINCT FROM public.current_app_user_id() THEN
                RAISE EXCEPTION 'La confirmación debe identificar al docente actual' USING ERRCODE = '42501';
            END IF;
            -- La fecha la fija el servidor, no un valor arbitrario del cliente.
            NEW.confirmada_en := now();
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_ai_review_confirmation BEFORE INSERT OR UPDATE ON public.ai_reviews
    FOR EACH ROW EXECUTE FUNCTION public.validate_ai_review_confirmation();

CREATE OR REPLACE FUNCTION public.validate_ai_observation_citation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE source_text TEXT; source_submission UUID; reviewed_submission UUID; reviewed_n INTEGER; cited_n INTEGER;
BEGIN
    SELECT v.texto_extraido, v.submission_id, v.version_n INTO source_text, source_submission, cited_n
    FROM public.submission_versions v WHERE v.id = NEW.cita_version_id;
    SELECT v.submission_id, v.version_n INTO reviewed_submission, reviewed_n
    FROM public.ai_reviews r JOIN public.submission_versions v ON v.id = r.version_id
    WHERE r.id = NEW.review_id;
    IF source_submission IS NULL OR reviewed_submission IS NULL
        OR source_submission IS DISTINCT FROM reviewed_submission OR cited_n > reviewed_n
        OR NEW.cita_texto IS DISTINCT FROM substring(source_text FROM NEW.char_start + 1 FOR NEW.char_end - NEW.char_start) THEN
        RAISE EXCEPTION 'La cita debe ser literal y pertenecer a esta entrega o una versión anterior'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_ai_observation_citation BEFORE INSERT OR UPDATE ON public.ai_observations
    FOR EACH ROW EXECUTE FUNCTION public.validate_ai_observation_citation();

CREATE OR REPLACE FUNCTION public.validate_rag_chunk_owner()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE valid_owner BOOLEAN;
BEGIN
    CASE NEW.owner_type
        WHEN 'version' THEN
            SELECT EXISTS (SELECT 1 FROM public.submission_versions v WHERE v.id = NEW.owner_id
                AND NEW.texto = substring(v.texto_extraido FROM NEW.char_start + 1 FOR NEW.char_end - NEW.char_start)) INTO valid_owner;
        WHEN 'rubric' THEN
            SELECT EXISTS (SELECT 1 FROM public.activities a WHERE a.id = NEW.owner_id) INTO valid_owner;
        WHEN 'course_material' THEN
            SELECT EXISTS (SELECT 1 FROM public.courses c WHERE c.id = NEW.owner_id) INTO valid_owner;
        ELSE valid_owner := false;
    END CASE;
    IF NOT valid_owner THEN
        RAISE EXCEPTION 'Propietario o fragmento RAG inválido' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_rag_chunk_owner BEFORE INSERT OR UPDATE ON public.rag_chunks
    FOR EACH ROW EXECUTE FUNCTION public.validate_rag_chunk_owner();

CREATE OR REPLACE FUNCTION public.next_version_n(submission_id UUID)
RETURNS INTEGER LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE next_n INTEGER;
BEGIN
    -- El llamador debe insertar la versión en ESTA transacción, antes de COMMIT.
    PERFORM 1 FROM public.activity_submissions s WHERE s.id = $1 FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Entrega inexistente o no autorizada' USING ERRCODE = '23503';
    END IF;
    SELECT COALESCE(max(v.version_n), 0) + 1 INTO next_n
    FROM public.submission_versions v WHERE v.submission_id = $1;
    RETURN next_n;
END $$;

CREATE OR REPLACE FUNCTION public.match_rag_chunks(query_embedding extensions.vector, owner_ids UUID[], k INTEGER)
RETURNS TABLE (id UUID, owner_type TEXT, owner_id UUID, idx INTEGER, texto TEXT,
    char_start INTEGER, char_end INTEGER, similarity DOUBLE PRECISION)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = public, extensions, pg_temp AS $$
BEGIN
    IF query_embedding IS NULL OR extensions.vector_dims(query_embedding) <> 1536 OR k IS NULL OR k < 1 OR k > 100 THEN
        RAISE EXCEPTION 'Vector de 1536 dimensiones y k entre 1 y 100 requeridos' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY SELECT c.id, c.owner_type, c.owner_id, c.idx, c.texto, c.char_start, c.char_end,
        1 - (c.embedding OPERATOR(extensions.<=>) query_embedding)
    FROM public.rag_chunks c
    WHERE c.owner_id = ANY(COALESCE(owner_ids, '{}'::uuid[])) AND c.embedding IS NOT NULL
    ORDER BY c.embedding OPERATOR(extensions.<=>) query_embedding
    LIMIT k;
END $$;

-- Bloqueo explícito: no depender de los privilegios por defecto del proyecto Supabase.
DO $$
DECLARE t TEXT; fn TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['submission_versions', 'rag_chunks', 'ai_reviews', 'ai_observations', 'ai_audit_log'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, anon, authenticated, service_role', t);
    END LOOP;
    FOREACH fn IN ARRAY ARRAY['validate_ai_review_confirmation()', 'validate_ai_observation_citation()',
        'validate_rag_chunk_owner()', 'next_version_n(uuid)',
        'match_rag_chunks(extensions.vector,uuid[],integer)'] LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, anon, authenticated', fn);
        EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO service_role', fn);
    END LOOP;
END $$;
GRANT SELECT ON public.submission_versions, public.rag_chunks, public.ai_reviews, public.ai_observations, public.ai_audit_log TO authenticated;
GRANT UPDATE (estado, nota_final, confirmada_por, confirmada_en) ON public.ai_reviews TO authenticated;
GRANT UPDATE (estado_docente, texto) ON public.ai_observations TO authenticated;
-- Las versiones son inmutables: extraer el texto antes de insertar, o insertar otra versión.
GRANT SELECT, INSERT, DELETE ON public.submission_versions TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.rag_chunks, public.ai_reviews, public.ai_observations TO service_role;
GRANT SELECT, INSERT ON public.ai_audit_log TO service_role;
GRANT EXECUTE ON FUNCTION public.match_rag_chunks(extensions.vector, UUID[], INTEGER) TO authenticated;

DROP POLICY IF EXISTS ia_versions_select ON public.submission_versions;
CREATE POLICY ia_versions_select ON public.submission_versions FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM public.activity_submissions s WHERE s.id = submission_id
        AND (public.is_section_staff(public.section_of_activity(s.activity_id)) OR s.student_id = public.current_student_id()))
);

DROP POLICY IF EXISTS ia_chunks_select ON public.rag_chunks;
CREATE POLICY ia_chunks_select ON public.rag_chunks FOR SELECT TO authenticated USING (
    (owner_type = 'version' AND EXISTS (SELECT 1 FROM public.submission_versions v WHERE v.id = owner_id))
    OR (owner_type = 'rubric' AND EXISTS (SELECT 1 FROM public.activities a WHERE a.id = owner_id
        AND (public.is_section_staff(a.section_id) OR (a.is_published AND public.is_section_member(a.section_id)))))
    OR (owner_type = 'course_material' AND EXISTS (SELECT 1 FROM public.sections s
        WHERE s.course_id = owner_id AND public.is_section_member(s.id)))
);

-- La tabla completa contiene notas preliminares: ningún estudiante tiene filas visibles.
DROP POLICY IF EXISTS ia_reviews_select_staff ON public.ai_reviews;
CREATE POLICY ia_reviews_select_staff ON public.ai_reviews FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE v.id = version_id AND public.is_section_staff(public.section_of_activity(s.activity_id)))
);
DROP POLICY IF EXISTS ia_reviews_update_teacher ON public.ai_reviews;
CREATE POLICY ia_reviews_update_teacher ON public.ai_reviews FOR UPDATE TO authenticated USING (
    EXISTS (SELECT 1 FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE v.id = version_id AND public.is_section_teacher(public.section_of_activity(s.activity_id)))
) WITH CHECK (
    EXISTS (SELECT 1 FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE v.id = version_id AND public.is_section_teacher(public.section_of_activity(s.activity_id)))
);

DROP POLICY IF EXISTS ia_observations_select_staff ON public.ai_observations;
CREATE POLICY ia_observations_select_staff ON public.ai_observations FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM public.ai_reviews r JOIN public.submission_versions v ON v.id = r.version_id
        JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE r.id = review_id AND public.is_section_staff(public.section_of_activity(s.activity_id)))
);
DROP POLICY IF EXISTS ia_observations_update_teacher ON public.ai_observations;
CREATE POLICY ia_observations_update_teacher ON public.ai_observations FOR UPDATE TO authenticated USING (
    EXISTS (SELECT 1 FROM public.ai_reviews r JOIN public.submission_versions v ON v.id = r.version_id
        JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE r.id = review_id AND public.is_section_teacher(public.section_of_activity(s.activity_id)))
) WITH CHECK (
    EXISTS (SELECT 1 FROM public.ai_reviews r JOIN public.submission_versions v ON v.id = r.version_id
        JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE r.id = review_id AND public.is_section_teacher(public.section_of_activity(s.activity_id)))
);

DROP POLICY IF EXISTS ia_audit_select_staff ON public.ai_audit_log;
CREATE POLICY ia_audit_select_staff ON public.ai_audit_log FOR SELECT TO authenticated
    USING (public.is_section_staff(section_id));

COMMIT;
