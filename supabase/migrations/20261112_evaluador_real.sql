-- [IA-08] Evaluador real: RPCs del servicio para proponer, decidir observaciones y confirmar la nota.
-- Depende de 20261105_modelo_ia.sql (tablas y triggers) y 20261104_rls_por_rol.sql (helpers de identidad/rol).
--
-- Las tres funciones son solo para service_role (agent-services). La IA solo crea propuestas: la única
-- forma de escribir nota_final es confirm_ai_review, que exige identificar al docente (p_actor).
--
-- CONFIANZA: el frontend no usa Supabase Auth, así que no hay JWT del docente que reenviar. El puente
-- backend (IA-09) autentica la sesión del docente y entrega su users.id como p_actor. Estas funciones lo
-- fijan como `sub` SOLO en las claims locales de la transacción para que los helpers de IA-01 y el trigger
-- de IA-04 verifiquen el rol real (is_section_teacher, confirmada_por = current_app_user_id()).
-- La llave de servicio sola no basta: sin un docente activo de la sección, la confirmación se rechaza.
--
-- SQLSTATE propios: IA001 = quedan observaciones pendientes; IA002 = la revisión ya está confirmada.
BEGIN;

-- Propuesta de la IA + observaciones en una sola transacción. Nunca escribe nota_final ni confirma.
-- p_observaciones: [{criterio, tipo, severidad, texto, cita_texto, cita_version_id, char_start, char_end}].
-- El trigger de IA-04 vuelve a verificar que cada cita sea literal y de esta entrega.
CREATE OR REPLACE FUNCTION public.create_ai_review(
    p_version_id UUID, p_agente TEXT, p_modelo TEXT, p_nota_preliminar NUMERIC,
    p_decimas_preliminares NUMERIC, p_resumen TEXT, p_observaciones JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE v_review_id UUID; v_ids JSONB := '[]'::jsonb; v_obs JSONB; v_obs_id UUID;
BEGIN
    IF p_observaciones IS NULL OR jsonb_typeof(p_observaciones) <> 'array' THEN
        RAISE EXCEPTION 'Observaciones deben ser un arreglo' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.ai_reviews (version_id, agente, modelo, estado, nota_preliminar, decimas_preliminares, resumen)
    VALUES (p_version_id, p_agente, p_modelo, 'propuesta', p_nota_preliminar, p_decimas_preliminares, COALESCE(p_resumen, ''))
    RETURNING id INTO v_review_id;

    -- En orden: observation_ids[i] corresponde a p_observaciones[i].
    FOR v_obs IN SELECT value FROM jsonb_array_elements(p_observaciones) WITH ORDINALITY ORDER BY ordinality LOOP
        INSERT INTO public.ai_observations (review_id, criterio, tipo, severidad, texto, cita_texto,
            cita_version_id, char_start, char_end)
        VALUES (v_review_id, v_obs->>'criterio', v_obs->>'tipo', v_obs->>'severidad', v_obs->>'texto',
            v_obs->>'cita_texto', (v_obs->>'cita_version_id')::uuid, (v_obs->>'char_start')::integer,
            (v_obs->>'char_end')::integer)
        RETURNING id INTO v_obs_id;
        v_ids := v_ids || to_jsonb(v_obs_id::text);
    END LOOP;
    RETURN jsonb_build_object('review_id', v_review_id, 'observation_ids', v_ids);
END $$;

-- Decisión del docente sobre una observación: aceptada | rechazada | editada (con texto nuevo).
CREATE OR REPLACE FUNCTION public.decide_ai_observation(
    p_observation_id UUID, p_actor UUID, p_estado TEXT, p_texto TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE v_review_id UUID; v_review public.ai_reviews; v_section UUID; v_obs public.ai_observations;
BEGIN
    SELECT o.review_id INTO v_review_id FROM public.ai_observations o WHERE o.id = p_observation_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Observación inexistente' USING ERRCODE = 'P0002';
    END IF;
    -- Bloquea la revisión: serializa confirmaciones y decisiones concurrentes.
    SELECT * INTO v_review FROM public.ai_reviews r WHERE r.id = v_review_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Revisión inexistente' USING ERRCODE = 'P0002';
    END IF;
    SELECT public.section_of_activity(s.activity_id) INTO v_section
    FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
    WHERE v.id = v_review.version_id;
    -- Identidad del docente solo para esta transacción (is_local = true); ver CONFIANZA arriba.
    PERFORM set_config('request.jwt.claim.sub', COALESCE(p_actor::text, ''), true);
    PERFORM set_config('request.jwt.claims', (COALESCE(NULLIF(current_setting('request.jwt.claims', true), '')::jsonb,
        '{}'::jsonb) || jsonb_build_object('sub', p_actor))::text, true);
    IF p_actor IS NULL OR NOT public.is_section_teacher(v_section) THEN
        RAISE EXCEPTION 'Solo el docente de la sección decide observaciones' USING ERRCODE = '42501';
    END IF;
    IF v_review.estado = 'confirmada' THEN
        RAISE EXCEPTION 'La revisión ya está confirmada' USING ERRCODE = 'IA002';
    END IF;
    IF p_estado IS NULL OR p_estado NOT IN ('aceptada', 'rechazada', 'editada') THEN
        RAISE EXCEPTION 'Estado de observación inválido' USING ERRCODE = '22023';
    END IF;
    IF p_estado = 'editada' AND (p_texto IS NULL OR btrim(p_texto) = '') THEN
        RAISE EXCEPTION 'Una observación editada requiere texto' USING ERRCODE = '22023';
    END IF;
    UPDATE public.ai_observations o
    SET estado_docente = p_estado,
        texto = CASE WHEN p_estado = 'editada' THEN btrim(p_texto) ELSE o.texto END
    WHERE o.id = p_observation_id
    RETURNING * INTO v_obs;
    RETURN to_jsonb(v_obs);
END $$;

-- Único camino a nota_final. Exige docente activo de la sección y cero observaciones pendientes.
-- Sin p_nota_final, confirma la propuesta: nota_preliminar + decimas_preliminares, con tope 7,0.
CREATE OR REPLACE FUNCTION public.confirm_ai_review(p_review_id UUID, p_actor UUID, p_nota_final NUMERIC DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE v_review public.ai_reviews; v_section UUID; v_pending INTEGER; v_final NUMERIC;
BEGIN
    -- Bloquea la revisión: serializa confirmaciones y decisiones concurrentes.
    SELECT * INTO v_review FROM public.ai_reviews r WHERE r.id = p_review_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Revisión inexistente' USING ERRCODE = 'P0002';
    END IF;
    SELECT public.section_of_activity(s.activity_id) INTO v_section
    FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
    WHERE v.id = v_review.version_id;
    -- Identidad del docente solo para esta transacción (is_local = true); ver CONFIANZA arriba.
    PERFORM set_config('request.jwt.claim.sub', COALESCE(p_actor::text, ''), true);
    PERFORM set_config('request.jwt.claims', (COALESCE(NULLIF(current_setting('request.jwt.claims', true), '')::jsonb,
        '{}'::jsonb) || jsonb_build_object('sub', p_actor))::text, true);
    IF p_actor IS NULL OR NOT public.is_section_teacher(v_section) THEN
        RAISE EXCEPTION 'Solo el docente de la sección confirma la nota' USING ERRCODE = '42501';
    END IF;
    IF v_review.estado = 'confirmada' THEN
        RAISE EXCEPTION 'La revisión ya está confirmada' USING ERRCODE = 'IA002';
    END IF;
    -- Bloquea las observaciones: una decisión concurrente no puede colarse entre el conteo y la confirmación.
    PERFORM 1 FROM public.ai_observations o WHERE o.review_id = p_review_id FOR UPDATE;
    SELECT count(*) INTO v_pending FROM public.ai_observations o
    WHERE o.review_id = p_review_id AND o.estado_docente = 'pendiente';
    IF v_pending > 0 THEN
        RAISE EXCEPTION 'Quedan % observaciones pendientes', v_pending USING ERRCODE = 'IA001';
    END IF;
    v_final := COALESCE(p_nota_final,
        LEAST(7, round(v_review.nota_preliminar + COALESCE(v_review.decimas_preliminares, 0), 1)));
    IF v_final IS NULL OR v_final < 1 OR v_final > 7 THEN
        RAISE EXCEPTION 'Nota final fuera de la escala 1,0–7,0' USING ERRCODE = '22023';
    END IF;
    -- El trigger de IA-04 vuelve a exigir docente y confirmada_por = current_app_user_id(), y fija la fecha.
    UPDATE public.ai_reviews r
    SET estado = 'confirmada', nota_final = round(v_final, 1), confirmada_por = p_actor, confirmada_en = now()
    WHERE r.id = p_review_id
    RETURNING * INTO v_review;
    RETURN to_jsonb(v_review);
END $$;

DO $$
DECLARE fn TEXT;
BEGIN
    FOREACH fn IN ARRAY ARRAY['create_ai_review(uuid,text,text,numeric,numeric,text,jsonb)',
        'decide_ai_observation(uuid,uuid,text,text)', 'confirm_ai_review(uuid,uuid,numeric)'] LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, anon, authenticated', fn);
        EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO service_role', fn);
    END LOOP;
END $$;

COMMIT;
