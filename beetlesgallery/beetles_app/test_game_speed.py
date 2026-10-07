"""The game's speed (round 5): what is remembered within one request, and the indexes its hot queries rely on."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import game
from .models import Beetles, GameAnswer


class RevealsMemo(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="speed-player", password="x")

    def test_one_request_computes_reveals_once_per_player(self):
        with mock.patch.object(game, "_reveals", return_value={}) as compute:
            token = game.reveals_memo.set({})
            try:
                game.reveals(self.user)
                game.reveals(self.user)
                game.held_back_ids(self.user)
            finally:
                game.reveals_memo.reset(token)
        self.assertEqual(compute.call_count, 1)

    def test_outside_a_timed_request_nothing_is_remembered(self):
        with mock.patch.object(game, "_reveals", return_value={}) as compute:
            game.reveals(self.user)
            game.reveals(self.user)
        self.assertEqual(compute.call_count, 2)

    def test_the_memo_is_per_player(self):
        other = get_user_model().objects.create_user(username="speed-other", password="x")
        with mock.patch.object(game, "_reveals", return_value={}) as compute:
            token = game.reveals_memo.set({})
            try:
                game.reveals(self.user)
                game.reveals(other)
            finally:
                game.reveals_memo.reset(token)
        self.assertEqual(compute.call_count, 2)


class SpeedIndexes(TestCase):
    def test_the_hot_lookups_have_their_indexes(self):
        beetle_indexes = {i.name for i in Beetles._meta.indexes}
        answer_indexes = {i.name for i in GameAnswer._meta.indexes}
        self.assertIn("beetles_lower_specimen_idx", beetle_indexes)
        self.assertIn("game_answer_player_day_idx", answer_indexes)
        self.assertIn("game_answer_tiles_gin", answer_indexes)
