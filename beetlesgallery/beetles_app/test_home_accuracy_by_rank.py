"""
The game home's accuracy plot as four small plots, one per rank (subfamily, tribe, genus, species): each is the
players' naming accuracy at that rank, their skills there added up over every branch as the report adds them, with
the player's own value, colour and place, the minimum-answers rule, the empty state and the page.
"""
import re

from django.db import connection
from django.template.loader import render_to_string
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from beetlesgallery.beetles_app import game_board, game_trust
from beetlesgallery.beetles_app.game import RANKS
from beetlesgallery.beetles_app.game_scale import value_step
from beetlesgallery.beetles_app.models import PlayerScore, PlayerSkill
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


def skill(player, rank, branch, correct, judged):
    return PlayerSkill.objects.create(player=player, rank=rank, branch=branch, correct=correct, judged=judged)


def at(standing, rank):
    return next(s for s in standing["ranks"] if s["rank"] == rank)


@override_settings(GAME_MIN_JUDGED_FOR_ACCURACY=10)
class RankStandingTests(ScoringCase):
    def test_four_distributions_from_each_players_skills_added_up_per_rank(self):
        a, b = self.player("a"), self.player("b")
        # a: species in two genera, 8/10 and 4/10 -> 12/20 = 60%; b: one genus, 18/20 = 90%
        skill(a, "species", "Xyleborus", 8, 10)
        skill(a, "species", "Ips", 4, 10)
        skill(b, "species", "Xyleborus", 18, 20)
        skill(a, "subfamily", "", 19, 20)
        skill(b, "subfamily", "", 10, 20)
        s = game_board.accuracy_standing_by_rank(self.user)
        self.assertEqual([r["rank"] for r in s["ranks"]], list(RANKS))
        species = at(s, "species")
        self.assertEqual(species["players"], 2)
        self.assertEqual(len(species["bins"]), 20)
        counts = [bin_["count"] for bin_ in species["bins"]]
        self.assertEqual((counts[12], counts[18], sum(counts)), (1, 1, 2))   # 60% and 90%, not 80% and 40% apart
        self.assertAlmostEqual(species["average"], (0.6 + 0.9) / 2)
        subfamily = at(s, "subfamily")
        self.assertEqual([i for i, bin_ in enumerate(subfamily["bins"]) if bin_["count"]], [10, 19])
        self.assertEqual((at(s, "tribe")["players"], at(s, "genus")["players"]), (0, 0))
        self.assertIsNone(at(s, "tribe")["average"])
        self.assertEqual(s["players"], 2)

    def test_the_players_own_value_and_marker_per_rank(self):
        for i, (ok, n) in enumerate([(5, 10), (6, 10), (9, 10)]):
            skill(self.player(f"p{i}"), "genus", "Xyleborini", ok, n)
        skill(self.user, "genus", "Xyleborini", 10, 12)
        skill(self.user, "genus", "Ipini", 4, 8)   # 14/20 = 70%
        skill(self.user, "subfamily", "", 7, 20)   # 35%: alone at this rank
        s = game_board.accuracy_standing_by_rank(self.user)
        me = at(s, "genus")["me"]
        self.assertAlmostEqual(me["accuracy"], 0.7)
        self.assertEqual(me["step"], value_step(0.7))   # its own step on the site's scale, "great"
        self.assertEqual(me["bin"], 14)
        self.assertEqual((me["percentile"], me["rank"]), (67, "Top 33%"))   # above 2 of the 3 others
        alone = at(s, "subfamily")["me"]
        self.assertEqual((alone["bin"], alone["step"]), (7, "decent"))   # 7/20 is the 35-40% bin, in whole numbers
        self.assertIsNone(alone["rank"])   # nobody to compare with
        self.assertIsNone(at(s, "species")["me"])
        self.assertTrue(s["me"])

    def test_the_same_number_as_the_reports_accuracy_by_rank(self):
        roi = self.roi(self.t_affinis)
        for i in range(12):
            self.answer(self.user, self.roi(self.t_affinis) if i else roi, AFFINIS if i % 3 else FERR)
        game_trust.recompute_skills(self.user)
        by_rank = game_trust.accuracy_by_rank(game_trust.skills_for(self.user), {})   # the report's numbers
        report = {row["rank"]: row["naming"] for row in by_rank}
        s = game_board.accuracy_standing_by_rank(self.user)
        for rank in RANKS:
            with self.subTest(rank=rank):
                self.assertEqual(report[rank]["n"], 12)
                self.assertAlmostEqual(at(s, rank)["me"]["accuracy"], report[rank]["accuracy"])
        self.assertAlmostEqual(at(s, "species")["me"]["accuracy"], 8 / 12)

    def test_a_rank_counts_from_the_minimum_answers_added_over_branches(self):
        skill(self.user, "tribe", "Scolytinae", 5, 6)
        skill(self.user, "tribe", "Platypodinae", 3, 4)   # 10 in all: counts, though neither branch has 10
        skill(self.user, "species", "Xyleborus", 9, 9)   # 9: not yet
        skill(self.player("few"), "tribe", "Scolytinae", 9, 9)   # left out of everyone's too
        s = game_board.accuracy_standing_by_rank(self.user)
        self.assertAlmostEqual(at(s, "tribe")["me"]["accuracy"], 0.8)
        self.assertEqual(at(s, "tribe")["players"], 1)
        self.assertIsNone(at(s, "species")["me"])
        self.assertEqual(at(s, "species")["players"], 0)
        with override_settings(GAME_MIN_JUDGED_FOR_ACCURACY=5):
            s = game_board.accuracy_standing_by_rank(self.user)
            self.assertEqual((at(s, "tribe")["players"], at(s, "species")["players"]), (2, 1))

    def test_the_overall_accuracy_no_longer_decides_it(self):
        PlayerScore.objects.create(player=self.user, accuracy=0.9, judged=50)
        s = game_board.accuracy_standing_by_rank(self.user)
        self.assertFalse(s["me"])
        self.assertEqual(s["players"], 0)

    def test_not_enough_answers_at_any_rank_yet(self):
        skill(self.user, "subfamily", "", 3, 4)
        skill(self.user, "genus", "Xyleborini", 1, 2)
        skill(self.player("other"), "subfamily", "", 15, 20)
        s = game_board.accuracy_standing_by_rank(self.user)
        self.assertFalse(s["me"])
        self.assertEqual(s["needed"], 6)   # their best rank, subfamily, has 4 of 10
        self.assertTrue(all(r["me"] is None for r in s["ranks"]))
        self.assertEqual(s["players"], 1)
        self.assertEqual(game_board.accuracy_standing_by_rank(self.player("new"))["needed"], 10)

    def test_one_query_whatever_the_number_of_players(self):
        for i in range(30):
            p = self.player(f"q{i}")
            for rank in RANKS:
                skill(p, rank, "", i, 30)
                skill(p, rank, "b", 1, 2)
        game_board.accuracy_standing_by_rank(self.user)   # the game settings, read once and kept
        with CaptureQueriesContext(connection) as queries:
            s = game_board.accuracy_standing_by_rank(self.user)
        self.assertLessEqual(len(queries), 2)
        self.assertEqual([r["players"] for r in s["ranks"]], [30] * 4)


