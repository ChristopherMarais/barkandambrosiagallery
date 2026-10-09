"""
The leaderboard splits accuracy by rank: one pooled accuracy mixes easy subfamily calls with hard species ones, so each
row also shows naming and telling apart at every rank, subfamily to species. The numbers are the player's report's
("Accuracy by rank", game_trust.accuracy_by_rank), counted for the board's period, for all the rows in a few queries,
and kept in the cache per player until they play again.
"""
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_board, game_trust
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


def by_rank(rows):
    return {r["rank"]: r for r in rows}


class ByRankCase(ScoringCase):
    def setUp(self):
        super().setUp()
        self.ann, self.bob = self.player("ann"), self.player("bob")
        for p in (self.ann, self.bob):
            PlayerScore.objects.create(player=p, score=10, viewed=5)

    def apart(self, player, when=None):
        """Two Xyleborus species rightly called "same genus": telling apart judged at every rank."""
        return self.answer(player, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr), pair="genus",
                           when=when)

    def grid(self, player, picks):
        """A Find Them All grid of two Xyleborus affinis (species within Xyleborus), tapping the places in ``picks``."""
        tiles = [self.roi(self.t_affinis), self.roi(self.t_affinis)]
        rnd = GameRound.objects.create(player=player, mode="select", items=[])
        return GameAnswer.objects.create(round=rnd, player=player, mode="select", index=0, roi=tiles[0], is_check=True,
                                         picks=picks, tiles=[str(t.id) for t in tiles], grid_rank="species",
                                         grid_group=game.lineage(self.t_affinis, "species"))


class CountsTests(ByRankCase):
    def test_all_time_they_are_the_reports_own_numbers(self):
        self.answer(self.ann, self.roi(self.t_affinis), AFFINIS)
        self.answer(self.ann, self.roi(self.t_affinis), FERR)   # right genus, wrong species
        self.grid(self.ann, picks=[0])                          # one affinis found, one left out
        self.apart(self.ann)
        game_trust.recompute_skills(self.ann)
        mine = game_trust.accuracy_by_rank_many([self.ann.id])[self.ann.id]
        self.assertEqual(mine, game_trust.player_report(self.ann)["by_rank"])
        species = by_rank(mine)["species"]
        self.assertEqual((species["naming"]["ok"], species["naming"]["n"]), (1, 3))   # Naming and Find Them All
        self.assertEqual((species["distinction"]["ok"], species["distinction"]["n"]), (1, 1))

    def test_each_player_is_counted_alone(self):
        roi = self.roi(self.t_affinis)   # the same beetle, named by both: each counts it once
        self.answer(self.ann, roi, AFFINIS)
        self.answer(self.bob, roi, FERR)
        self.apart(self.bob)
        naming = game_trust.skill_counts_many([self.ann.id, self.bob.id])
        self.assertEqual(naming[self.ann.id][("species", "xyleborus")][:2], [1, 1])
        self.assertEqual(naming[self.bob.id][("species", "xyleborus")][:2], [0, 1])
        apart = game_trust.apart_counts_many([self.ann.id, self.bob.id])
        self.assertNotIn(self.ann.id, apart)
        self.assertEqual(apart[self.bob.id][("species", "xyleborus")][:2], [1, 1])
        self.assertEqual(game_trust.skill_counts(self.bob), naming[self.bob.id])   # one player: as before
        self.assertEqual(game_trust.apart_counts(self.bob), apart[self.bob.id])

    def test_a_period_counts_its_own_answers_only(self):
        long_ago = timezone.now() - timedelta(days=60)
        self.answer(self.ann, self.roi(self.t_affinis), AFFINIS, when=long_ago)
        self.apart(self.ann, when=long_ago)
        self.answer(self.ann, self.roi(self.t_affinis), FERR)
        week = by_rank(game_trust.accuracy_by_rank_many([self.ann.id], since=game.week_start())[self.ann.id])
        self.assertEqual((week["species"]["naming"]["ok"], week["species"]["naming"]["n"]), (0, 1))
        self.assertEqual(week["species"]["distinction"]["n"], 0)
        # a period that holds every answer gives the all-time (report) numbers
        game_trust.recompute_skills(self.ann)
        self.assertEqual(game_trust.accuracy_by_rank_many([self.ann.id], since=long_ago - timedelta(days=1)),
                         game_trust.accuracy_by_rank_many([self.ann.id]))

    def test_a_few_queries_for_all_the_rows_whatever_their_number(self):
        players = [self.ann, self.bob, self.player("cy")]
        for p in players:
            self.answer(p, self.roi(self.t_affinis), AFFINIS)
            self.apart(p)
            self.grid(p, picks=[0, 1])
        for since in (None, game.week_start()):
            game_board.with_by_rank([{"player_id": self.ann.id}], since)   # settings read once
            with CaptureQueriesContext(connection) as one:
                game_board.with_by_rank([{"player_id": self.ann.id}], since)
            with CaptureQueriesContext(connection) as three:
                game_board.with_by_rank([{"player_id": p.id} for p in players], since)
            self.assertEqual(len(one), len(three), since)
            self.assertLessEqual(len(three), 5, since)


