"""
Nightly storage cleanup (beetles_app/storage_cleanup.py): expired downloads, temp leftovers and upload ZIPs go;
the gallery's images, species tables and upload records stay, and a ZIP stays while any of its images is not in
the gallery.
"""
import hashlib
import os
import tempfile
import time
import zipfile
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from beetlesgallery.beetles_app import storage_cleanup
from beetlesgallery.beetles_app.models import DownloadJob, UploadBatch
from beetlesgallery.beetles_app.testing import make_image

OLD = time.time() - 3 * 86400


class CleanupCase(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.enterContext(override_settings(MEDIA_ROOT=self.root))
        self.now = timezone.now()

    def write(self, rel, data=b"x" * 100, old=True):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if old:
            os.utime(path, (OLD, OLD))
        return path

    def run_cleanup(self, *args):
        out = StringIO()
        call_command("cleanup_storage", *args, stdout=out)
        return out.getvalue()


class DownloadTests(CleanupCase):
    def test_an_expired_download_loses_its_files_and_says_so(self):
        self.write("downloads/results/2026/09/a.csv")
        self.write("downloads/results/2026/09/a.zip")
        job = DownloadJob.objects.create(selection_mode="ids", status=DownloadJob.Status.READY,
                                         csv_file="downloads/results/2026/09/a.csv", zip_file="downloads/results/2026/09/a.zip",
                                         finished_at=self.now - timedelta(days=15), expires_at=self.now - timedelta(days=1))
        fresh = DownloadJob.objects.create(selection_mode="ids", status=DownloadJob.Status.READY,
                                           csv_file="downloads/results/2026/09/b.csv", expires_at=self.now + timedelta(days=3))
        self.write("downloads/results/2026/09/b.csv")
        self.run_cleanup()
        job.refresh_from_db()
        self.assertEqual(job.status, DownloadJob.Status.EXPIRED)
        self.assertFalse(job.csv_file or job.zip_file)
        self.assertFalse((self.root / "downloads/results/2026/09/a.zip").exists())
        self.assertTrue((self.root / "downloads/results/2026/09/b.csv").exists())
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, DownloadJob.Status.READY)

    def test_leftovers_of_crashed_builds_and_unlinked_results_go_but_a_running_build_stays(self):
        running = DownloadJob.objects.create(selection_mode="ids", status=DownloadJob.Status.BUILDING)
        self.write(f"downloads/tmp/{running.id}/part.zip")
        os.utime(self.root / f"downloads/tmp/{running.id}", (OLD, OLD))
        self.write("downloads/tmp/dead-build/part.zip")
        os.utime(self.root / "downloads/tmp/dead-build", (OLD, OLD))
        self.write("downloads/tmp/just-started/part.zip", old=False)
        self.write("downloads/results/2026/01/orphan.zip")
        month_old = time.time() - 30 * 86400
        os.utime(self.root / "downloads/results/2026/01/orphan.zip", (month_old, month_old))
        self.write("downloads/results/2026/09/recent-orphan.zip")   # within the 14 days a download is kept
        self.run_cleanup()
        self.assertTrue((self.root / "downloads/results/2026/09/recent-orphan.zip").exists())
        self.assertTrue((self.root / f"downloads/tmp/{running.id}/part.zip").exists())
        self.assertFalse((self.root / "downloads/tmp/dead-build").exists())
        self.assertTrue((self.root / "downloads/tmp/just-started/part.zip").exists())
        self.assertFalse((self.root / "downloads/results/2026/01/orphan.zip").exists())


