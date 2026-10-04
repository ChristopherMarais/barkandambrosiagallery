"""
Guards for what a production deploy can do to existing data.

A deploy (see .github/workflows/deploy.yml) rebuilds the containers and runs
``migrate`` against the live database and media volume. These tests fail when a
change would make that unsafe:

* a new migration that drops or deletes data,
* a deploy step that calls a command that does not exist, or one that purges data.

They are static checks (no database is touched), so they run in the normal suite.
"""
import re
import tomllib

from django.conf import settings
from django.core.management import get_commands
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import DeleteModel, RemoveField, RunPython, RunSQL
from django.test import SimpleTestCase

# Production was on migration 0017 when this guard was added. The older destructive
# migrations (0003, 0008, 0014) ran long ago and are not re-run, so only newer ones are checked.
BASELINE = 17

# A migration that has to remove or delete data on purpose sets, on its Migration class,
#     DESTRUCTIVE_OK = "why this is safe / who signed it off"
# A data migration (RunPython) sets
#     DATA_MIGRATION_REVIEWED = "what it reads and writes"
# Either makes the reviewer look at it twice, which is the point.

DESTRUCTIVE_SQL = re.compile(r"\b(DROP|DELETE|TRUNCATE)\b", re.I)

# Things a deploy must never run against production.
DEPLOY_DENYLIST = [
    "migrate_taxonomy_to_db",       # purges and reloads the taxonomy
    # import_pathogen_interactions is fine on its own (it only adds missing rows) but not with these:
    "--clear",                      # deletes and reloads the published interactions
    "--refresh",                    # overwrites corrections made since the first load
    "flush",
    "sqlflush",
    "reset_db",
    "dropdb",
    "docker compose down -v",
    "docker-compose down -v",
    "docker volume rm",
    "docker volume prune",
    "rm -rf /opt/barkandambrosia_data",
]


def new_beetles_migrations():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    for (app, name), migration in sorted(loader.disk_migrations.items()):
        if app != "beetles_app":
            continue
        number = int(name.split("_", 1)[0])
        if number > BASELINE:
            yield name, migration


class MigrationSafetyTests(SimpleTestCase):
    def test_there_are_migrations_to_check(self):
        # If the loader finds nothing, the checks below pass vacuously.
        self.assertTrue(list(new_beetles_migrations()))

    def test_new_migrations_do_not_destroy_data(self):
        problems = []
        for name, migration in new_beetles_migrations():
            if getattr(migration, "DESTRUCTIVE_OK", None):
                continue
            for op in migration.operations:
                if isinstance(op, (RemoveField, DeleteModel)):
                    problems.append(f"{name}: {op.__class__.__name__} {op.describe()}")
                elif isinstance(op, RunSQL):
                    statements = op.sql if isinstance(op.sql, (list, tuple)) else [op.sql]
                    if any(DESTRUCTIVE_SQL.search(str(s)) for s in statements):
                        problems.append(f"{name}: RunSQL with DROP/DELETE/TRUNCATE")
        self.assertEqual(
            problems, [],
            "These migrations remove data on the next deploy. If that is intended, set "
            "DESTRUCTIVE_OK = '<why it is safe>' on the migration's Migration class.",
        )

    def test_new_data_migrations_are_marked_as_reviewed(self):
        unreviewed = [
            name for name, migration in new_beetles_migrations()
            if any(isinstance(op, RunPython) for op in migration.operations)
            and not getattr(migration, "DATA_MIGRATION_REVIEWED", None)
        ]
        self.assertEqual(
            unreviewed, [],
            "RunPython migrations rewrite production rows. Set "
            "DATA_MIGRATION_REVIEWED = '<what it reads and writes>' on the Migration class once someone has checked.",
        )


