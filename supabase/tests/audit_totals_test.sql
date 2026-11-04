-- IA-07: ai_audit_totals con datos ficticios, en una transacción que se revierte.
\set ON_ERROR_STOP 1
BEGIN;
CREATE SCHEMA ia_test;
GRANT USAGE ON SCHEMA ia_test TO anon, authenticated, service_role;
CREATE FUNCTION ia_test.expect(actual NUMERIC, expected NUMERIC, label TEXT) RETURNS VOID LANGUAGE plpgsql AS $$
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

-- El seed trae 2 filas (fake/test): sec1 con costo 0.000002 y sec2 con costo NULL. Se parte de una tabla vacía.
DELETE FROM public.ai_audit_log;
INSERT INTO public.ai_audit_log (ts, section_id, agente, modelo, tokens_in, tokens_out, costo_usd, latencia_ms, ok, error) VALUES
    ('2026-11-01 12:00+00', :'sec1', 'evaluador', 'openai/gpt-4o', 100, 20, 0.0000000001, 10, true, NULL),
    ('2026-11-01 13:00+00', :'sec1', 'evaluador', 'openai/gpt-4o', 200, 30, 0.0000000002, 10, true, NULL),
    ('2026-11-02 23:59:59+00', :'sec1', 'tutor', 'google/gemini-2.5-flash', 50, 5, NULL, 10, true, NULL),
    ('2026-11-03 00:00:00+00', :'sec2', 'evaluador', 'openai/gpt-4o', 7, 3, 0.0000000004, 10, false, 'LLM_TIMEOUT'),
    ('2026-11-03 10:00+00', NULL, 'activity_designer', 'fake/deterministic', 0, 0, 0, 5, true, NULL);

SET ROLE service_role;
-- Sin filtros: 5 llamadas; el costo suma exacto (sin error de coma flotante) y el NULL no cuenta como cero.
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals() WHERE es_total), 5, 'total: llamadas');
SELECT ia_test.expect((SELECT costo_usd * 10000000000 FROM public.ai_audit_totals() WHERE es_total), 7, 'total: costo exacto a 1e-10');
SELECT ia_test.expect((SELECT llamadas_sin_costo FROM public.ai_audit_totals() WHERE es_total), 1, 'total: costo desconocido contado aparte');
SELECT ia_test.expect((SELECT llamadas_error FROM public.ai_audit_totals() WHERE es_total), 1, 'total: errores');
SELECT ia_test.expect((SELECT tokens_in + tokens_out FROM public.ai_audit_totals() WHERE es_total), 415, 'total: tokens');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_audit_totals() WHERE NOT es_total), 3, 'una fila por modelo');
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals() WHERE modelo = 'openai/gpt-4o'), 3, 'por modelo: llamadas');
SELECT ia_test.expect((SELECT tokens_in FROM public.ai_audit_totals() WHERE modelo = 'openai/gpt-4o'), 307, 'por modelo: tokens de entrada');
SELECT ia_test.expect((SELECT llamadas_sin_costo FROM public.ai_audit_totals() WHERE modelo = 'google/gemini-2.5-flash'), 1, 'por modelo: sin costo');
SELECT ia_test.expect((SELECT costo_usd FROM public.ai_audit_totals() WHERE modelo = 'fake/deterministic'), 0, 'costo cero distinto de desconocido');
SELECT ia_test.expect((SELECT llamadas_sin_costo FROM public.ai_audit_totals() WHERE modelo = 'fake/deterministic'), 0, 'costo cero no es desconocido');
-- Orden: modelos por costo descendente y el total al final.
SELECT ia_test.expect((SELECT es_total::int FROM public.ai_audit_totals() OFFSET 3), 1, 'el total va al final');

-- Filtros: sección, agente y fechas (UTC, ambas inclusivas).
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals(p_section_id => :'sec1') WHERE es_total), 3, 'filtro por sección');
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals(p_agente => 'evaluador') WHERE es_total), 3, 'filtro por agente');
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals(p_desde => '2026-11-02') WHERE es_total), 3, 'desde inclusivo');
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals(p_hasta => '2026-11-02') WHERE es_total), 3, 'hasta inclusivo (todo el día)');
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals(p_desde => '2026-11-02', p_hasta => '2026-11-02') WHERE es_total), 1, 'un solo día');
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals(p_section_id => :'sec1', p_agente => 'tutor') WHERE es_total), 1, 'filtros combinados');

-- Sin coincidencias: total en cero y ninguna fila por modelo.
SELECT ia_test.expect((SELECT llamadas FROM public.ai_audit_totals(p_agente => 'inexistente') WHERE es_total), 0, 'sin coincidencias: total en cero');
SELECT ia_test.expect((SELECT costo_usd FROM public.ai_audit_totals(p_agente => 'inexistente') WHERE es_total), 0, 'sin coincidencias: costo cero');
SELECT ia_test.expect((SELECT count(*) FROM public.ai_audit_totals(p_agente => 'inexistente') WHERE NOT es_total), 0, 'sin coincidencias: sin modelos');
RESET ROLE;

-- Solo el servicio ejecuta la RPC.
SET ROLE anon;
SELECT ia_test.expect_error('SELECT * FROM public.ai_audit_totals()', '42501', 'anon no ejecuta la RPC');
RESET ROLE;
SET ROLE authenticated;
SELECT ia_test.expect_error('SELECT * FROM public.ai_audit_totals()', '42501', 'authenticated no ejecuta la RPC');
RESET ROLE;

ROLLBACK;
\echo 'IA-07: todos los tests pasaron.'
