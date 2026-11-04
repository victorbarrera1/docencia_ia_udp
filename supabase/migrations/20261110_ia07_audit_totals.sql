-- [IA-07] Totales exactos del registro de uso de IA para la rendición del fondo. Depende de 20261105_modelo_ia.sql.
-- Prefijo propio (IA-05 también agrega una migración): esta solo requiere ai_audit_log.
BEGIN;

-- Una fila por modelo más una fila de total general (es_total = true, modelo NULL). Sin filas que coincidan,
-- devuelve igualmente el total en cero. costo_usd suma solo costos conocidos; llamadas_sin_costo cuenta los NULL.
CREATE OR REPLACE FUNCTION public.ai_audit_totals(
    p_section_id UUID DEFAULT NULL, p_desde DATE DEFAULT NULL, p_hasta DATE DEFAULT NULL, p_agente TEXT DEFAULT NULL)
RETURNS TABLE (modelo TEXT, es_total BOOLEAN, llamadas BIGINT, llamadas_ok BIGINT, llamadas_error BIGINT,
    tokens_in BIGINT, tokens_out BIGINT, costo_usd NUMERIC, llamadas_sin_costo BIGINT)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = public, pg_temp AS $$
    SELECT l.modelo, GROUPING(l.modelo) = 1, count(*), count(*) FILTER (WHERE l.ok), count(*) FILTER (WHERE NOT l.ok),
        COALESCE(sum(l.tokens_in), 0)::BIGINT, COALESCE(sum(l.tokens_out), 0)::BIGINT,
        COALESCE(sum(l.costo_usd), 0), count(*) FILTER (WHERE l.costo_usd IS NULL)
    FROM public.ai_audit_log l
    WHERE (p_section_id IS NULL OR l.section_id = p_section_id)
      AND (p_agente IS NULL OR l.agente = p_agente)
      -- Fechas UTC; ambas inclusivas.
      AND (p_desde IS NULL OR l.ts >= p_desde::TIMESTAMP AT TIME ZONE 'UTC')
      AND (p_hasta IS NULL OR l.ts < (p_hasta + 1)::TIMESTAMP AT TIME ZONE 'UTC')
    GROUP BY ROLLUP (l.modelo)
    ORDER BY GROUPING(l.modelo), sum(l.costo_usd) DESC NULLS LAST, l.modelo
$$;

-- Solo el servicio: la autorización por sección la hace el backend (igual que next_version_n).
REVOKE ALL ON FUNCTION public.ai_audit_totals(UUID, DATE, DATE, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ai_audit_totals(UUID, DATE, DATE, TEXT) TO service_role;

COMMIT;
