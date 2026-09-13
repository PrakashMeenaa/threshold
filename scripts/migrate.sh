#!/usr/bin/env bash
set -euo pipefail

PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5433}"
PGUSER="${PGUSER:-threshold_app}"
PGPASSWORD="${PGPASSWORD:-threshold_app}"
PGDATABASE="${PGDATABASE:-threshold}"
export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE

if [[ -z "${THRESHOLD_API_PASSWORD:-}" ]]; then
    echo "THRESHOLD_API_PASSWORD must be set" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="$SCRIPT_DIR/../db/migrations"

for migration in "$MIGRATIONS_DIR"/*.sql; do
    echo "Applying $(basename "$migration")"
    psql -v ON_ERROR_STOP=1 -f "$migration"
done

psql -v ON_ERROR_STOP=1 -v pass="$THRESHOLD_API_PASSWORD" <<< "ALTER ROLE threshold_api_user WITH PASSWORD :'pass';"

echo "Migrations applied successfully"
