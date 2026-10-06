"""
One balanced scoring system (#530): a claim pays only from GAME_POINTS_CONFIDENCE sure (70%), in every game; a
correct deeper answer pays clearly more; a claim beyond the truth is wrong, never partly right, in the points and in
the verdicts; cautious answers earn something; no game is the way to farm points; old grids keep the points they had;
agreement on unvalidated beetles follows the same rule; the grid ladder and the difficulty agree with the scoring.
"""
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import (game, game_feedback, game_grid_ladder, game_scoring, game_tuning)
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, GameTuning
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase
from beetlesgallery.beetles_app.test_grid_builders import GridCase
from beetlesgallery.beetles_app.testing import make_taxon

RANKS = game.RANKS


def classify(named, right, weight=1.0):
    """Identification points for naming the first ``named`` ranks, the first ``right`` of them correctly."""
    return sum(game_scoring.classify_points({r: i < right for i, r in enumerate(RANKS[:named])}).values()) * weight


def pays(p, claim, stop):
    """Whether making a claim right with chance ``p`` earns more on average than not making it."""
    return p * claim[0] + (1 - p) * claim[1] > p * stop[0] + (1 - p) * stop[1]


class DefaultsTests(SimpleTestCase):
    """The rules at the default numbers, without a database (no overrides are stored)."""

    def setUp(self):
        game_tuning._cache.update(at=float("inf"), values={})   # the defaults: no GameTuning table needed
        self.addCleanup(game_tuning.forget)
        self.t, self.k = game_scoring.confidence(), game_scoring.wrong_cost()

    def test_the_default_asks_for_seventy_percent(self):
        self.assertAlmostEqual(self.t, 0.7)
        self.assertAlmostEqual(self.k, 0.7 / 0.3)

    def test_a_correct_deeper_answer_pays_clearly_more_and_more_so_toward_species(self):
        stops = [classify(n, n) for n in range(1, 5)]
        steps = [b - a for a, b in zip([0, *stops], stops)]
        self.assertEqual(steps, sorted(set(steps)))   # strictly growing steps
        rungs = [game_scoring.PAIR_POINTS[d] for d in range(-1, 4)]
        rises = [b - a for a, b in zip(rungs[1:], rungs[2:])]
        self.assertEqual(rungs, sorted(set(rungs)))
        self.assertEqual(rises, sorted(rises))
        self.assertGreater(game_scoring.pair_points(3, 3)[0], game_scoring.pair_points(2, 3)[0])

    def test_one_rank_deeper_pays_only_from_the_confidence_threshold(self):
        for named in range(1, 4):
            claim, stop = (classify(named + 1, named + 1), classify(named + 1, named)), (classify(named, named),) * 2
            with self.subTest(rank=RANKS[named]):
                self.assertTrue(pays(self.t + 0.02, claim, stop))
                self.assertFalse(pays(self.t - 0.02, claim, stop))

    def test_one_rung_closer_pays_only_from_the_confidence_threshold(self):
        for d in range(1, 4):
            claim = (game_scoring.pair_points(d, d)[0], game_scoring.pair_points(d, d - 1)[0])
            stop = (game_scoring.pair_points(d - 1, d)[0], game_scoring.pair_points(d - 1, d - 1)[0])
            with self.subTest(rung=d):
                self.assertTrue(pays(self.t + 0.02, claim, stop))
                self.assertFalse(pays(self.t - 0.02, claim, stop))

    def test_a_find_them_all_tap_pays_only_from_the_confidence_threshold(self):
        worth, members = 10.0, 4
        for p, expected in ((self.t + 0.02, True), (self.t - 0.02, False)):
            tap = (game_scoring.select_points(worth, members, 3, 0), game_scoring.select_points(worth, members, 2, 1))
            stop = (game_scoring.select_points(worth, members, 2, 0),) * 2
            self.assertEqual(pays(p, tap, stop), expected)

    def test_a_claim_beyond_the_truth_scores_below_zero_and_below_the_true_answer(self):
        for truth in range(-1, 4):
            for said in range(truth + 1, 4):
                points, state = game_scoring.pair_points(said, truth, bonus=1.25)
                with self.subTest(truth=truth, said=said):
                    self.assertLess(points, 0)
                    self.assertLess(points, game_scoring.pair_points(truth, truth)[0])
                    self.assertIn(state, ("too_close", "wrong"))
        # the owner's case: "same genus" for beetles of two subfamilies
        self.assertEqual(game_scoring.pair_points(2, -1), (-self.k * 7.0, "wrong"))
        # and the other way round: "different subfamilies" for two of one genus
        self.assertEqual(game_scoring.pair_points(-1, 2), (-self.k * 3, "wrong"))

    def test_a_wrong_rank_scores_below_stopping_above_it_and_below_zero(self):
        for named in range(1, 5):
            for right in range(named):
                with self.subTest(named=named, right=right):
                    self.assertLess(classify(named, right), classify(right, right) if right else 0)
                    self.assertLess(classify(named, right), 0)

    def test_cautious_answers_earn_something_and_less_than_the_precise_one(self):
        for truth in range(0, 4):
            for said in range(truth):
                points, state = game_scoring.pair_points(said, truth)
                self.assertEqual(state, "cautious")
                self.assertTrue(0 <= points < game_scoring.pair_points(truth, truth)[0])
        for stop in range(1, 4):
            self.assertTrue(0 < classify(stop, stop) < classify(stop + 1, stop + 1))

    def test_a_skip_costs_less_than_the_smallest_mistake_even_with_the_point_for_taking_part(self):
        part, unsure = 0.5, 0.25
        mistakes = [classify(n, n - 1) for n in range(1, 5)]
        mistakes += [game_scoring.pair_points(d + 1, d)[0] for d in range(-1, 3)] + [game_scoring.pair_points(-1, 0)[0]]
        self.assertLess(max(mistakes) + part, -unsure)

    def test_find_them_all_wrong_taps_are_bounded(self):
        worth = 13.125   # species, 9 beetles: 1.25 × 7 × 1.5
        share = worth / 3
        self.assertAlmostEqual(game_scoring.select_points(worth, 3, 0, 1), -self.k * share)
        self.assertAlmostEqual(game_scoring.select_points(worth, 3, 0, 6), -worth)   # never below a perfect grid's worth
        self.assertGreater(game_scoring.select_points(worth, 3, 3, 1), 0)            # all found, one slip: still a gain
        self.assertGreater(game_scoring.select_points(17.5, 6, 5, 1), 0)             # 16 beetles, 5 of 6 and one slip
        for size in (4, 9, 16):
            self.assertLess(game_tuning.select_tap_all(size), 0)   # tapping everything loses
            self.assertLess(game_tuning.odd_guess(size), 0)        # so does a blind Odd One Out pick

    def test_harder_tasks_pay_more_per_answer_but_no_game_farms_points(self):
        self.assertGreater(classify(4, 4, 3.0), 1.5 * 7 * 2)   # a species named beats the best grid
        self.assertGreater(1.5 * 7 * 2, 12 * 1.25)             # which beats the best Similarity answer
        play = game_tuning.expected_play()
        self.assertTrue(all(b <= game_tuning.FARM_BAND for b in play["band"]), play)
        for game_name, cells in play["rows"]:
            self.assertTrue(all(minute > 0 for _, minute in cells), (game_name, cells))

    def test_difficulty_and_retries_keep_the_threshold_sensible(self):
        for game_name, what, deeper, cells in game_tuning.thresholds():
            with self.subTest(game=game_name, claim=what):
                self.assertGreaterEqual(min(cells), 0.49)   # a coin flip never pays, even on the hardest beetles
                if deeper:
                    self.assertAlmostEqual(cells[1], self.t, places=2)
        for m in (0.75, 1.0, 1.25):   # a gain stays a gain and a loss a loss at any difficulty
            self.assertGreater(game_scoring.by_difficulty(classify(4, 4), m), 0)
            self.assertLess(game_scoring.by_difficulty(classify(4, 3), m), 0)

    def test_the_difficulty_zone_brackets_the_threshold(self):
        tuning = game_tuning.TUNABLES
        self.assertLess(tuning["GAME_DIFFICULTY_EASE_BELOW"]["default"], self.t)
        self.assertLess(self.t, tuning["GAME_DIFFICULTY_PUSH_ABOVE"]["default"])

    def test_the_old_cost_settings_are_gone(self):
        for key in ("GAME_POINTS_OVERREACH", "GAME_POINTS_WRONG_FACTOR", "GAME_POINTS_PAIR_STEP",
                    "GAME_POINTS_ODD_WRONG_FACTOR", "GAME_POINTS_SELECT_WRONG"):
            self.assertNotIn(key, game_tuning.TUNABLES)
        self.assertIn("GAME_POINTS_CONFIDENCE", game_tuning.TUNABLES)


