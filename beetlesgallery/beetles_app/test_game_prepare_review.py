"""
Submit and Next answer at once (#602). While the player chooses, the server works out what the other players and
IBBI-AI say about the beetles on screen (game_prepare) and keeps it; the review after Submit reads it. The page only
learns it with the review: game_prepare's reply is empty. With "item_later" an answer brings its review alone and the
next beetle comes from game_item, asked for while the review is read.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, ModelPrediction
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase


class PrepareTests(ReviewCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.member = self.roi(self.t_affinis)
        self.open = [self.roi(self.t_affinis, validated=False), self.roi(self.t_plat, validated=False)]
        self.other("someone", self.open[0], {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus"})
        self.predict(self.open[1], self.t_plat, 0.9)
        tiles = [self.member, *self.open, self.roi(self.t_affinis)]
        self.item = {"a": str(tiles[0].id), "b": None, "check": True, "mode": "select", "tiles": [str(t.id) for t in tiles],
                     "rank": "genus", "group": game.lineage(self.t_affinis, "genus")}
        self.rnd = GameRound.objects.create(player=self.user, mode="select", items=[self.item])
        self.client.force_login(self.user)

    def prepare(self, rnd=None, index=0):
        return self.client.get(reverse("game_prepare", args=[(rnd or self.rnd).id]), {"index": index})

    def test_the_reply_gives_nothing_away(self):
        res = self.prepare()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {})
        for word in ("Xyleborus", "Platypus", "Scolytinae", "Platypodinae", str(self.open[0].id)):
            self.assertNotIn(word, res.content.decode())

    def test_the_review_reads_what_was_prepared(self):
        self.prepare()
        # gone since: worked out now, the review would know nothing about them
        GameAnswer.objects.exclude(player=self.user).delete()
        ModelPrediction.objects.all().delete()
        res = self.post("game_answer", {"index": 0, "picks": [0]}, self.rnd.id)
        self.assertEqual(res.status_code, 200, res.content)
        tiles = res.json()["review"]["grid"]["tiles"]
        self.assertEqual(tiles[1]["players"]["name"], "Xyleborus")
        self.assertEqual(tiles[2]["ai"]["name"], "Platypus")

    def test_without_it_the_review_works_it_out_as_before(self):
        res = self.post("game_answer", {"index": 0, "picks": [0]}, self.rnd.id)
        tiles = res.json()["review"]["grid"]["tiles"]
        self.assertEqual(tiles[1]["players"]["name"], "Xyleborus")
        self.assertEqual(tiles[2]["ai"]["name"], "Platypus")

    def test_kept_for_this_player_only(self):
        self.prepare()
        GameAnswer.objects.exclude(player=self.user).delete()
        stranger = get_user_model().objects.create_user("stranger", password="pw")
        rnd = GameRound.objects.create(player=stranger, mode="select", items=[self.item])
        self.client.force_login(stranger)
        tiles = self.post("game_answer", {"index": 0, "picks": [0]}, rnd.id).json()["review"]["grid"]["tiles"]
        self.assertIsNone(tiles[1]["players"])   # worked out for them, now that the other answer is gone

    def test_only_the_players_own_round(self):
        stranger = get_user_model().objects.create_user("stranger", password="pw")
        self.client.force_login(stranger)
        self.assertEqual(self.prepare().status_code, 404)

    def test_a_bad_index_is_harmless(self):
        self.assertEqual(self.prepare(index=7).json(), {})
        self.assertEqual(self.client.get(reverse("game_prepare", args=[self.rnd.id]), {"index": "x"}).status_code, 400)


class ItemLaterTests(ReviewCase):
    def setUp(self):
        super().setUp()
        rois = [self.roi(self.t_affinis), self.roi(self.t_ferr), self.roi(self.t_plat)]
        self.rnd = GameRound.objects.create(player=self.user, mode="classify",
                                            items=[{"a": str(r.id), "b": None, "check": True} for r in rois])
        self.client.force_login(self.user)

    def answer(self, index, **extra):
        body = dict({"index": index, "subfamily": "Scolytinae"}, **extra)
        return self.post("game_answer", body, self.rnd.id)

    def item(self, index, rnd=None):
        return self.client.get(reverse("game_item", args=[(rnd or self.rnd).id, index]))

    def test_the_review_alone_then_the_next_beetle(self):
        data = self.answer(0, item_later=True).json()
        self.assertIn("review", data)
        self.assertNotIn("item", data)
        self.assertEqual(data["next"], 1)
        res = self.item(1)
        self.assertEqual(res.status_code, 200)
        nxt = res.json()["item"]
        self.assertEqual((nxt["index"], len(nxt["images"])), (1, 1))
        self.assertEqual(self.answer(1).status_code, 200)   # and it is answered as usual

    def test_without_it_the_answer_carries_the_next_beetle(self):
        data = self.answer(0).json()
        self.assertEqual(data["item"]["index"], 1)
        self.assertNotIn("next", data)

    def test_only_the_next_beetle_to_answer(self):
        self.answer(0, item_later=True)
        self.assertEqual(self.item(0).status_code, 409)   # answered already
        self.assertEqual(self.item(2).status_code, 409)   # not yet
        stranger = get_user_model().objects.create_user("stranger", password="pw")
        self.client.force_login(stranger)
        self.assertEqual(self.item(1).status_code, 404)

    def test_the_last_beetle_still_ends_or_carries_on_in_one_answer(self):
        self.answer(0, item_later=True)
        self.answer(1, item_later=True)
        data = self.answer(2, item_later=True).json()
        self.assertNotIn("next", data)   # the batch's end: the next batch's first beetle, or the end, as before
        self.assertTrue("item" in data or data.get("done"))
