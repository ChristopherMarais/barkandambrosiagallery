"""The media folder is served to anyone, so files that must stay private are refused even if they end up in it."""
import tempfile
from pathlib import Path

from django.http import Http404
from django.test import RequestFactory, TestCase

from beetlesgallery import urls


class PrivateMediaTests(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "db_full_backup.sql").write_text("-- dump")
        (self.root / "thumbs").mkdir()
        (self.root / "thumbs" / "beetle.jpg").write_bytes(b"jpg")
        (self.root / ".env").write_text("SECRET=1")

    def get(self, path):
        return urls.media_serve_with_cache(RequestFactory().get(f"/media/{path}"), path, document_root=self.root)

    def test_a_database_dump_is_never_served(self):
        for path in ("db_full_backup.sql", "DB.SQL", "a/b/dump.sql.gz", "x.dump", "worker.log", ".env", "a/.git/config"):
            res = self.client.get(f"/media/{path}")
            self.assertEqual(res.status_code, 404, path)
        for path in ("db_full_backup.sql", ".env"):   # files that are there
            with self.assertRaises(Http404):
                self.get(path)

    def test_images_are_still_served(self):
        res = self.get("thumbs/beetle.jpg")
        self.assertEqual(res.status_code, 200)
        self.assertIn("max-age", res["Cache-Control"])
        self.assertIsNone(urls.PRIVATE_MEDIA.search("display/2026/10/beetle_1.sql_notes.jpg"))
