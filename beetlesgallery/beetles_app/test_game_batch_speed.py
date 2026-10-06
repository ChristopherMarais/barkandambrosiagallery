"""The game loads faster (#494): crops prefetched, the next batch built while the last beetle of a batch is on
screen, the end of a batch refreshed on the worker, a level reached there still announced, and a timing line."""
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_views
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")
QUEUE = "beetlesgallery.beetles_app.tasks.finish_game_round_task.apply_async"


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


@override_settings(GAME_ROUND_SIZE=2)
class BatchAheadTests(GameCase):
    def setUp(self):
        super().setUp()
        for _ in range(6):
            self.roi(self.t_affinis, validated=False)

    def answer(self, rnd, item):
        return self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()

    def test_the_next_beetles_crops_are_prefetched_small_first(self):
        rnd, item = self.play("classify")
        following = game_views._item_images(rnd, game_views._next_index(rnd, item["index"] + 1))
        self.assertEqual(item["prefetch"], [following[0]["small"], following[0]["large"]])
        self.assertNotIn(following[0]["url"], item["prefetch"])   # never the whole photo

    def test_on_the_last_beetle_the_next_batch_is_built_and_its_first_beetle_prefetched(self):
        rnd, item = self.play("classify")
        data = self.answer(rnd, item)                       # on to the last beetle of the batch
        ahead = GameRound.objects.exclude(id=rnd.id).get()
        self.assertIsNone(ahead.finished_at)
        self.assertIn(f"/round/{ahead.id}/crop/", data["item"]["prefetch"][0])
        # none of the beetles still to come in this batch
        self.assertNotIn(rnd.items[data["item"]["index"]]["a"], [it["a"] for it in ahead.items])
        # answering the last beetle carries on into that batch
        data = self.answer(rnd, data["item"])
        self.assertEqual(data["round"], str(ahead.id))
        self.assertEqual(data["item"]["index"], game_views._next_index(ahead, 0))

    def test_a_reload_on_the_last_beetle_comes_back_to_it_not_to_the_batch_built_ahead(self):
        rnd, item = self.play("classify")
        last = self.answer(rnd, item)["item"]
        res = self.post("game_start", {"mode": "classify"}).json()
        self.assertEqual(res["round"], str(rnd.id))
        self.assertEqual(res["item"]["index"], last["index"])
        self.assertEqual(GameRound.objects.count(), 2)          # the batch ahead is reused, not built again

    def test_after_starting_afresh_a_reload_keeps_the_new_batch(self):
        rnd, item = self.play("classify")
        self.answer(rnd, item)                                  # a batch is built ahead of this one...
        fresh = self.post("game_start", {"mode": "classify", "fresh": True}).json()["round"]
        # ...and left behind by the fresh start: a reload stays on the new batch
        self.assertEqual(self.post("game_start", {"mode": "classify"}).json()["round"], fresh)

    def test_a_batch_built_ahead_and_never_reached_is_dropped_not_counted(self):
        rnd, item = self.play("classify")
        self.answer(rnd, item)
        ahead = GameRound.objects.exclude(id=rnd.id).get()
        GameRound.objects.update(started_at=timezone.now() - timedelta(hours=1))
        GameAnswer.objects.update(answered_at=timezone.now() - timedelta(minutes=30))
        with override_settings(GAME_RECOMPUTE_IN_BACKGROUND=False):
            game.close_idle_rounds(self.user)
        self.assertFalse(GameRound.objects.filter(id=ahead.id).exists())
        self.assertIsNotNone(GameRound.objects.get(id=rnd.id).finished_at)