class ChecksTests(GridCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)

    def failing(self):
        game_tuning.forget()
        return [rule for rule, ok, _ in game_tuning.checks() if not ok]

    def test_every_check_is_green_with_the_defaults(self):
        self.assertEqual(self.failing(), [])

    def test_flat_rungs_and_a_late_ease_off_are_flagged(self):
        GameTuning.objects.create(key="GAME_PAIR_POINTS", value={"-1": 1, "0": 2, "1": 4, "2": 7, "3": 7})
        GameTuning.objects.create(key="GAME_DIFFICULTY_EASE_BELOW", value=0.8)
        failing = self.failing()
        self.assertIn("Precise beats vague", failing)
        self.assertIn("The difficulty eases off before careful answers stop paying", failing)

    def test_a_lower_confidence_moves_every_threshold_with_it(self):
        GameTuning.objects.create(key="GAME_POINTS_CONFIDENCE", value=0.6)
        game_tuning.forget()
        self.assertAlmostEqual(game_scoring.wrong_cost(), 1.5)
        middling = [cells[1] for _, _, deeper, cells in game_tuning.thresholds() if deeper]
        self.assertTrue(all(abs(p - 0.6) < 0.01 for p in middling), middling)

    def test_the_scoring_page_shows_the_assessment(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("game_scoring")).content.decode()
        for testid in ("scoring-balance", "scoring-thresholds", "scoring-play", "tunable-GAME_POINTS_CONFIDENCE"):
            self.assertIn(f'data-testid="{testid}"', page)
        self.assertNotIn("GAME_POINTS_OVERREACH", page)


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class OldGridTests(GridCase):
    """A grid from before the ladder (no grid_step) keeps ×1 for its size, so a re-score doesn't inflate it."""

    def odd(self, size, step):
        rest, odd = self.known["affinis"][: size - 1], self.known["cylindrus"][0]
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="odd", index=0, roi=odd, roi_b=odd, is_check=True, grid_step=step,
            tiles=[str(b.id) for b in (*rest, odd)], grid_rank="species", correct_species=True,
            grid_group=game.lineage(self.taxa["affinis"], "species"))

    def select(self, size, step):
        tiles = self.known["affinis"][:3] + self.known["typographus"][: size - 3]
        rnd = GameRound.objects.create(player=self.user, mode="select", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="select", index=0, roi=tiles[0], is_check=True, grid_step=step,
            picks=[0, 1, 2], tiles=[str(t.id) for t in tiles], grid_rank="genus",
            grid_group=game.lineage(self.taxa["affinis"], "genus"))

    def points(self, answer):
        game_scoring.score_new_answer(answer)
        return AnswerPoints.objects.get(answer=answer).points

    def test_old_grids_keep_their_points_and_ladder_grids_get_the_size_factor(self):
        self.assertEqual(self.points(self.odd(9, None)), 1.5 * 1)
        self.assertEqual(self.points(self.odd(9, 2)), 1.5 * 1.5)
        worth = 1.25 * game_scoring.PAIR_POINTS[1]
        self.assertAlmostEqual(self.points(self.select(9, None)), worth)
        self.assertAlmostEqual(self.points(self.select(16, 9)), worth * 2)

    def test_a_re_score_leaves_an_old_grid_where_it_was(self):
        answer = self.odd(9, None)
        before = self.points(answer)
        game_scoring.recompute([self.user.id])
        self.assertEqual(AnswerPoints.objects.get(answer=answer).points, before)


