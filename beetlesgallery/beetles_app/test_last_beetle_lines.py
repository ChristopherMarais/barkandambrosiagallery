"""
The "Last beetle" bar after an Identification answer also says what proven experts said and what the species
classifier leans to (#382): as agreement, never "correct", and the same for validated and unvalidated beetles.
"""
from django.contrib.auth import get_user_model

from beetlesgallery.beetles_app.game_views import _community
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, ModelPrediction, PlayerSkill
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR, GameCase

PROVEN_FOR_XYLEBORUS = [("subfamily", ""), ("tribe", "Scolytinae"), ("genus", "Xyleborini"), ("species", "Xyleborus")]


class LastBeetleLineTests(GameCase):
    def setUp(self):
        super().setUp()
        self.beetle = self.roi(self.t_affinis, validated=False)

    def expert(self, name):
        user = get_user_model().objects.create_user(name, password="pw")
        for rank, branch in PROVEN_FOR_XYLEBORUS:
            PlayerSkill.objects.create(player=user, rank=rank, branch=branch, correct=20, judged=20, proven=True)
        return user

    def answer(self, player, roi=None, **ranks):
        rnd = GameRound.objects.create(player=player, mode="classify", items=[])
        return GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=roi or self.beetle,
                                         **ranks)

    def predict(self, taxon, confidence, said=None, roi=None):
        ModelPrediction.objects.create(roi=roi or self.beetle, valid_species_id=taxon.valid_species_id, taxon=taxon,
                                       confidence=confidence, rank_confidence=said or {}, model_name="m", model_version="1")

    def test_an_expert_who_agrees_says_so_and_never_correct(self):
        self.answer(self.expert("e"), **AFFINIS)
        out = _community(self.answer(self.user, **AFFINIS))
        self.assertEqual(out["experts"], "A proven expert agrees with you to species.")
        self.assertNotIn("correct", " ".join(str(v) for v in out.values()).lower())

    def test_where_experts_differ_the_bar_says_what_they_said(self):
        self.answer(self.expert("e"), **AFFINIS)
        self.answer(self.expert("f"), **AFFINIS)
        out = _community(self.answer(self.user, **FERR))
        self.assertEqual(out["experts"], "2 proven experts agree with you to genus; on species they said Xyleborus affinis.")

    def test_where_the_player_stopped_the_bar_says_how_far_experts_went(self):
        self.answer(self.expert("e"), **AFFINIS)
        out = _community(self.answer(self.user, subfamily="Scolytinae", tribe="Xyleborini"))
        self.assertEqual(out["experts"], "A proven expert agrees with you to tribe and went on to genus Xyleborus.")

    def test_split_experts_are_reported_as_split(self):
        self.answer(self.expert("e"), **AFFINIS)
        self.answer(self.expert("f"), **FERR)
        out = _community(self.answer(self.user, **AFFINIS))
        self.assertEqual(out["experts"], "2 proven experts agree with you to genus; they're split on species.")

    def test_players_who_are_not_proven_are_not_experts(self):
        self.answer(get_user_model().objects.create_user("x", password="pw"), **AFFINIS)
        self.assertNotIn("experts", _community(self.answer(self.user, **AFFINIS)))

    def test_the_classifier_line_is_its_deepest_rank_at_half_or_more(self):
        self.predict(self.t_affinis, 0.64)
        self.assertEqual(_community(self.answer(self.user, **AFFINIS))["model"],
                         "The species classifier leans Xyleborus affinis (64%).")

    def test_the_classifier_line_uses_a_rank_it_was_surer_of(self):
        self.predict(self.t_affinis, 0.3, said={"genus": {"value": "Xyleborus", "confidence": 0.71}})
        self.assertEqual(_community(self.answer(self.user, **AFFINIS))["model"],
                         "The species classifier leans genus Xyleborus (71%).")

    def test_no_prediction_no_line(self):
        self.assertNotIn("model", _community(self.answer(self.user, **AFFINIS)))

    def test_a_validated_beetle_gets_the_same_lines(self):
        checked = self.roi(self.t_affinis)
        self.answer(self.expert("e"), roi=checked, **AFFINIS)
        self.predict(self.t_affinis, 0.8, roi=checked)
        out = _community(self.answer(self.user, roi=checked, is_check=True, **AFFINIS))
        self.assertEqual(out["experts"], "A proven expert agrees with you to species.")
        self.assertEqual(out["model"], "The species classifier leans Xyleborus affinis (80%).")
