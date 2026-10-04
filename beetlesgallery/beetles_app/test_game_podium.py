"""When a week, month or year ends, the game home praises its top three once: gold, silver, bronze, with numbers."""
from datetime import datetime, timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_podium
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_beetle


def aware(*args):
    return timezone.make_aware(datetime(*args))


class FinishedPeriodTests(ScoringCase):
    def test_midweek_shows_only_last_week(self):
        self.assertEqual([k for k, *_ in game_podium.finished_periods(aware(2026, 10, 14, 12))], ["week"])

    def test_early_in_a_month_shows_last_month_too(self):
        kinds = game_podium.finished_periods(aware(2026, 10, 6, 12))
        self.assertEqual([k for k, *_ in kinds], ["month", "week"])
        _, start, end = kinds[0]
        self.assertEqual((start.date().isoformat(), end.date().isoformat()), ("2026-09-01", "2026-10-01"))

    def test_new_year_shows_all_three(self):
        kinds = game_podium.finished_periods(aware(2027, 1, 4, 9))
        self.assertEqual([k for k, *_ in kinds], ["year", "month", "week"])
        self.assertEqual(kinds[0][1].date().isoformat(), "2026-01-01")


@override_settings(GAME_MIN_JUDGED_FOR_ACCURACY=1)
class PodiumTests(ScoringCase):
    def setUp(self):
        super().setUp()
        self.last_week = game.week_start() - timedelta(days=7)
        self.players = [self.player(n) for n in ("ann", "bob", "cy", "dee")]

    def play(self, player, pts, right=False, n=1, when=None):
        for _ in range(n):
            rnd = GameRound.objects.create(player=player, mode="classify", items=[])
            ans = GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0,
                                            roi=make_beetle(bbox="validated"), is_check=True,
                                            correct_species=right, correct_genus=right)
            GameAnswer.objects.filter(pk=ans.pk).update(answered_at=when or self.last_week + timedelta(days=2))
            AnswerPoints.objects.create(answer=ans, points=pts)

    def test_the_top_three_with_their_numbers(self):
        ann, bob, cy, dee = self.players
        self.play(ann, 50, right=True, n=2)
        self.play(bob, 70)
        self.play(cy, 20, right=True)
        self.play(dee, 5)
        self.play(dee, 900, when=game.week_start() + timedelta(hours=1))   # this week: not last week's
        p = game_podium.podium("week", self.last_week, game.week_start(), viewer=dee)
        self.assertEqual([(r["username"], r["points"]) for r in p["top"]], [("ann", 100), ("bob", 70), ("cy", 20)])
        self.assertEqual((p["top"][0]["beetles"], p["top"][0]["species"], p["top"][0]["accuracy"]), (2, 2, 1.0))
        self.assertEqual(p["top"][1]["accuracy"], 0.0)
        self.assertEqual((p["me"]["place_text"], p["me"]["points"]), ("4th", 5))
        self.assertEqual([game_podium.ordinal(n) for n in (1, 2, 3, 11, 12, 21, 22, 103)],
                         ["1st", "2nd", "3rd", "11th", "12th", "21st", "22nd", "103rd"])

    def test_the_game_home_shows_it_once(self):
        self.play(self.players[0], 30)
        self.client.force_login(self.user)
        first = self.client.get(reverse("game_home")).content.decode()
        self.assertIn('data-testid="podium"', first)
        self.assertIn('data-kind="week"', first)   # (a month or year that just ended can come first)
        self.assertIn("ann", first)
        self.assertNotIn('data-testid="podium"', self.client.get(reverse("game_home")).content.decode())

    def test_no_pop_up_for_a_quiet_period(self):
        self.client.force_login(self.user)
        self.assertNotIn('data-testid="podium"', self.client.get(reverse("game_home")).content.decode())
