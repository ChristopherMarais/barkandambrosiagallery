"""
Polish on the performance report (#618): "Where you lost points" rounds to whole points, another player's report
is titled with "performance" so it isn't confused with their profile, and "Last six months" waits for a second
month of data rather than showing a one-row "trend".
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game_feedback
from beetlesgallery.beetles_app.models import GameAnswer
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class WholePointsTests(ScoringCase):
    def test_loss_summary_rounds_to_whole_points(self):
        for fields in (dict(AFFINIS, species="nope"), dict(AFFINIS, species="nope"), AFFINIS):
            self.points(self.answer(self.user, self.roi(self.t_affinis), fields))
        summary = game_feedback.loss_summary(GameAnswer.objects.filter(player=self.user).select_related("points"))
        self.assertIsInstance(summary["lost"], int)
        for row in summary["ranks"]:
            self.assertIsInstance(row["lost"], int)

    def test_the_page_shows_no_decimal_in_the_lost_column(self):
        self.points(self.answer(self.user, self.roi(self.t_affinis), dict(AFFINIS, species="nope")))
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_report")).content.decode()
        losses = page[page.index('data-testid="losses"'):page.index("</section>")]
        # numbers as plain text, the way other tests read |digit_groups output
        losses = losses.replace('<span class="digit-group">', "").replace('<span class="digit-group" style="margin-left:0.4em">', "").replace("</span>", "")
        self.assertIn("&minus;", losses)
        import re
        amounts = re.findall(r"&minus;([\d,]+)", losses)
        self.assertTrue(amounts)
        for amount in amounts:
            self.assertNotIn(".", amount)


class PlayerReportTitleTests(ScoringCase):
    def test_your_own_report_says_my_performance(self):
        self.client.force_login(self.user)
        self.assertIn("My performance", self.client.get(reverse("game_report")).content.decode())

    def test_another_players_report_is_not_titled_like_their_profile(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("game_player_report", args=[self.user.id])).content.decode()
        self.assertIn(f"{self.user.username} &middot; performance", page)


class SixMonthTrendTests(ScoringCase):
    def test_a_single_months_data_is_not_shown_as_a_trend(self):
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=timezone.now())
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_report")).content.decode()
        self.assertNotIn("Last six months", page)

    def test_two_months_of_data_show_the_trend(self):
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=timezone.now())
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=timezone.now() - timedelta(days=40))
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_report")).content.decode()
        self.assertIn("Last six months", page)
