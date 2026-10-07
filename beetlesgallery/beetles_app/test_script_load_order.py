"""
Scripts that pages' inline code calls while the page is still parsing must not be deferred. Deferring
digit_groups.js (#614) made the image browser's inline script throw "digitGroupsText is not defined", so its
"Loading image browser..." overlay never went away.
"""
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


class ScriptLoadOrderTests(SimpleTestCase):
    def test_digit_groups_is_not_deferred(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        tag = next(line for line in base.splitlines() if "js/digit_groups.js" in line and "<script" in line)
        self.assertNotIn("defer", tag)
        self.assertNotIn("async", tag)

    def test_the_image_browser_overlay_hides_independently_of_the_main_script(self):
        html = (TEMPLATES / "beetles" / "image_browser.html").read_text(encoding="utf-8")
        overlay = html.index('id="page-loading-overlay"')
        guard = html.index("can never leave the overlay up")
        main = html.index("function hideLoadingOverlay")
        self.assertLess(overlay, guard)
        self.assertLess(guard, main)
