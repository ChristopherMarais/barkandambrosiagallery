"""
Quick switches and batch ends on a busy server (#575): a batch built while the player waits starts with its first
beetles and grows behind their back (game_grow), the feed's look-ahead (game_upcoming) loads the next beetles' crops
and does the worker's job when it is behind, and the page keeps decoded crops ready for Next.
"""
import time
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_grid_ladder, game_grow, game_views, game_warm
from beetlesgallery.beetles_app.models import GamePreference, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")
GROW_TASK = "beetlesgallery.beetles_app.tasks.grow_game_round_task.apply_async"
FINISH_TASK = "beetlesgallery.beetles_app.tasks.finish_game_round_task.apply_async"


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


def ids(items):
    return [i for item in items for i in sorted(game._item_ids(item))]


class FastCase(GameCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        for _ in range(12):
            self.roi(self.t_affinis, validated=False)
        cutting = mock.patch("beetlesgallery.beetles_app.game_crops.ensure")   # the test photos have no files
        cutting.start()
        self.addCleanup(cutting.stop)

    def answer(self, rnd_id, item):
        return self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd_id).json()

    def start(self, mode="classify", **body):
        self.client.force_login(self.user)
        with mock.patch(GROW_TASK) as queued, self.captureOnCommitCallbacks(execute=True):
            res = self.post("game_start", dict(mode=mode, **body))
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        return GameRound.objects.get(id=data["round"]), data, queued

    def upcoming(self, rnd, index):
        return self.client.get(reverse("game_upcoming", args=[rnd.id]), {"index": index})


@override_settings(GAME_ROUND_SIZE=6, GAME_RECOMPUTE_IN_BACKGROUND=True)
class StartSmallTests(FastCase):
    def test_a_batch_built_while_the_player_waits_starts_with_its_first_beetles(self):
        rnd, data, queued = self.start()
        self.assertEqual(len(rnd.items), game_grow.FIRST)
        self.assertEqual(data["item"]["total"], 6)                  # what it will be
        self.assertEqual(queued.call_args.kwargs["args"], [str(rnd.id)])   # the rest on the worker
        self.assertEqual(game_grow.wanted(rnd), 4)

    def test_the_worker_grows_it_whole_without_moving_or_repeating_a_beetle(self):
        rnd, _, _ = self.start()
        first = list(rnd.items)
        self.assertEqual(game_grow.grow_now(str(rnd.id)), 4)
        rnd.refresh_from_db()
        self.assertEqual(rnd.items[:2], first)                      # the beetles already shown keep their places
        self.assertEqual(len(rnd.items), 6)
        self.assertEqual(len(ids(rnd.items)), len(set(ids(rnd.items))))
        self.assertEqual(game_grow.wanted(rnd), 0)
        self.assertEqual(game_grow.grow_now(str(rnd.id)), 0)        # once

    def test_the_worker_cuts_the_crops_of_the_new_beetles(self):
        rnd, _, _ = self.start()
        with mock.patch("beetlesgallery.beetles_app.game_crops.ensure") as cut:
            game_grow.grow_now(str(rnd.id))
        self.assertEqual({c.args[1] for c in cut.call_args_list}, {"small", "large"})
        self.assertEqual(cut.call_count, 2 * 4)

    def test_one_grower_at_a_time(self):
        rnd, _, _ = self.start()
        cache.set(game_grow.LOCK.format(rnd.id), 1, 60)
        self.assertEqual(game_grow.grow(rnd), 0)
        self.assertEqual(len(GameRound.objects.get(id=rnd.id).items), 2)

    def test_a_finished_batch_stops_growing(self):
        rnd, _, _ = self.start()
        game._close(rnd)
        self.assertEqual(game_grow.grow(rnd), 0)
        self.assertEqual(len(GameRound.objects.get(id=rnd.id).items), 2)
        self.assertEqual(game_grow.wanted(rnd), 0)

    def test_a_batch_with_nothing_new_to_add_ends_where_it_is(self):
        rnd, _, _ = self.start()
        with mock.patch.object(game, "batch_items", return_value=([], "")):
            self.assertEqual(game_grow.grow(rnd), 0)
        self.assertEqual(game_grow.wanted(rnd), 0)

    def test_the_last_beetle_so_far_grows_it_on_the_spot_when_nobody_has(self):
        rnd, data, _ = self.start()
        item = data["item"]
        for _ in range(3):
            out = self.answer(rnd.id, item)
            self.assertEqual(out.get("round", str(rnd.id)), str(rnd.id))   # the same batch carries on
            item = out["item"]
        self.assertGreater(len(GameRound.objects.get(id=rnd.id).items), 3)

    def test_an_answer_does_not_wait_for_the_worker_that_is_growing_it(self):
        rnd, _, _ = self.start()
        cache.set(game_grow.LOCK.format(rnd.id), 1, 60)             # the worker is at it this moment
        with mock.patch("time.sleep") as slept:
            started = time.monotonic()
            self.assertEqual(game_grow.grow_or_wait(rnd, game_grow.FIRST), 0)
        self.assertFalse(slept.called)                              # never a sleep in the request
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(len(GameRound.objects.get(id=rnd.id).items), 2)

    def test_it_picks_up_what_the_worker_added_meanwhile(self):
        rnd, _, _ = self.start()
        cache.set(game_grow.LOCK.format(rnd.id), 1, 60)
        grown = GameRound.objects.get(id=rnd.id)
        GameRound.objects.filter(id=rnd.id).update(items=grown.items + [dict(grown.items[0], a="x")])
        self.assertEqual(game_grow.grow_or_wait(rnd, game_grow.FIRST), 1)
        self.assertEqual(len(rnd.items), 3)

    def test_the_whole_batch_where_game_work_stays_in_the_request(self):
        with override_settings(GAME_RECOMPUTE_IN_BACKGROUND=False):
            rnd, _, queued = self.start()
        self.assertEqual(len(rnd.items), 6)
        queued.assert_not_called()

    def test_the_whole_batch_when_starting_small_is_turned_off(self):
        with override_settings(GAME_FIRST_ITEMS=0):
            rnd, _, queued = self.start()
        self.assertEqual(len(rnd.items), 6)
        queued.assert_not_called()

    def test_switching_with_nothing_built_ahead_starts_small(self):
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="classify")
        old, data, _ = self.start("mixed")
        # to All modes: Similarity alone has no checked beetles here, and a chosen game never plays another (#604)
        self.post("game_prefs", {"play_mode": "both"})
        with mock.patch.object(game, "refresh_round") as refresh, mock.patch(FINISH_TASK):
            rnd, data, queued = self.start("mixed", fresh=True)
        refresh.assert_not_called()                                 # the old batch is counted on the worker
        self.assertNotEqual(rnd.id, old.id)
        self.assertLessEqual(len(rnd.items), game_grow.FIRST)
        self.assertTrue(queued.called)