class DeployScriptTests(SimpleTestCase):
    def setUp(self):
        self.deploy = (settings.BASE_DIR / ".github" / "workflows" / "deploy.yml").read_text()
        self.pixi_tasks = tomllib.loads((settings.BASE_DIR / "pixi.toml").read_text())["tasks"]

    def test_pixi_tasks_the_deploy_runs_exist(self):
        # Steps that run inside the production web container. (The Modal job runs on
        # GitHub's runner and calls the 'modal' program, not a pixi task.)
        wanted = set(re.findall(r"(?:exec -T|run --rm --no-deps -T) web pixi run ([\w-]+)", self.deploy))
        self.assertTrue(wanted, "found no production 'pixi run' steps in deploy.yml")
        wanted.discard("python")   # 'pixi run python manage.py ...' runs Python itself, not a named task
        self.assertEqual(sorted(t for t in wanted if t not in self.pixi_tasks), [])

    def test_management_commands_behind_pixi_tasks_exist(self):
        available = set(get_commands())
        missing = []
        for task, command in self.pixi_tasks.items():
            for name in re.findall(r"manage\.py (\w+)", str(command)):
                if name not in available:
                    missing.append(f"pixi task '{task}' runs 'manage.py {name}'")
        self.assertEqual(missing, [])

    def test_deploy_loads_the_interactions_dataset_the_page_is_built_from(self):
        lines = [ln for ln in self.deploy.splitlines() if not ln.strip().startswith("#")]
        runs = [ln for ln in lines if "import-interactions" in ln]
        self.assertEqual(len(runs), 1, "the deploy should load the interactions dataset exactly once")
        self.assertNotIn("--", runs[0].split("import-interactions", 1)[1])
        # the task is the importer with no flags (so it only adds what is missing)
        self.assertEqual(self.pixi_tasks["import-interactions"], "python manage.py import_pathogen_interactions")
        # after the migrations, which create the table
        self.assertLess(self.deploy.index("pixi run migrate"), self.deploy.index("import-interactions"))

    def test_deploy_never_runs_data_destroying_commands(self):
        # Only look at the commands themselves, not comments that mention them.
        lines = [ln for ln in self.deploy.splitlines() if not ln.strip().startswith("#")]
        found = [item for item in DEPLOY_DENYLIST if any(item in ln for ln in lines)]
        self.assertEqual(found, [], "deploy.yml must not run these against production")

    def test_the_live_site_is_only_switched_after_the_checks_and_migrations(self):
        # A failing step must stop the deploy before the running site changes (set -e), the database must be
        # checked before anything else, and the switch to new containers comes after the migrations.
        self.assertIn("set -eu", self.deploy)
        self.assertIn("set -o pipefail", self.deploy)
        order = ["build web worker", "migrate --plan", "pixi run migrate", "import-interactions", "up -d --no-deps web worker"]
        positions = [self.deploy.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("up -d --build", self.deploy)   # that swapped the site before the checks

    def test_a_failed_health_check_rolls_back_and_fails_the_job(self):
        self.assertIn('curl -s -o /dev/null -w "%{http_code}" "$SITE/"', self.deploy)
        tail = self.deploy[self.deploy.index("Health check failed"):]
        self.assertIn('git reset --hard "$PREVIOUS"', tail)
        self.assertIn("exit 1", tail)

    def test_deploy_keeps_the_data_volumes(self):
        prod = (settings.BASE_DIR / "docker-compose.prod.yml").read_text()
        # Database and uploaded images live on the host, outside the containers.
        self.assertIn("/opt/barkandambrosia_data/postgres:/var/lib/postgresql/data", prod)
        self.assertIn("/opt/barkandambrosia_data/media:/app/media", prod)


class LocalOnlyComposeTests(SimpleTestCase):
    """docker-compose.override.yml (the local mail catcher) must never reach production."""

    def test_the_server_names_its_compose_files_so_the_local_override_is_never_loaded(self):
        for workflow in (".github/workflows/deploy.yml", ".github/workflows/backup.yaml", ".github/workflows/access-reminders.yml", ".github/workflows/game-scores.yml"):
            text = (settings.BASE_DIR / workflow).read_text()
            for line in text.splitlines():
                if "docker compose" in line and not line.strip().startswith("#"):
                    self.assertIn("-f docker-compose.yml -f docker-compose.prod.yml", line, f"{workflow}: {line.strip()}")

    def test_the_override_only_adds_the_mail_catcher_and_points_the_site_at_it(self):
        text = (settings.BASE_DIR / "docker-compose.override.yml").read_text()
        self.assertIn("axllent/mailpit", text)
        self.assertIn("EMAIL_HOST: ${EMAIL_HOST:-mailpit}", text)
        self.assertNotIn("postgres_data", text)   # no volumes or database settings are touched


class BackupCoversTheInteractionsTests(SimpleTestCase):
    """The interactions live in the database, so they are backed up exactly as the rest of the site is."""

    def test_the_backup_dumps_the_whole_database_and_sends_it_to_dropbox(self):
        backup = (settings.BASE_DIR / ".github" / "workflows" / "backup.yaml").read_text()
        dump = [ln for ln in backup.splitlines() if "pg_dump" in ln and not ln.strip().startswith("#")]
        self.assertEqual(len(dump), 1)
        options = dump[0].split("pg_dump", 1)[1]   # (the " -T " before it is docker's "no terminal", not a table filter)
        for narrowing in (" -t ", "--table", "--exclude-table", " -T ", "--schema-only", "--data-only"):
            self.assertNotIn(narrowing, options, "the dump must cover every table, pathogen_interactions included")
        # the dump is written into the data folder that is copied to Dropbox (but not into media/, which the site
        # serves), and the fast backup does not exclude .sql files
        self.assertIn("> /opt/barkandambrosia_data/db_backup/db_full_backup.sql", dump[0])
        self.assertNotIn("/media/", dump[0])
        self.assertIn("rclone copy /opt/barkandambrosia_data ", backup)
        self.assertNotIn('--exclude "*.sql"', backup)
        self.assertNotIn('--exclude "db_backup/', backup)

    def test_the_interactions_table_is_an_ordinary_table_of_the_database(self):
        from beetlesgallery.beetles_app.models import PathogenInteraction
        self.assertEqual(PathogenInteraction._meta.db_table, "pathogen_interactions")
        self.assertFalse(PathogenInteraction._meta.managed is False)
