"""The house style written down in static/css/input.css: one main button style, dark-grey ticks."""
from django.conf import settings
from django.test import SimpleTestCase

TEMPLATES = settings.BASE_DIR / "beetlesgallery" / "templates"
CSS = settings.BASE_DIR / "beetlesgallery" / "static" / "css"


def read(*parts):
    return TEMPLATES.joinpath(*parts).read_text(encoding="utf-8")


class HouseStyleTests(SimpleTestCase):
    def test_the_rules_are_written_down_and_built(self):
        source = (CSS / "input.css").read_text()
        self.assertIn("Each page has at most one main button", source)
        built = (CSS / "style.css").read_text()
        self.assertIn(".btn-main", built)
        self.assertIn('input[type="checkbox"], input[type="radio"], input[type="range"]', built)
        self.assertIn("accent-color: #4b5563", built)   # gray-600, not the browser's blue

    def test_the_main_buttons_share_one_style_and_none_is_black(self):
        for page, marker in (("image_browser.html", ">Search</button>"), ("tool_classify.html", 'data-testid="classify-button"'),
                             ("game_home.html", 'data-testid="play"')):
            html = read("beetles", page)
            start = html.rfind("<", 0, html.index(marker) + 1) if marker.startswith(">") else html.rfind("<", 0, html.index(marker))
            tag = html[start:html.index(">", start) + 1]
            with self.subTest(page=page):
                self.assertIn("btn-main", tag)
                self.assertNotIn("bg-gray-900", tag)
                self.assertNotIn("bg-black", tag)
