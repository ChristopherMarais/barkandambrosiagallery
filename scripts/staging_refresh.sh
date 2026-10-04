#!/usr/bin/env bash
# Replace the staging site's data with a fresh copy of production's, scrubbed (docs/staging.md). Run on demand:
# by the owner on the server (cd /opt/barkandambrosia_staging/app && bash scripts/staging_refresh.sh), or with
# "Refresh data" on the Deploy Staging workflow.
#
# Production is only read, with the same pg_dump the nightly backup runs. Everything else happens in the
# barkandambrosia-staging containers. Images are not copied: staging sees production's through its own layer.
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(pwd -P)" != "/opt/barkandambrosiagallery" ] || { echo "This is the production checkout: stopping"; exit 1; }
grep -qx 'STAGING=1' .env.staging 2>/dev/null || { echo "No .env.staging with STAGING=1 here: stopping"; exit 1; }
STAGING="docker compose -f docker-compose.staging.yml -p barkandambrosia-staging"
PROD_DIR=/opt/barkandambrosiagallery

$STAGING up -d --wait db redis
$STAGING stop web worker || true

echo "Emptying the staging database..."
$STAGING exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'

echo "Copying production's database into staging (production is only read)..."
(cd "$PROD_DIR" && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db pg_dump -U beetles_user --no-owner --no-privileges beetles_db) \
  | $STAGING exec -T db sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > /dev/null

$STAGING exec -T redis redis-cli FLUSHALL > /dev/null
$STAGING run --rm --no-deps -T web pixi run migrate
$STAGING run --rm --no-deps -T web pixi run python manage.py staging_prepare
$STAGING up -d --no-deps web worker
echo "Staging has a fresh copy of production's data from $(date -u +%Y-%m-%d\ %H:%M) UTC"
