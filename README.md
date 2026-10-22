# Bark and Ambrosia Beetle Gallery

A dedicated web platform for storing, browsing, and managing large datasets of annotated images for **Bark and Ambrosia Beetles**.

## Documentation

Developer setup, the daily dev workflow, git/PR conventions, and the production deployment guide all live in the **[project wiki](https://github.com/ChristopherMarais/barkandambrosiagallery/wiki)**:

- **[Contributing](https://github.com/ChristopherMarais/barkandambrosiagallery/wiki/Contributing)** — how to contribute, from issue to merged PR, and the house rules.
- **[Getting Started](https://github.com/ChristopherMarais/barkandambrosiagallery/wiki/Getting-Started)** — prerequisites, first-time setup, running the app locally, editing code/CSS/models, running tests.
- **[Git Workflow](https://github.com/ChristopherMarais/barkandambrosiagallery/wiki/Git-Workflow)** — how we branch, commit, and open PRs.
- **[Deployment](https://github.com/ChristopherMarais/barkandambrosiagallery/wiki/Deployment)** — the production server, logs, and how deploys happen.

## Quick Start

The site runs only in Docker. You need Git and Docker with Docker Compose (on Windows or Mac, Docker Desktop must be
running); `docker compose` also loads `docker-compose.override.yml`, which adds a local mail inbox.

```bash
git clone https://github.com/ChristopherMarais/barkandambrosiagallery.git
cd barkandambrosiagallery
echo DJANGO_DEBUG=True > .env
docker compose build
docker compose run --rm web pixi run install-js
docker compose up -d --wait db
docker compose exec db psql -U beetles_user -d beetles_db -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
docker compose run --rm web pixi run migrate
docker compose run --rm web pixi run python manage.py createsuperuser
docker compose up -d
```

Then open [http://localhost:8000](http://localhost:8000). See [Getting Started](https://github.com/ChristopherMarais/barkandambrosiagallery/wiki/Getting-Started) in the wiki for the full walkthrough and the daily dev loop.

## Licence

The platform code is MIT-licensed (see [LICENSE](LICENSE)).

The AI service (`beetlesgallery/tools/modal_ibbi_api.py`) is deployed separately from the web app. It installs the
[`ibbi`](https://github.com/ChristopherMarais/IBBI) package together with
[Ultralytics](https://github.com/ultralytics/ultralytics), which is AGPL-3.0, and runs IBBI model weights under their
own licences: AGPL-3.0 for the Ultralytics-trained detectors, the DINOv3 License for the DINOv3 classifiers and MIT for
the BioCLIP 2 classifier (see [IBBI's licences](https://github.com/ChristopherMarais/IBBI#licences)).

Images in the gallery keep the licence given by their contributor, shown on each specimen record.