class LadderTests(GridCase):
    """A good grid always scores and a poor one always loses (#530)."""

    def select(self, right, wrong):
        tiles = self.known["affinis"][:4] + self.known["typographus"][:5]
        rnd = GameRound.objects.create(player=self.user, mode="select", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="select", index=0, roi=tiles[0], is_check=True, grid_step=8,
            picks=list(range(right)) + list(range(4, 4 + wrong)), tiles=[str(t.id) for t in tiles], grid_rank="genus",
            grid_group=game.lineage(self.taxa["affinis"], "genus"))

    def test_outcomes_follow_the_points(self):
        for right, wrong in ((4, 0), (3, 0), (4, 1), (2, 1), (1, 1), (0, 0)):
            answer = self.select(right, wrong)
            points = game_scoring.select_truth(answer)[0]
            outcome = game_grid_ladder.outcome(answer)
            with self.subTest(right=right, wrong=wrong):
                if outcome == game_grid_ladder.GOOD:
                    self.assertGreater(points, 0)
                if outcome == game_grid_ladder.POOR:
                    self.assertLessEqual(points, 0)
                if points < 0:
                    self.assertEqual(outcome, game_grid_ladder.POOR)
        self.assertEqual(game_grid_ladder.outcome(self.select(2, 1)), game_grid_ladder.POOR)   # 2 − 2⅓ < 0
        self.assertIsNone(game_grid_ladder.outcome(self.select(4, 1)))


