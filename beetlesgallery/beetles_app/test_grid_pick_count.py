"""
How many beetles a grid asks for changes from grid to grid, so a player can't count on it (the owner: "the number you
have to select can also vary"). Odd One Out hides one fewer to one more odd ones than the player's step says (never
more than its size allows, always one in 4) and the question says how many; Find Them All draws its number of members
for each grid, at least one and never all, and never tells it.
"""
import random
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from beetlesgallery.beetles_app import game, game_grid_ladder as ladder
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound
from beetlesgallery.beetles_app.test_grid_builders import GridCase, at


def seeded(case, seed=642):
    state = random.getstate()
    random.seed(seed)
    case.addCleanup(random.setstate, state)


@override_settings(GAME_GRID_VARY_COUNT=True)
class OddCountTests(TestCase):
    def test_around_the_steps_number_within_what_the_size_allows(self):
        seeded(self)
        for size, odds in ladder.ODD_SHAPES:
            with self.subTest(size=size, odds=odds):
                seen = {game.odd_count(size, odds) for _ in range(200)}
                low, high = max(1, odds - 1), min(ladder.most_odds(size), odds + 1)
                self.assertEqual(seen, set(range(low, high + 1)))

    def test_a_grid_of_four_always_hides_one(self):
        seeded(self)
        self.assertEqual({game.odd_count(4, 1) for _ in range(50)}, {1})

    def test_bounds_per_size(self):
        seeded(self)
        for size, most in ((4, 1), (9, 2), (16, 3), (25, 4)):
            seen = {game.odd_count(size, odds) for odds in range(1, most + 1) for _ in range(100)}
            self.assertEqual(seen, set(range(1, most + 1)), size)

    @override_settings(GAME_GRID_VARY_COUNT=False)
    def test_off_it_is_the_steps_number(self):
        self.assertEqual([game.odd_count(size, odds) for size, odds in ladder.ODD_SHAPES],
                         [odds for _, odds in ladder.ODD_SHAPES])


class SelectMixTests(TestCase):
    def test_members_vary_within_the_band_never_all_and_never_more_than_the_others(self):
        seeded(self)
        for size, (low, high) in game.SELECT_MEMBERS.items():
            with self.subTest(size=size):
                ai = game.SELECT_AI[size][0]
                mixes = [game.select_mix(size, high, ai, size, ai) for _ in range(200)]
                members = {m for m, _, _ in mixes}
                self.assertTrue(members and min(members) >= max(1, low) and max(members) <= high, members)
                self.assertTrue(all(m < size and o >= m and m + a + o == size for m, a, o in mixes))
                if size > 4:
                    self.assertGreater(len(members), 1)   # not the same number every grid

    def test_at_least_one_member_in_every_band(self):
        self.assertTrue(all(1 <= low <= high <= size // 2 for size, (low, high) in game.SELECT_MEMBERS.items()))


@override_settings(GAME_GRID_VARY_COUNT=True, GAME_ROUND_SIZE=2)
class OddGridTests(GridCase):
    def test_grids_at_one_step_hide_different_numbers_of_odd_ones(self):
        seeded(self)
        at(self.user, "odd", ladder.step_for(16, "genus", 2))
        counts = []
        for _ in range(10):
            item = game.build_odd_items(self.user, 1)[0]
            counts.append(len(item["odds"]))
            group = item["group"]["genus"]
            found = game.Beetles.objects.select_related("taxon").filter(id__in=item["tiles"])
            self.assertEqual({str(b.id) for b in found if b.taxon.genus != group}, set(item["odds"]))
            self.assertEqual(item["size"], 16)
        self.assertTrue(set(counts) <= {1, 2, 3}, counts)
        self.assertGreater(len(set(counts)), 1, counts)

    def test_the_feed_says_how_many_and_takes_exactly_that_many(self):
        for k in (1, 2, 3):
            with self.subTest(odds=k), mock.patch.object(game, "odd_count", lambda size, odds, k=k: k):
                GameRound.objects.all().delete()
                at(self.user, "odd", ladder.step_for(16, "species", 2))
                res = self.post("game_start", {"mode": "odd"})
                self.assertEqual(res.status_code, 200, res.content)
                rnd, item = GameRound.objects.get(id=res.json()["round"]), res.json()["item"]
                grid = rnd.items[item["index"]]
                self.assertEqual((item["odds"], len(grid["odds"])), (k, k))
                places = [grid["tiles"].index(t) for t in grid["odds"]]
                rest = next(i for i in range(len(grid["tiles"])) if i not in places)
                body = {"picks": places + [rest], "index": item["index"]}
                self.assertEqual(self.post("game_answer", body, rnd.id).status_code, 400)
                body = {"picks": places, "index": item["index"]}
                self.assertEqual(self.post("game_answer", body, rnd.id).status_code, 200)
                self.assertEqual(GameAnswer.objects.get(round=rnd).picks, sorted(places))

    def test_the_question_states_the_number(self):
        page = Path(settings.BASE_DIR, "beetlesgallery", "templates", "beetles", "game_play.html").read_text(
            encoding="utf-8")
        self.assertIn('return n > 1 ? "Which " + n + " are a different " + rank + "?"', page)
        self.assertIn("oddPrompt(item.rank, oddWant)", page)


@override_settings(GAME_POINTS_PARTICIPATION=0.0, GAME_ROUND_SIZE=2)
class SelectGridTests(GridCase):
    def members(self, grid):
        rank, group = grid["rank"], grid["group"][grid["rank"]].lower()
        found = {str(b.id): b for b in game.Beetles.objects.select_related("taxon").filter(id__in=grid["tiles"])}
        return [i for i, t in enumerate(grid["tiles"]) if game.lineage(found[t].taxon, rank)[rank].lower() == group]

    def test_a_grid_of_one_to_four_members_plays_and_scores_in_full(self):
        for m in (1, 2, 3, 4):
            with self.subTest(members=m), mock.patch.dict(game.SELECT_MEMBERS, {9: (m, m)}):
                GameRound.objects.all().delete()
                at(self.user, "select", ladder.step_for(9, "species", game_key="select"))
                res = self.post("game_start", {"mode": "select"})
                self.assertEqual(res.status_code, 200, res.content)
                rnd, item = GameRound.objects.get(id=res.json()["round"]), res.json()["item"]
                self.assertNotIn("odds", item)       # Find Them All never says how many
                self.assertNotIn("members", item)
                grid = rnd.items[item["index"]]
                members = self.members(grid)
                self.assertEqual(len(members), m)
                self.assertGreaterEqual(9 - m, m)
                data = self.post("game_answer", {"picks": members, "index": item["index"]}, rnd.id).json()
                self.assertEqual(data["review"]["verdict"], "right")
                points = AnswerPoints.objects.get(answer__round=rnd)
                self.assertEqual(points.detail["members"], m)
                self.assertAlmostEqual(points.points, points.detail["worth"], delta=0.006)   # a perfect grid, whatever m

    def test_one_tap_is_enough_to_submit(self):
        at(self.user, "select", ladder.step_for(9, "species", game_key="select"))
        res = self.post("game_start", {"mode": "select"})
        rnd, item = GameRound.objects.get(id=res.json()["round"]), res.json()["item"]
        self.assertEqual(self.post("game_answer", {"picks": [0], "index": item["index"]}, rnd.id).status_code, 200)
