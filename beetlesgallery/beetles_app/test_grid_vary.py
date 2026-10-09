"""
The number to find varies from grid to grid, so it isn't always the same amount to select (the owner's ask).

Odd One Out: a step's odd ones are the most its grids hide; GAME_ODD_FEWER_SHARE of them hide evenly fewer, never none.
Find Them All: GAME_SELECT_FEWER_SHARE of the grids hold evenly fewer AI beetles and more of other groups, with the
members drawn across their whole range (SELECT_MEMBERS); validated non-members are still never fewer than members.
The page asks for each grid's own number of odd ones, and its points, review and ladder outcome go by it.
"""
import random
from collections import Counter
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_grid_ladder as ladder, game_scoring, game_tuning
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, GameTuning, GridStep
from beetlesgallery.beetles_app.test_grid_builders import GridCase, at

KEYS = ("GAME_ODD_FEWER_SHARE", "GAME_SELECT_FEWER_SHARE")
DEFAULTS = {key: game_tuning.TUNABLES[key]["default"] for key in KEYS}   # GridCase turns the variation off


def seeded(case, seed=540):
    state = random.getstate()
    random.seed(seed)
    case.addCleanup(random.setstate, state)


class OddCountTests(TestCase):
    def setUp(self):
        seeded(self)

    def test_the_defaults_are_the_tunables(self):
        self.assertEqual(DEFAULTS, {"GAME_ODD_FEWER_SHARE": 0.5, "GAME_SELECT_FEWER_SHARE": 0.3})
        with mock.patch.object(game, "game_setting", side_effect=lambda name, default: default) as setting:
            game.odd_count(3)
            game.select_fewer()
        self.assertEqual({c.args for c in setting.call_args_list}, {(k, v) for k, v in DEFAULTS.items()})

    def test_counts_vary_up_to_the_steps_number_and_never_reach_none(self):
        for wanted in (2, 3, 4):
            with self.subTest(wanted=wanted):
                counts = Counter(game.odd_count(wanted) for _ in range(4000))
                self.assertEqual(set(counts), set(range(1, wanted + 1)))   # every count from one to the step's
                self.assertAlmostEqual(counts[wanted] / 4000, 1 - DEFAULTS["GAME_ODD_FEWER_SHARE"], delta=0.04)
                fewer = [counts[k] / 4000 for k in range(1, wanted)]
                for share in fewer:   # evenly among the fewer counts
                    self.assertAlmostEqual(share, DEFAULTS["GAME_ODD_FEWER_SHARE"] / (wanted - 1), delta=0.04)

    def test_a_step_of_one_odd_one_stays_at_one(self):
        self.assertEqual({game.odd_count(1) for _ in range(200)}, {1})

    def test_the_share_is_tunable(self):
        with override_settings(GAME_ODD_FEWER_SHARE=0):
            self.assertEqual({game.odd_count(4) for _ in range(200)}, {4})
        with override_settings(GAME_ODD_FEWER_SHARE=1):
            self.assertEqual({game.odd_count(4) for _ in range(400)}, {1, 2, 3})
        GameTuning.objects.create(key="GAME_ODD_FEWER_SHARE", value=0)   # from the Scoring page
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)
        self.assertEqual({game.odd_count(3) for _ in range(200)}, {3})


class SelectMixTests(TestCase):
    """Every composition select_mix can make, for every size and AI count a level brings, with and without fewer."""

    def setUp(self):
        seeded(self)

    def mixes(self, size, ai, fewer, n=600):
        return [game.select_mix(size, 16, ai, 25, ai, fewer) for _ in range(n)]

    def test_within_the_rules_at_every_size(self):
        k = game_scoring.wrong_cost()
        for size in game.GRID_SIZES:
            low, high = game.SELECT_MEMBERS[size]
            cap = game.SELECT_AI[size][1]
            for ai in range((max(2, cap) if size > 4 else cap) + 1):   # a pair of AI beetles takes two (select_ai)
                for fewer in (False, True):
                    for m, a, others in self.mixes(size, ai, fewer, 100):
                        with self.subTest(size=size, ai=ai, fewer=fewer, mix=(m, a, others)):
                            self.assertEqual(m + a + others, size)
                            self.assertTrue(1 <= low <= m <= high)   # a member to find: tapping nothing never wins
                            self.assertLessEqual(a, ai)
                            self.assertGreaterEqual(others, m)   # tapping everything never wins
                            self.assertLess(game_scoring.select_points(1.0, m, m, others), 0)
                            self.assertLess(m - k * others, 0)

    def test_fewer_grids_vary_how_many_are_the_groups_and_reach_every_member_count(self):
        for size in game.GRID_SIZES:
            low, high = game.SELECT_MEMBERS[size]
            ai = game.SELECT_AI[size][1]
            with self.subTest(size=size):
                full = self.mixes(size, ai, False)
                fewer = self.mixes(size, ai, True)
                self.assertEqual({a for _, a, _ in full}, {ai})   # the level's AI beetles, as before
                self.assertLess(max(a for _, a, _ in fewer), ai)
                group = {m + a for m, a, _ in full + fewer}   # the beetles a player should select (AI ones included)
                self.assertGreaterEqual(len(group), 2, group)
                self.assertEqual({m for m, _, _ in full + fewer}, set(range(low, high + 1)))

    def test_short_of_others_a_fewer_grid_keeps_more_ai_beetles_instead_of_shrinking(self):
        self.assertEqual({game.select_mix(9, 3, 3, 3, 3, True) for _ in range(50)}, {(3, 3, 3)})
        self.assertEqual({game.select_mix(4, 1, 1, 2, 1, True) for _ in range(50)}, {(1, 1, 2)})

    def test_without_fewer_nothing_changes(self):
        self.assertEqual({game.select_mix(9, 4, 3, 6, 3) for _ in range(50)}, {(3, 3, 3)})
        self.assertIsNone(game.select_mix(9, 2, 3, 6, 3))   # too few members found


