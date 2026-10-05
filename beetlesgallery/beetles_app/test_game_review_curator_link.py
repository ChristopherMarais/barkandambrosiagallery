"""
#380: a curator playing the game who sees a verified name that looks wrong can open that beetle in the annotator,
where it can be un-validated or corrected, straight from the round review. Players without those rights don't see it.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app import areas, game, game_feedback
from beetlesgallery.beetles_app.models import AreaGrant, GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


class CuratorLinkTests(GameCase):
    def played(self, player):
        """A finished round of one verified beetle, answered by ``player``."""
        beetle = self.roi(self.t_affinis)
        rnd = GameRound.objects.create(player=player, mode="classify", items=[{"a": str(beetle.id), "b": None, "check": True}])
        GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=beetle, is_check=True, **AFFINIS)
        game.finish_round(rnd)
        return rnd, beetle

    def page(self, player):
        rnd, _ = self.played(player)
        self.client.force_login(player)
        res = self.client.get(reverse("game_round_review", args=[rnd.id]))
        self.assertEqual(res.status_code, 200)
        return res.content.decode()

    def test_the_review_knows_each_beetles_photo(self):
        rnd, beetle = self.played(self.user)
        side = game_feedback.round_feedback(rnd)["items"][0]["sides"][0]
        self.assertEqual(side["image_id"], str(beetle.image_asset_id))

    def test_only_people_who_may_annotate_and_validate_get_the_link(self):
        self.assertIn("const CAN_REVOKE = false", self.page(self.user))
        curator = get_user_model().objects.create_user("curator", password="pw")
        AreaGrant.objects.create(user=curator, area=areas.ANNOTATE)
        self.assertIn("const CAN_REVOKE = false", self.page(curator))   # may annotate, not validate
        AreaGrant.objects.create(user=curator, area=areas.VALIDATE)
        page = self.page(curator)
        self.assertIn("const CAN_REVOKE = true", page)
        self.assertIn(reverse("tool_annotate"), page)
        self.assertIn("const CAN_REVOKE = true", self.page(self.superuser))
