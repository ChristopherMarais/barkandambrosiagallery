"""
The first batch is ready before the feed asks for it (round 5): the worker builds the player's current game as well
as the others, on a page load, a sign-in and after each round; a normal start takes that batch.
"""
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_warm
from beetlesgallery.beetles_app.models import GameRound
from beetlesgallery.beetles_app.test_game_switch_fast import FastCase


@override_settings(GAME_ROUND_SIZE=6, GAME_RECOMPUTE_IN_BACKGROUND=True)
class WarmBeforeTheFeed(FastCase):
    def setUp(self):
        super().setUp()
        for _ in range(6):   # checked beetles too: the Similarity game (the start a new player plays) needs them
            self.roi(self.t_affinis, validated=True)

    def waiting_batch(self):
        return cache.get(game_warm.KEY.format(self.user.pk, game.play_mode(self.user)))

    def test_the_worker_builds_the_game_the_player_plays_too(self):
        self.assertIsNone(self.waiting_batch())
        game_warm.build(self.user)
        self.assertIsNotNone(self.waiting_batch())   # the current game, not only the ones to switch to

    def test_a_normal_start_takes_the_batch_built_ahead(self):
        game_warm.build(self.user)
        self.assertIsNotNone(self.waiting_batch())
        self.start(mode="mixed")                    # not a switch: an ordinary start of the feed
        self.assertIsNone(self.waiting_batch())     # it was taken, so the request did not build one

    def test_a_page_load_queues_the_builds(self):
        self.client.force_login(self.user)
        with mock.patch.object(game_warm, "warm_later", return_value=True) as warm:
            self.client.get(reverse("game_play", args=["mixed"]))
        warm.assert_called_once_with(self.user, game_warm.MIXED)

    def test_a_finished_round_queues_the_builds(self):
        rnd = GameRound.objects.create(player=self.user, mode="mixed", items=[])
        with mock.patch.object(game_warm, "warm_later") as warm, \
                mock.patch("beetlesgallery.beetles_app.tasks.finish_game_round_task.apply_async"):
            game.finish_round_later(rnd)
        warm.assert_called_once_with(self.user, game_warm.MIXED)

    def test_the_built_ahead_batches_live_fifteen_minutes(self):
        self.assertEqual(game_warm.KEEP, 15 * 60)
