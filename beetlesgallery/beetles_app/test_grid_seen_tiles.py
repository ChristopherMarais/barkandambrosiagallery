"""
A grid's review says WHICH tiles were seen before (#600): each such tile gets a small "Seen before" mark, and the
headline pill is left to the single-photo games. game.seen_recently_ids answers photo by photo what seen_recently
answers for a set.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import GameAnswer
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase


class SeenTilesTests(ReviewCase):
    def setUp(self):
        super().setUp()
        self.members = [self.roi(self.t_affinis) for _ in range(3)]
        self.others = [self.roi(self.t_ferr), self.roi(self.t_ferr), self.roi(self.t_plat)]

    def grid(self, tiles, picks):
        item = {"a": str(tiles[0].id), "b": None, "check": True, "mode": "select",
                "tiles": [str(t.id) for t in tiles], "rank": "species", "group": game.lineage(self.t_affinis, "species")}
        return self.answer(item, {"picks": picks})

    def test_a_first_grid_has_nothing_seen_and_its_own_answer_never_counts(self):
        review = self.grid([*self.members, *self.others], [0])
        self.assertEqual(review["grid"]["seen"], [])
        self.assertFalse(review["again"])

    def test_the_tiles_seen_in_an_earlier_grid_are_marked_one_by_one(self):
        self.grid([*self.members, *self.others], [0])
        fresh = [self.roi(self.t_affinis), self.roi(self.t_ferr)]
        review = self.grid([fresh[0], self.members[1], fresh[1], self.others[2]], [0])
        self.assertEqual(review["grid"]["seen"], [1, 3])
        self.assertFalse(review["again"])   # the headline pill is for one photo

    def test_a_beetle_named_in_identification_counts_too(self):
        self.classify(self.others[0], AFFINIS)
        review = self.grid([*self.members, *self.others], [0])
        self.assertEqual(review["grid"]["seen"], [3])

    def test_ids_helper_matches_seen_recently_and_forgets_after_the_recall_days(self):
        self.classify(self.members[0], AFFINIS)
        ids = [m.id for m in self.members]
        self.assertEqual(game.seen_recently_ids(self.user, ids), {self.members[0].id})
        self.assertTrue(game.seen_recently(self.user, ids))
        GameAnswer.objects.filter(player=self.user).update(answered_at=timezone.now() - timedelta(days=400))
        self.assertEqual(game.seen_recently_ids(self.user, ids), set())
        self.assertFalse(game.seen_recently(self.user, ids))

    def test_a_single_photo_seen_before_keeps_the_headline_pill(self):
        roi = self.roi(self.t_affinis)
        self.classify(roi, AFFINIS)
        self.assertTrue(self.classify(roi, AFFINIS)["again"])


class PageTests(ReviewCase):
    def test_each_seen_tile_gets_its_mark(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn('if ((g.seen || []).includes(i)) cellEl.appendChild(seenMark("tile-seen"));', page)
        self.assertIn("function seenMark(testid)", page)
        self.assertIn("seen.dataset.testid = testid;", page)
        self.assertIn('[data-size="25"] .rv-seen {', page)
