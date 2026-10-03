"""
Answers on beetles nobody has validated earn points straight away when they match a reference: what proven experts
said, or the model's name where the model is sure and has been right about that taxon at that rank (#395).
"""
from django.core.cache import cache
from django.test import override_settings

from beetlesgallery.beetles_app import game_reference, game_scoring, game_trust
from beetlesgallery.beetles_app.models import AnswerPoints, ModelPrediction
from beetlesgallery.beetles_app.test_game import TrustCase

GENUS_ONLY = dict(subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus")


@override_settings(GAME_REF_MODEL_MIN_CHECKED=3, GAME_REF_MODEL_MIN_CONFIDENCE=0.9, GAME_REF_MODEL_MIN_PRECISION=0.95)
class ReferenceTests(TrustCase):
    def predict(self, roi, genus="Xyleborus", conf=0.97, species=None, model="m1"):
        species = species or self.t_affinis
        return ModelPrediction.objects.create(
            roi=roi, valid_species_id=species.valid_species_id, taxon=species, confidence=0.5, model_name=model,
            rank_confidence={"genus": {"value": genus, "confidence": conf}})

    def track_record(self, n=3, genus="Xyleborus", on=None):
        """The model's sure calls on validated beetles: right n times."""
        for _ in range(n):
            self.predict(self.roi(on or self.t_affinis), genus=genus)
        cache.delete(game_reference.PRECISION_CACHE)

    def points(self, answer):
        game_scoring.recompute([answer.player_id])
        return AnswerPoints.objects.get(answer=answer)

    def test_a_trusted_model_name_earns_points_on_an_unvalidated_beetle(self):
        self.track_record()
        target = self.roi(validated=False)
        self.predict(target)
        row = self.points(self.answer(self.user, target, check=False, **GENUS_ONLY))
        genus = row.detail["reference"]["genus"]
        self.assertEqual((genus["name"], genus["source"], genus["match"]), ("Xyleborus", "model", True))
        expected = 0.6 * game_scoring.RANK_POINTS["genus"] * game_scoring.classify_weight()
        self.assertGreaterEqual(row.points, expected)

    def test_a_model_without_a_track_record_for_that_taxon_is_not_used(self):
        self.track_record(n=2)                                      # fewer sure calls than required
        target = self.roi(validated=False)
        self.predict(target)
        row = self.points(self.answer(self.user, target, check=False, **GENUS_ONLY))
        self.assertNotIn("reference", row.detail)

    def test_a_model_wrong_about_that_taxon_is_not_used(self):
        self.track_record(n=3, on=self.t_xylo)                      # it calls Xylosandrus beetles "Xyleborus"
        target = self.roi(validated=False)
        self.predict(target)
        row = self.points(self.answer(self.user, target, check=False, **GENUS_ONLY))
        self.assertNotIn("reference", row.detail)

    def test_an_unsure_model_is_not_used(self):
        self.track_record()
        target = self.roi(validated=False)
        self.predict(target, conf=0.6)
        row = self.points(self.answer(self.user, target, check=False, **GENUS_ONLY))
        self.assertNotIn("reference", row.detail)

    def test_disagreeing_with_the_reference_costs_nothing(self):
        self.track_record()
        target = self.roi(validated=False)
        self.predict(target)
        row = self.points(self.answer(self.user, target, check=False, subfamily="Scolytinae", tribe="Xyleborini",
                                      genus="Xylosandrus"))
        self.assertFalse(row.detail["reference"]["genus"]["match"])
        self.assertGreaterEqual(row.points, 0)

    def test_a_proven_experts_name_is_a_reference_but_not_for_themselves(self):
        expert = self.staff
        self.prove(expert, self.t_affinis)
        target = self.roi(validated=False)
        own = self.answer(expert, target, check=False, subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                          species="affinis")
        mine = self.answer(self.user, target, check=False, **GENUS_ONLY)
        row = self.points(mine)
        self.assertEqual(row.detail["reference"]["genus"]["source"], "expert")
        self.assertNotIn("reference", self.points(own).detail)

    def test_it_never_counts_towards_accuracy(self):
        self.track_record()
        target = self.roi(validated=False)
        self.predict(target)
        ans = self.answer(self.user, target, check=False, **GENUS_ONLY)
        self.points(ans)
        ans.refresh_from_db()
        self.assertEqual([getattr(ans, f"correct_{r}") for r in ("subfamily", "tribe", "genus", "species")],
                         [None, None, None, None])
