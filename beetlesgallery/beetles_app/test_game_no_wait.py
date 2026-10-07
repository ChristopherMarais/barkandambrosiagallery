"""
An answer never waits for another batch build (round 5): when a worker or a look-ahead request is growing the batch
at the same moment, the answer returns at once with what is ready, and the other build finishes the batch.
"""
import threading
import time
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from beetlesgallery.beetles_app import game_grow
from beetlesgallery.beetles_app.models import GameRound
from beetlesgallery.beetles_app.test_game_switch_fast import FastCase


@override_settings(GAME_ROUND_SIZE=6, GAME_RECOMPUTE_IN_BACKGROUND=True)
class TwoBuildsColliding(FastCase):
    def test_a_second_build_at_the_same_moment_is_not_waited_for(self):
        rnd, _, _ = self.start()
        key = game_grow.LOCK.format(rnd.id)
        release = threading.Event()

        def first_build():   # the worker: holds the batch while it grows it, and lets go a little later
            cache.add(key, 1, 60)
            release.wait(5)
            cache.delete(key)

        builder = threading.Thread(target=first_build)
        builder.start()
        try:
            while not cache.get(key):   # the first build has the batch
                time.sleep(0.01)
            with mock.patch("time.sleep") as slept:
                started = time.monotonic()
                added = game_grow.grow_or_wait(rnd, game_grow.FIRST)
                elapsed = time.monotonic() - started
        finally:
            release.set()
            builder.join()
        self.assertEqual(added, 0)
        self.assertFalse(slept.called)
        self.assertLess(elapsed, 0.5)   # the answer goes on at once (the old wait was up to five seconds)
        self.assertEqual(len(rnd.items), len(GameRound.objects.get(id=rnd.id).items))

    def test_a_build_that_is_free_still_grows_the_batch_here(self):
        rnd, _, _ = self.start()
        self.assertEqual(game_grow.grow_or_wait(rnd, game_grow.FIRST), 2)
