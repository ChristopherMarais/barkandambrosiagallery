"""
The feed preloads while the player decides (#542): the next two beetles' crops are prefetched, the next batch is built
on the worker from the middle of a batch, switching game doesn't wait for the old batch to be counted, a batch is built
ahead for each game the player could switch to, and a request looks up each beetle once.
"""
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_views, game_warm
from beetlesgallery.beetles_app.models import GameAnswer, GamePreference, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")
AHEAD_TASK = "beetlesgallery.beetles_app.tasks.build_game_batch_ahead_task.apply_async"
WARM_TASK = "beetlesgallery.beetles_app.tasks.warm_game_batches_task.apply_async"
FINISH_TASK = "beetlesgallery.beetles_app.tasks.finish_game_round_task.apply_async"


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


class PreloadCase(GameCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        for _ in range(8):
            self.roi(self.t_affinis, validated=False)

    def answer(self, rnd, item):
        return self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()


@override_settings(GAME_ROUND_SIZE=4, GAME_FIRST_ITEMS=0)   # whole batches: a batch started small has its own tests
class PrefetchTests(PreloadCase):
    def test_the_next_beetle_and_the_small_crops_of_the_one_after_it_are_prefetched(self):
        rnd, item = self.play("classify")
        nxt = game_views._next_index(rnd, item["index"] + 1)
        one, two = game_views._item_images(rnd, nxt), game_views._item_images(rnd, game_views._next_index(rnd, nxt + 1))
        self.assertEqual(item["prefetch"], [one[0]["small"], one[0]["large"], two[0]["small"]])

    def test_the_last_but_one_beetle_prefetches_only_the_last(self):
        rnd, item = self.play("classify")
        for _ in range(2):
            item = self.answer(rnd, item)["item"]
        last = game_views._item_images(rnd, game_views._next_index(rnd, item["index"] + 1))
        self.assertEqual(item["prefetch"], [last[0]["small"], last[0]["large"]])


@override_settings(GAME_ROUND_SIZE=4, GAME_RECOMPUTE_IN_BACKGROUND=True)
class AheadOnTheWorkerTests(PreloadCase):
    def test_from_the_middle_of_a_batch_the_next_one_is_queued_for_the_worker_once(self):
        rnd, item = self.play("classify")
        with mock.patch(AHEAD_TASK) as queued, self.captureOnCommitCallbacks(execute=True):
            item = self.answer(rnd, item)["item"]                  # the 2nd of 4: not yet
        queued.assert_not_called()
        with mock.patch(AHEAD_TASK) as queued, self.captureOnCommitCallbacks(execute=True):
            item = self.answer(rnd, item)["item"]                  # the 3rd: from here on
        self.assertEqual(queued.call_args.kwargs["args"], [str(rnd.id)])
        with mock.patch(AHEAD_TASK) as again, self.captureOnCommitCallbacks(execute=True):
            self.answer(rnd, item)                                 # the last
        again.assert_not_called()                                  # once
        self.assertEqual(GameRound.objects.count(), 1)             # nothing built while the player waited

    def coming_first(self, rnd):
        """
        The next batch draws the beetles still to come in ``rnd`` first, all but one of them. Left to chance it draws
        the same four of the eight now and then, and the batch built ahead, which leaves those out, would be
        empty; this way it always has to leave some out and always has one left.
        """
        coming = {it["a"] for it in rnd.items}

        def pick(candidates, n, target):
            if len(candidates) <= n:
                return list(candidates)
            first = [c for c in candidates if str(c) in coming][:n - 1]
            return (first + [c for c in candidates if str(c) not in coming])[:n]
        return mock.patch.object(game, "_pick_near", pick)

    def test_the_worker_builds_the_next_batch_without_the_beetles_still_to_come(self):
        rnd, item = self.play("classify")
        coming = {it["a"] for it in rnd.items[1:]}
        with self.coming_first(rnd):
            ahead = game_views.build_ahead_now(str(rnd.id))
        self.assertIsNotNone(ahead)
        self.assertTrue(coming.isdisjoint(it["a"] for it in ahead.items))
        self.assertEqual(game_views._batch_ahead(rnd), ahead)
        self.assertIsNone(game_views.build_ahead_now(str(rnd.id)))  # once
        self.assertEqual(GameRound.objects.count(), 2)

    def test_the_worker_builds_nothing_for_a_batch_already_closed(self):
        rnd, _ = self.play("classify")
        game._close(rnd)
        self.assertIsNone(game_views.build_ahead_now(str(rnd.id)))
        self.assertEqual(GameRound.objects.count(), 1)

    def test_while_the_worker_builds_it_the_last_beetle_does_not_build_it_too(self):
        rnd, item = self.play("classify")
        cache.set(game_views.AHEAD_LOCK.format(rnd.id), 1, 60)
        for _ in range(3):
            data = self.answer(rnd, item)
            item = data["item"]
        self.assertEqual(GameRound.objects.count(), 1)
        # the worker didn't deliver in time: the end of the batch still carries on into a new one
        data = self.answer(rnd, item)
        self.assertNotEqual(data["round"], str(rnd.id))

    def test_when_the_queue_cannot_be_reached_the_last_beetle_builds_it_as_before(self):
        rnd, item = self.play("classify")
        with mock.patch(AHEAD_TASK, side_effect=OSError("no broker")), self.captureOnCommitCallbacks(execute=True):
            for _ in range(2):
                item = self.answer(rnd, item)["item"]
        self.assertIsNone(cache.get(game_views.AHEAD_LOCK.format(rnd.id)))
        item = self.answer(rnd, item)["item"]                      # the last
        ahead = GameRound.objects.exclude(id=rnd.id).get()
        self.assertIn(f"/round/{ahead.id}/crop/", item["prefetch"][0])

    def test_the_worker_task_builds_it(self):
        from beetlesgallery.beetles_app.tasks import build_game_batch_ahead_task

        rnd, _ = self.play("classify")
        cache.set(game_views.AHEAD_LOCK.format(rnd.id), 1, 60)
        with self.coming_first(rnd):
            build_game_batch_ahead_task(str(rnd.id))
        self.assertIsNotNone(game_views._batch_ahead(rnd))
        self.assertIsNone(cache.get(game_views.AHEAD_LOCK.format(rnd.id)))   # and lets go


@override_settings(GAME_ROUND_SIZE=4, GAME_RECOMPUTE_IN_BACKGROUND=True)
class SwitchTests(PreloadCase):
    def setUp(self):
        super().setUp()
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="classify")
        cutting = mock.patch("beetlesgallery.beetles_app.game_crops.ensure")   # the test photos have no files
        cutting.start()
        self.addCleanup(cutting.stop)

    def test_starting_afresh_counts_the_old_batch_on_the_worker_and_drops_the_one_built_ahead(self):
        rnd, item = self.play("mixed")
        self.answer(rnd, item)
        ahead = game_views.build_ahead_now(str(rnd.id))
        with mock.patch(FINISH_TASK) as queued, mock.patch.object(game, "refresh_round") as refresh, \
                self.captureOnCommitCallbacks(execute=True):
            data = self.post("game_start", {"mode": "mixed", "fresh": True}).json()
        refresh.assert_not_called()                                 # nothing heavy while the player waits
        self.assertEqual(queued.call_args.kwargs["args"], [str(rnd.id)])
        self.assertIsNotNone(GameRound.objects.get(id=rnd.id).finished_at)
        self.assertFalse(GameRound.objects.filter(id=ahead.id).exists())
        self.assertNotIn(data["round"], (str(rnd.id), str(ahead.id)))

    def test_switching_to_a_game_built_ahead_uses_that_batch(self):
        self.play("mixed")
        GamePreference.objects.filter(player=self.user).update(play_mode="pair")   # classify is now one to switch to
        self.assertIn("classify", game_warm.build(self.user))
        built = cache.get(game_warm.KEY.format(self.user.pk, "classify"))["items"]
        self.post("game_prefs", {"play_mode": "classify"})
        with mock.patch.object(game, "batch_items", wraps=game.batch_items) as building:
            data = self.post("game_start", {"mode": "mixed", "fresh": True}).json()
        building.assert_not_called()
        self.assertEqual(GameRound.objects.get(id=data["round"]).items, built)
        self.assertIsNone(cache.get(game_warm.KEY.format(self.user.pk, "classify")))   # used once

    def test_beetles_answered_since_are_left_out(self):
        rnd, item = self.play("mixed")
        GamePreference.objects.filter(player=self.user).update(play_mode="pair")
        game_warm.build(self.user)
        entry = cache.get(game_warm.KEY.format(self.user.pk, "classify"))
        # they answer one of its beetles in the batch they are playing
        first = entry["items"][0]["a"]
        rnd.items[item["index"]]["a"] = first
        rnd.save(update_fields=["items"])
        self.answer(rnd, item)
        self.assertTrue(GameAnswer.objects.filter(roi_id=first).exists())
        self.post("game_prefs", {"play_mode": "classify"})
        data = self.post("game_start", {"mode": "mixed", "fresh": True}).json()
        self.assertEqual(GameRound.objects.get(id=data["round"]).items, entry["items"][1:])

    def test_a_batch_built_under_another_focus_is_not_used(self):
        self.play("mixed")
        GamePreference.objects.filter(player=self.user).update(play_mode="pair")
        game_warm.build(self.user)
        GamePreference.objects.filter(player=self.user).update(play_mode="classify", focus_rank="genus",
                                                               focus_value="Xyleborus")
        self.assertIsNone(game_warm.take(self.user, "mixed"))

    def test_only_the_mixed_feed_switches(self):
        self.client.force_login(self.user)
        self.assertIsNone(game_warm.take(self.user, "classify"))
        self.assertFalse(game_warm.warm_later(self.user, "classify"))


