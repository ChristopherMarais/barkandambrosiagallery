"""
Every playable beetle's small crop cut ahead of any batch (game_crops.precut): a sweep on the heavy worker, a part at a
time, at most every few hours, cutting only what is missing. The files are the ones the feed would cut on request, so
nothing the player sees changes; they just never wait for one.
"""
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache

from beetlesgallery.beetles_app import game_crops
from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.test_game_crops import CropFileCase

QUEUE = "beetlesgallery.beetles_app.tasks.precut_game_crops_task.apply_async"


class PrecutTests(CropFileCase):
    def setUp(self):
        super().setUp()
        cache.delete(game_crops.PRECUT_KEY)
        self.addCleanup(cache.delete, game_crops.PRECUT_KEY)

    def small_crops(self):
        return sorted(p.name for p in (Path(self.media) / "crops").rglob("*_small.*"))

    def test_cuts_the_small_crop_of_every_playable_beetle_once(self):
        rois = [self.roi(self.t_affinis), self.roi(self.t_ferr, validated=False)]
        with mock.patch(QUEUE) as queued:
            self.assertEqual(game_crops.precut(), 2)
        queued.assert_not_called()   # all done in one part
        self.assertEqual(self.small_crops(), sorted(game_crops.crop_path(r, "small").name for r in rois))
        self.assertFalse(list((Path(self.media) / "crops").rglob("*_large.*")))   # the large ones stay per batch
        with mock.patch.object(game_crops, "cut") as cut:
            self.assertEqual(game_crops.precut(), 0)   # nothing left to cut
        cut.assert_not_called()

    def test_the_same_file_the_feed_would_have_cut(self):
        roi = self.roi(self.t_affinis)
        game_crops.precut()
        path = game_crops.crop_path(roi, "small")
        with mock.patch.object(game_crops, "cut") as cut:
            self.assertEqual(game_crops.ensure(roi, "small"), path)
        cut.assert_not_called()

    def test_only_beetles_the_game_can_show(self):
        label = self.roi(self.t_affinis)
        Beetles.objects.filter(pk=label.pk).update(aspect="Label")
        gone = self.roi(self.t_affinis)
        Beetles.objects.filter(pk=gone.pk).update(is_deleted=True)
        self.assertEqual(game_crops.precut(), 0)
        self.assertEqual(self.small_crops(), [])

    def test_a_part_at_a_time_each_queueing_the_rest(self):
        rois = sorted((self.roi(self.t_affinis) for _ in range(5)), key=lambda r: str(r.pk))
        with mock.patch(QUEUE) as queued:
            self.assertEqual(game_crops.precut(part=2), 2)
        queued.assert_called_once_with(args=[str(rois[1].pk)], retry=False)
        with mock.patch(QUEUE) as queued:
            self.assertEqual(game_crops.precut(after=str(rois[1].pk), part=2), 2)
            self.assertEqual(game_crops.precut(after=str(rois[3].pk), part=2), 1)
        self.assertEqual(queued.call_count, 1)   # the last part found the end
        self.assertEqual(len(self.small_crops()), 5)

    def test_a_photo_that_cannot_be_read_is_passed_over(self):
        broken, fine = sorted((self.roi(self.t_affinis) for _ in range(2)), key=lambda r: str(r.pk))
        (Path(self.media) / broken.image_asset.image_file.name).write_bytes(b"not a photo")
        with self.assertLogs("beetlesgallery.beetles_app.game_crops", "WARNING"), mock.patch(QUEUE):
            self.assertEqual(game_crops.precut(part=1), 1)
            self.assertEqual(game_crops.precut(after=str(broken.pk), part=1), 1)   # the next part moves on
        self.assertEqual(self.small_crops(), [game_crops.crop_path(fine, "small").name])

    def test_a_new_batch_queues_a_sweep_at_most_every_few_hours(self):
        for _ in range(3):
            self.roi(self.t_affinis, validated=False)
        with mock.patch(QUEUE) as queued, self.captureOnCommitCallbacks(execute=True):
            self.play("classify")
        queued.assert_called_once_with(args=[None], retry=False)
        with mock.patch(QUEUE) as queued, self.captureOnCommitCallbacks(execute=True):
            self.assertFalse(game_crops.precut_later())
        queued.assert_not_called()
        self.assertEqual(game_crops.PRECUT_EVERY, 6 * 60 * 60)

    def test_a_queue_that_cannot_be_reached_tries_again_with_the_next_batch(self):
        with mock.patch(QUEUE, side_effect=OSError("no broker")), self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(game_crops.precut_later())
        with mock.patch(QUEUE) as queued, self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(game_crops.precut_later())
        queued.assert_called_once()

    def test_runs_on_the_heavy_worker(self):
        route = settings.CELERY_TASK_ROUTES["beetlesgallery.beetles_app.tasks.precut_game_crops_task"]
        self.assertEqual(route, {"queue": settings.HEAVY_QUEUE})
