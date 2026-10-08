"""
Back draws the last beetle's photos from the crops the feed showed them with, not from the whole photos: the review
names the same crop addresses as the item did, so the page has them already, and a grid's tiles no longer download a
whole photo each. A big grid's sharp crops wait until a photo is zoomed, as in the feed.
"""
from pathlib import Path

from django.test import SimpleTestCase

from beetlesgallery.beetles_app import game, game_crops
from beetlesgallery.beetles_app.game_views import _item_images
from beetlesgallery.beetles_app.models import GameRound
from beetlesgallery.beetles_app.test_game_crops import CropFileCase
from beetlesgallery.beetles_app.test_r7_review import GAME_PLAY, js_function


class BackCropTests(CropFileCase):
    def answer(self, item, body):
        rnd = GameRound.objects.create(player=self.user, mode=item.get("mode", "classify"), items=[item])
        self.client.force_login(self.user)
        res = self.post("game_answer", dict(body, index=0), rnd.id)
        self.assertEqual(res.status_code, 200, res.content)
        return rnd, res.json()["review"]

    def feed_images(self, rnd):
        return _item_images(rnd, 0)   # what the feed showed

    def test_a_pair_s_review_names_the_crops_the_feed_showed_in_the_order_shown(self):
        a, b = self.roi(self.t_affinis), self.roi(self.t_ferr)
        for flip in (False, True):
            rnd, review = self.answer({"a": str(a.id), "b": str(b.id), "check": True, "flip": flip, "mode": "pair"},
                                      {"pair_answer": "genus"})
            feed = self.feed_images(rnd)
            self.assertEqual([(im["small"], im["large"]) for im in review["images"]],
                             [(im["small"], im["large"]) for im in feed])
            self.assertEqual([im["url"] for im in review["images"]], [im["url"] for im in feed])   # whole photo kept
            self.assertFalse(review["sharp_on_zoom"])

    def test_each_crop_address_serves_that_photo_s_own_crop(self):
        a, b = self.roi(self.t_affinis), self.roi(self.t_ferr)
        _, review = self.answer({"a": str(a.id), "b": str(b.id), "check": True, "flip": True, "mode": "pair"},
                                {"pair_answer": "genus"})
        for im, roi in zip(review["images"], (b, a)):   # flipped: B shown first
            res = self.client.get(im["small"])
            self.assertEqual(res.status_code, 200)
            self.assertEqual(b"".join(res.streaming_content), Path(game_crops.crop_path(roi, "small")).read_bytes())

    def test_a_grid_s_tiles_have_their_crops_and_a_big_one_sharpens_on_zoom(self):
        members = [self.roi(self.t_affinis) for _ in range(9)]
        others = [self.roi(self.t_plat) for _ in range(7)]
        for tiles, big in ((members[:3] + others[:1], False), (members + others, True)):
            item = {"a": str(members[0].id), "b": None, "check": True, "mode": "select", "rank": "species",
                    "tiles": [str(t.id) for t in tiles], "group": game.lineage(self.t_affinis, "species")}
            rnd, review = self.answer(item, {"picks": [0]})
            self.assertEqual(len(review["images"]), len(tiles))
            self.assertEqual([im["small"] for im in review["images"]], [im["small"] for im in self.feed_images(rnd)])
            self.assertTrue(all(im["large"] for im in review["images"]))
            self.assertEqual(review["sharp_on_zoom"], big)


class PageTests(SimpleTestCase):
    def test_back_loads_the_crops_and_sharpens_a_big_grid_on_zoom(self):
        page = GAME_PLAY.read_text(encoding="utf-8")
        cell = js_function(page, "reviewCell")
        self.assertIn("const loaded = im ? loadCrop(im) : null;", cell)    # the small crop (whole photo if it fails)
        self.assertIn("makeZoomable(frame, canvas, () => openReviewPhoto(c), false, sharpOnZoom ? sharpen : null);",
                      cell)
        self.assertIn("if (!sharpOnZoom) sharpen();", cell)
        self.assertIn("reviewCell(im, i, n, !!previous.sharp_on_zoom)", page)