@override_settings(**DEFAULTS)
class BuildTests(GridCase):
    def setUp(self):
        super().setUp()
        seeded(self)

    def name_at(self, roi, rank):
        return game.lineage(roi.taxon, rank)[rank].lower()

    def test_odd_one_out_grids_hide_from_one_to_the_steps_number(self):
        step = ladder.step_for(16, "genus", 3)
        at(self.user, "odd", step)
        counts = Counter()
        for _ in range(24):
            item = game.build_odd_items(self.user, 1)[0]
            self.assertEqual((item["size"], item["rank"], item["step"]), (16, "genus", step))
            found = game.Beetles.objects.select_related("taxon").filter(id__in=item["tiles"])
            outside = {str(b.id) for b in found if self.name_at(b, "genus") != item["group"]["genus"].lower()}
            self.assertEqual(outside, set(item["odds"]))   # exactly the odd ones, all validated
            self.assertTrue(all(b.bbox_is_validated for b in found if str(b.id) in outside))
            self.assertEqual(item["a"], item["odds"][0])
            counts[len(item["odds"])] += 1
        self.assertLessEqual(set(counts), {1, 2, 3})
        self.assertIn(3, counts)
        self.assertGreaterEqual(len(counts), 2, counts)

    def test_find_them_all_grids_vary_how_many_are_the_groups(self):
        for beetles in self.unknown.values():   # a sure and an unsure AI beetle in every species
            self.predict(beetles[0], 0.95)
            self.predict(beetles[1], 0.3)
        at(self.user, "select", ladder.step_for(9, "species", game_key="select"))
        group_sizes, members = Counter(), Counter()
        with override_settings(GAME_SELECT_FEWER_SHARE=0.5):
            for _ in range(40):
                item = game.build_select_items(self.user, 1)[0]
                found = list(game.Beetles.objects.select_related("taxon").filter(id__in=item["tiles"]))
                self.assertEqual(len(found), 9)
                known = [b for b in found if b.bbox_is_validated]
                m = sum(self.name_at(b, "species") == item["group"]["species"].lower() for b in known)
                low, high = game.SELECT_MEMBERS[9]
                self.assertTrue(low <= m <= high, m)
                self.assertGreaterEqual(len(known) - m, m)   # validated non-members never fewer than members
                self.assertLessEqual(9 - len(known), 2)   # AI beetles: at most what level 3 brings (a pair)
                group_sizes[m + 9 - len(known)] += 1
                members[m] += 1
        self.assertGreaterEqual(len(group_sizes), 2, group_sizes)
        self.assertEqual(set(members), {3, 4})   # the members reach the top of their range

    def test_off_every_grid_asks_for_the_steps_number(self):
        at(self.user, "odd", ladder.step_for(9, "species", 2))
        with override_settings(GAME_ODD_FEWER_SHARE=0):
            self.assertEqual({len(game.build_odd_items(self.user, 1)[0]["odds"]) for _ in range(6)}, {2})


