# Deployment (owner only)

Production runs on a Contabo server with Docker Compose, behind Cloudflare. **Only the owner deploys, and only the owner
works on the server.** Contributors never need server access: merged work is visible on [Staging](Staging).

## How a release reaches production

1. The owner merges pull requests into `main`. Each merge updates staging, not production.
2. When staging looks right, the owner **publishes a GitHub release** (for example `v2.2.0`). That starts the
   *Deploy App* workflow (`.github/workflows/deploy.yml`). It can also be started by hand from *Actions*.
   First it deploys the AI classifier to Modal, but only if its code (`beetlesgallery/tools/`) changed since the
   previous release; then the website. If the AI deploy fails, the website is not deployed.
3. The workflow connects over SSH to `/opt/barkandambrosiagallery` and does these steps in order:
   1. It pulls `main` and builds new images. The live site keeps running on the old version meanwhile.
   2. It checks settings and the database connection in a throw-away container (`check --deploy`,
      `migrate --plan`).
   3. It builds CSS and static files, runs migrations, and loads the published interactions dataset. The last step
      only adds missing rows.
   4. It switches the web container and the workers to the new version.
   5. It runs a health check: the public home page must answer 200 within two minutes. If it doesn't, the previous
      version is put back and the job fails.

Any failing step stops the deploy before the live site changes.

After a release with new settings or one-off steps, the PR description says what to run. `scripts/post_deploy_setup.sh`
covers email and the interactions dataset
([`docs/production_setup.md`](https://github.com/ChristopherMarais/barkandambrosiagallery/blob/main/docs/production_setup.md)).

## The server

| | Path |
|---|---|
| Code and settings (`.env.prod`, never committed) | `/opt/barkandambrosiagallery` |
| Database files | `/opt/barkandambrosia_data/postgres` |
| Images and other uploads (served at `/media/`) | `/opt/barkandambrosia_data/media` |
| Database dumps for backups | `/opt/barkandambrosia_data/db_backup` |
| Staging | `/opt/barkandambrosia_staging` |

Always name both compose files on the server. Otherwise Docker would also load the local-only override (Mailpit):

```bash
cd /opt/barkandambrosiagallery
P="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

$P ps                                   # what is running
$P logs -f --tail=200 web               # web log (Ctrl+C to leave)
$P logs -f --tail=200 worker-heavy      # CSV uploads, updates and downloads
$P restart web worker worker-heavy      # after editing .env.prod
$P exec web pixi run python manage.py createsuperuser
```

Services:
- `web`: gunicorn, on port 80 behind Cloudflare;
- `db`: Postgres 15;
- `redis`;
- `worker`: quick jobs such as game re-scoring;
- `worker-heavy`: uploads and downloads, one at a time, at low priority.

## Scheduled workflows

They all run against the server:
- **Dropbox Backup** (`backup.yaml`): dumps the database to `db_backup/`, then copies the data folder to Dropbox.
  On the 1st of the month it copies everything; on other days it skips the images.
- **Access reminders** and **game scores**: run their management commands in the web container.

## Rules

- **Never** run `docker compose down -v`, delete the data folders, or run `flush`, `reset_db` or
  `migrate_taxonomy_to_db` against production unless that is exactly what you intend. `migrate_taxonomy_to_db`
  reloads the taxonomy, and admins run it by hand.
- **Never** run `git clean -fdx` in `/opt/barkandambrosiagallery`: it would delete `.env.prod`.
- **Never** print or paste secrets. To check that two copies match, compare `md5sum` fingerprints.
