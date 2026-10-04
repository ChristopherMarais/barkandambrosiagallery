# Code Map

Where things live. Paths are relative to the repository root.

## Top level

| Path | What it is |
|---|---|
| `manage.py`, `beetlesgallery/settings.py`, `beetlesgallery/urls.py` | Django entry points; every URL is in `urls.py`. |
| `beetlesgallery/beetles_app/` | The one Django app: models, views, game, access control, commands, tests. |
| `beetlesgallery/templates/` | All page templates: `base.html` (layout, sidebar, notices), `beetles/` (pages), `beetles/includes/` (partials), `accounts/`, `emails/`. |
| `beetlesgallery/static/` | `css/input.css` → Tailwind → `css/style.css` (do not edit `style.css` by hand), `js/`, images. |
| `docker-compose.yml` | Base and local development. `docker-compose.override.yml` adds Mailpit locally only. |
| `docker-compose.prod.yml` | Production, always used together with the base file. |
| `docker-compose.staging.yml` | The staging site, used on its own. |
| `pixi.toml` / `pixi.lock` | Python environment and tasks (`pixi run migrate`, `build-css`, `gunicorn` ...). |
| `.github/workflows/` | `tests.yml` (CI), `deploy.yml` (production, on a release), `deploy-staging.yml`, `backup.yaml`, scheduled jobs. |
| `scripts/` | Server-side helper scripts (post-deploy setup, staging). |
| `docs/` | Longer guides that ship with the code: game spec, email setup, interactions data, production and staging setup. |
| `loadtest/` | Locust load test (see its README). |

## Inside `beetles_app/`

| Area | Files |
|---|---|
| **Data model** | `models.py` (images, beetles/ROIs, taxa, interactions, game, access); `migrations/`. |
| **Gallery & annotation pages** | `views.py`, `forms.py`, `image_pipeline.py` (thumbnails, display JPEGs), `bbox_rules.py`, `roi_defaults.py`. |
| **Uploads & updates (CSV)** | `csv_columns.py`, `schema.py`, `tasks.py` (Celery jobs), `management/commands/process_single_*`, `validate_*`. |
| **AI classifier** | `classify_assist.py`, `predictions.py`; the model itself runs on Modal (`beetlesgallery/tools/modal_ibbi_api.py`). |
| **Accounts & permissions** | `access.py`, `access_views.py` (request access, approval), `areas.py` (which parts of the site an account may use), `auth_backends.py`. |
| **Interactions database** | `interaction_*.py`. |
| **Beetle ID Game** | `game.py` (rounds), `game_views.py` (pages and JSON API), `game_scoring.py`, `game_levels.py` (levels, perks, unlocks), `game_rewards.py` (streaks, goals, badges), `game_board.py` (leaderboard), `game_queue.py` (what to show next), `game_taxa.py` (names players can choose), `game_trust.py`, `game_feedback.py`, `game_tips.py` ... one module per job. The design is in `docs/beetle_id_game_spec.md`. |
| **Site-wide bits** | `context_processors.py`, `middleware.py` (time zones), `site_notice.py`, `staging.py`, `templatetags/`. |
| **Management commands** | `management/commands/`: imports, thumbnails, downloads, `recompute_game_scores`, `audit_taxa`, `remind_access_requests` ... |
| **API** | `api/` (Django REST Framework, `/api/v1/`). |
| **Test helpers** | `testing.py` (`make_image`, `make_beetle`, `make_taxon`, `PageBehaviourCase`). |
| **Tests** | `test_*.py`, one file per topic. |

## Settings worth knowing

All are read from the environment in `settings.py`:
- `DJANGO_DEBUG`;
- `SITE_URL`;
- `EMAIL_*`;
- `ACCESS_REQUEST_RECIPIENTS`;
- `MODAL_API_URL`;
- `STAGING`;
- the `GAME_*` tuning values.

Locally they come from `.env`; on the server, from `.env.prod` or `.env.staging`.