class UploadTests(CleanupCase):
    def make_zip(self, rel, images):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in images.items():
                zf.writestr(name, data)
            zf.writestr("__MACOSX/._a.jpg", b"cruft")
        return path

    def in_gallery(self, data, on_disk=True):
        sha = hashlib.sha256(data).hexdigest()
        rel = f"originals/{sha[:2]}/{sha[2:4]}/{sha}.jpg"
        if on_disk:
            self.write(rel, data)
        make_image(image_sha256=sha, image_file=rel)
        return rel

    def batch(self, status, zip_rel, days_ago=10):
        b = UploadBatch.objects.create(file=zip_rel.replace(".zip", ".csv"), zip_file=zip_rel, original_filename="u.csv",
                                       status=status, imported_at=self.now - timedelta(days=days_ago))
        UploadBatch.objects.filter(pk=b.pk).update(updated_at=self.now - timedelta(days=days_ago))
        self.write(zip_rel.replace(".zip", ".csv"))
        return b

    def test_an_imported_zip_goes_once_every_image_is_in_the_gallery(self):
        kept = self.in_gallery(b"beetle-one")
        self.in_gallery(b"beetle-two")
        self.make_zip("uploads/archived/2026/09/a.zip", {"x/one.jpg": b"beetle-one", "two.JPG": b"beetle-two"})
        b = self.batch(UploadBatch.Status.IMPORTED, "uploads/archived/2026/09/a.zip")
        out = self.run_cleanup()
        self.assertFalse((self.root / "uploads/archived/2026/09/a.zip").exists())
        self.assertTrue((self.root / "uploads/archived/2026/09/a.csv").exists())   # the record of what came in stays
        self.assertTrue((self.root / kept).exists())                                 # the gallery's copy stays
        b.refresh_from_db()
        self.assertFalse(b.zip_file)
        self.assertEqual(b.status, UploadBatch.Status.IMPORTED)
        self.assertIn("Freed", out)

    def test_a_zip_stays_while_an_image_is_missing_from_the_gallery_or_its_file_is_gone(self):
        self.in_gallery(b"beetle-one")
        self.make_zip("uploads/archived/2026/09/a.zip", {"one.jpg": b"beetle-one", "new.jpg": b"never-imported"})
        self.batch(UploadBatch.Status.IMPORTED, "uploads/archived/2026/09/a.zip")
        self.in_gallery(b"lost-file", on_disk=False)
        self.make_zip("uploads/archived/2026/09/b.zip", {"lost.jpg": b"lost-file"})
        self.batch(UploadBatch.Status.IMPORTED, "uploads/archived/2026/09/b.zip")
        out = self.run_cleanup()
        self.assertTrue((self.root / "uploads/archived/2026/09/a.zip").exists())
        self.assertTrue((self.root / "uploads/archived/2026/09/b.zip").exists())
        self.assertIn("kept uploads/archived/2026/09/a.zip: 1 image(s) not in the gallery, e.g. new.jpg", out)

    def test_a_recent_import_keeps_its_zip_for_a_week(self):
        self.in_gallery(b"beetle-one")
        self.make_zip("uploads/archived/2026/10/a.zip", {"one.jpg": b"beetle-one"})
        self.batch(UploadBatch.Status.IMPORTED, "uploads/archived/2026/10/a.zip", days_ago=2)
        self.run_cleanup()
        self.assertTrue((self.root / "uploads/archived/2026/10/a.zip").exists())

    def test_rejected_and_unfinished_upload_zips_go_after_a_month(self):
        self.make_zip("uploads/rejected/2026/08/old.zip", {"a.jpg": b"a"})
        old = self.batch(UploadBatch.Status.REJECTED, "uploads/rejected/2026/08/old.zip", days_ago=31)
        self.make_zip("uploads/rejected/2026/09/new.zip", {"a.jpg": b"a"})
        self.batch(UploadBatch.Status.REJECTED, "uploads/rejected/2026/09/new.zip", days_ago=5)
        self.make_zip("uploads/staging/2026/08/stuck.zip", {"a.jpg": b"a"})
        self.batch(UploadBatch.Status.STAGING, "uploads/staging/2026/08/stuck.zip", days_ago=40)
        self.run_cleanup()
        self.assertFalse((self.root / "uploads/rejected/2026/08/old.zip").exists())
        self.assertTrue((self.root / "uploads/rejected/2026/08/old.csv").exists())
        self.assertTrue((self.root / "uploads/rejected/2026/09/new.zip").exists())
        self.assertFalse((self.root / "uploads/staging/2026/08/stuck.zip").exists())
        old.refresh_from_db()
        self.assertEqual(old.status, UploadBatch.Status.REJECTED)

    def test_interrupted_upload_temp_files_go_after_a_day(self):
        self.write("tmp_uploads/abc.upload")
        self.write("tmp_uploads/now.upload", old=False)
        self.run_cleanup()
        self.assertFalse((self.root / "tmp_uploads/abc.upload").exists())
        self.assertTrue((self.root / "tmp_uploads/now.upload").exists())

    def test_a_dry_run_reports_and_changes_nothing(self):
        self.in_gallery(b"beetle-one")
        self.make_zip("uploads/archived/2026/09/a.zip", {"one.jpg": b"beetle-one"})
        b = self.batch(UploadBatch.Status.IMPORTED, "uploads/archived/2026/09/a.zip")
        self.write("tmp_uploads/abc.upload")
        out = self.run_cleanup("--dry-run")
        self.assertIn("Would free", out)
        self.assertIn("Imported upload ZIPs (images already in the gallery): 1 file(s)", out)
        self.assertTrue((self.root / "uploads/archived/2026/09/a.zip").exists())
        self.assertTrue((self.root / "tmp_uploads/abc.upload").exists())
        b.refresh_from_db()
        self.assertTrue(b.zip_file)


class OnlyTheseFoldersTests(SimpleTestCase):
    def test_nothing_outside_downloads_uploads_and_tmp_uploads_can_be_deleted(self):
        for rel in ("originals/ab/cd/x.jpg", "display/x.jpg", "thumbnails/x.webp", "reference/valid_species.csv",
                    "uploads/../originals/x.jpg", "../etc/passwd", "db_full_backup.sql"):
            with self.assertRaises(ValueError, msg=rel):
                storage_cleanup.delete_path(rel, storage_cleanup.Freed(), dry_run=True)

    def test_the_nightly_workflow_runs_it_on_the_production_files(self):
        workflow = (settings.BASE_DIR / ".github/workflows/storage-cleanup.yml").read_text()
        self.assertIn("docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T web nice -n 15 pixi run cleanup-storage", workflow)
        self.assertIn("cron:", workflow)
        self.assertIn("default: true", workflow)   # by hand, it only reports unless told otherwise
