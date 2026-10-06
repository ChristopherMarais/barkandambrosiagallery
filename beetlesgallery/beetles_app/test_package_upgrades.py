"""#570: Django 5.2, Pillow 12 and the rest of the security updates; what had to change with them keeps working."""
import io
import os
import tempfile
import warnings

import django
import PIL
from django.conf import settings
from django.test import SimpleTestCase
from PIL import Image

from beetlesgallery.beetles_app.image_pipeline import _guess_ext_from_path_or_hdr
from beetlesgallery.beetles_app.templatetags.ibbi_tags import ibbi_model_options
from beetlesgallery.tools import ibbi_models


class VersionTests(SimpleTestCase):
    def test_supported_versions(self):
        self.assertGreaterEqual(django.VERSION[:2], (5, 2))
        self.assertGreaterEqual(tuple(int(p) for p in PIL.__version__.split(".")[:2]), (12, 3))


class ImageFormatTests(SimpleTestCase):
    """The upload pipeline names the stored original by its real format (Pillow, now that imghdr is going)."""

    def guess(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(data)
        try:
            return _guess_ext_from_path_or_hdr(handle.name)
        finally:
            os.unlink(handle.name)

    def encoded(self, fmt):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, fmt)
        return buffer.getvalue()

    def test_formats(self):
        for fmt, ext in (("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp"), ("TIFF", "tiff"), ("BMP", "bmp"),
                         ("GIF", "gif")):
            with self.subTest(fmt=fmt):
                self.assertEqual(self.guess(self.encoded(fmt)), ext)

    def test_unreadable_file_is_taken_as_jpeg(self):
        self.assertEqual(self.guess(b"not an image"), "jpg")


class Django6ReadyTests(SimpleTestCase):
    def test_model_options_mark_the_default_without_deprecated_calls(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            html = str(ibbi_model_options())
        self.assertIn(f'<option value="{ibbi_models.DEFAULT}" selected>', html)

    def test_no_imghdr(self):
        # imghdr leaves the standard library in Python 3.13
        source = (settings.BASE_DIR / "beetlesgallery" / "beetles_app" / "image_pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn("import imghdr", source)


class SecureCookieTests(SimpleTestCase):
    """The live site's sign-in and CSRF cookies only travel over HTTPS (manage.py check --deploy)."""

    def test_cookies_follow_the_secure_setting(self):
        self.assertEqual(settings.SESSION_COOKIE_SECURE, settings.SECURE_COOKIES)
        self.assertEqual(settings.CSRF_COOKIE_SECURE, settings.SECURE_COOKIES)

    def test_secure_by_default_when_not_debugging(self):
        source = (settings.BASE_DIR / "beetlesgallery" / "settings.py").read_text(encoding="utf-8")
        self.assertIn('SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "1" if IS_PRODUCTION else "0") == "1"',
                      source)
