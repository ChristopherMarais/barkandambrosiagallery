"""
The ratings table is worked out on the worker (round 5): a web request reads the stored table and never scans every
answer. The stored table is the full recompute, exactly.
"""
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from beetlesgallery.beetles_app import game_scoring
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import GameCase

REFRESH = "beetlesgallery.beetles_app.tasks.refresh_game_ratings_task.apply_async"


class RatingsOnTheWorker(GameCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        for n, correct in enumerate([True, True, False, True]):
            roi = self.roi(self.t_affinis, validated=True)
            rnd = GameRound.objects.create(player=self.user, mode="classify", items=[], finished_at=None)
            GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, roi=roi, is_check=True,
                                      correct_genus=correct, genus="Genus")

    @override_settings(GAME_RECOMPUTE_IN_BACKGROUND=True)
    def test_a_web_request_never_works_the_table_out_when_nothing_is_stored(self):
        with mock.patch.object(game_scoring, "ratings") as full, mock.patch(REFRESH) as queued:
            self.assertEqual(game_scoring.cached_ratings(), {})
        full.assert_not_called()
        queued.assert_called_once()

    @override_settings(GAME_RECOMPUTE_IN_BACKGROUND=True)
    def test_the_worker_stores_exactly_the_full_recompute(self):
        full = game_scoring.ratings()
        game_scoring.refresh_ratings()
        self.assertEqual(game_scoring.cached_ratings(), full)
        self.assertTrue(full)   # the test answers did count

    @override_settings(GAME_RECOMPUTE_IN_BACKGROUND=True)
    def test_a_stale_table_is_used_as_it_is_and_refreshed_once(self):
        game_scoring.refresh_ratings()
        stored = cache.get(game_scoring.RATINGS_STORE)
        stored["at"] -= 10_000
        cache.set(game_scoring.RATINGS_STORE, stored, 60)
        with mock.patch(REFRESH) as queued, mock.patch.object(game_scoring, "ratings") as full:
            self.assertEqual(game_scoring.cached_ratings(), stored["table"])
            game_scoring.cached_ratings()   # a second request while the refresh is queued: queued once
        full.assert_not_called()
        queued.assert_called_once()

    @override_settings(GAME_RECOMPUTE_IN_BACKGROUND=False)
    def test_where_game_work_stays_in_the_request_it_is_worked_out_and_stored(self):
        self.assertEqual(game_scoring.cached_ratings(), game_scoring.ratings())
        self.assertIsNotNone(cache.get(game_scoring.RATINGS_STORE))
