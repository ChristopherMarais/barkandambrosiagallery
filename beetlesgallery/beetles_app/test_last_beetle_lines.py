"""
#382, now in the review (#488): after an Identification answer on a beetle nobody has validated, the card says how many
proven experts back what the other players say, and IBBI-AI's guess at each rank with its confidence. A validated
beetle shows its true name instead (the owner decided a beetle may show as validated once the answer is in).
"""
from django.contrib.auth import get_user_model

from beetlesgallery.beetles_app import game, game_answer_review, game_scoring
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

    def card(self, answer):
        return game_answer_review.review(answer, {})

    def rank(self, answer, rank):
        return next(r for r in self.card(answer)["classify"]["ranks"] if r["rank"] == rank)

    def predict(self, taxon, confidence, said=None, roi=None):
        ModelPrediction.objects.create(roi=roi or self.beetle, valid_species_id=taxon.valid_species_id, taxon=taxon,
                                       confidence=confidence, rank_confidence=said or {}, model_name="m", model_version="1")

    def test_an_expert_who_agrees_shows_and_it_never_says_correct(self):
        self.answer(self.expert("e"), **AFFINIS)
        card = self.card(self.answer(self.user, **AFFINIS))
        species = card["classify"]["ranks"][3]["players"]
        self.assertEqual((species["name"], species["experts"], species["agrees"]), ("Xyleborus affinis", 1, True))
        self.assertNotIn("correct", card["headline"].lower())

    def test_where_experts_differ_the_card_says_what_they_said(self):
        self.answer(self.expert("e"), **AFFINIS)
        self.answer(self.expert("f"), **AFFINIS)
        species = self.rank(self.answer(self.user, **FERR), "species")["players"]
        self.assertEqual((species["name"], species["experts"], species["agrees"]), ("Xyleborus affinis", 2, False))

    def test_where_the_player_stopped_the_card_says_how_far_experts_went(self):
        self.answer(self.expert("e"), **AFFINIS)
        genus = self.rank(self.answer(self.user, subfamily="Scolytinae", tribe="Xyleborini"), "genus")
        self.assertEqual((genus["yours"], genus["players"]["name"], genus["players"]["agrees"]), ("", "Xyleborus", None))

    def test_split_experts_back_nothing(self):
        self.answer(self.expert("e"), **AFFINIS)
        self.answer(self.expert("f"), **FERR)
        mine = self.answer(self.user, **AFFINIS)
        self.assertEqual(self.rank(mine, "species")["players"]["experts"], 0)
        self.assertEqual(self.rank(mine, "genus")["players"]["experts"], 2)

    def test_players_who_are_not_proven_are_not_experts(self):
        self.answer(get_user_model().objects.create_user("x", password="pw"), **AFFINIS)
        self.assertEqual(self.rank(self.answer(self.user, **AFFINIS), "species")["players"]["experts"], 0)

    def test_ibbi_ai_shows_its_guess_at_each_rank(self):
        self.predict(self.t_affinis, 0.64)
        self.assertEqual(self.rank(self.answer(self.user, **AFFINIS), "species")["ai"],
                         {"name": "Xyleborus affinis", "sure": 64, "agrees": True})

    def test_ibbi_ai_uses_a_rank_it_was_surer_of(self):
        self.predict(self.t_affinis, 0.3, said={"genus": {"value": "Xyleborus", "confidence": 0.71}})
        self.assertEqual(self.rank(self.answer(self.user, **AFFINIS), "genus")["ai"]["sure"], 71)

    def test_no_prediction_no_ibbi_ai(self):
        self.assertTrue(all(r["ai"] is None for r in self.card(self.answer(self.user, **AFFINIS))["classify"]["ranks"]))

    def test_a_validated_beetle_shows_its_name_instead(self):
        checked = self.roi(self.t_affinis)
        self.predict(self.t_affinis, 0.8, roi=checked)
        answer = self.answer(self.user, roi=checked, is_check=True, **AFFINIS)
        for r, ok in game.score_classification(AFFINIS, checked.taxon).items():
            setattr(answer, f"correct_{r}", ok)
        answer.save()
        game_scoring.score_new_answer(answer)
        answer.refresh_from_db()
        card = self.card(answer)
        self.assertEqual(card["classify"]["truth"]["name"], "Xyleborus affinis")
        self.assertNotIn("ai", card["classify"]["ranks"][3])
