"""
The photos never move while a beetle is answered. Two things moved them: the tip under the photos ("Pick the odd one
out") came and went, and the panel grew or shrank by its line; and Naming's tile took its shape from the photo only once
the photo was in, then grew again when the sharp crop took the small one's place (the canvas set its size). Now the
tip keeps its line while answering, and Naming's tile has the crop's shape from the item and a size of its own.
"""
from pathlib import Path

from django.test import SimpleTestCase

from beetlesgallery.beetles_app import game_crops
from beetlesgallery.beetles_app.models import GameRound, ImageAsset
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.test_r7_review import js_function

PAGE = (Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


class ShapeTests(GameCase):
    def test_the_crop_keeps_the_box_s_shape_on_the_photo(self):
        roi = self.roi(self.t_affinis)
        ImageAsset.objects.filter(pk=roi.image_asset_id).update(image_width=4000, image_height=3000)
        roi.refresh_from_db()
        expected = round(float(roi.bbox_width) * 4000 / (float(roi.bbox_height) * 3000), 4)
        self.assertEqual(game_crops.aspect(roi), expected)

    def test_no_shape_without_the_photo_s_size(self):
        roi = self.roi(self.t_affinis)
        ImageAsset.objects.filter(pk=roi.image_asset_id).update(image_width=None, image_height=None)
        roi.refresh_from_db()
        self.assertIsNone(game_crops.aspect(roi))

    def test_each_photo_of_an_item_says_its_shape(self):
        roi = self.roi(self.t_affinis)
        ImageAsset.objects.filter(pk=roi.image_asset_id).update(image_width=2000, image_height=1000)
        roi.refresh_from_db()
        rnd = GameRound.objects.create(player=self.user, mode="classify",
                                       items=[{"a": str(roi.id), "b": None, "check": True}])
        from beetlesgallery.beetles_app.game_views import _item_images

        (image,) = _item_images(rnd, 0)
        self.assertEqual(image["ar"], game_crops.aspect(roi))


class PageTests(SimpleTestCase):
    def test_the_tip_keeps_its_line_while_answering(self):
        update = js_function(PAGE, "updateSubmit")
        self.assertIn('$("submit-hint").textContent = hint || "\\u00a0";', update)
        self.assertIn('$("submit-hint").classList.toggle("hidden", onReview);', update)        # gone on the review only
        self.assertIn('$("submit-hint").classList.toggle("invisible", !onReview && !hint);', update)
        self.assertNotIn('classList.toggle("hidden", !hint)', update)

    def test_naming_s_tile_has_its_shape_before_the_photo_and_a_size_of_its_own(self):
        self.assertIn("width: min(100%, calc((min(45vh, 100cqh) - 1rem) * var(--ar, 1.3333)));", PAGE)
        self.assertNotIn("flex: 0 1 auto; width: auto; height: auto;", PAGE)   # the canvas no longer sizes it
        self.assertIn('if (MODE === "classify" && im.ar) photoShape(cell, im.ar);', PAGE)
        shape = js_function(PAGE, "photoShape")
        self.assertIn('cell.style.setProperty("--photo-ar", String(ar));', shape)
        self.assertIn('cell.style.setProperty("--ar", String(ar));', shape)
        # the photo itself only reshapes the tile when the item had no shape or a different one
        self.assertIn('(!im.ar || Math.abs(ar - im.ar) > 0.02 * im.ar)) photoShape(cell, ar);', PAGE)