@override_settings(GAME_ROUND_SIZE=1, GAME_RECOMPUTE_IN_BACKGROUND=True)
class BackgroundTests(GameCase):
    def setUp(self):
        super().setUp()
        for _ in range(3):
            self.roi(self.t_affinis, validated=False)

    def test_the_end_of_a_batch_is_refreshed_on_the_worker_and_the_next_beetle_comes_at_once(self):
        rnd, item = self.play("classify")
        with mock.patch(QUEUE) as queued, mock.patch.object(game, "refresh_round") as refresh, \
                self.captureOnCommitCallbacks(execute=True):
            data = self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()
        self.assertIn("item", data)
        self.assertNotEqual(data["round"], str(rnd.id))
        refresh.assert_not_called()                              # nothing heavy while the player waits
        self.assertEqual(queued.call_args.kwargs["args"], [str(rnd.id)])
        self.assertIsNotNone(GameRound.objects.get(id=rnd.id).finished_at)

    def test_done_here_when_the_queue_cannot_be_reached(self):
        rnd, item = self.play("classify")
        with mock.patch(QUEUE, side_effect=OSError("no broker")), mock.patch.object(game, "refresh_round") as refresh, \
                self.captureOnCommitCallbacks(execute=True):
            self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id)
        self.assertEqual(refresh.call_args.args[0].id, rnd.id)

    def test_the_worker_task_refreshes_the_batch(self):
        from beetlesgallery.beetles_app.tasks import finish_game_round_task

        rnd, _ = self.play("classify")
        with mock.patch.object(game, "refresh_round") as refresh:
            finish_game_round_task(str(rnd.id))
        self.assertEqual(refresh.call_args.args[0].id, rnd.id)

    def test_leaving_closes_the_batch_and_refreshes_it_on_the_worker(self):
        rnd, item = self.play("classify")
        with mock.patch(QUEUE) as queued, mock.patch.object(game, "refresh_round") as refresh, \
                self.captureOnCommitCallbacks(execute=True):
            self.post("game_exit", {"round": str(rnd.id)})
        refresh.assert_not_called()
        self.assertEqual(queued.call_args.kwargs["args"], [str(rnd.id)])
        self.assertIsNotNone(GameRound.objects.get(id=rnd.id).finished_at)

    def test_where_game_work_stays_here_the_batch_is_refreshed_at_once(self):
        rnd, _ = self.play("classify")
        with override_settings(GAME_RECOMPUTE_IN_BACKGROUND=False), mock.patch(QUEUE) as queued, \
                mock.patch.object(game, "refresh_round") as refresh:
            game.finish_round_later(rnd)
        queued.assert_not_called()
        refresh.assert_called_once()

    def test_each_feed_request_logs_how_long_it_took(self):
        with self.assertLogs("beetlesgallery.beetles_app.game_views", "INFO") as logs:
            rnd, item = self.play("classify")
            self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id)
        self.assertRegex(logs.output[0], r"game_start took \d+ ms: 2 new batch\(es\), 2 item\(s\), 4 crop\(s\) queued")
        self.assertRegex(logs.output[1], r"game_answer took \d+ ms: ")


class LateLevelTests(GameCase):
    def setUp(self):
        super().setUp()
        for _ in range(4):
            self.roi(self.t_affinis, validated=False)
        cache.clear()

    def answer(self, rnd, item):
        data = self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()
        return [e for e in data["events"] if e["kind"] == "level"], data

    def test_a_level_reached_in_the_background_is_announced_once_on_the_next_answer(self):
        rnd, item = self.play("classify")
        levels, data = self.answer(rnd, item)
        self.assertEqual(levels, [])                              # level 1, as before
        # the work after a batch, on the worker, lifts them to level 2 between two answers
        PlayerScore.objects.filter(player=self.user).update(score=60)
        levels, data = self.answer(GameRound.objects.get(id=data.get("round", rnd.id)), data["item"])
        self.assertEqual([e["title"] for e in levels], ["Level 2"])
        self.assertIn("Unlocked:", levels[0]["text"])
        self.assertIn("prefs", data)                               # the toolbar learns about the new unlocks
        levels, _ = self.answer(GameRound.objects.get(id=data.get("round", rnd.id)), data["item"])
        self.assertEqual(levels, [])                              # once

    def test_nothing_is_announced_when_the_last_level_shown_is_not_known(self):
        rnd, item = self.play("classify")
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 60})
        cache.clear()
        levels, _ = self.answer(rnd, item)
        self.assertEqual(levels, [])


class TemplateTests(GameCase):
    def test_small_crops_first_then_the_large_ones(self):
        show = js_function("showItem")
        self.assertIn("item.images.map((im) => loadCrop(im)", show)   # each tile its own, as it comes (#602)
        self.assertNotIn("loadImage(im.url)", show)              # the whole photo isn't loaded for the feed
        self.assertIn("loadImage(im.large).then((big) => { if (canvas.isConnected) drawCrop(canvas, big, im.box, true); })", show)
        crop = js_function("loadCrop")
        self.assertIn("loadImage(im.small)", crop)
        self.assertIn("loadImage(im.url)", crop)                 # the fallback: cut the whole photo here

    def test_server_cut_crops_get_the_box_outline_at_the_known_place(self):
        draw = js_function("drawCrop")
        self.assertIn("function drawCrop(canvas, img, box, cut)", draw)
        self.assertIn("const at = CROP_PAD / (1 + 2 * CROP_PAD);", draw)
        self.assertIn("outlineBox(canvas,", draw)

    def test_images_are_decoded_off_the_main_thread(self):
        self.assertIn("img.decode().catch(() => {})", js_function("loadImage"))
        self.assertIn('img.decoding = "async"', js_function("loadImage"))
        self.assertIn("loadImage(url)", js_function("prefetch"))   # prefetched crops are decoded the same way (#575)

    def test_the_whole_photo_loads_only_when_its_view_opens(self):
        self.assertIn('$("lightbox-img").src = url;', js_function("showPhoto"))
        self.assertNotIn(".url", js_function("prefetch"))
