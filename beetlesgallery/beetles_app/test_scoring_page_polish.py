"""
Scoring page polish (#618): the wide game x answer tables actually scroll instead of just cramming at 390px, the
formula blocks have a fade hinting there's more to scroll to, and the section chips become a sticky contents on
desktop with a "Jump to" select on a phone.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase


class ScoringPagePolishTests(GameCase):
    def page(self):
        self.client.force_login(self.superuser)
        return self.client.get(reverse("game_scoring")).content.decode()

    def test_the_wide_tables_scroll_instead_of_cramming(self):
        page = self.page()
        for testid in ("scoring-thresholds", "scoring-play", "difficulty-examples"):
            mark = page.index(f'data-testid="{testid}"')
            start = page.rindex("<table", 0, mark)
            tag = page[start:page.index(">", mark) + 1]
            self.assertIn("whitespace-nowrap", tag)

    def test_formula_blocks_have_a_scroll_fade(self):
        page = self.page()
        self.assertIn("scroll-fade", page)
        self.assertGreaterEqual(page.count('<pre class="text-xs bg-gray-50 border border-gray-200 rounded-lg p-3 overflow-x-auto scroll-fade">')
                                 + page.count('<pre class="mt-3 text-xs bg-gray-50 border border-gray-200 rounded-lg p-3 overflow-x-auto scroll-fade">'), 1)

    def test_the_contents_are_sticky_with_a_jump_to_select_for_phones(self):
        page = self.page()
        start = page.rindex("<div", 0, page.index('data-testid="scoring-toc"'))
        toc = page[start:page.index("</select>")]
        opening_tag = toc[:toc.index(">") + 1]
        self.assertIn("sticky top-0", opening_tag)
        self.assertIn('aria-label="Jump to section"', toc)
        self.assertIn('<option value="tune">Tune</option>', toc)
