"""Reporting a photo from the feed, and superusers granting unlocks."""
import json

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_levels
from beetlesgallery.beetles_app.models import AnswerPoints, Beetles, GameAnswer, GamePreference, GameReport
from beetlesgallery.beetles_app.test_game import GameCase


class ReportFromFeedTests(GameCase):
    def setUp(self):
        super().setUp()
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat) * 3:
            self.roi(taxon)
            self.roi(taxon, validated=False)
        self.client.force_login(self.user)
        self.data = self.post("game_start", {"mode": "mixed"}).json()
        self.rnd = game.GameRound.objects.get(id=self.data["round"])
        self.item = self.data["item"]

    def report(self, image=0, reason="bad_image"):
        return self.client.post(reverse("game_report_item"), json.dumps(
            {"round": self.data["round"], "index": self.item["index"], "image": image, "reason": reason}),
            content_type="application/json")

    def shown(self, image=0):
        it = self.rnd.items[self.item["index"]]
        ids = [it["a"]] if not it.get("b") else ([it["b"], it["a"]] if it.get("flip") else [it["a"], it["b"]])
        return Beetles.objects.get(id=ids[image])

    def test_a_report_reaches_the_curators_and_takes_the_photo_out_of_the_game(self):
        res = self.report()
        self.assertEqual(res.status_code, 200, res.content)
        roi = self.shown()
        self.assertTrue(GameReport.objects.filter(roi=roi, reporter=self.user, status="open").exists())
        self.assertFalse(game.check_rois().filter(id=roi.id).exists())
        self.assertFalse(game.open_rois().filter(id=roi.id).exists())

    def test_it_comes_back_once_a_curator_validates_it(self):
        self.report()
        roi = self.shown()
        Beetles.objects.filter(id=roi.id).update(bbox_is_validated=True, bbox_validated_at=timezone.now())
        self.assertTrue(game.playable_rois().filter(id=roi.id).filter(~game.reported()).exists())

    def test_moving_on_after_reporting_costs_nothing(self):
        self.report()
        res = self.post("game_answer", {"index": self.item["index"], "skipped": True, "reported": True}, self.data["round"])
        self.assertEqual(res.status_code, 200, res.content)
        ans = GameAnswer.objects.get(round=self.rnd, index=self.item["index"])
        self.assertTrue(ans.score_hold)
        self.assertEqual(AnswerPoints.objects.get(answer=ans).points, 0.0)

    def test_bad_requests(self):
        self.assertEqual(self.report(reason="nope").status_code, 400)
        self.assertEqual(self.report(image=5).status_code, 400)
        other = get_user_model().objects.create_user("other", password="pw")
        self.client.force_login(other)
        self.assertEqual(self.report().status_code, 404)

    def test_the_cog_is_in_the_full_image_view(self):
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn('id="report-cog"', page)
        self.assertIn('data-reason="bad_box"', page)
        self.assertNotIn('data-reason="wrong_label"', page)   # only after answering (the round review)

    def test_the_annotation_list_can_show_only_reported_photos(self):
        self.report()
        self.client.force_login(self.staff)
        data = self.client.get("/api/v1/beetles/images-with-annotations/?game=reported").json()
        self.assertEqual([r["image_asset_id"] for r in data["results"]], [str(self.shown().image_asset_id)])


class GrantTests(GameCase):
    def test_superusers_can_unlock_everything_for_anyone(self):
        boss = get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.force_login(boss)
        self.assertEqual(self.client.get(reverse("game_settings")).status_code, 200)
        self.client.post(reverse("game_settings"), {"player": self.user.id, "all": "1"})
        info = game_levels.for_player(self.user)
        self.assertEqual(info["perks"], set(game_levels.PERKS))
        self.assertTrue(info["proposals"])
        self.assertEqual(info["level"], 1)                      # the level itself doesn't change
        self.assertIn(self.user.id, game_levels.suggestion_voters())

    def test_single_unlocks_and_taking_them_back(self):
        boss = get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.force_login(boss)
        self.client.post(reverse("game_settings"), {"player": self.user.id, "perks": ["focus_genus"]})
        self.assertEqual(game_levels.for_player(self.user)["perks"], {"focus_genus"})
        self.client.post(reverse("game_settings"), {"player": self.user.id})
        self.assertEqual(game_levels.for_player(self.user)["perks"], set())
        self.assertEqual(GamePreference.objects.get(player=self.user).granted_perks, [])

    def test_a_granted_unlock_works_in_the_feed(self):
        GamePreference.objects.create(player=self.user, granted_perks=["choose_game"])
        self.client.force_login(self.user)
        res = self.client.post(reverse("game_prefs"), json.dumps({"play_mode": "pair"}), content_type="application/json")
        self.assertEqual(res.status_code, 200)

    def test_only_superusers(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("game_settings")).status_code, 404)
        self.assertEqual(self.client.post(reverse("game_settings"), {"player": self.user.id, "all": "1"}).status_code, 404)
