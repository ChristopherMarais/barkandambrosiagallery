"""The Image Annotation list can filter to images with game proposals and sort them by confidence."""
from django.core.cache import cache
from django.test import override_settings

from beetlesgallery.beetles_app import game_queue
from beetlesgallery.beetles_app.models import LabelReview
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR, SMALL_TRUST, TrustCase

URL = "/api/v1/beetles/images-with-annotations/"


@override_settings(GAME_PROPOSALS_NEED_LEVEL=False, **SMALL_TRUST)
class QueueTests(TrustCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.players = [self.make_player(f"p{i}") for i in range(4)]
        # weak: one player names a genus only
        self.weak = self.roi(validated=False)
        self.answer(self.players[0], self.weak, check=False, subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus")
        # agreed: three players agree on the species
        self.agreed = self.roi(validated=False)
        for p in self.players[:3]:
            self.answer(p, self.agreed, check=False, **AFFINIS)
        # expert: a proven expert names it
        self.prove(self.user, self.t_affinis)
        self.expert = self.roi(validated=False)
        self.label(self.user, self.expert, self.t_ferr)
        self.untouched = self.roi(validated=False)
        cache.clear()

    def make_player(self, name):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user(name, password="pw")

    def feed(self, query):
        self.client.force_login(self.staff)
        res = self.client.get(f"{URL}?{query}")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def ids(self, data):
        return [r["image_asset_id"] for r in data["results"]]

    def img(self, roi):
        return str(roi.image_asset_id)

    def test_sorted_from_most_to_least_confident(self):
        order = self.ids(self.feed("game=any&ordering=game_confidence"))
        self.assertEqual(order, [self.img(self.expert), self.img(self.agreed), self.img(self.weak)])

    def test_filter_to_images_with_proposals_or_only_expert_backed(self):
        self.assertEqual(set(self.ids(self.feed("game=any"))),
                         {self.img(self.expert), self.img(self.agreed), self.img(self.weak)})
        self.assertEqual(self.ids(self.feed("game=expert")), [self.img(self.expert)])

    def test_every_image_carries_its_proposal_for_the_badge(self):
        rows = {r["image_asset_id"]: r for r in self.feed("ordering=newest")["results"]}
        self.assertIsNone(rows[self.img(self.untouched)]["game"])
        agreed = rows[self.img(self.agreed)]["game"]
        self.assertEqual((agreed["agreed_rank"], agreed["players"], agreed["support"]), ("species", 3, 1.0))
        self.assertEqual(rows[self.img(self.expert)]["game"]["trusted_rank"], "species")

    def test_a_reviewed_proposal_drops_out_until_new_answers(self):
        LabelReview.objects.create(roi=self.agreed, decision="dismissed", answers=3)
        game_queue.forget()
        self.assertNotIn(self.img(self.agreed), self.ids(self.feed("game=any")))
        self.answer(self.players[3], self.agreed, check=False, **FERR)
        game_queue.forget()
        self.assertIn(self.img(self.agreed), self.ids(self.feed("game=any")))

    def test_reviewing_on_the_page_clears_the_cached_queue(self):
        self.feed("game=any")
        self.client.force_login(self.staff)
        res = self.client.post(f"/game/api/proposals/{self.agreed.id}/review/", '{"decision": "dismiss"}',
                               content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotIn(self.img(self.agreed), self.ids(self.feed("game=any")))
