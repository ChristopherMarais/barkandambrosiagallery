#!/usr/bin/env bash
# One-time setup of the staging site, run ON THE SERVER by the owner (docs/staging.md):
#
#     bash /opt/barkandambrosiagallery/scripts/staging_setup.sh [staging.barkandambrosiagallery.org]
#
# It makes /opt/barkandambrosia_staging (its own checkout, database folder and image layer), and writes its
# .env.staging from production's settings, with its own secret key and database password, no email settings,
# and STAGING=1. Nothing of production is changed: .env.prod is only read. Safe to run again.
set -euo pipefail

DOMAIN="${1:-staging.barkandambrosiagallery.org}"
PROD=/opt/barkandambrosiagallery
ROOT=/opt/barkandambrosia_staging
APP="$ROOT/app"

[ -f "$PROD/.env.prod" ] || { echo "No $PROD/.env.prod: is this the production server?"; exit 1; }
mkdir -p "$ROOT/postgres" "$ROOT/media-upper" "$ROOT/media-work"
[ -d "$APP/.git" ] || git clone --branch main "$(git -C "$PROD" remote get-url origin)" "$APP"

if [ -f "$APP/.env.staging" ]; then
  echo "$APP/.env.staging is already there: left as it is."
else
  # Production's settings (e.g. the AI classifier keys) minus everything staging must have its own of
  grep -vE '^(DJANGO_SECRET_KEY|DJANGO_DEBUG|DATABASE_URL|POSTGRES_[A-Z_]*|SITE_URL|ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS|SESSION_COOKIE_DOMAIN|EMAIL_[A-Z_]*|DEFAULT_FROM_EMAIL|ACCESS_REQUEST_RECIPIENTS|CELERY_BROKER_URL|REDIS_CACHE_URL|STAGING[A-Z_]*|APP_VERSION)=' \
    "$PROD/.env.prod" > "$APP/.env.staging" || true
  cat >> "$APP/.env.staging" <<VARS

# --- Staging (written by scripts/staging_setup.sh) ---
STAGING=1
STAGING_ACCOUNT=stagedtesting
STAGING_PASSWORD=gallerystaging
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_USER=beetles_user
POSTGRES_DB=beetles_db
POSTGRES_PASSWORD=$(openssl rand -hex 16)
SITE_URL=https://$DOMAIN
ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://$DOMAIN
GAME_RECOMPUTE_IN_BACKGROUND=1
VARS
  chmod 600 "$APP/.env.staging"
  echo "Wrote $APP/.env.staging for https://$DOMAIN"
fi

echo
echo "Next:"
echo "  1. Point $DOMAIN at this server's port 8080 (docs/staging.md, 'The address')."
echo "  2. cd $APP && bash scripts/staging_deploy.sh && bash scripts/staging_refresh.sh"
echo "  3. Sign in at https://$DOMAIN as stagedtesting / gallerystaging."
