"""
The UI audit's site-wide text items, finished across every template (#618):
  site-contrast  gray-500 is the lightest text colour; gray-400 only on icons, icon-only buttons and switched-off controls
  site-casing    sentence case for headings, labels and buttons (names keep their capitals)
  site-sections  bold .section-heading for page sections, .card-label for small labels inside a card
  site-empty     one empty state (includes/empty_state.html) instead of "None yet." and the like
  site-dash      one "no value" mark, the em dash (en dashes stay for ranges such as 0–1)
  site-mono      no raw database field names in sentences for people
  site-cards     page sections are spacing and a heading, not bordered boxes around more boxes
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "beetlesgallery" / "templates"
INPUT_CSS = REPO / "beetlesgallery" / "static" / "css" / "input.css"


def read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def all_templates():
    return sorted(TEMPLATES.rglob("*.html"))


# gray-400 may stay on these: icon-only buttons (copy, close, clear, the GitHub link), the search field's icon, a
# chevron cell, and the game's switched-off choices (locked modes and ranks)
ICON_OR_DISABLED = re.compile(r"copy-uuid|copy-id|closeModal|clear-search|close-modal-btn|focus-btn|play-opt|"
                              r"r\.unlocked \?|View source on GitHub|board-row-chev|icon\.className|pointer-events-none")


class ContrastTests(SimpleTestCase):
    def test_gray_400_is_only_on_icons_and_switched_off_controls(self):
        for path in all_templates():
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if "text-gray-400" not in line or ICON_OR_DISABLED.search(line):
                    continue
                for match in re.finditer(r"(?<![\w:-])text-gray-400", line):
                    tag = re.match(r"<(\w+)", line[line.rfind("<", 0, match.start()):] or "")
                    with self.subTest(template=path.name, line=number):
                        self.assertIn(tag and tag.group(1), ("i", "svg"), line.strip()[:120])

    def test_placeholders_are_gray_500(self):
        css = INPUT_CSS.read_text(encoding="utf-8")
        rule = css[css.index("input::placeholder"):]
        self.assertIn("var(--color-gray-500)", rule[:rule.index("}")])
        self.assertNotIn("placeholder-gray-400", read("beetles/interactions_preview.html"))

    def test_the_old_eyebrows_and_captions_are_readable(self):
        self.assertIn('<span class="text-xs font-normal text-gray-500"> pts</span>', read("beetles/game_leaderboard.html"))
        self.assertIn('<p class="truncate text-xs text-gray-500">{{ u.email }}</p>', read("accounts/my_account.html"))
        self.assertIn("bg-gray-100 text-gray-500 ring-1", read("beetles/includes/game_level_badge.html"))   # locked level


class CasingTests(SimpleTestCase):
    CHANGED = {
        "accounts/my_account.html": [("User Directory", "User directory"), ("Change Password", "Change password"),
                                     ("Save Changes", "Save changes"), ("Edit User Details", "Edit user details")],
        "beetles/data_management.html": [("Data Actions", "Data actions"), ("Version History", "Version history"),
                                         ("Upload New Dataset", "Upload new dataset"), ("Activity Logs", "Activity logs"),
                                         ("Field Definitions", "Field definitions"), ("Show All", "Show all")],
        "beetles/interactions_preview.html": [("Unique Taxa", "unique taxa"), ("Record Details", "Record details"),
                                              ("Copy Citation", "Copy citation"), ("Browse Dataset", "Browse dataset")],
        "beetles/tool_annotate.html": [("Advanced Filters", "Advanced filters"), ("Bounding Box", "Bounding box"),
                                       ("No Image Selected", "No image selected")],
        "beetles/detail.html": [("Specimen and Collection", "Specimen and collection"), ("Type Status", "Type status")],
        "beetles/image_browser.html": [("Apply Filters", "Apply filters"), ("Items Per Page", "Items per page")],
    }

    def test_headings_and_buttons_are_in_sentence_case(self):
        for name, pairs in self.CHANGED.items():
            page = read(name)
            for old, new in pairs:
                with self.subTest(template=name, heading=new):
                    self.assertIn(new, page)
                    self.assertNotIn(f">{old}<", page)
                    self.assertNotIn(f"> {old}", page)

    def test_names_keep_their_capitals(self):
        self.assertIn("About IBBI-AI", read("beetles/tool_classify.html"))


class SectionTests(SimpleTestCase):
    def test_the_detail_pages_sections_are_bold_headings(self):
        page = read("beetles/detail.html")
        for heading in ("Taxonomy", "Specimen and collection", "Attribution", "Validation", "Technical", "Identifiers"):
            with self.subTest(heading=heading):
                self.assertRegex(page, rf'<h3 class="section-heading[^"]*">{heading}</h3>')
        self.assertNotIn("tracking-wide text-text/70 uppercase", page)

    def test_data_management_sections_use_section_heading(self):
        page = read("beetles/data_management.html")
        for heading in ("Data actions", "Species tables", "Activity logs"):
            with self.subTest(heading=heading):
                self.assertRegex(page, rf'class="section-heading">{heading}</h2>')

    def test_small_labels_inside_a_card_are_card_labels(self):
        self.assertIn('<span class="card-label">Downloads</span>', read("beetles/game_settings.html"))
        self.assertIn('class="card-label', read("beetles/game_profile.html"))


class EmptyStateTests(SimpleTestCase):
    INCLUDE = 'include "beetles/includes/empty_state.html"'

    def test_empty_lists_use_the_standard_empty_state(self):
        for name in ("beetles/game_profile.html", "beetles/game_scoring.html", "beetles/game_history.html",
                     "beetles/game_settings.html", "beetles/data_management.html", "beetles/game_leaderboard.html",
                     "beetles/interaction_review.html", "beetles/includes/gallery_results.html", "accounts/my_account.html"):
            with self.subTest(template=name):
                self.assertIn(self.INCLUDE, read(name))

    def test_no_bare_none_yet_left(self):
        for path in all_templates():
            text = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotIn(">None yet.<", text)
                self.assertNotIn(">No players yet.<", text)

    def test_the_include_has_a_compact_form(self):
        include = read("beetles/includes/empty_state.html")
        self.assertIn("{% if compact %}", include)
        self.assertIn("data-empty-state", include)


class DashTests(SimpleTestCase):
    def test_no_en_dash_stands_for_a_missing_value(self):
        for path in all_templates():
            text = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotRegex(text, r"\{% else %\}(<span[^>]*>)?&ndash;")
                self.assertNotIn(">&ndash;<", text)
                self.assertNotRegex(text, r"""[?:] ["']–["']""")

    def test_ranges_keep_their_en_dash(self):
        self.assertIn("0&ndash;1", read("beetles/interaction_review.html"))
        self.assertIn("&ndash;{{ page.end_index", read("beetles/includes/pager.html"))

    def test_the_leaderboard_uses_the_em_dash(self):
        page = read("beetles/game_leaderboard.html")
        self.assertIn('<span class="text-gray-500">&mdash;</span>', page)
        self.assertNotIn("&ndash;", page)


class FieldNameTests(SimpleTestCase):
    def test_game_settings_says_validated_in_words(self):
        page = read("beetles/game_settings.html")
        self.assertNotIn("bbox_is_validated", page)
        self.assertIn("beetles a curator has validated", page)


class CardTests(SimpleTestCase):
    def test_data_management_sections_are_not_framed_cards(self):
        page = read("beetles/data_management.html")
        self.assertNotIn("bg-gray-50 rounded-xl p-6 border border-gray-200 shadow-xs", page)
        self.assertIn("divide-y divide-gray-200 rounded-lg border", page)   # the action rows: a list you act on

    def test_the_user_directory_is_a_section(self):
        page = read("accounts/my_account.html")
        self.assertIn('<section id="user-directory">', page)
        self.assertNotIn("bg-gray-50 rounded-xl p-6 border border-gray-200 shadow-xs", page)

    def test_scoring_subsections_are_not_boxes_but_its_charts_are_cards(self):
        page = read("beetles/game_scoring.html")
        self.assertNotIn('<div class="p-4 rounded-xl border border-gray-200">', page)
        self.assertEqual(page.count('<div class="card" data-testid="dist-'), 4)
