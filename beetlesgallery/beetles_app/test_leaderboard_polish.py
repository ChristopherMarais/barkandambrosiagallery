"""
Leaderboard polish (#618): the sort select applies itself without a "Show" button, the period pills are one
segmented control instead of wrapping pills, the Specialists selects are labelled, your own row says "you", and
a player's name/shield and level pill sit on their own lines instead of stacking unevenly.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.models import PlayerScore
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class LeaderboardPolishTests(ScoringCase):
    def setUp(self):
        super().setUp()
        self.ann = self.player("ann")
        PlayerScore.objects.create(player=self.ann, score=500, rating=0.6, accuracy=0.6, judged=40, viewed=50)
        PlayerScore.objects.create(player=self.user, score=10, rating=0.5, accuracy=0.5, judged=10, viewed=10)
        self.client.force_login(self.user)

    def page(self, **params):
        params.setdefault("period", "all")
        return self.client.get(reverse("game_leaderboard"), params).content.decode()

    def test_the_sort_select_applies_on_change_and_there_is_no_show_button(self):
        page = self.page()
        start = page.index("role=\"search\"")
        form = page[start:page.index("</form>", start)]
        self.assertIn('onchange="this.form.submit()"', form)
        self.assertNotIn(">Show<", form)

    def test_the_periods_are_one_segmented_control(self):
        page = self.page()
        mark = page.index('data-testid="periods"')
        start = page.rindex("<div", 0, mark)
        nav = page[start:page.index("</div>", mark)]
        self.assertIn("grid grid-cols-4", nav)
        self.assertNotIn("rounded-full", nav)   # not pills that wrap to a second line

    def test_the_specialists_selects_are_labelled(self):
        page = self.page()
        self.assertIn('<label for="branch-rank" class="block text-xs font-medium text-gray-500 mb-1">Rank</label>', page)
        self.assertIn('<label for="branch-value" class="block text-xs font-medium text-gray-500 mb-1">Taxon</label>', page)

    def test_your_row_says_you(self):
        page = self.page()
        self.assertIn('data-testid="board-row-me">&middot; you</span>', page)

    def test_the_name_comes_before_the_level_pill_so_they_sit_on_separate_lines(self):
        # Previously the level badge sat before the name on the same line, so a long name and the badge
        # could each wrap onto their own line unevenly (#618 lb-badges). Now the name (plus shield) is the
        # row's first line, and the level badge always follows it, starting the second line.
        page = self.page()
        start = page.index('data-testid="board-row"')
        row = page[start:page.index("</details>", start)]
        self.assertIn('data-testid="level-badge"', row)
        self.assertLess(row.index(self.ann.username), row.index('data-testid="level-badge"'))
