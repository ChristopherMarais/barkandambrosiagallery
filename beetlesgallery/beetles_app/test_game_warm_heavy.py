"""
Batches built ahead for the games a player might switch to (game_warm) are the heavy worker's job: they are only ever
a head start, and on the quick worker they held up what a player in the middle of a feed waits on (a batch growing,
the next batch). The game played now is built first, so a page load finds its batch soonest.
"""
from unittest import mock

from django.test import SimpleTestCase, override_settings

from beetlesgallery.beetles_app import game, game_warm
from beetlesgallery.beetles_app.models import GamePreference
from beetlesgallery.beetles_app.test_game_switch_fast import FastCase


class QueueTests(SimpleTestCase):
    def test_built_on_the_heavy_worker(self):
        from beetlesgallery.beetles_app import tasks
        from beetlesgallery.celery import app

        self.assertEqual(app.amqp.router.route({}, tasks.warm_game_batches_task.name)["queue"].name, "heavy")
        for task in (tasks.grow_game_round_task, tasks.build_game_batch_ahead_task, tasks.prepare_game_crops_task):
            self.assertEqual(app.amqp.router.route({}, task.name)["queue"].name, "celery", task.name)


@override_settings(GAME_ROUND_SIZE=6, GAME_RECOMPUTE_IN_BACKGROUND=True)
class OrderTests(FastCase):
    def setUp(self):
        super().setUp()
        for _ in range(6):
            self.roi(self.t_affinis, validated=True)
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="pair")

    def test_the_game_played_now_comes_first(self):
        missing = game_warm.missing(self.user)
        self.assertEqual(missing[0], "pair")
        self.assertEqual(sorted(missing[1:]), sorted(game_warm.choices(self.user)))

    def test_and_is_built_first(self):
        real, order = game.batch_items, []

        def batch_items(player, mode, **kwargs):
            order.append(kwargs.get("choice"))
            return real(player, mode, **kwargs)

        with mock.patch.object(game, "batch_items", side_effect=batch_items):
            built = game_warm.build(self.user)
        self.assertEqual(order[0], "pair")
        self.assertEqual(built[0], "pair")
