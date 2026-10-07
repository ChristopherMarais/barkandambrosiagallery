"""Guards for the phone layouts (the real check is a browser at 375px wide; these catch the code being undone)."""
from django.conf import settings
from django.test import SimpleTestCase

TEMPLATES = settings.BASE_DIR / "beetlesgallery" / "templates"


def read(*parts):
    return (TEMPLATES.joinpath(*parts)).read_text(encoding="utf-8")


class PhoneLayoutTests(SimpleTestCase):
    def test_every_page_scales_to_the_device(self):
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1.0"', read("base.html"))

    def test_touch_screens_get_finger_sized_controls_and_no_iphone_zoom(self):
        base = read("base.html")
        self.assertIn("@media (pointer: coarse)", base)
        self.assertIn("font-size: 16px", base)          # under 16px, iPhones zoom in when a field is tapped
        self.assertIn("min-height: 2.5rem", base)

    def test_the_annotation_tool_is_three_tabs_on_a_phone_and_takes_a_finger(self):
        page = read("beetles", "tool_annotate.html")
        self.assertIn('id="annot-tabs"', page)
        for tab in ("left", "center", "right"):
            self.assertIn(f"mobileTab('{tab}')", page)
        self.assertIn("addEventListener('pointerdown', onMouseDown)", page)   # mouse, pen and finger
        self.assertNotIn("addEventListener('mousedown', onMouseDown)", page)
        self.assertIn("touch-action: none", page)

    def test_the_taxonomy_browser_stacks_on_a_phone(self):
        page = read("beetles", "taxonomy_browser.html")
        self.assertIn("@media (max-width: 899px)", page)
        self.assertIn("flex-direction: column", page)
        self.assertIn("width: 100%;           /* it sits in a plain wrapper", page)


class ThemeTests(SimpleTestCase):
    """One look across the site: white and grey, colour only where it means something (see input.css)."""

    NOT_A_MEANING = ("blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose", "cyan", "teal", "sky", "lime")
    # The annotation page and the interaction charts use colour as data (status, pathogen group) and are left as they are.
    DATA_COLOUR_PAGES = {"tool_annotate.html", "interactions_preview.html"}

    def pages(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            if "admin" in path.parts or path.name in self.DATA_COLOUR_PAGES or path.name == "landing.html":
                continue
            if path.name.startswith("game_"):
                continue   # the game may be more colourful than the rest of the site
            yield path

    def test_no_decorative_colour_on_the_pages(self):
        import re
        pattern = re.compile(r"\b(?:bg|text|border|ring|from|to|via|fill|stroke|accent)-(%s)-\d+" % "|".join(self.NOT_A_MEANING))
        offenders = {p.name: sorted(set(pattern.findall(p.read_text(encoding="utf-8")))) for p in self.pages()}
        self.assertEqual({k: v for k, v in offenders.items() if v}, {})

    def test_every_page_title_is_the_same_style(self):
        import re
        for path in self.pages():
            if path.name in ("game_play.html", "base.html"):
                continue
            for tag in re.findall(r"<h1[^>]*>", path.read_text(encoding="utf-8")):
                self.assertIn('class="page-title"', tag, f"{path.name}: {tag}")

    def test_the_annotation_page_is_called_image_annotation(self):
        # The page's own <h1> is sentence case (site-casing, #618); the nav item that links to it keeps its own
        # case in base.html, checked separately below.
        self.assertIn("Image annotation", read("beetles", "tool_annotate.html"))
        self.assertNotIn("Data Curation", read("beetles", "tool_annotate.html"))
        self.assertNotIn("Data Annotation", read("base.html"))
        self.assertIn("Image Annotation", read("base.html"))

    def test_solid_buttons_use_the_house_grey(self):
        for name in ("game_home.html", "game_play.html", "access_requests.html", "interaction_review.html"):
            page = read("beetles", name)
            self.assertNotIn("bg-gray-900 rounded", page, name)
            self.assertNotIn("text-white bg-gray-900", page, name)
