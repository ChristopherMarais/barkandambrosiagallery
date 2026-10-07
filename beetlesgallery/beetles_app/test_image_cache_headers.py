"""Cache-Control for static files (WhiteNoise) and uploaded media: long for content-addressed files, short for the rest."""
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase
from whitenoise import WhiteNoise

from beetlesgallery import urls
from beetlesgallery.cache_policy import ONE_YEAR, media_cache_control, whitenoise_add_headers

YEAR = f"public, max-age={ONE_YEAR}, immutable"


class MediaCachePolicyTests(SimpleTestCase):
    def test_content_addressed_images_cache_for_a_year(self):
        for path in (
            "originals/ab/cd/" + "e" * 64 + ".jpg",
            "thumbnails/ab/cd/" + "e" * 64 + "_96.webp",
            "display/ab/cd/" + "e" * 64 + ".jpg",
        ):
            self.assertEqual(media_cache_control(path), YEAR, path)
            self.assertEqual(media_cache_control("/" + path), YEAR, path)

    def test_other_media_keeps_the_thirty_day_cache(self):
        for path in ("crops/12_34.png", "downloads/export.zip", "uploads/photo.jpg", "reference/valid_species.csv"):
            self.assertEqual(media_cache_control(path), "public, max-age=2592000, immutable", path)

    def test_hashed_static_files_cache_for_a_year_and_others_for_five_minutes(self):
        headers = {}
        whitenoise_add_headers(headers, "/x", "/static/css/style.3f2a9c1b8d7e.css")
        self.assertEqual(headers["Cache-Control"], YEAR)
        headers = {}
        whitenoise_add_headers(headers, "/x", "/static/js/digit_groups.js")
        self.assertEqual(headers["Cache-Control"], "public, max-age=300")

    def test_settings_wire_the_policy_in(self):
        self.assertIs(settings.WHITENOISE_ADD_HEADERS_FUNCTION, whitenoise_add_headers)
        self.assertEqual(settings.WHITENOISE_MAX_AGE, 300)


class MediaResponseHeaderTests(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "thumbnails" / "ab" / "cd").mkdir(parents=True)
        (self.root / "thumbnails" / "ab" / "cd" / "x_96.webp").write_bytes(b"RIFF")
        (self.root / "crops").mkdir()
        (self.root / "crops" / "1_2.png").write_bytes(b"png")

    def get(self, path):
        return urls.media_serve_with_cache(RequestFactory().get(f"/media/{path}"), path, document_root=self.root)

    def test_thumbnail_is_served_with_a_year_cache(self):
        res = self.get("thumbnails/ab/cd/x_96.webp")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Cache-Control"], YEAR)

    def test_crop_is_served_with_the_thirty_day_cache(self):
        res = self.get("crops/1_2.png")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Cache-Control"], "public, max-age=2592000, immutable")


class WhiteNoiseHeaderTests(SimpleTestCase):
    """Runs the real WhiteNoise with the project's header function over a small static folder."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "css").mkdir()
        (self.root / "css" / "style.3f2a9c1b8d7e.css").write_text("a{}")
        (self.root / "js").mkdir()
        (self.root / "js" / "digit_groups.js").write_text("//")
        self.app = WhiteNoise(
            lambda environ, start_response: start_response("404 Not Found", []) or [b""],
            root=str(self.root),
            prefix="static/",
            max_age=settings.WHITENOISE_MAX_AGE,
            add_headers_function=settings.WHITENOISE_ADD_HEADERS_FUNCTION,
        )

    def cache_control(self, path):
        environ = RequestFactory().get(f"/static/{path}").environ
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        self.app(environ, start_response)
        self.assertTrue(captured["status"].startswith("200"), captured["status"])
        return captured["headers"]["Cache-Control"]

    def test_hashed_file_is_immutable_for_a_year(self):
        self.assertEqual(self.cache_control("css/style.3f2a9c1b8d7e.css"), YEAR)

    def test_plain_file_has_the_short_cache(self):
        self.assertEqual(self.cache_control("js/digit_groups.js"), "public, max-age=300")
