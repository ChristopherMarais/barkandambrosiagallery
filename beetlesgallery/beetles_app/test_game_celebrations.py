"""
Issue #425: beetle confetti for a validated beetle named right, ordinary confetti for a strong answer on a beetle
nobody has checked yet, and points that came in later marked on the History page.
"""
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.game_views import _strong_unvalidated
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, RetroCredit
from beetlesgallery.beetles_app.test_game import GameCase

AFFINIS = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "affinis"}


@override_settings(GAME_ROUND_SIZE=1)
class CelebrationKindTests(GameCase):
    def answer(self, validated):
        self.roi(self.t_affinis, validated=validated)
        rnd, item = self.play("classify")
        return self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()

    def test_a_validated_hit_gets_beetles(self):
        self.assertEqual(self.answer(validated=True)["celebrate"], "validated")

    def test_a_strong_unvalidated_answer_gets_ordinary_confetti(self):
        with mock.patch("beetlesgallery.beetles_app.game_views._strong_unvalidated", return_value=True):
            self.assertEqual(self.answer(validated=False)["celebrate"], "strong")

    def test_a_plain_unvalidated_answer_gets_nothing(self):
        self.assertFalse(self.answer(validated=False)["celebrate"])

    def test_the_game_page_draws_both_kinds(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["classify"])).content.decode()
        self.assertIn("js/beetle_confetti.js", page)
        self.assertIn('kind === "strong" ? "plain" : (kind || "validated")', page)


class StrongAnswerTests(GameCase):
    def points(self, detail):
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        ans = GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0,
                                        roi=self.roi(self.t_affinis, validated=False), **AFFINIS)
        AnswerPoints.objects.create(answer=ans, points=3, detail=detail)
        return ans

    def test_backed_by_experts_or_a_trusted_model_to_genus_or_species(self):
        self.assertTrue(_strong_unvalidated(self.points({"reference": {"genus": {"match": True}}})))
        self.assertFalse(_strong_unvalidated(self.points({"reference": {"tribe": {"match": True}}})))

    def test_or_clear_agreement_at_species(self):
        self.assertTrue(_strong_unvalidated(self.points({"agreement": {"species": 0.8}})))
        self.assertFalse(_strong_unvalidated(self.points({"agreement": {"species": 0.4}})))


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