@override_settings(GAME_POINTS_PARTICIPATION=0.0, **DEFAULTS)
class FeedTests(GridCase):
    """A grid hiding fewer odd ones than its step: the page asks for its own number, and everything after goes by it."""

    def start(self, size=16, odds=3, hides=1):
        at(self.user, "odd", ladder.step_for(size, "species", odds))
        with mock.patch.object(game, "odd_count", return_value=hides):
            res = self.post("game_start", {"mode": "odd"})
        self.assertEqual(res.status_code, 200, res.content)
        self.rnd, self.item = GameRound.objects.get(id=res.json()["round"]), res.json()["item"]
        self.grid = self.rnd.items[self.item["index"]]
        self.odd_places = [self.grid["tiles"].index(t) for t in self.grid["odds"]]
        self.rest = [i for i in range(len(self.grid["tiles"])) if i not in self.odd_places]
        return self.item

    def answer(self, picks):
        return self.post("game_answer", {"picks": picks, "index": self.item["index"]}, self.rnd.id)

    def test_the_page_asks_for_the_grids_own_number(self):
        item = self.start(hides=2)
        self.assertEqual((item["size"], item["odds"], len(self.grid["odds"])), (16, 2, 2))
        self.assertEqual(ladder.steps("odd")[item["step"] - 1][2], 3)   # its step hides three
        for picks in ([self.odd_places[0]], [*self.odd_places, self.rest[0]]):   # neither one nor the step's three
            with self.subTest(picks=picks):
                res = self.answer(picks)
                self.assertEqual((res.status_code, res.json()["error"]), (400, "Please pick 2 beetles."))
        self.assertEqual(self.answer(self.odd_places).status_code, 200)

    def test_found_the_one_it_hid_is_a_full_good_grid(self):
        self.start(hides=1)
        self.assertEqual(self.item["odds"], 1)
        data = self.answer(self.odd_places).json()
        answer = GameAnswer.objects.get()
        self.assertEqual((answer.picks, answer.is_check, answer.correct_species), (self.odd_places, True, True))
        points = AnswerPoints.objects.get(answer=answer)
        self.assertAlmostEqual(points.points, points.detail["worth"], delta=0.006)   # all of it: the grid was solved
        self.assertEqual((points.detail["odds"], points.detail["found"]), (1, 1))
        self.assertEqual(data["review"]["verdict"], "right")
        self.assertEqual(ladder.outcome(answer), ladder.GOOD)
        self.assertEqual(GridStep.objects.get(player=self.user, game="odd").good_run, 1)

    def test_one_found_one_wrong_of_two_is_poor(self):
        self.start(hides=2)
        data = self.answer([self.odd_places[0], self.rest[0]]).json()
        answer = GameAnswer.objects.get()
        points = AnswerPoints.objects.get(answer=answer)
        share = points.detail["worth"] / 2   # split over the grid's two, not the step's three
        self.assertAlmostEqual(points.points, share * (1 - game_scoring.wrong_cost()), places=2)
        self.assertTrue(data["review"]["headline"].startswith("Found 1 of 2 · 1 wrong"))
        self.assertEqual(ladder.outcome(answer), ladder.POOR)


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class ScoringTests(GridCase):
    """At one step a grid is worth the same whether it hides one, two or three odd ones: each pick is a share of it."""

    def answer(self, odds, picks):
        rest = self.known["affinis"][: 16 - len(odds)]
        tiles = [*rest, *odds]
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="odd", index=0, roi=tiles[picks[0]], roi_b=odds[0], is_check=True,
            picks=sorted(picks), tiles=[str(t.id) for t in tiles], grid_rank="species",
            grid_step=ladder.step_for(16, "species", 3), grid_group=game.lineage(self.taxa["affinis"], "species"))

    def test_points_and_the_ladder_go_by_the_grids_own_odd_ones(self):
        others = [self.known["cylindrus"][0], self.known["parallelus"][0], self.known["cylindrus"][1]]
        worth = 1.5 * game_scoring.PAIR_POINTS[-1] * 2.0   # the odd one weight, another subfamily, sixteen beetles
        for n in (1, 2, 3):
            with self.subTest(odd_ones=n):
                GameAnswer.objects.all().delete()
                places = list(range(16 - n, 16))
                answer = self.answer(others[:n], places)
                game_scoring.score_new_answer(answer)
                row = AnswerPoints.objects.get(answer=answer)
                self.assertAlmostEqual(row.points, worth)
                self.assertEqual((row.detail["odds"], row.detail["share"]), (n, round(worth / n, 3)))
                self.assertEqual(ladder.outcome(answer), ladder.GOOD)
                grid = game.score_odd_grid(game_scoring.grid_tiles(answer), answer.picks, "species",
                                           answer.grid_group)
                self.assertEqual((grid["odds"], grid["right"]), (n, n))


class TuningTests(GridCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)

    def test_every_count_a_grid_can_hide_still_loses_to_blind_picks(self):
        for size, most in ladder.ODD_SHAPES:
            for k in range(1, most + 1):
                with self.subTest(size=size, odds=k):
                    self.assertLess(game_tuning.odd_guess(size, k), 0)
        self.assertTrue(all(ok for _, ok, _ in game_tuning.checks()), game_tuning.checks())

    def test_the_scoring_page_has_both_shares(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("game_scoring")).content.decode()
        for key in KEYS:
            self.assertIn(f'data-testid="tunable-{key}"', page)
        self.assertIn("Odd One Out: grids hiding fewer odd ones", page)
        self.assertIn("Find Them All: grids with fewer AI beetles", page)
