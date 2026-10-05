# The staging site

`main` running on the server next to production, at **https://staging.barkandambrosiagallery.org**, so changes can
be tried there before a release. Production keeps deploying only when you publish a release.

| | Production | Staging |
|---|---|---|
| Code | the last release | `main`, deployed on every push to it |
| Folder | `/opt/barkandambrosiagallery` | `/opt/barkandambrosia_staging/app` |
| Database | `/opt/barkandambrosia_data/postgres` | its own, `/opt/barkandambrosia_staging/postgres` |
| Images | `/opt/barkandambrosia_data/media` | production's, read through a copy-on-write layer: what staging adds, changes or deletes goes to `/opt/barkandambrosia_staging/media-upper` |
| Containers | `docker compose -f docker-compose.yml -f docker-compose.prod.yml` | `docker compose -f docker-compose.staging.yml -p barkandambrosia-staging` |
| Port | 80 | 8080 |
| Email | sent | never sent (written to the log) |
| AI classifier (Modal) | app `ibbi-api` | its own app, `ibbi-api-staging` |
| Who can sign in | everyone with an account | only the shared account **stagedtesting** / **gallerystaging** |

Every staging page has a red **STAGING** bar, asks search engines not to index it, and sends visitors who are not
signed in to the sign-in page. Its sign-in cookie has a different name from production's, so the two never mix.

## Data

Staging's data is only replaced when you ask for it ("Refresh data" below). A refresh copies production's database
with the same read-only `pg_dump` the nightly backup runs. Then `manage.py staging_prepare` scrubs it:
- it replaces every email address with `staging-<id>@staging.invalid`;
- it locks every account except stagedtesting, which is a superuser, and drops all sessions and API tokens.

Everything else in the copy stays as it was.

Images are never copied. Staging sees production's images as they are. Uploads, edits and deletions on staging
stay on staging.

## Setting it up (once, on the server)

```
bash /opt/barkandambrosiagallery/scripts/staging_setup.sh            # or: ... staging_setup.sh other.domain.org
cd /opt/barkandambrosia_staging/app
bash scripts/staging_deploy.sh      # builds and starts staging
bash scripts/staging_refresh.sh     # fills it with a scrubbed copy of production's data
```

`staging_setup.sh` clones the repository into `/opt/barkandambrosia_staging/app` and writes `.env.staging` there.
- It copies production's settings, for example the classifier keys.
- It leaves out email, cookie and host settings.
- It adds a new secret key, a new database password and `STAGING=1`.
- It only reads `.env.prod`.

To change the account name or password later, edit `STAGING_ACCOUNT` / `STAGING_PASSWORD` in `.env.staging`, then
run `staging_deploy.sh`.

### The address

Cloudflare sends `staging.barkandambrosiagallery.org` to the server's port 80 by default, which is production's
port. Production would refuse it, because the address is not in its `ALLOWED_HOSTS`. Two settings fix that:

1. **DNS:** add a proxied (orange cloud) `A` record `staging` pointing to the server's IP, like the main one.
2. **Rules → Origin Rules → Create rule:** set *Hostname equals staging.barkandambrosiagallery.org*, then
   *Destination Port → Rewrite to 8080*.

Check it with `curl -sI https://staging.barkandambrosiagallery.org/`. It should answer with a redirect to `/accounts/login/`.

### Staging's AI service

Staging has its own copy of the AI classifier on Modal (`ibbi-api-staging`), so a change to the classifier can be
tried on staging while the live site keeps using the released one. *Deploy Staging* deploys it whenever a push to
`main` changes `beetlesgallery/tools/`, or when you run the workflow by hand with **"Also deploy staging's own AI service"** ticked
(do that once after setting staging up). It costs nothing while nobody uses it.

### Deploys from GitHub

`.github/workflows/deploy-staging.yml` deploys staging on every push to `main`. It does nothing until you turn it on:
- go to *GitHub → Settings → Secrets and variables → Actions → Variables*;
- add `STAGING_ENABLED` = `true`.

It uses the same SSH secrets as the production deploy, but only ever works in `/opt/barkandambrosia_staging/app`.

## Refresh data

Use either of these:
- *Actions → Deploy Staging → Run workflow → tick "Refresh data"*;
- on the server: `cd /opt/barkandambrosia_staging/app && bash scripts/staging_refresh.sh`.

Staging is down for the few minutes this takes. Production keeps running.

## Turning it off

```
cd /opt/barkandambrosia_staging/app
docker compose -f docker-compose.staging.yml -p barkandambrosia-staging down
```

Then remove `STAGING_ENABLED`. Staging's data stays in `/opt/barkandambrosia_staging` until you delete that folder.
Production's folders are not affected.
