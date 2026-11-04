#!/usr/bin/env bash
# Dos conexiones compiten por el mismo número. Solo desde run.sh en una base desechable.
set -euo pipefail
: "${DATABASE_URL:?Solo ejecutar dentro del harness local/CI}"
PSQL_BIN="${PSQL_BIN:-psql}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$(mktemp -d)"
trap 'rm -rf "$LOG_DIR"' EXIT

# A diferencia del test transaccional, estas fixtures deben ser visibles entre conexiones.
# run.sh vacía public al pasar de escenario y al salir (también ante un fallo).
"$PSQL_BIN" "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 -f "$DIR/modelo_ia_seed.sql" >/dev/null
worker() {
  "$PSQL_BIN" "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
SET LOCAL ROLE service_role;
SELECT public.next_version_n('e1040000-0000-4000-a000-000000000001') AS next_n \gset
-- Amplía deliberadamente la carrera: sin bloqueo, ambas conexiones reservarían el 3.
SELECT pg_sleep(0.5);
INSERT INTO public.submission_versions (submission_id, version_n, storage_path, subido_por)
VALUES ('e1040000-0000-4000-a000-000000000001', :next_n, 'test-ia/concurrente.pdf', 'a1040000-0000-4000-a000-000000000004');
COMMIT;
SQL
}
worker >"$LOG_DIR/a.log" 2>&1 &
pid_a=$!
worker >"$LOG_DIR/b.log" 2>&1 &
pid_b=$!
failed=0
wait "$pid_a" || failed=1
wait "$pid_b" || failed=1
if [ "$failed" != 0 ]; then
  cat "$LOG_DIR/a.log" "$LOG_DIR/b.log" >&2
  exit 1
fi
versions="$("$PSQL_BIN" "$DATABASE_URL" -X -q -At -v ON_ERROR_STOP=1 -c "SELECT string_agg(version_n::text, ',' ORDER BY version_n) FROM public.submission_versions WHERE submission_id = 'e1040000-0000-4000-a000-000000000001'")"
if [ "$versions" != '1,2,3,4' ]; then
  echo "FALLA: versiones concurrentes: $versions" >&2
  exit 1
fi
echo 'ok: dos conexiones asignan versiones 3 y 4 sin colisiones'