@override_settings(GAME_ROUND_SIZE=6, GAME_RECOMPUTE_IN_BACKGROUND=True)
class RestepTests(FastCase):
    def test_a_grid_built_again_keeps_the_beetles_added_since(self):
        rnd, _, _ = self.start()
        stale = GameRound.objects.get(id=rnd.id)
        game_grow.grow(rnd)                                         # the batch grew in another request
        stale.items[0] = dict(stale.items[0], mode="odd", step=1, tiles=[stale.items[0]["a"]], rank="genus", group={})
        GameRound.objects.filter(id=rnd.id).update(items=[stale.items[0]] + rnd.items[1:])
        with mock.patch.object(game_grid_ladder, "current", return_value=2), \
                mock.patch.object(game, "build_grid_items", return_value=[{"a": rnd.items[0]["a"], "tiles": [], "step": 2,
                                                                           "check": False}]):
            self.assertTrue(game_grid_ladder.restep(stale, 0))
        saved = GameRound.objects.get(id=rnd.id).items
        self.assertEqual(len(saved), 6)
        self.assertEqual(saved[0]["step"], 2)
        self.assertEqual(saved[1:], rnd.items[1:])


@override_settings(GAME_ROUND_SIZE=6, GAME_RECOMPUTE_IN_BACKGROUND=True)
class UpcomingTests(FastCase):
    def test_the_next_beetles_crops(self):
        rnd, data, _ = self.start()
        game_grow.grow_now(str(rnd.id))
        res = self.upcoming(rnd, 0)
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        self.assertEqual([it["index"] for it in items], [1, 2, 3])
        self.assertEqual(items[0]["small"], [im["small"] for im in game_views._item_images(rnd, 1)])
        self.assertEqual(items[0]["large"], [im["large"] for im in game_views._item_images(rnd, 1)])
        self.assertNotIn("a", items[0])                             # photos only, never which beetle it is

    def test_it_grows_a_batch_the_worker_has_not_got_to(self):
        rnd, _, _ = self.start()
        items = self.upcoming(rnd, 0).json()["items"]
        self.assertEqual([it["index"] for it in items], [1, 2, 3])
        self.assertEqual(len(GameRound.objects.get(id=rnd.id).items), 2 + game_grow.STEP)

    @staticmethod
    def coming_first(coming):
        """
        The batch built ahead draws one of the beetles still to come first, then others. It starts with only
        game_grow.FIRST beetles and leaves out those still to come; left to chance it now and then draws exactly them
        and is empty. This way it always has one to leave out and always keeps one.
        """
        def pick(candidates, n, target):
            if len(candidates) <= n:
                return list(candidates)
            first = [c for c in candidates if str(c) in coming][:1]
            return (first + [c for c in candidates if str(c) not in coming])[:n]
        return mock.patch.object(game, "_pick_near", pick)

    def test_near_the_end_it_builds_the_next_batch_and_reaches_into_it(self):
        rnd, _, _ = self.start()
        game_grow.grow_now(str(rnd.id))
        rnd.refresh_from_db()                                       # grown to the whole batch
        coming = {str(i) for item in rnd.items[4:] for i in game._item_ids(item)}
        with self.coming_first(coming):
            items = self.upcoming(rnd, 4).json()["items"]
        ahead = game_views._batch_ahead(rnd)
        self.assertIsNotNone(ahead)
        self.assertEqual(items[0], dict(items[0], round=str(rnd.id), index=5))
        self.assertEqual({it["round"] for it in items[1:]}, {str(ahead.id)})
        self.assertTrue(coming.isdisjoint(str(i) for item in ahead.items for i in game._item_ids(item)))

    def test_not_while_the_worker_is_building_it(self):
        rnd, _, _ = self.start()
        game_grow.grow_now(str(rnd.id))
        cache.set(game_views.AHEAD_RUNNING.format(rnd.id), 1, 60)
        items = self.upcoming(rnd, 4).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertIsNone(game_views._batch_ahead(rnd))

    def test_the_worker_does_not_build_one_the_feed_is_building(self):
        rnd, _, _ = self.start()
        game_grow.grow_now(str(rnd.id))
        cache.set(game_views.AHEAD_RUNNING.format(rnd.id), 1, 60)
        self.assertIsNone(game_views.build_ahead_now(str(rnd.id)))
        self.assertIsNone(game_views._batch_ahead(rnd))

    def test_a_finished_batch_has_nothing_coming(self):
        rnd, _, _ = self.start()
        game._close(rnd)
        self.assertEqual(self.upcoming(rnd, 0).json(), {"items": []})

    def test_a_bad_index(self):
        rnd, _, _ = self.start()
        self.assertEqual(self.upcoming(rnd, "x").status_code, 400)
        self.assertEqual(self.upcoming(rnd, 99).json(), {"items": []})

    def test_only_the_players_own_batch(self):
        rnd, _, _ = self.start()
        self.client.force_login(self.staff)
        self.assertEqual(self.upcoming(rnd, 0).status_code, 404)

    def test_it_needs_a_login(self):
        rnd, _, _ = self.start()
        self.client.logout()
        self.assertRedirectsToLogin(self.upcoming(rnd, 0))


