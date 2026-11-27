#!/usr/bin/env bash
# Applies the migration to a FRESH database and runs the constraint tests.
# Requires: docker compose, psql. Usage: ./scripts/db_verify.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
: "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}"
COMPOSE="docker compose -f infra/docker-compose.db.yml"
$COMPOSE down -v
$COMPOSE up -d --wait
export PGPASSWORD="$POSTGRES_PASSWORD"
URL="postgresql://signtranslator@127.0.0.1:5432/signtranslator"
python3 scripts/check_schema.py db/migrations/0001_initial.sql
psql -v ON_ERROR_STOP=1 -f db/migrations/0001_initial.sql "$URL"
psql -v ON_ERROR_STOP=1 -f db/tests/constraints.sql "$URL"
echo "DB VERIFIED"