def standing(me=True, needed=4):
    """Four ranks as accuracy_standing_by_rank gives them; with ``me``, the player is rated at the first two."""
    bins = [{"from": i / 20, "count": 1, "height": 50} for i in range(20)]
    ranks = []
    for i, rank in enumerate(RANKS):
        mine = None
        if me and i == 0:
            mine = {"accuracy": 0.9, "percentile": 80, "rank": "Top 20%", "step": "excellent", "bin": 18}
        elif me and i == 1:
            mine = {"accuracy": 0.6, "percentile": None, "rank": None, "step": "good", "bin": 12}
        ranks.append({"rank": rank, "players": 1_234 if i < 3 else 1, "bins": bins, "average": 0.75, "me": mine})
    return {"ranks": ranks, "players": 1_300, "me": me, "needed": needed}


def render(data):
    return render_to_string("beetles/includes/game_accuracy.html", {"standing": data})


def players(part):
    """The "n players" line's text, digit groups and all: "1 234 players"."""
    found = re.findall(r'data-testid="accuracy-players">(.*?(?:players?|yet))</span>', part, re.S)
    return [" ".join(re.sub(r"<[^>]+>", " ", text).split()) for text in found]


def plot(html, rank):
    """One rank's plot in the card's html."""
    return next(part for part in html.split('data-testid="accuracy-rank-plot"')[1:]
                if part.lstrip().startswith(f'data-rank="{rank}"'))


