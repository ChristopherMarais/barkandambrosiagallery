"""
Profile polish (#618): the stat line only shows what's non-zero, the level tile leads with a number (the level
name becomes its caption), and badge captions use a legible size and colour.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class StatChipsTests(ScoringCase):
    def test_zero_stats_are_left_out(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        chips = page[page.index('data-testid="stat-chips"'):page.index("</div>", page.index('data-testid="stat-chips"'))]
        self.assertNotIn("compared for similarity", chips)
        self.assertNotIn("odd ones spotted", chips)
        self.assertNotIn("grids", chips)

    def test_a_non_zero_stat_is_shown(self):
        self.answer(self.user, self.roi(self.t_affinis))
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        chips = page[page.index('data-testid="stat-chips"'):page.index("</div>", page.index('data-testid="stat-chips"'))]
        self.assertIn("named", chips)


class LevelTileTests(ScoringCase):
    def test_the_level_tile_leads_with_a_number(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        tile = page[page.index(">Level<"):page.index(">Score<")]
        self.assertIn('text-lg font-bold text-gray-900 tabular-nums">1<', tile)   # a brand-new player is level 1
        self.assertIn("Egg", tile)   # the level's name is now the caption


class BadgeCaptionTests(ScoringCase):
    def test_badge_captions_are_12px_gray_600(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        self.assertIn('<div class="text-xs leading-tight text-gray-600">', page)
        self.assertNotIn('text-[10px] leading-tight text-gray-400', page)

    def test_two_badges_per_row_on_phones(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        self.assertIn("grid grid-cols-2 sm:grid-cols-4 gap-2", page)
