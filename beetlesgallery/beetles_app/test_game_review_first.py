"""
The review first, always: an answer sent with "item_later" never builds beetles before its reply, not even at the end
of a batch. Building the next batch there (or the beetles a batch started small still gets) kept the review waiting for
seconds, most of all in Find Them All. Past the batch's last beetle, game_item carries the feed on while the review is
read: a beetle the batch still gets, the next batch's first (its "round"), or the end of the feed ("done").
"""
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app import game_grow, game_views
from beetlesgallery.beetles_app.models import GameRound
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


class ReviewFirstTests(ReviewCase):
    def setUp(self):
        super().setUp()
        rois = [self.roi(self.t_affinis), self.roi(self.t_ferr)]
        self.rnd = GameRound.objects.create(player=self.user, mode="classify",
                                            items=[{"a": str(r.id), "b": None, "check": True} for r in rois])
        self.client.force_login(self.user)

    def answer(self, index, **extra):
        return self.post("game_answer", dict({"index": index, "subfamily": "Scolytinae"}, **extra), self.rnd.id)

    def item(self, index, rnd=None):
        return self.client.get(reverse("game_item", args=[(rnd or self.rnd).id, index]))

    def test_the_last_beetles_answer_builds_nothing(self):
        self.answer(0, item_later=True)
        with mock.patch.object(game_views, "_next_batch") as next_batch, \
                mock.patch.object(game_grow, "grow_or_wait") as grow:
            data = self.answer(1, item_later=True).json()
        next_batch.assert_not_called()
        grow.assert_not_called()
        self.assertIn("review", data)
        self.assertEqual(data["next"], 2)   # the place after the batch's last beetle
        self.assertFalse({"item", "round", "done"} & set(data))
        self.rnd.refresh_from_db()
        self.assertIsNone(self.rnd.finished_at)   # closed once the feed has moved on (game_item)

    def test_then_the_next_batch_comes_with_the_next_beetle(self):
        for taxon in (self.t_plat, self.t_affinis, self.t_ferr):
            self.roi(taxon)
        self.answer(0, item_later=True)
        self.answer(1, item_later=True)
        res = self.item(2)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        fresh = GameRound.objects.get(id=data["round"])
        self.assertNotEqual(fresh.id, self.rnd.id)
        self.assertEqual(data["item"]["index"], game_views._next_index(fresh, 0))
        self.assertIn("notice", data)
        self.rnd.refresh_from_db()
        self.assertIsNotNone(self.rnd.finished_at)
        self.assertEqual(self.item(2).status_code, 409)   # asked again: the batch has moved on
        # and the new batch is answered as usual
        res = self.post("game_answer", {"index": data["item"]["index"], "subfamily": "Scolytinae"}, fresh.id)
        self.assertEqual(res.status_code, 200, res.content)

    def test_or_the_end_of_the_feed(self):
        self.answer(0, item_later=True)
        self.answer(1, item_later=True)
        data = self.item(2).json()   # every beetle there is has been answered
        self.assertTrue(data["done"])
        self.assertIn("summary", data)
        self.assertNotIn("item", data)

    def test_a_batch_still_growing_gets_its_next_beetle_there(self):
        extra = self.roi(self.t_plat)
        self.answer(0, item_later=True)
        self.answer(1, item_later=True)

        def grow(rnd, n):
            rnd.items = rnd.items + [{"a": str(extra.id), "b": None, "check": True}]
            GameRound.objects.filter(id=rnd.id).update(items=rnd.items)
            return 1

        with mock.patch.object(game_grow, "wanted", return_value=1), \
                mock.patch.object(game_grow, "grow_or_wait", side_effect=grow):
            data = self.item(2).json()
        self.assertNotIn("round", data)   # still this batch
        self.assertEqual(data["item"]["index"], 2)

    def test_only_the_place_after_the_last_answer(self):
        self.answer(0, item_later=True)
        self.answer(1, item_later=True)
        self.assertEqual(self.item(3).status_code, 409)
        self.assertEqual(self.item(1).status_code, 409)

    def test_a_skip_still_carries_on_in_one_answer(self):
        self.answer(0, item_later=True)
        data = self.answer(1, skipped=True).json()   # a skip goes straight on: it takes what comes next at once
        self.assertNotIn("next", data)
        self.assertTrue("item" in data or data.get("done"))


class PageTests(SimpleTestCase):
    def test_next_waits_for_what_comes_next_before_it_reads_it(self):
        self.assertIn("Object.assign(data, d)", js_function("fetchItem"))
        nxt = js_function("next")
        wait = nxt.index("if (!data.item && data.itemLoad) await data.itemLoad;")
        self.assertLess(wait, nxt.index("feedNotes(data);"))
        self.assertLess(wait, nxt.index("if (data.done)"))
        self.assertLess(wait, nxt.index("if (data.round) roundId = data.round;"))
