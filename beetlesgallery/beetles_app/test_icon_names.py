"""Icon names that do not exist in the site's icon font (uicons-regular-rounded 2.2.0) draw nothing: the IBBI-AI Crop
pill showed an empty icon (owner). These were used and are gone; their real names are used instead."""
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
STATIC_JS = Path(__file__).resolve().parent.parent / "static" / "js"
NOT_IN_THE_FONT = ("fi-rr-crop-alt", "fi-rr-exclamation-triangle", "fi-rr-file-alt")


class IconNameTests(SimpleTestCase):
    def test_no_page_uses_an_icon_the_font_does_not_have(self):
        for path in list(TEMPLATES.rglob("*.html")) + list(STATIC_JS.glob("*.js")):
            text = path.read_text(encoding="utf-8")
            for name in NOT_IN_THE_FONT:
                with self.subTest(file=path.name, icon=name):
                    self.assertNotIn(name, text)

    def test_the_crop_pill_has_the_fonts_crop_icon(self):
        page = (TEMPLATES / "beetles" / "tool_classify.html").read_text(encoding="utf-8")
        self.assertIn('<i id="cropIcon" class="fi fi-rr-tool-crop cls-icon"></i>', page)
        self.assertIn("'fi-rr-expand' : 'fi-rr-tool-crop'", page)
