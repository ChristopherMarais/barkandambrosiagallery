"""
A game the player chose plays only that game (#604): never another one in its place. When it runs short it widens its
own pool first; when there is still nothing it says so and the page can offer another game. The mix still mixes.
"""
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_data_fallbacks import FallbackCase


class WiderPoolTests(FallbackCase):
    def setUp(self):
        super().setUp()
        self.checked = [self.roi(t) for t in (self.t_affinis, self.t_ferr, self.t_plat)]

    def shown(self, ago):
        """The player was shown the names of every checked beetle ``ago``."""
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        for i, roi in enumerate(self.checked):
            GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=i, roi=roi,
                                      is_check=True, **AFFINIS)
        GameAnswer.objects.update(answered_at=timezone.now() - ago)

    def test_beetles_shown_before_this_sitting_come_back_when_the_chosen_game_is_empty(self):
        self.shown(timedelta(hours=1))   # within the cooldown, but not this sitting
        self.assertEqual(game.build("classify", self.user, 3), [])
        items = game.build_chosen("classify", self.user, 3)
        self.assertTrue(items)
        self.assertTrue(all(i["check"] for i in items))

    def test_beetles_shown_in_this_sitting_stay_held_back(self):
        self.shown(timedelta(minutes=5))
        self.assertEqual(game.build_chosen("classify", self.user, 3), [])

    def test_the_wider_pool_is_only_for_that_try(self):
        self.shown(timedelta(hours=1))
        game.build_chosen("classify", self.user, 3)
        self.assertFalse(game.widened.get())
        self.assertEqual(game.build("classify", self.user, 3), [])

    def test_a_chosen_game_in_the_feed_widens_too(self):
        self.shown(timedelta(hours=1))
        self.grant("identification", "choose_game", play_mode="classify")
        data = self.started()
        self.assertEqual(data["item"]["mode"], "classify")


class NothingElseInItsPlaceTests(FallbackCase):
    def setUp(self):
        super().setUp()
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat) * 3:
            self.roi(taxon)
            self.roi(taxon, validated=False)

    def test_every_chosen_game_plays_only_itself_or_nothing(self):
        perks = ("odd_one_out", "select_all", "identification", "choose_game")
        for key, builder in game.BUILDERS.items():
            with self.subTest(game=key), mock.patch.object(game, builder, return_value=[]):
                self.grant(*perks, play_mode=key)
                self.assertIsNone(game.start_round(self.user, "mixed", size=6))
                res = self.start(fresh=True)
                self.assertEqual(res.status_code, 404)
                self.assertIn("Pick another game", res.json()["error"])
                self.assertEqual(res.json()["prefs"]["play_mode"], key)   # the choice is kept

    def test_a_chosen_game_with_beetles_plays_only_that_game(self):
        self.grant("odd_one_out", "select_all", "identification", "choose_game", play_mode="pair")
        for _ in range(3):
            rnd = game.start_round(self.user, "mixed", size=6)
            self.assertEqual({i["mode"] for i in rnd.items}, {"pair"})
            self.assertEqual(rnd.notice, "")

    def test_the_mix_still_fills_in_for_a_game_that_runs_short(self):
        self.grant("identification", "choose_game")   # All modes: Similarity and Naming
        with mock.patch.object(game, "build_pair_items", return_value=[]):
            rnd = game.start_round(self.user, "mixed", size=6)
        self.assertEqual({i["mode"] for i in rnd.items}, {"classify"})

    def test_the_end_of_the_feed_names_the_chosen_game(self):
        self.grant("identification", "choose_game", play_mode="pair")
        with mock.patch.object(game, "build_pair_items", return_value=[]):
            text = game.nothing_to_play(self.user)["text"]
        self.assertEqual(text, "Not enough beetles for Similarity right now. Pick another game, or check back soon.")
