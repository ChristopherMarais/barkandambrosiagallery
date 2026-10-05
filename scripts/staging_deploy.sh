#!/usr/bin/env bash
# Deploy the staging site's checkout (normally run by .github/workflows/deploy-staging.yml after `git reset` to
# origin/main). Runs only in /opt/barkandambrosia_staging/app, and only touches the barkandambrosia-staging
# containers: production's containers, database and images are never written to.
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(pwd -P)" != "/opt/barkandambrosiagallery" ] || { echo "This is the production checkout: stopping"; exit 1; }
grep -qx 'STAGING=1' .env.staging 2>/dev/null || { echo "No .env.staging with STAGING=1 here: stopping"; exit 1; }
STAGING="docker compose -f docker-compose.staging.yml -p barkandambrosia-staging"

# Staging calls its own AI service (Modal app ibbi-api-staging), never the live one (older setups lacked this line)
STAGING_AI="https://christophermarais--ibbi-api-staging-fastapi-app.modal.run/analyze"
if ! grep -q '^MODAL_API_URL=.*ibbi-api-staging' .env.staging; then
  sed -i '/^MODAL_API_URL=/d' .env.staging
  echo "MODAL_API_URL=$STAGING_AI" >> .env.staging
fi

echo "APP_VERSION=staging-$(git rev-parse --short HEAD)" > .env.version
$STAGING build web worker
$STAGING up -d --wait db redis
$STAGING run --rm --no-deps -T web pixi run install-js
$STAGING run --rm --no-deps -T web pixi run build-css
$STAGING run --rm --no-deps -T web pixi run collectstatic --no-input
$STAGING run --rm --no-deps -T web pixi run migrate
$STAGING run --rm --no-deps -T web pixi run import-interactions
$STAGING run --rm --no-deps -T web pixi run python manage.py staging_prepare
$STAGING up -d --no-deps web worker
docker image prune -f > /dev/null
echo "Staging is on $(git rev-parse --short HEAD)"