class PairResultsTests(SimpleTestCase):
    """game.score_pair: nothing below a wrong claim is judged right (#530)."""

    def taxon(self, sub, tribe, genus, species):
        return SimpleNamespace(subfamily=sub, tribe=tribe, genus=genus, species=species)

    def setUp(self):
        self.a = self.taxon("Scolytinae", "Xyleborini", "Xyleborus", "affinis")
        self.same_genus = self.taxon("Scolytinae", "Xyleborini", "Xyleborus", "ferrugineus")
        self.same_tribe = self.taxon("Scolytinae", "Xyleborini", "Xylosandrus", "crassiusculus")
        self.stranger = self.taxon("Platypodinae", "Platypodini", "Platypus", "cylindrus")

    def verdict(self, said, other):
        return game_feedback.pair_verdict(game.PAIR_DEPTH[said], game.score_pair(said, self.a, other))

    def test_same_genus_for_two_subfamilies_is_wrong_and_not_right_anywhere(self):
        results = game.score_pair("genus", self.a, self.stranger)
        self.assertEqual(results, {"subfamily": False, "tribe": None, "genus": None, "species": None})
        self.assertEqual(self.verdict("genus", self.stranger), "wrong")

    def test_verdicts_follow_the_claim(self):
        self.assertEqual(self.verdict("genus", self.same_genus), "right")
        self.assertEqual(self.verdict("tribe", self.same_genus), "partly")        # cautious, true as far as it goes
        self.assertEqual(self.verdict("genus", self.same_tribe), "wrong")         # one rung too close
        self.assertEqual(self.verdict("species", self.same_genus), "wrong")
        self.assertEqual(self.verdict("different", self.same_genus), "wrong")
        self.assertEqual(self.verdict("different", self.stranger), "right")
        self.assertEqual(self.verdict("subfamily", self.stranger), "wrong")

    def test_old_answers_judged_the_old_way_get_the_same_verdict(self):
        # before #530 "same genus" for two subfamilies was stored with "not the same species" right
        old = {"subfamily": False, "tribe": False, "genus": False, "species": True}
        self.assertEqual(game_feedback.pair_verdict(2, old), "wrong")
        # and "different subfamilies" for two of one genus, with its "not the same species" right
        old_different = {"subfamily": False, "tribe": False, "genus": False, "species": True}
        self.assertEqual(game_feedback.pair_verdict(-1, old_different), "wrong")


