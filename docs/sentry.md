# Sentry error reports

When the site or the Celery worker hits an error, a report goes to [Sentry](https://sentry.io) with the stack trace,
the page, the browser and the release. It is off everywhere until a server sets `SENTRY_DSN`; local runs, CI and
tests never send anything.

## Turning it on (server owner)

1. In Sentry, create a project with platform **Django** and copy its **DSN**.
2. Add to the server's env file and redeploy (or restart the containers):

   | File | Lines |
   |---|---|
   | `/opt/barkandambrosiagallery/.env.prod` | `SENTRY_DSN=<the DSN>` and `SENTRY_ENVIRONMENT=production` |
   | staging's `.env.staging` | `SENTRY_DSN=<the DSN>` and `SENTRY_ENVIRONMENT=staging` |

   One project can take both; the environment keeps them apart in Sentry's filters.
3. Optional: `SENTRY_TRACES_SAMPLE_RATE=0.05` also records how long 5% of requests take (performance). It uses up the
   plan's allowance faster, so it is off (0) by default.

The release on each report is `APP_VERSION`, which the deploy already writes (`.env.version`, e.g. `v2.4.0`).

## What is sent, and what is not

- Sent: the error and stack trace, the URL, the browser and OS, the release and environment.
- Not sent: IP addresses, cookies, user names, request bodies (`send_default_pii=False`); Sentry's scrubber also
  removes anything named like a password, token or key.
- The privacy notice (`/privacy/`) shows an "Error reports" section only while `SENTRY_DSN` is set.

## Checking it works

After deploying with the DSN set, open a Django shell on the server and send a test message:

```
docker compose -f docker-compose.prod.yml exec web pixi run python manage.py shell -c "import sentry_sdk; sentry_sdk.capture_message('Sentry test from production')"
```

It shows up under Issues in Sentry within a minute.

Code: `beetlesgallery/sentry_setup.py` (started from `settings.py`), tests in `beetles_app/test_sentry.py`.
