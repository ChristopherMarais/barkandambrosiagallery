"""Thumbnails: the width/height attributes that stop layout shift, and the size the pipeline records for a thumbnail."""
import io
import tempfile
from types import SimpleNamespace

from django.template import Context, Template
from django.test import TestCase, override_settings
from PIL import Image

from beetlesgallery.beetles_app.image_pipeline import write_original_and_thumb96


def render_size(asset):
    return Template("{% load beetle_tags %}{% thumb_size asset %}").render(Context({"asset": asset}))


def asset(w, h):
    return SimpleNamespace(image_width=w, image_height=h)


class ThumbSizeTagTests(TestCase):
    def test_landscape_photo_keeps_its_shape_in_a_96_pixel_box(self):
        self.assertEqual(render_size(asset(4000, 3000)), 'width="96" height="72"')

    def test_portrait_photo_is_taller_than_wide(self):
        self.assertEqual(render_size(asset(3000, 4000)), 'width="72" height="96"')

    def test_small_photo_is_not_enlarged(self):
        self.assertEqual(render_size(asset(50, 40)), 'width="50" height="40"')

    def test_extreme_shape_never_rounds_to_zero(self):
        out = render_size(asset(10000, 3))
        self.assertEqual(out, 'width="96" height="1"')

    def test_no_attributes_without_stored_dimensions(self):
        self.assertEqual(render_size(None), "")
        self.assertEqual(render_size(asset(None, 300)), "")
        self.assertEqual(render_size(asset(300, None)), "")


class ThumbnailRecordedSizeTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()

    def tearDown(self):
        self.override.disable()

    def test_pipeline_records_the_real_thumbnail_shape_and_writes_webp(self):
        buf = io.BytesIO()
        Image.new("RGB", (200, 100), (120, 90, 60)).save(buf, format="PNG")
        buf.seek(0)
        sha = "ab" * 32
        result = write_original_and_thumb96(sha, buf)
        self.assertEqual(result["image_size"], (200, 100))
        self.assertEqual(result["thumb_size"], (96, 48))
        self.assertTrue(result["thumb_path"].endswith(".webp"), result["thumb_path"])
