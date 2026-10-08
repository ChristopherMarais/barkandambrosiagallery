"""
A big grid (16 or 25 beetles) shows each beetle smaller than its small crop on any screen, so its sharp crops are never
cut or loaded ahead: a tile loads its own once it is zoomed. They were most of the work of a big grid on the worker
(a large crop takes two to four times as long to cut as a small one) and most of its download on a phone.
"""
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app import game_crops, game_views
from beetlesgallery.beetles_app.models import GameRound
from beetlesgallery.beetles_app.test_game import GameCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


def grid(rois, mode="select"):
    ids = [str(r.id) for r in rois]
    return {"a": ids[0], "b": None, "check": True, "mode": mode, "tiles": ids, "rank": "genus",
            "group": {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus"}, "size": len(ids)}


class SizesTests(SimpleTestCase):
    def test_only_the_small_crops_of_a_big_grid_ahead(self):
        for tiles, sizes in ((4, ("small", "large")), (9, ("small", "large")), (16, ("small",)), (25, ("small",))):
            item = {"tiles": [str(n) for n in range(tiles)]}
            self.assertEqual(game_crops.sizes_ahead(item), sizes, tiles)
            self.assertEqual(game_crops.sharp_on_zoom(item), tiles >= 16)
        self.assertEqual(game_crops.sizes_ahead({"a": "x", "b": "y"}), ("small", "large"))   # a photo or a pair


class BigGridCase(GameCase):
    def setUp(self):
        super().setUp()
        self.big = [self.roi(self.t_affinis) for _ in range(16)]
        self.one = self.roi(self.t_ferr)
        self.rnd = GameRound.objects.create(player=self.user, mode="mixed", items=[
            {"a": str(self.one.id), "b": None, "check": True, "mode": "classify"}, grid(self.big)])


class WorkerTests(BigGridCase):
    def test_the_worker_cuts_a_big_grids_small_crops_only(self):
        with mock.patch.object(game_crops, "ensure", return_value=Path("x")) as ensure:
            game_crops.prepare(self.rnd.id)
        cut = [(str(c.args[0].id), c.args[1]) for c in ensure.call_args_list]
        self.assertEqual(cut[:17], [(str(self.one.id), "small")] + [(str(r.id), "small") for r in self.big])
        self.assertEqual(cut[17:], [(str(self.one.id), "large")])   # small ones first, then the photo's sharp one

    def test_a_beetle_in_a_big_grid_and_a_small_item_gets_both(self):
        items = [grid(self.big), {"a": str(self.big[0].id), "b": None, "check": True}]
        self.assertEqual(dict((str(r.id), s) for r, s in game_crops.ahead(items))[str(self.big[0].id)], ["small", "large"])


class FeedTests(BigGridCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_the_next_big_grid_is_loaded_ahead_small_only(self):
        payload = game_views._item_payload(self.rnd, 0)
        coming = game_views._item_images(self.rnd, 1)
        self.assertEqual(payload["prefetch"], [im["small"] for im in coming])
        self.assertNotIn("sharp_on_zoom", payload)

    def test_the_big_grid_says_its_sharp_crops_wait_for_a_zoom(self):
        payload = game_views._item_payload(self.rnd, 1)
        self.assertTrue(payload["sharp_on_zoom"])
        self.assertTrue(all(im["large"] for im in payload["images"]))   # there to load once a tile is zoomed

    def test_the_look_ahead_lists_no_sharp_crops_for_it(self):
        data = self.client.get(reverse("game_upcoming", args=[self.rnd.id]), {"index": 0}).json()
        self.assertEqual(data["items"][0]["index"], 1)
        self.assertEqual(len(data["items"][0]["small"]), 16)
        self.assertEqual(data["items"][0]["large"], [])


class PageTests(SimpleTestCase):
    def test_a_tile_loads_its_sharp_crop_once_zoomed(self):
        zoomable = js_function("makeZoomable")
        self.assertIn("function makeZoomable(frame, canvas, onTap, immediate, onZoom)", zoomable)
        self.assertIn("if (z > 1 && onZoom) { const zoomed = onZoom; onZoom = null; zoomed(); }", zoomable)
        show = js_function("showItem")
        self.assertIn("const sharpOnZoom = !!item.sharp_on_zoom;", show)
        self.assertIn("if (!sharpOnZoom) sharpen();", show)
        self.assertIn("sharpOnZoom ? sharpen : null", show)
