"""Levels and unlocks polish (#618): the Focus selects are labelled and the second starts disabled, and the ladder
says how far the next level is before the list of levels."""
from django.urls import reverse

from beetlesgallery.beetles_app.models import PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase


class FocusFormTests(GameCase):
    def test_both_selects_are_labelled(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_unlocks")).content.decode()
        self.assertIn('<label for="focus-rank" class="block text-xs font-medium text-gray-500 mb-1">Rank</label>', page)
        self.assertIn('<label for="focus-value" class="block text-xs font-medium text-gray-500 mb-1">Taxon</label>', page)

    def test_the_taxon_select_starts_disabled_without_a_rank(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_unlocks")).content.decode()
        value_select = page[page.index('id="focus-value"'):page.index("</select>", page.index('id="focus-value"'))]
        self.assertIn("disabled", value_select)


class NextLevelLineTests(GameCase):
    def line(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_unlocks")).content.decode()
        start = page.index('data-testid="level-next"')
        return page[start:page.index("</p>", start)]

    def test_it_says_the_next_level_and_how_far_to_go(self):
        PlayerScore.objects.create(player=self.user, score=0, rating=0.0)
        line = self.line()
        self.assertIn("Next:", line)
        self.assertIn("pts", line)
        self.assertIn("% to go", line)

    def test_it_is_gone_at_the_top_level(self):
        from beetlesgallery.beetles_app import game_levels

        top_points = game_levels.LEVELS[-1][0]
        PlayerScore.objects.create(player=self.user, score=top_points + 1000, rating=1.0)
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_unlocks")).content.decode()
        self.assertNotIn('data-testid="level-next"', page)
