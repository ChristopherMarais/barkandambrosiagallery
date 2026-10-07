"""
Game settings polish (#618): open reports are cards instead of a four-column table that crushes the reason text
on a phone, the long explanation of expert-backed labels starts collapsed, the section nav looks like tabs, and
the CSV buttons are a plain "Downloads" list beside one primary action.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.models import GameReport
from beetlesgallery.beetles_app.test_game import GameCase


class SettingsPolishTests(GameCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.superuser)

    def page(self, query=""):
        return self.client.get(reverse("game_settings") + query).content.decode()

    def test_open_reports_are_cards_not_a_wide_table(self):
        roi = self.roi(self.t_affinis)
        GameReport.objects.create(roi=roi, reporter=self.user, reason="bad_box", note="looks wrong")
        page = self.page()
        cards = page[page.index('data-testid="reports-cards"'):page.index('id="players"')]
        self.assertIn("looks wrong", cards)
        self.assertIn("Open in annotator", cards)
        self.assertNotIn("<table", cards)
        # sorting is still there, just above the cards instead of as column headers
        self.assertIn('data-testid="reports-sort"', page)
        self.assertIn("reports_sort=reporter", page)

    def test_the_expert_backed_explanation_starts_collapsed(self):
        page = self.page()
        start = page.index('<details class="mb-8 p-4 text-sm text-gray-600 bg-gray-50 border border-gray-200 rounded-lg" data-testid="expert-backed-help">')
        opening_tag = page[start:page.index(">", start) + 1]
        self.assertNotIn("open", opening_tag)

    def test_the_section_nav_looks_like_tabs(self):
        page = self.page()
        mark = page.index('data-testid="settings-nav"')
        start = page.rindex("<nav", 0, mark)
        nav = page[start:page.index("</nav>", mark)]
        self.assertIn("border-b", nav)

    def test_one_primary_button_and_a_downloads_list(self):
        page = self.page()
        section = page[page.index('data-testid="settings-review"'):page.index('data-testid="expert-backed-help"')]
        self.assertEqual(section.count("btn-primary"), 1)
        self.assertIn("Downloads", section)
        for label in ("Label proposals CSV", "Player reliability CSV", "Expertise CSV"):
            self.assertIn(label, section)
