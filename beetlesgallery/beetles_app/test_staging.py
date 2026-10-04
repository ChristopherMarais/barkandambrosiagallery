"""
The staging site (docs/staging.md): closed to everyone but the shared account, scrubbed of real email addresses and
passwords, and kept apart from production's containers, database and images.
"""
import re
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.authtoken.models import Token

from beetlesgallery.beetles_app.models import AccessRequest
from beetlesgallery.beetles_app.test_pages import NO_WHITENOISE, PLAIN_STATIC

BASE = settings.BASE_DIR
STAGING_COMPOSE = "docker compose -f docker-compose.staging.yml -p barkandambrosia-staging"


@override_settings(STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class ProductionIsUnchangedTests(TestCase):
    def test_staging_is_off_by_default(self):
        self.assertFalse(settings.STAGING)
        res = self.client.get("/robots.txt")
        self.assertNotEqual(res.content.decode(), "User-agent: *\nDisallow: /\n")
        self.assertNotIn("X-Robots-Tag", self.client.get("/accounts/login/"))
        self.assertNotContains(self.client.get("/accounts/login/"), 'data-testid="staging-signin"')

    def test_the_prepare_command_refuses_to_run_outside_staging(self):
        get_user_model().objects.create_user("someone", email="a@example.org", password="pw")
        with self.assertRaises(CommandError):
            call_command("staging_prepare", stdout=StringIO())
        self.assertTrue(get_user_model().objects.get(username="someone").has_usable_password())
        self.assertFalse(get_user_model().objects.filter(username="stagedtesting").exists())


@override_settings(STAGING=True, STAGING_ACCOUNT="stagedtesting", STAGING_PASSWORD="gallerystaging",
                   STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class StagingSiteTests(TestCase):
    def prepare(self):
        call_command("staging_prepare", stdout=StringIO())

    def test_every_page_asks_for_the_staging_account(self):
        res = self.client.get("/game/")
        self.assertRedirects(res, "/accounts/login/?next=/game/", fetch_redirect_response=False)
        self.assertEqual(res["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(self.client.get("/media/thumbs/x.jpg").status_code, 302)
        login = self.client.get("/accounts/login/")
        self.assertEqual(login.status_code, 200)
        self.assertContains(login, 'data-testid="staging-signin"')
        self.assertContains(login, '<meta name="robots" content="noindex, nofollow">')
        self.assertEqual(self.client.get("/robots.txt").content.decode(), "User-agent: *\nDisallow: /\n")

    def test_the_shared_account_signs_in_and_sees_the_staging_bar(self):
        self.prepare()
        res = self.client.post("/accounts/login/", {"username": "stagedtesting", "password": "gallerystaging"})
        self.assertEqual(res.status_code, 302)
        page = self.client.get("/game/", follow=True)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-testid="staging-banner"')
        account = get_user_model().objects.get(username="stagedtesting")
        self.assertTrue(account.is_superuser and account.is_staff and account.is_active)

    def test_real_accounts_are_locked_and_their_addresses_scrubbed(self):
        User = get_user_model()
        alice = User.objects.create_user("alice", email="alice@ufl.edu", password="real-password")
        Token.objects.create(user=alice)
        self.client.force_login(alice)
        req = AccessRequest.objects.create(name="Bob", email="bob@example.org")
        self.prepare()

        alice.refresh_from_db()
        self.assertFalse(alice.has_usable_password())
        self.assertEqual(alice.email, f"staging-{alice.pk}@staging.invalid")
        req.refresh_from_db()
        self.assertEqual(req.email, f"staging-{req.pk}@staging.invalid")
        self.assertFalse(Session.objects.exists())
        self.assertFalse(Token.objects.exists())
        self.assertFalse(self.client.login(username="alice", password="real-password"))
        self.assertTrue(self.client.login(username="stagedtesting", password="gallerystaging"))

    def test_running_it_again_keeps_the_account_and_resets_its_password(self):
        self.prepare()
        account = get_user_model().objects.get(username="stagedtesting")
        account.set_password("changed")
        account.save()
        self.prepare()
        self.assertEqual(get_user_model().objects.filter(username="stagedtesting").count(), 1)
        self.assertTrue(self.client.login(username="stagedtesting", password="gallerystaging"))
        self.assertTrue(get_user_model().objects.get(username="stagedtesting").email.endswith("@staging.invalid"))


class StagingIsKeptApartTests(SimpleTestCase):
    """Static checks of the compose file, scripts and workflow: staging never writes to production."""

    def read(self, path):
        return (BASE / path).read_text()

    def commands(self, path):
        return [ln.strip() for ln in self.read(path).splitlines() if ln.strip() and not ln.strip().startswith("#")]

    def test_the_compose_file_has_its_own_database_and_only_reads_production_images(self):
        compose = self.read("docker-compose.staging.yml")
        self.assertIn("name: barkandambrosia-staging", compose)
        self.assertIn("/opt/barkandambrosia_staging/postgres:/var/lib/postgresql/data", compose)
        self.assertNotIn("/opt/barkandambrosia_data/postgres", compose)
        self.assertNotIn(".env.prod", compose)
        self.assertNotIn('"80:', compose)
        # production's images appear only as the read-only lower layer of the overlay
        mentions = [ln for ln in compose.splitlines() if "/opt/barkandambrosia_data/media" in ln]
        self.assertEqual(len(mentions), 1)
        self.assertIn("lowerdir=/opt/barkandambrosia_data/media,upperdir=/opt/barkandambrosia_staging/", mentions[0])

    def test_the_scripts_only_touch_the_staging_containers(self):
        for script in ("scripts/staging_deploy.sh", "scripts/staging_refresh.sh"):
            lines = self.commands(script)
            self.assertIn(f'STAGING="{STAGING_COMPOSE}"', lines, script)
            self.assertTrue(any("/opt/barkandambrosiagallery" in ln and "stopping" in ln for ln in lines), script)
            self.assertTrue(any("STAGING=1" in ln and "stopping" in ln for ln in lines), script)
            for ln in lines:
                if "docker compose" in ln and not ln.startswith("STAGING="):
                    # the one production command: the read-only dump, as the nightly backup runs it
                    self.assertIn("exec -T db pg_dump", ln, f"{script}: {ln}")
                for word in ("dropdb", "createdb", "FLUSHALL", "migrate", "staging_prepare", "up -d", "stop "):
                    if re.search(rf"\b{word}", ln):
                        self.assertTrue(ln.startswith("$STAGING "), f"{script}: {ln}")

    def test_the_workflow_is_off_until_enabled_and_never_uses_the_production_checkout(self):
        workflow = self.read(".github/workflows/deploy-staging.yml")
        self.assertIn("if: vars.STAGING_ENABLED == 'true'", workflow)
        self.assertIn("cd /opt/barkandambrosia_staging/app ||", workflow)
        self.assertNotIn("cd /opt/barkandambrosiagallery", workflow)
        self.assertNotIn("docker-compose.prod.yml", workflow)
        self.assertNotIn("\n  release:", workflow)   # production alone deploys on a release

    def test_the_staging_settings_file_is_never_committed(self):
        self.assertIn(".env.staging", self.read(".gitignore").splitlines())
