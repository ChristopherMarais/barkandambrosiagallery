"""Beetles validated after they were answered: points, accuracy and expertise follow, and the player sees a recap."""
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_scoring
from beetlesgallery.beetles_app.models import AnswerPoints, Beetles, GameAnswer, GameRound, PlayerScore, RetroCredit
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class LateValidationTests(ScoringCase):
    def setUp(self):
        super().setUp()
        self.target = self.roi(self.t_affinis, validated=False)
        self.ans = self.answer(self.user, self.target, AFFINIS, check=False)
        game_scoring.recompute([self.user.id])

    def validate(self, roi=None):
        Beetles.objects.filter(pk=(roi or self.target).pk).update(bbox_is_validated=True, bbox_validated_at=timezone.now())

    def test_before_validation_it_is_agreement_and_no_recap(self):
        self.assertEqual(AnswerPoints.objects.get(answer=self.ans).basis, "consensus")
        self.assertFalse(RetroCredit.objects.exists())

    def test_after_validation_it_is_scored_on_the_truth_with_a_recap(self):
        self.validate()
        game_scoring.recompute([self.user.id])
        self.ans.refresh_from_db()
        self.assertTrue(self.ans.validated_later)
        self.assertTrue(self.ans.correct_species)
        self.assertEqual(AnswerPoints.objects.get(answer=self.ans).basis, "truth")
        credit = RetroCredit.objects.get(answer=self.ans)
        self.assertEqual((credit.points_before, credit.points_after), (0.0, 15.0))
        self.assertEqual(credit.validated_name, "Xyleborus affinis")
        self.assertIsNotNone(credit.validated_at)
        game_scoring.recompute([self.user.id])
        self.assertEqual(RetroCredit.objects.count(), 1)    # only once

    def test_it_counts_for_accuracy_and_expertise(self):
        self.validate()
        game_scoring.sync_late_truth([self.user.id])
        rating, accuracy, judged = game_scoring.ratings()[self.user.id]
        self.assertEqual((accuracy, judged), (1.0, 4))
        from beetlesgallery.beetles_app.game_trust import skill_counts
        self.assertEqual(skill_counts(self.user)[("species", "xyleborus")][:2], [1, 1])

    def test_a_wrong_name_loses_points_and_says_so(self):
        other = self.roi(self.t_affinis, validated=False)
        wrong = self.answer(self.staff, other, FERR, check=False)
        self.validate(other)
        game_scoring.recompute([self.staff.id])
        credit = RetroCredit.objects.get(answer=wrong)
        self.assertLess(credit.points_after, 15.0)
        self.assertEqual(credit.points_after, -11.667)   # right genus, wrong species: 7 − 8 × 2⅓

    def test_losing_validation_undoes_it(self):
        self.validate()
        game_scoring.sync_late_truth([self.user.id])
        Beetles.objects.filter(pk=self.target.pk).update(bbox_is_validated=False)
        game_scoring.sync_late_truth([self.user.id])
        self.ans.refresh_from_db()
        self.assertFalse(self.ans.validated_later)
        self.assertIsNone(self.ans.correct_species)

    def test_the_recap_shows_once_on_the_home_and_always_in_the_history(self):
        self.validate()
        game_scoring.recompute([self.user.id])
        self.client.force_login(self.user)
        first = self.client.get(reverse("game_home")).content.decode()
        self.assertIn('data-testid="checked-recap"', first)
        self.assertIn("Xyleborus affinis", first)
        self.assertIn("+15.0 pts", first)
        self.assertNotIn('data-testid="checked-recap"', self.client.get(reverse("game_home")).content.decode())
        page = self.client.get(reverse("game_history") + "?tab=checked").content.decode()
        self.assertIn('data-testid="checked-item"', page)
        self.assertIn("checked ", page)


class AgreementLaterTests(ScoringCase):
    # in the request here; the background path (production) is tested in test_game_polish.BackgroundTests
    @override_settings(GAME_RECOMPUTE_IN_BACKGROUND=False)
    def test_finishing_a_round_rescores_everyone_on_the_same_beetles(self):
        target = self.roi(self.t_affinis, validated=False)
        early = self.player("early")
        self.answer(early, target, AFFINIS, check=False)
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, roi=target, is_check=False, **AFFINIS)
        with mock.patch.object(game_scoring, "recompute") as recompute:
            game.finish_round(rnd)
        self.assertIn(early.id, recompute.call_args[0][0])


class SiteTests(ScoringCase):
    def test_the_sidebar_shows_the_player_and_links_the_game(self):
        PlayerScore.objects.create(player=self.user, score=420, rating=0.55)
        self.client.force_login(self.user)
        page = self.client.get("/").content.decode()
        self.assertIn('data-testid="sidebar-player"', page)
        self.assertIn(self.user.username, page)
        self.assertIn("420 pts", page)
        self.assertNotIn('data-testid="game-invite"', page)    # no coloured pop-up on the home page
        self.assertNotIn('data-testid="beta"', page)           # out of beta (#538): no pill

    def test_signed_out_visitors_are_invited_to_sign_in_and_play(self):
        page = self.client.get("/").content.decode()
        self.assertNotIn('data-testid="sidebar-player"', page)
        self.assertIn("/game/", page)

    def test_the_game_is_out_of_beta(self):
        self.client.force_login(self.user)
        for url in (reverse("game_home"), reverse("game_how"), reverse("game_play", args=["mixed"])):
            self.assertNotIn('data-testid="beta"', self.client.get(url).content.decode(), url)


class HistoryTests(ScoringCase):
    def test_sessions_are_listed_newest_first_with_their_points(self):
        from beetlesgallery.beetles_app import game
        from beetlesgallery.beetles_app.test_game import AFFINIS
        old = self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        game.finish_round(old.round)
        new = self.answer(self.user, self.roi(self.t_affinis), dict(AFFINIS, species=""))
        game.finish_round(new.round)
        skipped = self.answer(self.user, self.roi(self.t_affinis), skipped=True)
        game.finish_round(skipped.round)      # nothing labelled: not a session worth listing
        game_scoring.recompute([self.user.id])
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_history")).content.decode()
        links = [reverse("game_round_review", args=[r]) for r in (new.round_id, old.round_id)]
        self.assertLess(page.index(links[0]), page.index(links[1]))
        self.assertNotIn(reverse("game_round_review", args=[skipped.round_id]), page)
        self.assertIn("+15 pts", page)
        self.assertIn("Identification", page)

    def test_nobody_sees_anyone_elses_history(self):
        from beetlesgallery.beetles_app import game
        from beetlesgallery.beetles_app.test_game import AFFINIS
        theirs = self.answer(self.player("someone"), self.roi(self.t_affinis), AFFINIS)
        game.finish_round(theirs.round)
        self.client.force_login(self.user)
        self.assertNotIn(str(theirs.round_id), self.client.get(reverse("game_history")).content.decode())
