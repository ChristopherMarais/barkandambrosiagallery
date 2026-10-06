"""Where players lose points on validated beetles, so they can learn (game_feedback.answer_losses / loss_summary)."""
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_feedback
from beetlesgallery.beetles_app.models import GameAnswer
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import PLAT, ScoringCase


class AnswerLossTests(ScoringCase):
    def losses(self, fields, **kw):
        ans = self.answer(self.user, self.roi(self.t_affinis), fields, **kw)
        self.points(ans)
        return game_feedback.answer_losses(GameAnswer.objects.select_related("points").get(pk=ans.pk))

    def test_fully_right_loses_nothing(self):
        loss = self.losses(AFFINIS)
        self.assertEqual((loss["earned"], loss["lost"]), (15.0, 0.0))
        self.assertEqual({c["state"] for c in loss["ranks"].values()}, {"right"})

    def test_a_wrong_species_shows_what_it_was_worth_and_the_penalty(self):
        loss = self.losses(dict(AFFINIS, species="nope"))
        species = loss["ranks"]["species"]
        self.assertEqual(species["state"], "wrong")
        self.assertAlmostEqual(species["lost"], 8 + 8 * 7 / 3, delta=0.06)   # what it was worth, and k times that
        self.assertAlmostEqual(loss["earned"], -11.7)

    def test_stopping_early_is_points_left_on_the_table(self):
        loss = self.losses(dict(AFFINIS, species=""))
        self.assertEqual(loss["ranks"]["species"], {"state": "stopped", "points": 0.0, "lost": 8.0})

    def test_a_wrong_subfamily_makes_everything_below_wrong_too(self):
        loss = self.losses(PLAT)
        self.assertEqual([loss["ranks"][r]["state"] for r in game.RANKS], ["wrong", "after", "after", "after"])
        self.assertAlmostEqual(loss["lost"], 15 + 35, delta=0.06)   # shown to one decimal

    def test_only_validated_beetles_count(self):
        ans = self.answer(self.user, self.roi(self.t_affinis, validated=False), AFFINIS)
        self.points(ans)
        self.assertIsNone(game_feedback.answer_losses(GameAnswer.objects.get(pk=ans.pk)))


class SummaryTests(ScoringCase):
    def test_the_worst_rank_comes_first_with_a_tip(self):
        for fields in (dict(AFFINIS, species="nope"), dict(AFFINIS, species="nope"), AFFINIS):
            self.points(self.answer(self.user, self.roi(self.t_affinis), fields))
        summary = game_feedback.loss_summary(GameAnswer.objects.filter(player=self.user).select_related("points"))
        self.assertEqual(summary["answers"], 3)
        first = summary["ranks"][0]
        self.assertEqual((first["rank"], first["right"], first["wrong"]), ("species", 1, 2))
        self.assertIn("species", summary["tip"])

    def test_the_review_and_performance_pages_show_it(self):
        ans = self.answer(self.user, self.roi(self.t_affinis), dict(AFFINIS, species=""))
        self.points(ans)
        game.finish_round(ans.round)
        self.client.force_login(self.user)
        review = self.client.get(reverse("game_round_review", args=[ans.round_id])).content.decode()
        self.assertIn("Where you lost points", review)
        self.assertIn('"losses"', review)   # each item carries its breakdown for the page script
        report = self.client.get(reverse("game_report")).content.decode()
        self.assertIn("Where you lost points", report)
        self.assertIn("last 30 days", report)
