"""The game's crops cut on the server (#494): the window around the box, grey past the photo, two sizes, files named
by the photo and the box, and an endpoint that only serves the player's own beetles."""
import io
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from PIL import Image

from beetlesgallery.beetles_app import game_crops
from beetlesgallery.beetles_app.models import Beetles, GameRound
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image

RED, BLUE = (220, 20, 20), (20, 20, 220)


def photo(width, height, colour=RED, exif=None, fmt="JPEG"):
    """A one-colour photo as a file object (a JPEG unless ``fmt`` says otherwise)."""
    buf = io.BytesIO()
    image = Image.new("RGB", (width, height), colour)
    if exif:
        info = image.getexif()
        info[0x0112] = exif
        image.save(buf, format=fmt, exif=info.tobytes())
    else:
        image.save(buf, format=fmt)
    buf.seek(0)
    return buf


def close(a, b, tolerance=40):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


class CutTests(SimpleTestCase):
    def test_the_window_is_the_box_with_a_quarter_of_it_on_each_side(self):
        for got, want in zip(game_crops.window([0.4, 0.2, 0.2, 0.4]), (0.35, 0.1, 0.3, 0.6)):
            self.assertAlmostEqual(got, want)
        crop = game_crops.cut(photo(400, 200), [0.25, 0.25, 0.5, 0.5], 1400)
        self.assertEqual(crop.size, (300, 150))   # 0.75 of the photo each way, at full size

    def test_the_box_sits_in_the_middle_two_thirds(self):
        # a blue box on a red photo: blue at 1/6..5/6 of the crop, red around it
        source = Image.new("RGB", (600, 600), RED)
        source.paste(BLUE, (150, 150, 450, 450))
        buf = io.BytesIO()
        source.save(buf, format="PNG")
        buf.seek(0)
        crop = game_crops.cut(buf, [0.25, 0.25, 0.5, 0.5], 1400)
        w, h = crop.size
        self.assertTrue(close(crop.getpixel((w // 2, h // 2)), BLUE))
        self.assertTrue(close(crop.getpixel((round(w / 6) + 3, round(h / 6) + 3)), BLUE))
        self.assertTrue(close(crop.getpixel((round(w / 6) - 4, h // 2)), RED))
        self.assertTrue(close(crop.getpixel((round(5 * w / 6) + 4, h // 2)), RED))

    def test_grey_where_the_window_runs_past_the_photo(self):
        crop = game_crops.cut(photo(1000, 1000), [0.0, 0.0, 0.2, 0.2], 1400)
        self.assertEqual(crop.size, (300, 300))
        self.assertEqual(crop.getpixel((2, 2)), game_crops.GREY)              # before the photo's top left corner
        self.assertEqual(crop.getpixel((2, 150)), game_crops.GREY)            # left of the photo
        self.assertTrue(close(crop.getpixel((150, 150)), RED))                # the beetle

    def test_two_sizes_never_bigger_than_asked(self):
        source = photo(4000, 3000).getvalue()
        small = game_crops.cut(io.BytesIO(source), [0.1, 0.1, 0.8, 0.8], game_crops.SIZES["small"])
        large = game_crops.cut(io.BytesIO(source), [0.1, 0.1, 0.8, 0.8], game_crops.SIZES["large"])
        self.assertEqual(max(small.size), 360)
        self.assertEqual(max(large.size), 1400)
        self.assertAlmostEqual(small.size[0] / small.size[1], 4 / 3, places=1)
        # a small photo isn't blown up
        self.assertEqual(game_crops.cut(photo(100, 100), [0.25, 0.25, 0.5, 0.5], 1400).size, (75, 75))

    def test_a_turned_photo_is_cut_the_way_it_is_shown(self):
        # stored 300 x 100 with "turn 90 degrees" in its EXIF: shown (and boxed) as 100 x 300
        crop = game_crops.cut(photo(300, 100, exif=6), [0.25, 0.25, 0.5, 0.5], 1400)
        self.assertEqual(crop.size, (75, 225))


class CropFileCase(GameCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.enterContext(override_settings(MEDIA_ROOT=self.media))

    def roi(self, taxon=None, validated=True, size=(2000, 1500)):
        sha = uuid.uuid4().hex * 2
        name = f"originals/{sha[:2]}/{sha[2:4]}/{sha}.jpg"
        path = Path(self.media) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(photo(*size).getvalue())
        image = make_image(image_file=name, image_sha256=sha)
        return make_beetle(image=image, taxon=taxon, bbox="validated" if validated else "unvalidated")


class CropFileTests(CropFileCase):
    def test_a_crop_is_cut_once_and_reused(self):
        roi = self.roi(self.t_affinis)
        path = game_crops.ensure(roi, "small")
        self.assertTrue(path.exists())
        self.assertTrue(str(path).startswith(str(Path(self.media) / "crops")))
        with Image.open(path) as im:
            self.assertEqual(max(im.size), 360)
            self.assertEqual(im.format, game_crops.file_format()[0])
        with mock.patch.object(game_crops, "cut") as cut:
            self.assertEqual(game_crops.ensure(roi, "small"), path)
        cut.assert_not_called()
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])   # no half-written file left behind

    def test_a_moved_box_gets_a_new_file(self):
        roi = self.roi(self.t_affinis)
        before = game_crops.ensure(roi, "large")
        Beetles.objects.filter(pk=roi.pk).update(bbox_x=0.3)
        roi.refresh_from_db()
        after = game_crops.ensure(roi, "large")
        self.assertNotEqual(before, after)
        self.assertTrue(before.exists() and after.exists())

    def test_a_photo_that_cannot_be_read_gives_no_crop(self):
        roi = self.roi(self.t_affinis)
        (Path(self.media) / roi.image_asset.image_file.name).write_bytes(b"not a photo")
        with self.assertLogs("beetlesgallery.beetles_app.game_crops", "WARNING"):
            self.assertIsNone(game_crops.ensure(roi, "small"))

    def test_a_new_batch_has_its_crops_cut_on_the_worker(self):
        for _ in range(2):
            self.roi(self.t_affinis, validated=False)
        with mock.patch("beetlesgallery.beetles_app.tasks.prepare_game_crops_task.apply_async") as queued, \
                self.captureOnCommitCallbacks(execute=True):
            rnd, _ = self.play("classify")
        self.assertIn([str(rnd.id)], [c.kwargs["args"] for c in queued.call_args_list])
        # what the worker does: every beetle of the batch, both sizes
        self.assertEqual(game_crops.prepare(rnd.id), 2 * len(rnd.items))
        self.assertEqual(len(list((Path(self.media) / "crops").rglob("*_small.*"))), len(rnd.items))

    def test_a_queue_that_cannot_be_reached_leaves_the_crops_to_be_cut_on_request(self):
        self.roi(self.t_affinis, validated=False)
        with mock.patch("beetlesgallery.beetles_app.tasks.prepare_game_crops_task.apply_async",
                        side_effect=OSError("no broker")), \
                mock.patch.object(game_crops, "prepare") as prepare, self.captureOnCommitCallbacks(execute=True):
            self.play("classify")
        prepare.assert_not_called()


class CropEndpointTests(CropFileCase):
    def crop(self, rnd, size="small", index=0, image=0):
        return self.client.get(reverse("game_crop", args=[rnd.id, index, image, size]))

    def test_the_payload_points_at_the_crops_never_at_the_beetle(self):
        roi = self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        image = item["images"][0]
        self.assertTrue(image["small"].startswith(reverse("game_crop", args=[rnd.id, item["index"], 0, "small"])))
        self.assertTrue(image["large"].startswith(reverse("game_crop", args=[rnd.id, item["index"], 0, "large"])))
        self.assertIn("?v=" + game_crops.crop_key(roi), image["small"])
        self.assertNotIn(str(roi.id), image["small"] + image["large"])
        self.assertEqual(image["url"], roi.display_url)   # the whole photo, for the whole-photo view

    def test_the_crop_is_served_with_long_cache_headers(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        res = self.crop(rnd, index=item["index"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], game_crops.file_format()[2])
        self.assertEqual(res["Cache-Control"], "private, max-age=31536000, immutable")
        with Image.open(io.BytesIO(b"".join(res.streaming_content))) as im:
            self.assertEqual(max(im.size), 360)
        res = self.crop(rnd, "large", index=item["index"])
        with Image.open(io.BytesIO(b"".join(res.streaming_content))) as im:
            self.assertEqual(max(im.size), 600)   # a 2,000 px photo, a box of 0.2: no bigger than the photo has

    def test_only_known_sizes(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.assertEqual(self.crop(rnd, "huge", index=item["index"]).status_code, 404)

    def test_needs_login(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.client.logout()
        self.assertRedirectsToLogin(self.crop(rnd, index=item["index"]))

    def test_only_the_players_own_batch(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.client.force_login(self.staff)
        self.assertEqual(self.crop(rnd, index=item["index"]).status_code, 404)

    def test_only_beetles_of_the_batch_that_can_still_be_played(self):
        roi = self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.assertEqual(self.crop(rnd, index=item["index"], image=1).status_code, 404)    # no second photo
        self.assertEqual(self.crop(rnd, index=len(rnd.items)).status_code, 404)           # no such item
        Beetles.objects.filter(pk=roi.pk).update(is_deleted=True)
        self.assertEqual(self.crop(rnd, index=item["index"]).status_code, 404)

    def test_a_photo_that_cannot_be_cut_is_a_404_so_the_feed_cuts_the_whole_photo_itself(self):
        roi = self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        (Path(self.media) / roi.image_asset.image_file.name).unlink()
        with self.assertLogs("beetlesgallery.beetles_app.game_crops", "WARNING"):
            self.assertEqual(self.crop(rnd, index=item["index"]).status_code, 404)

    def test_grid_tiles_have_crops_too(self):
        tiles = [self.roi(self.t_affinis) for _ in range(3)] + [self.roi(self.t_plat)]
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[{
            "a": str(tiles[3].id), "b": None, "check": True, "tiles": [str(t.id) for t in tiles],
            "rank": "subfamily", "group": {"subfamily": "Scolytinae"}}])
        self.client.force_login(self.user)
        for i in range(4):
            self.assertEqual(self.crop(rnd, index=0, image=i).status_code, 200)
        self.assertEqual(self.crop(rnd, index=0, image=4).status_code, 404)


class BackupTests(SimpleTestCase):
    def test_crops_can_be_cut_again_so_backups_leave_them_out(self):
        from django.conf import settings

        workflow = (Path(settings.BASE_DIR) / ".github" / "workflows" / "backup.yaml").read_text()
        self.assertEqual(workflow.count("media/crops/**"), 2)   # the fast backup and the full one
