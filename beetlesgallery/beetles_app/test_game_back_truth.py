"""
#424: Back shows your last beetle and what you said; for a validated beetle also its true name and how reliable that
name is (Taxonomist ID, Expert ID...). A beetle nobody has validated shows what other players said instead.
"""
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


@override_settings(GAME_ROUND_SIZE=1)
class BackTruthTests(GameCase):
    def answer(self, mode, body):
        rnd, item = self.play(mode)
        return self.post("game_answer", dict(body, index=item["index"]), rnd.id).json()

    def test_a_validated_beetle_comes_back_with_its_name_and_tier(self):
        roi = self.roi(self.t_affinis)
        Beetles.objects.filter(pk=roi.pk).update(label_source="taxonomist")
        data = self.answer("classify", AFFINIS)
        self.assertEqual(data["verified"], [{"name": "Xyleborus affinis", "rank": "species", "tier": "Taxonomist ID"}])

    def test_skipping_still_shows_it_and_an_unvalidated_beetle_shows_nothing(self):
        self.roi(self.t_affinis)
        self.assertEqual(self.answer("classify", {"skipped": True})["verified"][0]["name"], "Xyleborus affinis")
        Beetles.objects.all().delete()
        self.roi(self.t_affinis, validated=False)
        self.assertEqual(self.answer("classify", AFFINIS)["verified"], [])

    def test_similarity_names_a_and_b_in_the_order_shown(self):
        self.roi(self.t_affinis, validated=False)
        self.roi(self.t_ferr)
        verified = self.answer("pair", {"pair_answer": "genus"})["verified"]
        self.assertEqual(len(verified), 2)
        self.assertEqual([v["name"] if v else None for v in verified].count(None), 1)   # the unvalidated one
        self.assertIn("Xyleborus ferrugineus", [v["name"] for v in verified if v])

    def test_the_back_sheet_has_room_for_it_and_its_button_just_says_current_beetle(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn('id="previous-truth"', page)
        self.assertIn(">Current beetle</button>", page)
        self.assertNotIn("Back to the current beetle", page)
