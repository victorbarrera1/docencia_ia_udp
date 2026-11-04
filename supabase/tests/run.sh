#!/usr/bin/env bash
# Equivalente local/CI a `supabase db reset` + tests de RLS.
# Uso: DATABASE_URL=postgresql://localhost/docencia_rls_test supabase/tests/run.sh
#
# DATABASE_URL debe apuntar a una base VACÍA y desechable: el script se niega a correr si el
# esquema public tiene tablas, y al terminar deja public vacío otra vez.
#
# Escenario A (base que viene de producción): stub → schema.sql → migraciones en orden →
#   schema.sql otra vez → última migración otra vez (idempotencia) → tests.
# Escenario B (instalación nueva solo con schema.sql): stub → schema.sql → tests.
set -euo pipefail

: "${DATABASE_URL:?Define DATABASE_URL apuntando a una base Postgres vacía y desechable}"
PSQL_BIN="${PSQL_BIN:-psql}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPABASE_DIR="$(dirname "$DIR")"

psql_file() { "$PSQL_BIN" "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 -c "SET client_min_messages = warning" -f "$1" >/dev/null; }
psql_cmd()  { "$PSQL_BIN" "$DATABASE_URL" -X -q -At -v ON_ERROR_STOP=1 -c "$1"; }

tables="$(psql_cmd "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
if [ "$tables" != "0" ]; then
  echo "La base de DATABASE_URL tiene $tables tablas en public; usa una base vacía y desechable." >&2
  exit 1
fi

reset_public() { psql_cmd "SET client_min_messages = warning; DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;" >/dev/null; }
trap reset_public EXIT

run_tests() {
  # Resultados de las consultas a /dev/null; las aserciones informan por NOTICE ("ok: ...") o abortan con "FALLA: ..."
  local test_file
  for test_file in rls_test.sql modelo_ia_test.sql ingesta_pdf_test.sql audit_totals_test.sql; do
    "$PSQL_BIN" "$DATABASE_URL" -X -q -v ON_ERROR_STOP=1 -o /dev/null -f "$DIR/$test_file" 2>&1 | sed -n 's/.*NOTICE:  //p; s/.*ERROR:  //p'
  done
  PSQL_BIN="$PSQL_BIN" bash "$DIR/version_concurrency.sh"
}

echo "== Escenario A: schema.sql + migraciones (como producción)"
psql_file "$DIR/00_supabase_stub.sql"
psql_file "$SUPABASE_DIR/schema.sql"
latest=""
for migration in "$SUPABASE_DIR"/migrations/*.sql; do
  echo "   aplicando $(basename "$migration")"
  psql_file "$migration"
  latest="$migration"
done
echo "   re-aplicando schema.sql y $(basename "$latest") (idempotencia)"
psql_file "$SUPABASE_DIR/schema.sql"
psql_file "$latest"
run_tests

echo "== Escenario B: instalación nueva solo con schema.sql"
reset_public
psql_file "$DIR/00_supabase_stub.sql"
psql_file "$SUPABASE_DIR/schema.sql"
run_tests

echo "RLS OK en ambos escenarios."
