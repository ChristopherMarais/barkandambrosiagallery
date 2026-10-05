"""Backups put a notice at the top of every page while they run, without touching a notice a person put up."""
import io
from pathlib import Path

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from beetlesgallery.beetles_app import site_notice
from beetlesgallery.beetles_app.management.commands.backup_notice import BACKUP_TEXT
from beetlesgallery.beetles_app.models import SiteNotice

ROOT = Path(__file__).resolve().parents[2]


def run(state):
    call_command("backup_notice", state, stdout=io.StringIO())


class BackupNoticeTests(TestCase):
    def setUp(self):
        cache.delete(site_notice.CACHE_KEY)
        self.addCleanup(cache.delete, site_notice.CACHE_KEY)

    def test_on_shows_it_and_off_removes_it(self):
        run("on")
        self.assertEqual(site_notice.current(), BACKUP_TEXT)
        run("off")
        self.assertEqual(site_notice.current(), "")

    def test_a_notice_someone_put_up_is_left_alone(self):
        SiteNotice.objects.create(pk=1, text="Stress test tonight", active=True)
        run("on")
        self.assertEqual(site_notice.current(), "Stress test tonight")
        run("off")
        self.assertEqual(site_notice.current(), "Stress test tonight")

    def test_the_backup_workflow_switches_it_on_and_off(self):
        workflow = (ROOT / ".github" / "workflows" / "backup.yaml").read_text()
        self.assertIn("pixi run backup-notice on", workflow)
        self.assertEqual(workflow.count("pixi run backup-notice off"), 2)   # fast (trap) and full (after rclone)
        self.assertIn('backup-notice = "python manage.py backup_notice"', (ROOT / "pixi.toml").read_text())
