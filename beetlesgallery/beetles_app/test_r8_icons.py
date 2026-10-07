"""Icons sit dead-centre in their pills and round buttons, site-wide (owner): every .fi glyph is its own
line-height-1 flex box, and the shared .rv-pill centres its text."""
from pathlib import Path

from django.test import SimpleTestCase

INPUT_CSS = Path(__file__).resolve().parent.parent / "static" / "css" / "input.css"


class IconCentringTests(SimpleTestCase):
    def setUp(self):
        self.css = INPUT_CSS.read_text(encoding="utf-8")

    def test_every_icon_is_a_centred_line_height_one_box(self):
        rule = self.css[self.css.index("    .fi {"):]
        rule = rule[:rule.index("}")]
        for decl in ("display: inline-flex;", "align-items: center;", "justify-content: center;", "line-height: 1;"):
            self.assertIn(decl, rule)

    def test_the_shared_pill_centres_its_text(self):
        self.assertIn(".rv-pill { display: inline-flex; align-items: center; justify-content: center;", self.css)
