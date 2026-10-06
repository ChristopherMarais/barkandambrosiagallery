"""Issue #426, now in the review (#488): "what others said" after an answer must never break the answer itself."""
from django.contrib.auth import get_user_model

from beetlesgallery.beetles_app import game_answer_review
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import GameCase


class CommunityEdgeCaseTests(GameCase):
    def setUp(self):
        super().setUp()
        self.beetle = self.roi(self.t_affinis, validated=False)
        self.other = get_user_model().objects.create_user("other", password="pw")

    def answer(self, player, **ranks):
        rnd = GameRound.objects.create(player=player, mode="classify", items=[{"a": str(self.beetle.id), "b": None, "check": False}])
        return GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=self.beetle, **ranks)

    def ranks(self, answer):
        return {r["rank"]: r["players"] for r in game_answer_review.review(answer, {})["classify"]["ranks"]}

    def test_players_who_named_only_a_genus_do_not_break_it(self):
        self.answer(self.other, genus="Xyleborus")
        said = self.ranks(self.answer(self.user, subfamily="Scolytinae", genus="Xyleborus"))
        self.assertEqual((said["subfamily"], said["genus"]["name"], said["genus"]["agrees"]), (None, "Xyleborus", True))

    def test_players_who_named_nothing_comparable_leave_it_empty(self):
        self.answer(self.other, species="affinis")   # a species without its genus compares at no rank
        self.assertEqual(set(self.ranks(self.answer(self.user, subfamily="Scolytinae")).values()), {None})
