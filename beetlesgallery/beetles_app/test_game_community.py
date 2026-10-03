"""Issue #426: "what others said" after an answer must never break the answer itself."""
from django.contrib.auth import get_user_model

from beetlesgallery.beetles_app.game_views import _community
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase


class CommunityEdgeCaseTests(GameCase):
    def setUp(self):
        super().setUp()
        self.beetle = self.roi(self.t_affinis, validated=False)
        self.ahead = get_user_model().objects.create_user("ahead", password="pw")
        PlayerScore.objects.create(player=self.ahead, score=500)

    def answer(self, player, **ranks):
        rnd = GameRound.objects.create(player=player, mode="classify", items=[])
        return GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=self.beetle, **ranks)

    def test_players_ahead_who_named_only_a_genus_do_not_break_it(self):
        self.answer(self.ahead, genus="Xyleborus")
        mine = self.answer(self.user, subfamily="Scolytinae", genus="Xyleborus")
        out = _community(mine)
        self.assertEqual(out["ranks"][0]["rank"], "genus")
        self.assertIn("agree with you to genus", out["text"])

    def test_players_ahead_who_named_nothing_comparable_get_a_plain_line(self):
        self.answer(self.ahead, species="affinis")   # a species without its genus compares at no rank
        mine = self.answer(self.user, subfamily="Scolytinae")
        out = _community(mine)
        self.assertEqual(out["ranks"], [])
        self.assertEqual(out["text"], "1 player ahead of you named it.")
