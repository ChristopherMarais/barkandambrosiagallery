"""
#424, now through the review (#488): after an answer, and under Back, a validated beetle shows its true name and how
reliable that name is (Taxonomist ID, Expert ID...). A beetle nobody has validated shows what other players say instead,
and a skip shows no truth.
"""
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


@override_settings(GAME_ROUND_SIZE=1)
class BackTruthTests(GameCase):
    def answer(self, mode, body):
        rnd, item = self.play(mode)
        return self.post("game_answer", dict(body, index=item["index"]), rnd.id).json()["review"]

    def test_a_validated_beetle_comes_back_with_its_name_and_tier(self):
        roi = self.roi(self.t_affinis)
        Beetles.objects.filter(pk=roi.pk).update(label_source="taxonomist")
        review = self.answer("classify", AFFINIS)
        self.assertEqual(review["classify"]["truth"], {"name": "Xyleborus affinis", "rank": "species", "tier": "Taxonomist ID"})

    def test_skipping_shows_no_truth_and_an_unvalidated_beetle_shows_no_name(self):
        self.roi(self.t_affinis)
        skipped = self.answer("classify", {"skipped": True})
        self.assertTrue(skipped["skipped"])
        self.assertNotIn("classify", skipped)
        Beetles.objects.all().delete()
        self.roi(self.t_affinis, validated=False)
        self.assertIsNone(self.answer("classify", AFFINIS)["classify"]["truth"])

    def test_similarity_names_a_and_b_in_the_order_shown(self):
        self.roi(self.t_affinis, validated=False)
        self.roi(self.t_ferr)
        sides = self.answer("pair", {"pair_answer": "genus"})["pair"]["sides"]
        self.assertEqual(len(sides), 2)
        self.assertEqual([s["validated"] for s in sides].count(False), 1)   # the unvalidated one
        self.assertIn("Xyleborus ferrugineus", [s["name"] for s in sides if s["validated"]])

    def test_the_back_sheet_has_room_for_it_and_its_button_just_says_current_beetle(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn('id="previous-review"', page)
        self.assertIn('class="btn-primary w-full h-12 text-base rounded-xl">Current beetle</button>', page)
        self.assertNotIn("Back to the current beetle", page)
