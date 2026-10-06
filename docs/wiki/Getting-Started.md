# Getting Started

This guide covers how to set up the project, run it locally, and make changes (Python, HTML, CSS).
The site runs only in Docker: every command below goes through Docker Compose, and nothing else (Python, Node, Pixi)
needs installing on your computer.

See [Git Workflow](Git-Workflow) for how to share your changes, and [Code Map](Code-Map) to find your way around the code.

## 1. Prerequisites

Before starting, make sure you have:

- **Docker** with Docker Compose: Docker Desktop on Windows or Mac (start it and leave it running), or Docker Engine on Linux
- **Git**

## 2. Initial Setup (First Time Only)

If you are cloning this repository for the first time (or setting up a new machine), follow these steps to initialize the environment.

1. **Clone the repository**
   ```bash
   git clone https://github.com/ChristopherMarais/barkandambrosiagallery.git
   cd barkandambrosiagallery
   ```

2. **Create the `.env` file**

   Docker Compose will not start without a `.env` file in the project root. For local development it only needs debug mode turned on. Without it, pages fail with `Missing staticfiles manifest entry`.
   ```
   DJANGO_DEBUG=True
   ```

3. **Build the environment**

   This builds the Docker container and installs the Python (Pixi) dependencies.
   ```bash
   docker compose build
   ```

4. **Install JavaScript dependencies**

   The Docker build does not install the npm packages that Tailwind needs. This puts them in `node_modules/` in your project folder.
   ```bash
   docker compose run --rm web pixi run install-js
   ```

5. **Initialize the database**

   Start the database and enable the `pg_trgm` extension (the migrations add trigram indexes and fail without it). Then run the migrations to create the database schema.
   ```bash
   docker compose up -d --wait db
   docker compose exec db psql -U beetles_user -d beetles_db -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
   docker compose run --rm web pixi run migrate
   ```

6. **Create an admin user**

   You need this to access the upload tools and admin panel.
   ```bash
   docker compose run --rm web pixi run python manage.py createsuperuser
   ```

## 3. Daily Development Cycle

### Step A: Start the server

To view the website, start the containers. This runs the database and the Django web server.
```bash
docker compose up -d
```

- **View the site:** http://localhost:8000
- **Read the emails it sends:** http://localhost:8025. Mailpit, from `docker-compose.override.yml`, catches every email
  locally (sign-up confirmations, approvals, password resets), so nothing is sent for real.
- **Stop the site:** `docker compose down` (your database and uploads stay in Docker volumes).

### Step B: Editing code (Python & HTML)

**Hot reloading:** the project is configured to watch your folders. If you edit any `.py` file (views, models) or `.html` template, the server automatically reloads. Just refresh your browser.

### Step C: Editing styles (Tailwind CSS)

Because we use Tailwind, changing classes in HTML (e.g. `text-red-500` to `text-blue-500`) requires recompiling the CSS file.

1. Open a **new terminal** window.
2. Run the CSS watcher:
   ```bash
   docker compose run --rm web pixi run watch-css
   ```
   This runs in "watch mode" (it stays open — press `Ctrl+C` to stop it). As you save HTML or JS files, it regenerates `style.css` instantly.
3. For a one-time rebuild without watching: `docker compose run --rm web pixi run build-css`.

### Step D: Modifying the database (models)

If you edit `models.py` (e.g. adding a new field to `Beetles`), you must update the database schema.

1. **Create the migration file:**
   ```bash
   docker compose run --rm web pixi run python manage.py makemigrations
   ```
2. **Apply the migration:**
   ```bash
   docker compose run --rm web pixi run migrate
   ```

### Step E: Adding new dependencies

- **Python:** add the package to `pixi.toml` under `[dependencies]`, update `pixi.lock` with `docker compose run --rm web pixi lock`, then `docker compose build` to rebuild the container.
- **JavaScript:** edit `package.json`, then `docker compose run --rm web pixi run install-js` to update `node_modules/` and `package-lock.json`.

## 4. Running the Tests

```bash
docker compose run --rm web pixi run python manage.py test beetlesgallery --noinput
```

More on running and writing tests in [Testing](Testing).

## 5. Cheat Sheet (Commands)

| Goal | Command |
| --- | --- |
| **Start site** | `docker compose up` |
| **Stop site** | `docker compose down` |
| **Watch CSS** | `docker compose run --rm web pixi run watch-css` |
| **Rebuild CSS (once)** | `docker compose run --rm web pixi run build-css` |
| **Install JS dependencies** | `docker compose run --rm web pixi run install-js` |
| **Apply DB changes** | `docker compose run --rm web pixi run migrate` |
| **Create migration** | `docker compose run --rm web pixi run python manage.py makemigrations` |
| **Create admin (locally)** | `docker compose run --rm web pixi run python manage.py createsuperuser` |
| **Rebuild container** | `docker compose build` |
| **Run arbitrary command** | `docker compose run --rm web pixi run python manage.py <command>` |
| **Run tests** | `docker compose run --rm web pixi run python manage.py test beetlesgallery --noinput` |

Next: read [Contributing](Contributing), then [Git Workflow](Git-Workflow) for how to share your changes.
