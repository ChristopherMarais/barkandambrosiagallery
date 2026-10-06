"""
#570: the stylesheet is built with Tailwind 4, whose build tools no longer pull in braces or postcss-selector-parser
(Dependabot alerts 18 and 19), and the pages look as they did with Tailwind 3. These checks keep the vulnerable
packages out and the Tailwind 3 look in.
"""
import json
import re

from django.conf import settings
from django.test import SimpleTestCase

ROOT = settings.BASE_DIR
CSS = ROOT / "beetlesgallery" / "static" / "css"
APP = ROOT / "beetlesgallery"


def read(path):
    return path.read_text(encoding="utf-8")


class BuildToolsTests(SimpleTestCase):
    def test_no_vulnerable_build_packages_are_locked(self):
        packages = json.loads(read(ROOT / "package-lock.json"))["packages"]
        names = {key.rsplit("node_modules/", 1)[-1] for key in packages if key}
        for name in ("braces", "micromatch", "postcss-selector-parser", "chokidar"):
            with self.subTest(package=name):
                self.assertNotIn(name, names)

    def test_the_watcher_without_micromatch_is_forced(self):
        # @tailwindcss/cli pins @parcel/watcher 2.5.1, which needs micromatch and so braces; 2.6 doesn't
        package = json.loads(read(ROOT / "package.json"))
        self.assertRegex(package["overrides"]["@parcel/watcher"], r"^\^?2\.([6-9]|\d\d)")

    def test_the_theme_lives_in_input_css(self):
        self.assertFalse((ROOT / "tailwind.config.js").exists())
        self.assertFalse((ROOT / "postcss.config.js").exists())
        source = read(CSS / "input.css")
        self.assertIn('@import "tailwindcss"', source)
        self.assertIn("@theme {", source)
        self.assertIn('@source "../../templates";', source)

    def test_the_built_stylesheet_is_tailwind_4(self):
        self.assertRegex(read(CSS / "style.css").splitlines()[0], r"tailwindcss v4\.")


class Tailwind3LookTests(SimpleTestCase):
    """What input.css does so that Tailwind 4 draws the pages as Tailwind 3 did."""

    def setUp(self):
        self.source = read(CSS / "input.css")
        self.built = read(CSS / "style.css")

    def test_tailwind_3_colours(self):
        for name, value in (("gray-200", "#e5e7eb"), ("gray-600", "#4b5563"), ("green-600", "#16a34a"),
                            ("red-600", "#dc2626"), ("amber-500", "#f59e0b")):
            with self.subTest(colour=name):
                self.assertIn(f"--color-{name}: {value};", self.source)

    def test_no_dark_mode_and_hover_on_every_device(self):
        self.assertIn("@custom-variant dark (&:not(*));", self.source)
        self.assertIn("@custom-variant hover (&:hover);", self.source)
        self.assertNotIn("(hover: hover)", self.built)

    def test_line_heights_are_lengths(self):
        self.assertIn("--text-sm--line-height: 1.25rem;", self.source)

    def test_space_and_divide_go_above_every_child_but_the_first(self):
        self.assertIn(".space-y-4 > :not([hidden]) ~ :not([hidden])", self.built)
        self.assertIn(".divide-y > :not([hidden]) ~ :not([hidden])", self.built)

    def test_hidden_wins_over_inline_display_classes(self):
        self.assertIn(".hidden:is(.inline, .inline-block, .inline-flex", self.built)

    def test_base_styles_as_before(self):
        for rule in ("input::placeholder, textarea::placeholder", 'button:not(:disabled), [role="button"]:not(:disabled)',
                     "td, th {", '[type="search"]'):
            with self.subTest(rule=rule):
                self.assertIn(rule, self.built)

    def test_the_buttons_stay_below_the_utilities(self):
        # in the components layer, so "btn-main w-full py-6" still takes w-full and py-6
        components = self.source[self.source.index("@layer components {"):]
        self.assertLess(components.index(".btn-main {"), components.index("\n}\n"))


class TemplatesUseTailwind4NamesTests(SimpleTestCase):
    """Class names Tailwind 4 dropped or gave another size; the upgrade renamed them, new code shouldn't bring them back."""

    GONE = re.compile(r"(?<![\w:/\[-])(?:[a-z0-9]+:)*(?:bg|text|border|ring|divide|placeholder)-opacity-\d+"
                      r"|(?<![\w:/\[-])(?:[a-z0-9]+:)*(?:flex-shrink|flex-grow)(?:-0)?(?![\w-])"
                      r"|(?<![\w:/\[-])(?:[a-z0-9]+:)*(?:outline-none|overflow-ellipsis|decoration-slice|decoration-clone)(?![\w-])")
    CLASS = re.compile(r"""class(?:Name)?\s*=\s*["']([^"']*)["']""")

    def test_no_tailwind_3_only_classes(self):
        found = []
        for path in sorted(APP.joinpath("templates").rglob("*.html")):
            for classes in self.CLASS.findall(read(path)):
                found += [f"{path.name}: {m.group(0)}" for m in self.GONE.finditer(classes)]
        self.assertEqual(found, [])
