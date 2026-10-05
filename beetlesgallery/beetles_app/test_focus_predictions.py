"""
Focus also serves beetles nobody has named yet that the species classifier places in the focus taxon (#375, #340):
at least 50% for a genus, 60% for a tribe or subfamily, from the rank's own confidence or else the top species'.
Only when the focus has no beetles at all does the feed fall back to everything.
"""
from django.test import override_settings

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import Beetles, GamePreference, ModelPrediction, PlayerScore
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class FocusPredictionTests(ScoringCase):
    def setUp(self):
        super().setUp()
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 900, "rating": 0.65})   # genus focus open
        GamePreference.objects.create(player=self.user, focus_rank="genus", focus_value="Xyleborus")
        self.assertEqual(game.player_focus(self.user), ("genus", "Xyleborus"))

    def unnamed(self, taxon=None, confidence=None, said=None):
        """A beetle nobody has named, with a prediction: the top species and/or what the model said per rank."""
        roi = self.roi(validated=False)
        Beetles.objects.filter(pk=roi.pk).update(depicts_valid_name_id=None, taxon=None)
        if taxon is not None or said:
            t = taxon or self.t_plat
            ModelPrediction.objects.create(roi=roi, valid_species_id=t.valid_species_id, taxon=t,
                                           confidence=confidence or 0.1, rank_confidence=said or {},
                                           model_name="m", model_version="1")
        return roi

    def opens(self):
        return set(game.pools(self.user)[1].values_list("id", flat=True))

    def test_a_beetle_the_classifier_places_in_the_genus_is_in_focus(self):
        sure = self.unnamed(self.t_affinis, 0.55)
        unsure = self.unnamed(self.t_affinis, 0.4)
        elsewhere = self.unnamed(self.t_plat, 0.95)
        self.roi(self.t_affinis)   # a validated one too
        opens = self.opens()
        self.assertIn(sure.id, opens)
        self.assertNotIn(unsure.id, opens)
        self.assertNotIn(elsewhere.id, opens)

    def test_the_rank_confidence_counts_when_the_upload_gave_it(self):
        genus_only = self.unnamed(self.t_plat, 0.3, said={"genus": {"value": "Xyleborus", "confidence": 0.7}})
        self.roi(self.t_affinis)
        self.assertIn(genus_only.id, self.opens())

    def test_a_named_beetle_in_the_genus_still_counts(self):
        named = self.roi(self.t_affinis, validated=False)
        self.roi(self.t_affinis)
        self.assertIn(named.id, self.opens())

    @override_settings(GAME_FOCUS_AI_MIN={"subfamily": 0.6, "tribe": 0.6, "genus": 0.3})
    def test_the_threshold_is_a_setting(self):
        roi = self.unnamed(self.t_affinis, 0.4)
        self.roi(self.t_affinis)
        self.assertIn(roi.id, self.opens())

    def test_with_no_unvalidated_beetle_in_focus_the_round_is_still_all_in_focus(self):
        for _ in range(6):
            self.roi(self.t_affinis)
            self.roi(self.t_plat)
            self.unnamed(self.t_plat, 0.9)   # unvalidated, but elsewhere
        rnd = game.start_round(self.user, "classify", size=4)
        self.assertEqual({Beetles.objects.get(id=i["a"]).taxon.genus for i in rnd.items}, {"Xyleborus"})

    def test_with_nothing_in_focus_the_feed_shows_everything(self):
        elsewhere = self.unnamed(self.t_plat, 0.9)
        self.roi(self.t_plat)
        self.assertIn(elsewhere.id, self.opens())
