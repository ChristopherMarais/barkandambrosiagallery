"""
Issue #421: the photo panel. Search on every list, Light unlocked at level 4 and out of the way of the other
controls, a crop with its box and some of the photo around it (no "Photo edge" pill), zoom, a Report for every photo
and a marker on the photo to name.
"""
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game_levels
from beetlesgallery.beetles_app.models import GameReport, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image


class PhotoPanelPageTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["classify"])).content.decode()

    def test_every_list_can_be_searched(self):
        self.assertIn("const FIND_MIN = 0;", self.page())

    def test_light_sits_bottom_left_and_waits_for_its_level(self):
        page = self.page()
        self.assertIn('id="light-btn" class="hidden"', page)
        self.assertIn("#light-btn { position: absolute; left: 0.375rem; bottom: 0.375rem;", page)
        self.assertIn('$("light-btn").classList.toggle("hidden", !p.light)', page)

    def test_the_crop_can_be_zoomed_and_the_photo_to_name_is_marked(self):
        page = self.page()
        self.assertIn("makeZoomable(frame, canvas", page)
        self.assertIn('data-testid="primary-photo"', page)
        self.assertNotIn('$("report-cog").classList.toggle("hidden", galleryAt !== 0)', page)   # every photo can be reported


class LightUnlockTests(GameCase):
    def test_light_is_a_level_four_unlock(self):
        self.assertEqual(game_levels.perk_level(game_levels.LIGHT), 4)
        self.client.force_login(self.user)
        from beetlesgallery.beetles_app.game_views import _prefs
        self.assertFalse(_prefs(self.user)["light"])
        PlayerScore.objects.create(player=self.user, score=450, rating=0.6)
        self.assertTrue(_prefs(self.user)["light"])


@override_settings(GAME_ROUND_SIZE=1)
class ReportOtherPhotoTests(GameCase):
    def test_a_beetles_other_photo_can_be_reported(self):
        one = make_beetle(image=make_image(image_file="originals/aa/bb/a.jpg"), taxon=self.t_affinis, bbox="validated",
                           depicts_specimen="SPEC-1")
        two = make_beetle(image=make_image(image_file="originals/aa/bb/b.jpg"), taxon=self.t_affinis, bbox="validated",
                            depicts_specimen="SPEC-1")
        rnd, item = self.play("classify")
        other = two if rnd.items[item["index"]]["a"] == str(one.id) else one   # whichever wasn't served
        res = self.post("game_report_item", {"round": str(rnd.id), "index": item["index"], "image": 0, "photo": 1,
                                             "reason": "bad_box"})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(GameReport.objects.get().roi_id, other.id)
        res = self.post("game_report_item", {"round": str(rnd.id), "index": item["index"], "image": 0, "photo": 5,
                                             "reason": "bad_box"})
        self.assertEqual(res.status_code, 400)
