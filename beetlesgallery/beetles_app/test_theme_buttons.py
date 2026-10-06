"""
#499: the three button looks are written into the theme (static/css/input.css) and the pages use them:
.btn-main is light grey like the old Search, .btn-primary dark grey like the annotation page's Save,
.btn-secondary white with a grey outline. And the Tailwind config adds its own animations to Tailwind's
instead of replacing them, so animate-spin (the loading spinners) is built.
"""
import re

from django.conf import settings
from django.test import SimpleTestCase

ROOT = settings.BASE_DIR
TEMPLATES = ROOT / "beetlesgallery" / "templates"

# The agreed look of each class: its colours, and the text weight that goes with them.
LOOKS = {
    "btn-main": {"bg-gray-200", "border-gray-300", "text-gray-800", "font-bold", "tracking-wide", "shadow-sm",
                 "hover:bg-gray-300", "hover:text-black"},
    "btn-primary": {"bg-gray-500", "border-gray-500", "text-white", "font-semibold", "hover:bg-gray-600",
                    "disabled:bg-gray-300"},
    "btn-secondary": {"bg-white", "border-gray-300", "text-gray-700", "hover:bg-gray-50", "hover:text-black"},
}

# Buttons and links (also those written inside scripts), their class, and a solid dark background of their own
# (a see-through tint such as bg-black/5 or bg-gray-800/80 over the camera picture is not a button colour).
TAG = re.compile(r"""<(?:button|a|input)\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
CLASS = re.compile(r"""\bclass=(?:"([^"]*)"|'([^']*)')""")
DARK = re.compile(r"(?<![\w:/-])bg-(?:gray-[4-9]00|black)(?![\w/-])")


def template(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def opening_tag(html, marker):
    """The opening tag that holds ``marker``: one of its attributes, or the text right after it (">Search<")."""
    start = html.rfind("<", 0, html.index(marker))
    return html[start:html.index(">", start) + 1]


def object_paths(source):
    """Every key of a JavaScript object written as plain data (tailwind.config.js), as a path such as
    ("theme", "extend", "animation"). Quoted text and comments are read as one token, so their braces don't count."""
    tokens = re.findall(r"""//[^\n]*|/\*.*?\*/|"[^"]*"|'[^']*'|[{}\[\]:,]|[^\s{}\[\]:,'"]+""", source, re.S)
    paths, stack, previous, key = set(), [], None, None
    for token in tokens:
        if token.startswith(("//", "/*")):
            continue
        if token == ":":
            key = previous.strip("'\"")
            paths.add(tuple(k for k in stack if k) + (key,))
        elif token in ("{", "["):
            stack.append(key)
            key = None
        elif token in ("}", "]"):
            stack.pop()
        elif token == ",":
            key = None
        previous = token
    return paths


class ThemeButtonTests(SimpleTestCase):
    def test_the_three_looks_are_in_the_theme(self):
        css = (ROOT / "beetlesgallery" / "static" / "css" / "input.css").read_text(encoding="utf-8")
        for name, look in LOOKS.items():
            with self.subTest(name=name):
                rule = re.search(r"\." + name + r"\s*\{\s*@apply\s+([^;]+);", css)
                self.assertIsNotNone(rule, f".{name} is not in input.css")
                applied = set(rule.group(1).split())
                self.assertLessEqual(look, applied)
                # one background shade each, so the palette stays small
                self.assertEqual({u for u in applied if u.startswith("bg-")}, {u for u in look if u.startswith("bg-")})

    def test_the_main_buttons_have_the_light_look(self):
        for name, marker in (("beetles/image_browser.html", ">Search</button>"),
                             ("beetles/tool_classify.html", 'data-testid="classify-button"'),
                             ("beetles/game_home.html", 'data-testid="play"'),
                             ("beetles/bulk_validate.html", 'data-testid="validate-selected"'),
                             # "Keep playing" looks the same after a session and after a round's review
                             ("beetles/game_play.html", 'id="recap-more"'),
                             ("beetles/game_round_review.html", ">Keep playing</a>")):
            with self.subTest(name=name, marker=marker):
                self.assertIn("btn-main", opening_tag(template(name), marker))

    def test_the_dark_buttons_look_like_the_annotation_save(self):
        for name, marker in (("beetles/tool_annotate.html", 'id="btn-save-image"'),
                             ("beetles/tool_annotate.html", 'id="btn-save-roi-'),
                             ("beetles/game_play.html", 'id="previous-done"'),   # Current beetle
                             ("beetles/game_play.html", 'id="focus-save"'),
                             ("beetles/includes/game_tour.html", 'id="tour-next"'),
                             ("beetles/game_staff_unlocks.html", ">Save</button>"),
                             ("beetles/interaction_review.html", 'value="accept"')):
            with self.subTest(name=name, marker=marker):
                tag = opening_tag(template(name), marker)
                self.assertIn("btn-primary", tag)
                self.assertNotIn("text-white", tag)   # the class brings it

    def test_no_button_has_a_dark_grey_of_its_own(self):
        found = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            name = path.relative_to(TEMPLATES).as_posix()
            for tag in TAG.findall(path.read_text(encoding="utf-8")):
                match = CLASS.search(tag)
                classes = (match.group(1) or match.group(2) or "") if match else ""
                # a selected tab or period switches its classes with {% if %}: a state, not an action
                if "{%" in classes:
                    continue
                if DARK.search(classes):
                    found.append(f"{name}: {' '.join(tag.split())[:120]}")
        self.assertEqual(found, [], "use .btn-primary (or .btn-main / .btn-secondary) instead")

    def test_tailwind_keeps_its_own_animations(self):
        paths = object_paths((ROOT / "tailwind.config.js").read_text(encoding="utf-8"))
        for key in ("animation", "keyframes"):
            with self.subTest(key=key):
                self.assertNotIn(("theme", key), paths)   # would replace animate-spin, animate-pulse ...
                self.assertIn(("theme", "extend", key), paths)
        for name in ("blob", "tilt", "linspin", "easespin", "left-spin", "right-spin", "ping-once", "rotating",
                     "topbottom", "bottomtop", "spin-1.5", "spin-2", "spin-3"):
            self.assertIn(("theme", "extend", "animation", name), paths)
