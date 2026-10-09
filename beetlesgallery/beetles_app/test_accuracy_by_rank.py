"""
Accuracy rank by rank: the game home's four plots (subfamily, tribe, genus, species) and the leaderboard's per-player
table that keeps naming apart from telling beetles apart. Counted by the database, a few queries for a whole page.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game_board
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase


class RankCase(GameCase):
    def setUp(self):
        super().setUp()
        game_board.forget_rank_population()
        self.beetle = self.roi(self.t_affinis)

    def player(self, name):
        return get_user_model().objects.create_user(name, password="pw")

    def answers(self, player, n, mode="classify", when=None, **correct):
        """``n`` judged answers by ``player`` in ``mode``; correct: {rank: True/False/None} for every one of them."""
        rnd = GameRound.objects.create(player=player, mode=mode, items=[])
        made = GameAnswer.objects.bulk_create([
            GameAnswer(round=rnd, player=player, mode=mode, index=i, roi=self.beetle, roi_b=self.beetle,
                       is_check=True, **{f"correct_{r}": ok for r, ok in correct.items()})
            for i in range(n)])
        if when is not None:
            GameAnswer.objects.filter(pk__in=[a.pk for a in made]).update(answered_at=when)
        return made

    def ranks(self, player):
        return {s["rank"]: s for s in game_board.rank_standing(player)}


class RankStandingTests(RankCase):
    def test_each_rank_has_its_own_number(self):
        self.answers(self.user, 8, subfamily=True, tribe=True)
        self.answers(self.user, 2, subfamily=True, tribe=False)
        self.answers(self.user, 10, mode="pair", subfamily=False)   # every game counts, as in the overall accuracy
        s = self.ranks(self.user)
        self.assertEqual([x["rank"] for x in game_board.rank_standing(self.user)], ["subfamily", "tribe", "genus", "species"])
        self.assertAlmostEqual(s["subfamily"]["me"]["accuracy"], 10 / 20)
        self.assertEqual(s["subfamily"]["judged"], 20)
        self.assertAlmostEqual(s["tribe"]["me"]["accuracy"], 0.8)
        self.assertEqual(s["tribe"]["me"]["step"], game_board.value_step(0.8))
        self.assertEqual(s["tribe"]["me"]["bin"], 16)

    def test_a_rank_without_answers_is_empty_not_zero(self):
        self.answers(self.user, 10, subfamily=True)
        self.answers(self.user, 3, genus=False)
        s = self.ranks(self.user)
        self.assertTrue(s["species"]["empty"])
        self.assertIsNone(s["species"]["me"])
        self.assertIsNone(s["species"]["average"])
        self.assertEqual(s["species"]["players"], 0)
        self.assertFalse(s["genus"]["empty"])   # three answers: not enough for a number yet, but not empty
        self.assertIsNone(s["genus"]["me"])
        self.assertEqual(s["genus"]["needed"], 7)
        html = render_to_string("beetles/includes/game_accuracy_ranks.html", {"rank_standing": game_board.rank_standing(self.user)})
        species = html[html.index('data-rank="species"'):]
        self.assertIn('data-testid="accuracy-empty"', species)
        self.assertNotIn("accuracy-you-line", species)
        self.assertNotIn(">0%<", species.split('data-testid="accuracy-ticks"')[0])

    def test_answers_that_do_not_count_are_left_out(self):
        self.answers(self.user, 10, subfamily=True)
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        for i, extra in enumerate([{"is_retry": True}, {"seen_before": True}, {"skipped": True}, {"score_hold": True},
                                   {"is_check": False}]):
            GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=i, roi=self.beetle,
                                      correct_subfamily=False, **{"is_check": True, **extra})
        self.assertEqual(self.ranks(self.user)["subfamily"]["me"]["accuracy"], 1.0)

    def test_where_you_stand_among_players_at_each_rank(self):
        for i, right in enumerate([2, 5, 6, 9]):
            p = self.player(f"p{i}")
            self.answers(p, right, tribe=True)
            self.answers(p, 10 - right, tribe=False)
        self.answers(self.player("few"), 4, tribe=True)   # too few to be in anyone's plot
        self.answers(self.user, 7, tribe=True)
        self.answers(self.user, 3, tribe=False)
        tribe = self.ranks(self.user)["tribe"]
        self.assertEqual(tribe["players"], 5)
        self.assertEqual(sum(b["count"] for b in tribe["bins"]), 5)
        self.assertEqual([b["count"] for b in tribe["bins"]][4], 1)   # 0.2 in the 20-25% bin
        self.assertAlmostEqual(tribe["average"], (0.2 + 0.5 + 0.6 + 0.9 + 0.7) / 5)
        self.assertEqual(tribe["me"]["percentile"], 75)   # three of the four others below
        self.assertEqual(tribe["me"]["rank"], "Top 25%")
        self.assertIsNone(self.ranks(self.user)["genus"]["average"])

    def test_your_own_number_is_never_stale(self):
        other = self.player("other")
        self.answers(other, 10, species=True)
        self.answers(self.user, 10, species=False)
        self.assertEqual(self.ranks(self.user)["species"]["me"]["accuracy"], 0.0)
        self.answers(self.user, 10, species=True)   # everyone else's numbers are cached, theirs are not
        species = self.ranks(self.user)["species"]
        self.assertEqual(species["me"]["accuracy"], 0.5)
        self.assertEqual(species["players"], 2)   # their old value does not count twice

    def test_two_queries_however_many_players(self):
        for i in range(25):
            self.answers(self.player(f"q{i}"), 10, subfamily=i % 2 == 0, tribe=True, genus=False, species=None)
        self.answers(self.user, 10, subfamily=True)
        with CaptureQueriesContext(connection) as queries:
            game_board.rank_standing(self.user)
        self.assertLessEqual(len(queries), 2)
        with CaptureQueriesContext(connection) as queries:
            game_board.rank_standing(self.user)   # everyone's is cached
        self.assertLessEqual(len(queries), 1)

    def test_the_home_shows_four_plots(self):
        self.answers(self.user, 10, subfamily=True, tribe=False)
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_home")).content.decode()
        self.assertIn('data-testid="accuracy-by-rank"', page)
        for rank in ("subfamily", "tribe", "genus", "species"):
            self.assertIn(f'data-testid="accuracy-standing" data-rank="{rank}"', page)
        self.assertEqual(page.count('data-testid="accuracy-plot"'), 2)   # nobody has answered at genus or species
        self.assertEqual(page.count('data-testid="accuracy-empty"'), 2)


class RankAccuracyTableTests(RankCase):
    def test_naming_and_telling_apart_are_kept_apart_rank_by_rank(self):
        self.answers(self.user, 4, subfamily=True, tribe=True, genus=False)
        self.answers(self.user, 1, subfamily=True, tribe=False)
        self.answers(self.user, 2, mode="pair", genus=True)
        self.answers(self.user, 1, mode="odd", genus=False)
        self.answers(self.user, 1, mode="select", tribe=True)
        rows = {r["rank"]: r for r in game_board.rank_accuracy([self.user.id])[self.user.id]}
        self.assertEqual((rows["subfamily"]["naming"]["ok"], rows["subfamily"]["naming"]["n"]), (5, 5))
        self.assertEqual((rows["tribe"]["naming"]["ok"], rows["tribe"]["naming"]["n"]), (4, 5))
        self.assertEqual(rows["tribe"]["naming"]["step"], game_board.value_step(0.8))
        self.assertEqual((rows["genus"]["naming"]["ok"], rows["genus"]["naming"]["n"]), (0, 4))
        self.assertEqual(rows["genus"]["naming"]["accuracy"], 0.0)   # answered and wrong: a real 0%
        self.assertEqual((rows["genus"]["apart"]["ok"], rows["genus"]["apart"]["n"]), (2, 3))
        self.assertEqual((rows["tribe"]["apart"]["ok"], rows["tribe"]["apart"]["n"]), (1, 1))
        self.assertEqual(rows["subfamily"]["apart"], {"ok": 0, "n": 0, "accuracy": None, "step": "none"})
        self.assertEqual(rows["species"]["naming"]["n"], 0)

    def test_a_player_without_answers_gets_dashes(self):
        quiet = self.player("quiet")
        rows = game_board.rank_accuracy([quiet.id])[quiet.id]
        self.assertTrue(all(r["naming"]["accuracy"] is None and r["apart"]["n"] == 0 for r in rows))
        html = render_to_string("beetles/includes/game_rank_accuracy.html", {"by_rank": rows})
        self.assertEqual(html.count("&mdash;"), 8)

    def test_the_period_only_counts_its_own_answers(self):
        self.answers(self.user, 3, subfamily=False, when=timezone.now() - timedelta(days=400))
        self.answers(self.user, 2, subfamily=True)
        since = timezone.now() - timedelta(days=1)
        week = game_board.rank_accuracy([self.user.id], since=since)[self.user.id][0]["naming"]
        always = game_board.rank_accuracy([self.user.id])[self.user.id][0]["naming"]
        self.assertEqual((week["ok"], week["n"]), (2, 2))
        self.assertEqual((always["ok"], always["n"]), (2, 5))

    def test_the_board_adds_it_in_one_query_for_the_page(self):
        def players(n, start):
            for i in range(start, start + n):
                p = self.player(f"b{i}")
                PlayerScore.objects.create(player=p, score=10 + i, viewed=5, judged=10, accuracy=0.5)
                self.answers(p, 2, subfamily=True, tribe=False)

        players(3, 0)
        game_board.board(period="all")   # settings and the like read once
        with CaptureQueriesContext(connection) as few:
            rows = game_board.board(period="all")
        self.assertTrue(all(len(r["by_rank"]) == 4 for r in rows))
        players(12, 3)
        with CaptureQueriesContext(connection) as many:
            game_board.board(period="all")
        self.assertLessEqual(len(many), len(few))   # never one query per player
        self.assertEqual(sum("game_answer" in q["sql"] and "correct_tribe" in q["sql"] and "GROUP BY" in q["sql"]
                             for q in many.captured_queries), 1)   # the rank table, once for the page

    def test_the_leaderboard_row_opens_on_the_table(self):
        PlayerScore.objects.create(player=self.user, score=50, viewed=10, judged=10, accuracy=0.8)
        self.answers(self.user, 5, subfamily=True, tribe=True)
        self.answers(self.user, 5, subfamily=True, tribe=False)
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_leaderboard") + "?period=all").content.decode()
        table = page[page.index('data-testid="rank-accuracy"'):]
        table = table[:table.index("</table>")]
        self.assertIn("Naming", table)
        self.assertIn("Telling apart", table)
        self.assertIn(f'scale-{game_board.value_step(0.5)}">50%</span> <span class="text-gray-500">of 10', table)
        self.assertIn("100%", table)
        self.assertIn("&mdash;", table)   # nothing told apart yet
        self.assertIn("Naming, all ranks", page)
        self.assertIn("subfamily, tribe, genus and species together", page)
