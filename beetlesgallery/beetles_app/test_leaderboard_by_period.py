"""
Issue #422: every number on the leaderboard follows the chosen period (accuracy and finds too), with a "This year"
tab, and the tabs in the site's own grey.
"""
from datetime import datetime, timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_board
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, PlayerScore, SpeciesDiscovery
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_beetle


@override_settings(GAME_MIN_JUDGED_FOR_ACCURACY=4)
class LeaderboardByPeriodTests(ScoringCase):
    def setUp(self):
        super().setUp()
        self.ann = self.player("ann")
        PlayerScore.objects.create(player=self.ann, score=500, rating=0.5, viewed=20, judged=40, accuracy=0.9)
        self.roi = make_beetle(bbox="validated")
        self.this_week = game.week_start()

    def judged(self, right, when):
        """One validated beetle answered, right or wrong at all four ranks, at that time."""
        rnd = GameRound.objects.create(player=self.ann, mode="classify", items=[])
        ans = GameAnswer.objects.create(round=rnd, player=self.ann, mode="classify", index=0, roi=make_beetle(bbox="validated"),
                                        is_check=True, **{f"correct_{r}": right for r in game.RANKS})
        GameAnswer.objects.filter(pk=ans.pk).update(answered_at=when)
        AnswerPoints.objects.create(answer=ans, points=5 if right else -1)

    def test_accuracy_follows_the_period(self):
        self.judged(True, self.this_week - timedelta(days=60))     # long ago: right
        self.judged(False, self.this_week + timedelta(hours=1))    # this week: wrong
        [week] = game_board.board(period="week")
        self.assertEqual(week["accuracy"], 0.0)                     # this week's answers only
        [always] = game_board.board(period="all")
        self.assertEqual(always["accuracy"], 0.9)                   # all time: the overall rating

    def test_finds_follow_the_period(self):
        self.judged(True, self.this_week + timedelta(hours=1))
        old = SpeciesDiscovery.objects.create(player=self.ann, roi=self.roi, genus="Xyleborus", species="novus")
        SpeciesDiscovery.objects.filter(pk=old.pk).update(created_at=self.this_week - timedelta(days=60))
        self.assertEqual(game_board.board(period="week")[0]["discoveries"], 0)
        self.assertEqual(game_board.board(period="all")[0]["discoveries"], 1)

    def test_this_year(self):
        now = timezone.make_aware(datetime(2026, 10, 3, 12, 0))
        self.assertEqual(game_board.period_start("year", now).date().isoformat(), "2026-01-01")
        self.assertEqual(game_board.period_end("year", now).date().isoformat(), "2027-01-01")
        self.client.force_login(self.ann)
        page = self.client.get(reverse("game_leaderboard") + "?period=year").content.decode()
        self.assertIn('aria-current="page">This year</a>', page)
        self.assertIn("Since 1 January", page)
        self.assertNotIn("bg-gray-800", page)
        self.assertNotIn("Accuracy is all time", page)