class VerdictTests(ReviewCase):
    """The card after each answer and the round review call a claim beyond the truth wrong, never partly correct."""

    def pair(self, a, b, said):
        return self.answer({"a": str(a.id), "b": str(b.id), "check": True, "mode": "pair"}, {"pair_answer": said})

    def test_same_genus_for_two_subfamilies_is_not_quite(self):
        review = self.pair(self.roi(self.t_affinis), self.roi(self.t_plat), "genus")
        self.assertEqual((review["verdict"], review["pair"]["state"]), ("wrong", "wrong"))
        self.assertRegex(review["headline"], r"^Not quite · You said Same genus · It's Different subfamily · −")   # #569
        answer = GameAnswer.objects.get(player=self.user, mode="pair")
        self.assertIsNot(answer.correct_species, True)
        self.assertEqual(game_feedback.round_feedback(answer.round)["items"][0]["verdict"], "wrong")

    def test_one_rung_too_close_is_wrong_and_a_cautious_rung_partly_correct(self):
        xylosandrus = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xylosandrus",
                                 species="crassiusculus", scientific_name="Xylosandrus crassiusculus")
        too_close = self.pair(self.roi(self.t_affinis), self.roi(xylosandrus), "genus")
        self.assertEqual((too_close["verdict"], too_close["pair"]["state"]), ("wrong", "too_close"))
        self.assertLess(too_close["points"]["earned"], 0)
        cautious = self.pair(self.roi(self.t_affinis), self.roi(self.t_ferr), "tribe")
        self.assertEqual((cautious["verdict"], cautious["pair"]["state"]), ("partly", "cautious"))
        self.assertGreater(cautious["points"]["earned"], 0)

    def test_identification_verdicts(self):
        right = self.classify(self.roi(self.t_affinis), AFFINIS)
        stopped = self.classify(self.roi(self.t_affinis), dict(AFFINIS, species=""))
        overreach = self.classify(self.roi(self.t_affinis), FERR)
        self.assertEqual([r["verdict"] for r in (right, stopped, overreach)], ["right", "partly", "wrong"])
        self.assertEqual(stopped["points"]["earned"], 21.0)
        self.assertEqual(overreach["points"]["earned"], -35.0)
        self.assertEqual(overreach["headline"], "Correct to genus · −35")


class ConsensusTests(SimpleTestCase):
    """On an unvalidated beetle a disputed deeper name takes back what the ranks above it earned (#530)."""

    class Judges:
        def weight(self, *args):
            return 1.0

    def setUp(self):
        game_tuning._cache.update(at=float("inf"), values={})
        self.addCleanup(game_tuning.forget)

    def points(self, names, votes):
        answer = SimpleNamespace(mode="classify", skipped=False, player_id=1, **names)
        return game_scoring.consensus_points(answer, votes, self.Judges())[0]

    def test_a_guess_strong_players_dispute_pays_less_than_stopping(self):
        judge = [(2, {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus",
                      "species": "Xyleborus ferrugineus"})]
        agreed = self.points(FERR, judge)
        stopped = self.points(dict(AFFINIS, species=""), judge)
        guessed = self.points(AFFINIS, judge)
        self.assertAlmostEqual(agreed, 0.6 * 15 * 0.5)
        self.assertAlmostEqual(stopped, 0.6 * 7 * 0.5)
        self.assertGreater(stopped, guessed)
        self.assertGreaterEqual(guessed, 0.0)   # never below zero on an unvalidated beetle
