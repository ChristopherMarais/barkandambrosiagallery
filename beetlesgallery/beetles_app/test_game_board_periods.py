"""
The leaderboard starts on "this week", can show this month or all time, says when its points start again, and
keeps each finished week's top three on the players' profiles (#378). Levels and expertise never reset.
"""
from datetime import datetime, timedelta

from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_board
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_beetle


class PeriodTests(ScoringCase):
    def setUp(self):
        super().setUp()
        self.ann, self.bob, self.cy = (self.player(n) for n in ("ann", "bob", "cy"))
        for p, score in ((self.ann, 5000), (self.bob, 100), (self.cy, 300)):
            PlayerScore.objects.create(player=p, score=score, rating=0.5, viewed=10)
        self.roi = make_beetle(bbox="unvalidated")
        self.this_week = game.week_start()

    def points(self, player, pts, when):
        rnd = GameRound.objects.create(player=player, mode="classify", items=[])
        ans = GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=self.roi)
        GameAnswer.objects.filter(pk=ans.pk).update(answered_at=when)
        AnswerPoints.objects.create(answer=ans, points=pts)

    def test_this_week_is_the_default_and_only_counts_this_weeks_points(self):
        self.points(self.bob, 40, self.this_week + timedelta(hours=1))
        self.points(self.ann, 10, self.this_week + timedelta(hours=2))
        self.points(self.ann, 900, self.this_week - timedelta(days=3))     # last week: not on this week's board
        rows = game_board.board()
        self.assertEqual([(r["username"], r["score"]) for r in rows], [("bob", 40), ("ann", 10)])
        self.assertEqual([r["username"] for r in game_board.board(period="all")], ["ann", "cy", "bob"])

    def test_month_and_reset_times(self):
        now = timezone.make_aware(datetime(2026, 12, 16, 15, 0))      # a Wednesday
        self.assertEqual(game_board.period_start("week", now).date().isoformat(), "2026-12-14")
        self.assertEqual(game_board.period_end("week", now).date().isoformat(), "2026-12-21")
        self.assertEqual(game_board.period_start("month", now).date().isoformat(), "2026-12-01")
        self.assertEqual(game_board.period_end("month", now).date().isoformat(), "2027-01-01")
        self.assertIsNone(game_board.period_end("all", now))

    def test_finished_weeks_keep_their_top_three(self):
        last = self.this_week - timedelta(days=7)
        for player, pts in ((self.ann, 30), (self.bob, 50), (self.cy, 20)):
            self.points(player, pts, last + timedelta(days=1))
        self.points(self.cy, 99, self.this_week + timedelta(hours=1))      # this week isn't finished: no winner yet
        [week] = game_board.weekly_wins()
        self.assertEqual([p for p, _ in week["places"]], [self.bob.id, self.ann.id, self.cy.id])
        self.assertEqual(game_board.profile(self.bob)["weekly"]["wins"], 1)
        self.assertEqual(game_board.profile(self.cy)["weekly"]["podiums"], 1)

        self.client.force_login(self.ann)
        page = self.client.get(reverse("game_leaderboard")).content.decode()
        self.assertIn('aria-current="page">This week</a>', page)
        self.assertIn('data-testid="resets"', page)
        self.assertIn('data-testid="last-week"', page)
        profile = self.client.get(reverse("game_profile", args=[self.bob.id])).content.decode()
        self.assertIn('data-testid="weekly-wins"', profile)

    def test_the_home_board_stays_on_this_week_in_a_quiet_week(self):
        self.client.force_login(self.ann)
        ctx = self.client.get(reverse("game_home")).context
        self.assertEqual((ctx["board"], ctx["last_week"]), ([], []))   # never all time instead (#497)
        self.points(self.cy, 5, self.this_week + timedelta(hours=1))
        ctx = self.client.get(reverse("game_home")).context
        self.assertEqual([r["username"] for r in ctx["board"]], ["cy"])
