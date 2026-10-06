"""The small-image-first script only ever loads http(s) addresses (CodeQL js/xss-through-dom, #607)."""
from pathlib import Path

from django.test import SimpleTestCase

SCRIPT = (Path(__file__).resolve().parent.parent / "static" / "js" / "progressive_image.js").read_text(encoding="utf-8")


class SafeUrlTests(SimpleTestCase):
    def test_the_full_photo_address_is_checked_before_it_is_loaded(self):
        self.assertIn("function safeUrl(value)", SCRIPT)
        self.assertIn('u.protocol === "https:" || u.protocol === "http:"', SCRIPT)
        self.assertIn('const url = safeUrl(img.getAttribute("data-full-src") || "")', SCRIPT)

    def test_nothing_else_is_assigned_to_the_image(self):
        self.assertEqual(SCRIPT.count(".src = "), 2)   # the hidden loader and the swap, both with the checked url
        self.assertIn("full.src = url;", SCRIPT)
        self.assertIn("img.src = url;", SCRIPT)
