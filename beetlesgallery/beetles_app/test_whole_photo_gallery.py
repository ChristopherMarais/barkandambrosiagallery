"""
The whole photo goes through every photo of the specimen by default (#601), in play and from the review; each photo
shows its thumbnail first and its box only once that photo is on screen.
"""
from django.urls import reverse

from beetlesgallery.beetles_app import game_levels
from beetlesgallery.beetles_app.game_views import _item_payload
from beetlesgallery.beetles_app.models import GamePreference, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image


class GalleryDataTests(ReviewCase):
    def photo(self, specimen, thumb="", **fields):
        image = make_image(image_file="originals/aa/bb/test.jpg", thumb_small=thumb)
        return make_beetle(image=image, taxon=self.t_affinis, bbox="validated", depicts_specimen=specimen, **fields)

    def unlock(self):
        GamePreference.objects.create(player=self.user, granted_perks=[game_levels.SPECIMEN_PHOTOS])

    def test_the_feed_gives_each_photo_its_thumbnail(self):
        main = self.photo("UF-1", thumb="thumbnails/aa/bb/main_96.webp")
        self.photo("UF-1", thumb="thumbnails/aa/bb/side_96.webp", aspect="lateral")
        self.unlock()
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[{"a": str(main.id), "b": None, "check": True}])
        image = _item_payload(rnd, 0)["images"][0]
        self.assertTrue(image["thumb"].endswith("main_96.webp"))
        self.assertTrue(image["photos"][0]["thumb"].endswith("side_96.webp"))

    def test_no_thumbnail_is_an_empty_string(self):
        main = self.photo("")
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[{"a": str(main.id), "b": None, "check": True}])
        self.assertEqual(_item_payload(rnd, 0)["images"][0]["thumb"], "")

    def test_the_review_lists_the_specimen_s_other_photos_once_unlocked(self):
        main = self.photo("UF-2", thumb="thumbnails/aa/bb/m_96.webp")
        self.photo("UF-2", aspect="lateral")
        image, = self.classify(main, AFFINIS)["images"]
        self.assertTrue(image["thumb"].endswith("m_96.webp"))
        self.assertNotIn("photos", image)   # still locked
        self.unlock()
        image, = self.classify(main, AFFINIS)["images"]
        self.assertEqual([p["aspect"] for p in image["photos"]], ["lateral"])


class PageTests(ReviewCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_every_photo_by_default_with_a_counter(self):
        page = self.page()
        self.assertIn("gallery = photos || galleryOf(own);", page)
        self.assertIn("function galleryOf(im)", page)
        self.assertIn('(galleryAt + 1) + " / " + gallery.length', page)

    def test_the_box_waits_for_its_photo_and_the_thumbnail_comes_first(self):
        page = self.page()
        self.assertIn('b.classList.add("hidden");', page)
        self.assertIn('b.classList.remove("hidden");', page)
        self.assertIn("if (p.thumb) loadImage(p.thumb).then(soft, () => {});", page)
        self.assertIn("#lightbox-box.hidden { display: none; }", page)