@override_settings(GAME_RECOMPUTE_IN_BACKGROUND=True)
class WarmTests(PreloadCase):
    def test_nothing_to_switch_to_before_choosing_a_game_unlocks(self):
        self.assertEqual(game_warm.choices(self.user), [])

    def test_every_other_choice_once_the_choice_is_open(self):
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="odd")
        self.assertEqual(game_warm.choices(self.user), ["pair", "select", "classify", "both"])

    def test_the_page_asks_and_the_worker_is_queued_once_while_something_is_missing(self):
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="classify")
        beetle = str(self.roi(self.t_affinis).id)
        self.client.force_login(self.user)
        with mock.patch(WARM_TASK) as queued, self.captureOnCommitCallbacks(execute=True):
            res = self.post("game_warm", {"mode": "mixed"})
        self.assertTrue(res.json()["queued"])
        self.assertEqual(queued.call_args.kwargs["args"], [self.user.pk])
        with mock.patch.object(game, "batch_items", return_value=([{"a": beetle, "check": False}], "")), \
                mock.patch("beetlesgallery.beetles_app.game_crops.ensure"):
            from beetlesgallery.beetles_app.tasks import warm_game_batches_task
            warm_game_batches_task(self.user.pk)
        with mock.patch(WARM_TASK) as queued, self.captureOnCommitCallbacks(execute=True):
            res = self.post("game_warm", {"mode": "mixed"})
        self.assertFalse(res.json()["queued"])                     # all built: nothing to do
        queued.assert_not_called()

    def test_the_first_beetles_crops_are_cut_while_building(self):
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="pair")
        with mock.patch("beetlesgallery.beetles_app.game_crops.ensure") as cut:
            built = game_warm.build(self.user)
        self.assertIn("classify", built)
        self.assertTrue(cut.called)
        self.assertEqual({c.args[1] for c in cut.call_args_list}, {"small", "large"})

    def test_one_build_at_a_time(self):
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="pair")
        cache.set(game_warm.LOCK.format(self.user.pk), 1, 60)
        self.assertEqual(game_warm.build(self.user), [])

    def test_where_game_work_stays_here_nothing_is_built_ahead(self):
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="pair")
        with override_settings(GAME_RECOMPUTE_IN_BACKGROUND=False):
            self.assertFalse(game_warm.warm_later(self.user, "mixed"))

    def test_the_page_needs_a_login(self):
        self.assertRedirectsToLogin(self.client.post(reverse("game_warm"), "{}", content_type="application/json"))