@override_settings(GAME_RECOMPUTE_IN_BACKGROUND=True)
class WarmSkipsTheGameNowPlayedTests(FastCase):
    def test_a_game_they_switched_to_while_the_worker_ran_is_not_built_again(self):
        GamePreference.objects.create(player=self.user, granted_perks=["all"], play_mode="pair")
        real = game.play_mode
        with mock.patch.object(game, "play_mode", side_effect=lambda p, info=None: "classify" if info is None else real(p, info)):
            built = game_warm.build(self.user)
        self.assertNotIn("classify", built)
        self.assertIsNone(cache.get(game_warm.KEY.format(self.user.pk, "classify")))


class PageTests(GameCase):
    def test_the_page_knows_where_to_look_ahead(self):
        self.assertIn('data-upcoming-url="{% url \'game_upcoming\' \'00000000-0000-0000-0000-000000000000\' %}"', PAGE)

    def test_the_look_ahead_loads_only_the_next_beetles_large_crops(self):
        code = js_function("lookAhead")
        self.assertIn("upcomingUrl", code)
        self.assertIn("n === 0", code)                               # the large crop for the next beetle only
        self.assertIn("lean()", code)                                # a phone saving data or a slow connection:

    def test_a_slow_connection_or_saving_data_loads_just_the_next_small_crops(self):
        self.assertIn("saveData", js_function("lean"))
        self.assertIn('"slow-2g", "2g", "3g"', js_function("lean"))
        code = js_function("lookAhead")
        self.assertIn("slice(0, saving ? 1 : undefined)", code)
        self.assertIn("n === 0 && !saving", code)

    def test_crops_are_kept_decoded_and_reused(self):
        self.assertIn("const decoded = new Map()", PAGE)
        self.assertIn("decoded.get(url)", js_function("loadImage"))
        self.assertIn(".decode()", js_function("loadImage"))

    def test_the_new_beetle_fades_in_only_where_motion_is_welcome(self):
        self.assertIn("@media (prefers-reduced-motion: no-preference)", PAGE)
        # since #602 the tiles are laid one after another and each photo lands in its own (test_grid_lay_and_reveal)
        self.assertIn("#photos .cell.slot { animation: tile-lay", PAGE)
        self.assertIn('cell.className = "cell slot";', js_function("showItem"))

    def test_next_to_photo_is_measured(self):
        self.assertIn('performance.mark("game:next")', js_function("next"))
        self.assertIn('"game:photo"', PAGE)
