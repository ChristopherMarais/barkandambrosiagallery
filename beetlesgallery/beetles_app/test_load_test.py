"""
Issue #383: heavy jobs on their own queue, a site notice for stress tests, and throw-away players for the load test.
"""
import io
import os
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app import site_notice
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, SiteNotice
from beetlesgallery.beetles_app.test_pages import PageTestCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon

BASE = settings.BASE_DIR


class HeavyQueueTests(SimpleTestCase):
    def test_uploads_updates_and_downloads_go_to_the_heavy_queue(self):
        from beetlesgallery.celery import app
        from beetlesgallery.beetles_app import tasks

        for task in (tasks.process_upload_task, tasks.process_update_task, tasks.build_downloads_task):
            self.assertEqual(app.amqp.router.route({}, task.name)["queue"].name, "heavy", task.name)
        self.assertEqual(app.amqp.router.route({}, tasks.recompute_game_players_task.name)["queue"].name, "celery")

    def test_production_runs_a_worker_for_each_queue_and_the_deploy_starts_both(self):
        # (read as text: PyYAML is not one of the site's dependencies)
        prod = (BASE / "docker-compose.prod.yml").read_text()
        worker = prod.split("\n  worker:\n", 1)[1].split("\n  worker-heavy:\n", 1)[0]
        heavy = prod.split("\n  worker-heavy:\n", 1)[1]
        self.assertIn("-Q celery", worker)
        self.assertIn("command: nice -n 10 pixi run celery", heavy)
        self.assertIn("--concurrency=1", heavy)
        self.assertIn("-Q heavy", heavy)
        self.assertIn("/opt/barkandambrosia_data/media:/app/media", heavy)
        deploy = (BASE / ".github" / "workflows" / "deploy.yml").read_text()
        self.assertIn("build web worker worker-heavy", deploy)
        self.assertIn("up -d --no-deps web worker worker-heavy", deploy)

    def test_the_development_worker_takes_both_queues(self):
        dev = (BASE / "docker-compose.yml").read_text().split("\n  worker:\n", 1)[1]
        self.assertIn("-Q celery,heavy", dev)

    def test_the_locustfile_is_valid_python(self):
        compile((BASE / "loadtest" / "locustfile.py").read_text(), "locustfile.py", "exec")


class SiteNoticeTests(PageTestCase):
    def setUp(self):
        super().setUp()
        cache.delete(site_notice.CACHE_KEY)
        self.addCleanup(cache.delete, site_notice.CACHE_KEY)

    def test_only_superusers_can_edit_it(self):
        self.client.force_login(self.staff)
        self.assertNotEqual(self.client.get(reverse("site_notice")).status_code, 200)
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("site_notice")), "stress-testing the site")

    def test_switched_on_it_shows_on_every_page_and_off_it_goes(self):
        self.client.force_login(self.superuser)
        self.client.post(reverse("site_notice"), {"text": "Stress test tonight", "active": "on"})
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_home"))
        self.assertContains(page, 'data-testid="site-notice"')
        self.assertContains(page, "Stress test tonight")
        # not on the game screen itself, which is one fixed screen
        self.assertNotContains(self.client.get(reverse("game_play", args=["mixed"])), 'data-testid="site-notice"')
        self.client.force_login(self.superuser)
        self.client.post(reverse("site_notice"), {"text": "Stress test tonight"})
        self.assertNotContains(self.client.get(reverse("game_home")), 'data-testid="site-notice"')
        self.assertFalse(SiteNotice.objects.get().active)

    def test_an_empty_message_cannot_be_switched_on(self):
        self.client.force_login(self.superuser)
        self.client.post(reverse("site_notice"), {"text": "  ", "active": "on"})
        self.assertFalse(SiteNotice.objects.get().active)

    def test_my_account_links_to_it_for_superusers(self):
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("my_account")), 'data-testid="site-notice-link"')


class LoadTestPlayerTests(PageTestCase):
    def run_cmd(self, *args, password="a-long-enough-password"):
        with mock.patch.dict(os.environ, {"LOADTEST_PASSWORD": password}):
            call_command("loadtest_players", *args, stdout=io.StringIO(), stderr=io.StringIO())

    def test_create_needs_a_long_password(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--create", "3", password="short")

    def test_create_makes_ordinary_players_who_can_sign_in(self):
        self.run_cmd("--create", "3")
        users = get_user_model().objects.filter(username__startswith="loadtest-").order_by("username")
        self.assertEqual([u.username for u in users], ["loadtest-01", "loadtest-02", "loadtest-03"])
        self.assertTrue(all(u.is_active and not u.is_staff and not u.is_superuser for u in users))
        self.assertTrue(self.client.login(username="loadtest-02", password="a-long-enough-password"))

    def test_delete_removes_them_and_their_answers_and_rescores_the_rest(self):
        self.run_cmd("--create", "2")
        player = get_user_model().objects.get(username="loadtest-01")
        roi = make_beetle(image=make_image(), taxon=make_taxon(subfamily="Scolytinae"), bbox="unvalidated")
        rnd = GameRound.objects.create(player=player, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=roi, subfamily="Scolytinae")
        real = GameAnswer.objects.create(round=GameRound.objects.create(player=self.user, mode="classify", items=[]),
                                         player=self.user, mode="classify", index=0, roi=roi, subfamily="Scolytinae")
        with mock.patch("beetlesgallery.beetles_app.management.commands.loadtest_players.call_command") as rescore:
            self.run_cmd("--delete")
        rescore.assert_called_once()
        self.assertEqual(rescore.call_args.args[0], "recompute_game_scores")
        self.assertFalse(get_user_model().objects.filter(username__startswith="loadtest-").exists())
        self.assertEqual(list(GameAnswer.objects.all()), [real])