class LookupTests(PreloadCase):
    def test_a_timed_request_looks_each_beetle_up_once(self):
        rnd, item = self.play("classify")
        token = game_views._rows.set({})
        try:
            with CaptureQueriesContext(connection) as queries:
                game_views._item_rois(rnd.items[0])
                game_views._shown_rois(rnd.items[0])
                game_views._item_images(rnd, 0)
            self.assertEqual(sum('FROM "beetles"' in q["sql"] for q in queries.captured_queries), 1)
        finally:
            game_views._rows.reset(token)

    def test_a_beetle_that_is_gone_stays_gone(self):
        rnd, _ = self.play("classify")
        token = game_views._rows.set({})
        try:
            self.assertEqual(game_views._beetles(["00000000-0000-0000-0000-000000000001"]), {})
            self.assertEqual(game_views._beetles(["00000000-0000-0000-0000-000000000001", rnd.items[0]["a"]]).keys(),
                             {rnd.items[0]["a"]})
        finally:
            game_views._rows.reset(token)


class TemplateTests(GameCase):
    def test_the_page_asks_for_the_other_games_once_the_feed_is_up(self):
        self.assertIn("data-warm-url=\"{% url 'game_warm' %}\"", PAGE)
        warm = js_function("warmOthers")
        self.assertIn('PAGE_MODE === "mixed"', warm)
        self.assertIn("prefs.choose_game", warm)
        self.assertIn("api(root.dataset.warmUrl, { mode: PAGE_MODE }).catch(() => {})", warm)
        self.assertIn("warmOthers();", js_function("startFeed"))

    def test_a_switch_keeps_the_beetle_on_screen_until_the_new_one_is_in(self):
        start = js_function("startFeed")
        self.assertIn('const keep = !!fresh && !$("stage").classList.contains("hidden");', start)
        self.assertIn('if (keep) setPhase("next");', start)
        self.assertIn("if (keep) setPhase(before);", start)
