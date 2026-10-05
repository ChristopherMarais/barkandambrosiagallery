"""Issue #427: curators can find the labels the game wrote into the database, and put the earlier label back."""
from django.urls import reverse

from beetlesgallery.beetles_app import game_applied
from beetlesgallery.beetles_app.models import Beetles, LabelReview
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.test_game_queue import URL
from beetlesgallery.beetles_app.testing import make_beetle, make_image


class AppliedLabelTests(GameCase):
    def setUp(self):
        super().setUp()
        self.beetle = make_beetle(image=make_image(), taxon=self.t_ferr, bbox="unvalidated",
                                  label_source=Beetles.LabelSource.COMMUNITY)
        self.client.force_login(self.staff)

    def apply(self, by=None):
        """What accepting a proposal (by a curator) or auto-apply (by=None) does to the beetle."""
        self.beetle.depicts_valid_name_id = self.t_affinis.valid_species_id
        self.beetle.label_source = Beetles.LabelSource.EXPERT
        self.beetle.save()
        LabelReview.objects.create(roi=self.beetle, decision=LabelReview.Decision.ACCEPTED, reviewed_by=by,
                                   taxon=self.t_affinis, genus="Xyleborus", species="affinis", answers=4)

    def revert(self):
        return self.client.post(reverse("game_applied_revert", args=[self.beetle.id]))

    def test_an_automatic_label_is_listed_and_can_be_reverted(self):
        self.apply()
        self.assertIn(self.beetle.id, list(game_applied.applied_rois().values_list("id", flat=True)))
        data = self.client.get(reverse("game_proposals"), {"image_asset": self.beetle.image_asset_id}).json()
        applied = data["applied"][str(self.beetle.id)]
        self.assertEqual((applied["automatic"], applied["current"], applied["name"]), (True, True, "Xyleborus affinis"))

        res = self.revert()
        self.assertEqual(res.status_code, 200, res.content)
        self.beetle.refresh_from_db()
        self.assertEqual(self.beetle.depicts_valid_name_id, self.t_ferr.valid_species_id)   # the earlier label is back
        self.assertEqual(self.beetle.taxon, self.t_ferr)
        self.assertEqual(self.beetle.label_source, Beetles.LabelSource.COMMUNITY)
        newest = LabelReview.objects.filter(roi=self.beetle).first()
        self.assertEqual((newest.decision, newest.reviewed_by), (LabelReview.Decision.DISMISSED, self.staff))
        self.assertFalse(game_applied.applied_rois().filter(id=self.beetle.id).exists())
        self.assertEqual(self.revert().status_code, 409)   # nothing left to revert

    def test_a_curator_accepted_label_can_be_reverted_too(self):
        self.apply(by=self.superuser)
        self.assertEqual(self.revert().status_code, 200)
        self.beetle.refresh_from_db()
        self.assertEqual(self.beetle.taxon, self.t_ferr)

    def test_a_label_changed_since_is_left_alone(self):
        self.apply()
        self.beetle.depicts_valid_name_id = self.t_plat.valid_species_id
        self.beetle.save()
        res = self.revert()
        self.assertEqual(res.status_code, 409)
        self.assertIn("changed since", res.json()["error"])
        self.beetle.refresh_from_db()
        self.assertEqual(self.beetle.taxon, self.t_plat)

    def test_the_annotation_page_can_filter_to_them(self):
        self.apply()
        other = make_beetle(image=make_image(), taxon=self.t_ferr, bbox="unvalidated")
        res = self.client.get(f"{URL}?game=applied")
        self.assertEqual(res.status_code, 200, res.content)
        ids = {str(r["image_asset_id"]) for r in res.json()["results"]}
        self.assertIn(str(self.beetle.image_asset_id), ids)
        self.assertNotIn(str(other.image_asset_id), ids)

    def test_players_cannot_revert(self):
        self.apply()
        self.client.force_login(self.user)
        self.assertIn(self.revert().status_code, (302, 403))