class RankPlotsTemplateTests(ScoringCase):
    def test_four_plots_labelled_with_the_rank_your_value_and_the_players(self):
        html = render(standing())
        self.assertEqual(html.count('data-testid="accuracy-plot"'), 4)
        self.assertEqual([plot(html, rank).count('data-testid="accuracy-plot"') for rank in RANKS], [1] * 4)
        self.assertIn("grid grid-cols-2 sm:grid-cols-4", html)   # two by two on a phone, a row of four from a tablet
        for rank in RANKS:
            self.assertIn(f'data-testid="accuracy-rank-name">{rank.capitalize()}<', plot(html, rank))
            self.assertEqual(plot(html, rank).count("flex-1 rounded-t-sm"), 20)
        subfamily = plot(html, "subfamily")
        self.assertIn('scale-excellent" data-testid="accuracy-value">90%<', subfamily)
        self.assertIn("scale-fill-excellent", subfamily)   # your bar, in your value's own colour
        self.assertIn('data-testid="accuracy-rank">Top 20%<', subfamily)
        self.assertEqual(players(subfamily), ["1 234 players"])   # in groups of three
        self.assertIn('style="margin-left:0.4em">234</span>', subfamily)
        self.assertIn('data-testid="accuracy-you-label">You<', subfamily)
        self.assertEqual(players(plot(html, "species")), ["1 player"])
        self.assertIn("Accuracy by rank", html)

    def test_a_rank_without_enough_answers_shows_a_dash_and_no_marker(self):
        html = render(standing())
        genus = plot(html, "genus")
        self.assertIn('data-testid="accuracy-value">&mdash;<', genus)
        for testid in ("accuracy-you-line", "accuracy-you-label", "accuracy-rank"):
            self.assertNotIn(f'data-testid="{testid}"', genus)
        self.assertNotIn("scale-fill-", genus)
        tribe = plot(html, "tribe")   # rated, but nobody to compare with: a value, no rank
        self.assertIn('scale-good" data-testid="accuracy-value">60%<', tribe)
        self.assertNotIn('data-testid="accuracy-rank"', tribe)

    def test_the_rank_is_grey_and_only_the_number_has_the_colour(self):
        html = render(standing())
        rank = re.search(r'<span[^>]*data-testid="accuracy-rank"[^>]*>', html).group(0)
        self.assertIn("text-gray-700", rank)
        self.assertNotIn("scale-", rank)
        self.assertNotIn("scale-chip-", html)

    def test_the_lines_run_the_full_height_and_the_average_is_in_the_key(self):
        html = render(standing())
        subfamily = plot(html, "subfamily")
        for testid in ("accuracy-you-line", "accuracy-average-line"):
            line = re.search(rf'<div[^>]*data-testid="{testid}"[^>]*>', subfamily).group(0)
            self.assertIn("inset-y-0", line)
        self.assertEqual(html.count('data-testid="accuracy-average-line"'), 4)
        self.assertIn('title="Average 75%"', subfamily)
        key = re.search(r'data-testid="accuracy-average">(.*?)</span>Average</span>', html, re.S).group(1)
        self.assertIn("border-dashed", key)   # the key: a dashed line is the average
        ticks = subfamily[subfamily.index('data-testid="accuracy-ticks"'):]
        for pct in ("0%", "50%", "100%"):
            self.assertIn(f">{pct}<", ticks)

    def test_the_empty_state_says_how_many_answers_are_left(self):
        html = render(standing(me=False, needed=6))
        self.assertEqual(html.count('data-testid="accuracy-plot"'), 4)   # everyone's plots, without you
        self.assertEqual(html.count('data-testid="accuracy-value">&mdash;<'), 4)
        self.assertNotIn("accuracy-you-line", html)
        self.assertIn('data-testid="accuracy-needed">Name 6 more checked beetles to see where you stand.<', html)
        self.assertIn("Name 1 more checked beetle to", render(standing(me=False, needed=1)))
        nobody = render({"ranks": [], "players": 0, "me": False, "needed": 10})
        self.assertNotIn('data-testid="accuracy-plot"', nobody)
        self.assertIn("Name 10 more checked beetles", nobody)

    @override_settings(GAME_MIN_JUDGED_FOR_ACCURACY=10)
    def test_the_home_shows_the_four_plots(self):
        other = self.player("other")
        for rank in RANKS:
            skill(self.user, rank, "", 9, 10)
            skill(other, rank, "", 5, 10)
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_home")).content.decode()
        self.assertEqual(page.count('data-testid="accuracy-plot"'), 4)
        self.assertEqual(page.count('data-testid="accuracy-value">90%<'), 4)
        self.assertEqual(page.count('data-testid="accuracy-rank">Top 1%<'), 4)
        self.assertEqual(players(page), ["2 players"] * 4)