@override_settings(GAME_REPORT_MIN_JUDGED=2)
class BoardTests(ByRankCase):
    def setUp(self):
        super().setUp()
        for _ in range(2):
            self.answer(self.ann, self.roi(self.t_affinis), AFFINIS)
        self.apart(self.ann)
        self.answer(self.bob, self.roi(self.t_affinis), FERR)
        for p in (self.ann, self.bob):
            game_trust.recompute_skills(p)

    def rows(self, **kwargs):
        return {r["username"]: r for r in game_board.board(period="all", by_rank=True, **kwargs)}

    def test_each_row_gets_its_accuracy_by_rank_on_the_sites_scale(self):
        ann = by_rank(self.rows()["ann"]["by_rank"])
        self.assertEqual((ann["species"]["naming"]["accuracy"], ann["species"]["naming"]["step"]), (1.0, "excellent"))
        self.assertEqual(ann["species"]["distinction"]["n"], 1)
        self.assertEqual(ann["species"]["distinction"]["step"], "none")   # grey until GAME_REPORT_MIN_JUDGED answers
        self.assertTrue(self.rows()["ann"]["by_rank_has_distinction"])
        self.assertFalse(self.rows()["bob"]["by_rank_has_distinction"])
        self.assertNotIn("by_rank", game_board.board(period="all", limit=5)[0])   # the game home doesn't pay for it

    def test_kept_until_the_player_plays_again(self):
        self.assertEqual(by_rank(self.rows()["ann"]["by_rank"])["species"]["naming"]["n"], 2)
        self.answer(self.ann, self.roi(self.t_affinis), AFFINIS)
        game_trust.recompute_skills(self.ann)
        with mock.patch.object(game_trust, "accuracy_by_rank_many", wraps=game_trust.accuracy_by_rank_many) as counted:
            self.assertEqual(by_rank(self.rows()["ann"]["by_rank"])["species"]["naming"]["n"], 2)   # from the cache
            # every answer and every recompute moves the score row: then this player alone is counted again
            PlayerScore.objects.filter(player=self.ann).update(updated_at=timezone.now() + timedelta(seconds=1))
            self.assertEqual(by_rank(self.rows()["ann"]["by_rank"])["species"]["naming"]["n"], 3)
        self.assertEqual([c.args[0] for c in counted.call_args_list], [[self.ann.id]])

    def test_the_page_shows_naming_and_telling_apart_by_rank(self):
        self.client.force_login(self.ann)
        page = self.client.get(reverse("game_leaderboard"), {"period": "all"}).content.decode()
        self.assertIn('data-testid="accuracy-hint">Accuracy mixes all ranks. Bars: subfamily to species.</p>', page)
        start = page.index('data-testid="by-rank"')
        table = page[start:page.index("</table>", start)]   # ann's row comes first
        for heading in ("By rank", "Subfamily", "Tribe", "Genus", "Species", "Naming", "Telling apart"):
            self.assertIn(f">{heading}</th>", table)
        self.assertIn('<span class="block font-semibold scale-excellent">100%</span>'
                      '<span class="block text-gray-500">of <span class="digit-group">2</span></span>', table)
        self.assertIn('<span class="block font-semibold scale-none">100%</span>'
                      '<span class="block text-gray-500">of <span class="digit-group">1</span></span>', table)
        self.assertIn('aria-label="Naming by rank: subfamily 100%, tribe 100%, genus 100%, species 100%"', page)
        bob = page[page.index('data-testid="by-rank"', start + 1):]
        self.assertNotIn("Telling apart", bob[:bob.index("</table>")])   # bob told nothing apart: no line of dashes
        self.assertIn('aria-label="Naming by rank: subfamily 100%, tribe 100%, genus 100%, species 0%"', page)
        self.assertIn('<p class="text-gray-500 mb-1">By game, all ranks</p>', page)

    def test_sorted_by_a_telling_apart_game_the_bars_are_telling_apart(self):
        self.client.force_login(self.ann)
        page = self.client.get(reverse("game_leaderboard"), {"period": "all", "sort": "similarity"}).content.decode()
        self.assertIn('aria-label="Telling apart by rank: subfamily 100%, tribe 100%, genus 100%, species 100%"', page)
        self.assertIn('aria-label="Telling apart by rank: subfamily none, tribe none, genus none, species none"', page)
