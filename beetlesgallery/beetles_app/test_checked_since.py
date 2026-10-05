"""The game home says how many answers curators checked since the last visit and the points they moved (#382)."""
from django.urls import reverse
from django.utils.html import strip_tags

from beetlesgallery.beetles_app.models import GameAnswer, GameRound, RetroCredit
from beetlesgallery.beetles_app.test_game import GameCase


class CheckedSinceTests(GameCase):
    def credit(self, before, after):
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        answer = GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0,
                                           roi=self.roi(self.t_affinis), genus="Xyleborus")
        return RetroCredit.objects.create(answer=answer, player=self.user, points_before=before, points_after=after,
                                          validated_name="Xyleborus affinis")

    def home(self):
        self.client.force_login(self.user)
        return strip_tags(self.client.get(reverse("game_home")).content.decode())

    def test_the_answers_checked_and_points_moved_since_the_last_visit(self):
        self.credit(2.0, 8.0)
        self.credit(1.0, 1.5)
        self.assertIn("Since your last visit: 2 answers checked, +6.5 points.", self.home())
        self.assertNotIn("Since your last visit", self.home())   # once

    def test_points_lost_show_as_such(self):
        self.credit(3.0, 0.0)
        self.assertIn("Since your last visit: 1 answer checked, -3 points.", self.home())
