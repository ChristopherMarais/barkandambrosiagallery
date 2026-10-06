"""
Issue #425: beetle confetti for a validated beetle named right, ordinary confetti for a strong answer on a beetle
nobody has checked yet, and points that came in later marked on the History page.
"""
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game_answer_review, game_scoring
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, RetroCredit
from beetlesgallery.beetles_app.test_game import GameCase

AFFINIS = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "affinis"}


@override_settings(GAME_ROUND_SIZE=1)
class CelebrationKindTests(GameCase):
    def answer(self, validated):
        self.roi(self.t_affinis, validated=validated)
        rnd, item = self.play("classify")
        return self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()["review"]

    def test_a_validated_hit_gets_beetles(self):
        self.assertEqual(self.answer(validated=True)["celebrate"]["kind"], "validated")

    def test_points_by_agreement_get_ordinary_confetti(self):   # #488: the confetti follows the points
        real = game_scoring.score

        def agreed(answer, *args, **kwargs):
            points, basis, detail = real(answer, *args, **kwargs)
            return (points + 6, basis, detail) if basis == AnswerPoints.Basis.CONSENSUS else (points, basis, detail)

        with mock.patch.object(game_scoring, "score", agreed):
            self.assertEqual(self.answer(validated=False)["celebrate"]["kind"], "players")   # purple (#572)

    def test_a_plain_unvalidated_answer_gets_nothing(self):
        self.assertIsNone(self.answer(validated=False)["celebrate"])

    def test_the_game_page_draws_both_kinds(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["classify"])).content.decode()
        self.assertIn("js/beetle_confetti.js", page)
        self.assertIn('window.beetleConfetti($("confetti"), kind || "validated", size)', page)


class StrongAnswerTests(GameCase):
    """#488: on a beetle nobody has checked, the paper confetti comes with points (agreement or reference), sized by them."""

    def celebrate(self, points, detail):
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[{"a": "", "b": None, "check": False}])
        ans = GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0,
                                        roi=self.roi(self.t_affinis, validated=False), **AFFINIS)
        AnswerPoints.objects.create(answer=ans, points=points, basis=AnswerPoints.Basis.CONSENSUS,
                                    detail=dict(detail, participation=0.5))
        return game_answer_review.past(rnd, 0)["celebrate"]

    def test_backed_by_experts_or_a_trusted_model(self):
        self.assertEqual(self.celebrate(11.3, {"reference": {"genus": {"match": True}}})["kind"], "players")

    def test_more_agreement_more_confetti_and_none_without_points(self):
        small, big = self.celebrate(2.0, {"agreement": {"species": 0.4}}), self.celebrate(20.0, {"agreement": {"species": 0.9}})
        self.assertLess(small["size"], big["size"])
        self.assertIsNone(self.celebrate(0.5, {"agreement": {"species": -0.4}}))


class LaterPointsInHistoryTests(GameCase):
    def setUp(self):
        super().setUp()
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        ans = GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0,
                                        roi=self.roi(self.t_affinis), **AFFINIS)
        RetroCredit.objects.create(answer=ans, player=self.user, points_before=0, points_after=7.5,
                                   validated_name="Xyleborus affinis")
        self.client.force_login(self.user)

    def test_new_later_points_pop_once_on_the_checked_tab(self):
        sessions = self.client.get(reverse("game_history")).content.decode()
        self.assertIn('data-testid="checked-new-dot"', sessions)          # waiting on the other tab, not yet seen
        first = self.client.get(reverse("game_history") + "?tab=checked").content.decode()
        self.assertIn("rounded-2xl checked-new\"", first)
        self.assertIn("+7.5 pts came in later", first)
        self.assertIn("js/beetle_confetti.js", first)
        again = self.client.get(reverse("game_history") + "?tab=checked").content.decode()
        self.assertNotIn("came in later", again)
        self.assertNotIn("js/beetle_confetti.js", again)
