"""
Where the staging-feedback batch's PRs meet (the train): the review card (#488) with the grids that grow and drop
flagged photos (#489), mistakes coming back (#490) in those grids, a batch built ahead (#494) that keeps the notice of a
fallback mix (#493), and what a retry's ranks were worth following the tuning.
"""
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from beetlesgallery.beetles_app import game, game_feedback, game_scoring, game_grid_ladder as ladder, game_relearn, game_views
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, GridStep, ModelPrediction
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase
from beetlesgallery.beetles_app.test_game_data_fallbacks import FallbackCase
from beetlesgallery.beetles_app.test_grid_flags import FlagCase


class FlaggedTilesInTheReviewTests(FlagCase):
    def test_a_flagged_photo_in_imposter_picker_shows_as_flagged_and_nothing_is_said_about_it(self):
        self.start("odd")
        odd = self.grid["tiles"].index(self.grid["a"])
        other = next(i for i in range(len(self.grid["tiles"])) if i != odd)
        self.flag(other)
        review = self.answer(pick=odd, flagged=[other]).json()["review"]
        tile = review["grid"]["tiles"][other]
        self.assertEqual(tile, {"validated": False, "name": "", "state": "flagged", "points": None})
        self.assertEqual(review["grid"]["tiles"][odd]["state"], "right")

    def test_a_flagged_member_is_no_mistake_to_come_back(self):
        self.start("select")
        members = self.places(member=True)
        self.flag(members[0])
        self.answer(picks=members[1:-1], flagged=[members[0]])   # the last member left out: a real mistake
        mistakes = {str(b) for b in game_relearn.open_mistakes(self.user)}
        self.assertIn(self.grid["tiles"][members[-1]], mistakes)
        self.assertNotIn(self.grid["tiles"][members[0]], mistakes)


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class BigGridPointsTests(ReviewCase):
    def setUp(self):
        super().setUp()
        self.members = [self.roi(self.t_affinis) for _ in range(5)]
        self.others = [self.roi(self.t_ferr) for _ in range(6)] + [self.roi(self.t_plat) for _ in range(5)]

    def grid(self, picks, **extra):
        item = dict({"a": str(self.members[0].id), "b": None, "check": True, "mode": "select",
                     "tiles": [str(t.id) for t in self.members + self.others], "rank": "species",
                     "group": game.lineage(self.t_affinis, "species"), "step": 12}, **extra)
        return self.answer(item, {"picks": picks})

    def test_sixteen_tiles_add_up_to_the_points_with_the_size_factor(self):
        review = self.grid([0, 1, 2, 5])   # three members, one wrong tap
        tiles = review["grid"]["tiles"]
        self.assertEqual(len(tiles), 16)
        self.assertAlmostEqual(sum(t["points"] or 0 for t in tiles), self.earned("select"), delta=0.03)
        depth = game.RANKS.index("species") - 1   # the Similarity points for the grid's rank
        worth = game.game_setting("GAME_POINTS_SELECT_WEIGHT", 1.25) * game_scoring.PAIR_POINTS[depth] * game_scoring.size_factor(16)
        self.assertAlmostEqual(self.points_of("select").detail["worth"], worth, places=2)
        self.assertGreater(game_scoring.size_factor(16), game_scoring.size_factor(4))

    def test_a_retry_grid_adds_up_too_at_its_lower_worth(self):
        full = self.grid([0, 1, 2])
        full_worth = self.points_of("select").detail["worth"]
        GameAnswer.objects.all().delete()
        retry = self.grid([0, 1, 2], retry=True)
        row = self.points_of("select")
        factor = game.game_setting("GAME_POINTS_RETRY_FACTOR", 0.5)
        self.assertAlmostEqual(row.detail["worth"], round(full_worth * factor, 2), places=2)
        self.assertAlmostEqual(row.detail["share"] * row.detail["members"], row.detail["worth"], delta=0.01)
        self.assertAlmostEqual(sum(t["points"] or 0 for t in retry["grid"]["tiles"]), self.earned("select"), delta=0.03)
        self.assertLess(self.earned("select"), sum(t["points"] or 0 for t in full["grid"]["tiles"]))

    def test_a_retry_grid_leaves_the_ladder_alone(self):
        self.grid([0, 1, 2, 3, 4], retry=True)   # a perfect grid
        answer = GameAnswer.objects.get()
        self.assertTrue(answer.is_retry)
        self.assertIsNone(ladder.outcome(answer))
        self.assertFalse(GridStep.objects.filter(player=self.user).exists())
        GameAnswer.objects.all().delete()
        self.grid([0, 1, 2, 3, 4])                  # the same grid in play moves it
        self.assertTrue(GridStep.objects.filter(player=self.user).exists())


class RetryWorthTests(ReviewCase):
    @override_settings(GAME_POINTS_RETRY_FACTOR=0.25)
    def test_what_a_retrys_ranks_were_worth_follows_the_tuning(self):
        roi = self.roi(self.t_affinis)
        self.answer({"a": str(roi.id), "b": None, "check": True, "retry": True}, AFFINIS)
        answer = GameAnswer.objects.get()
        losses = game_feedback.answer_losses(answer, AnswerPoints.objects.get(answer=answer))
        # the ranks add up to what it earned (with a hard-coded half they would come to twice that)
        self.assertAlmostEqual(sum(r["points"] for r in losses["ranks"].values()), losses["earned"], delta=0.2)


@override_settings(GAME_ROUND_SIZE=1)
class AheadNoticeTests(FallbackCase):
    """A one-beetle batch: its beetle is the last, so the next batch is built ahead as soon as it shows."""

    def setUp(self):
        super().setUp()
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat) * 3:
            self.roi(taxon)
            unchecked = self.roi(taxon, validated=False)
            ModelPrediction.objects.create(roi=unchecked, valid_species_id=taxon.valid_species_id, taxon=taxon,
                                           confidence=0.75, model_name="m", model_version="1")
        self.no_grids()
        self.grant("odd_one_out", "select_all", "choose_game", play_mode="odd")
        self.enterContext(mock.patch.object(game, "start_round", self.start_round_apart))

    real_start_round = staticmethod(game.start_round)

    def start_round_apart(self, player, mode, size=None, fresh_only=False):
        """
        The batch built ahead is drawn at random and may share a beetle with the one on screen, and then nothing is
        built ahead (_build_ahead leaves out beetles still to come). Drawn again until it shares none, so these tests
        always have a batch ahead.
        """
        on_screen = set().union(*(game._item_ids(item) for r in GameRound.objects.all() for item in r.items))
        for _ in range(50):
            rnd = self.real_start_round(player, mode, size, fresh_only)
            if rnd is None or not on_screen or on_screen.isdisjoint(set().union(*map(game._item_ids, rnd.items))):
                return rnd
            rnd.delete()
        self.fail("no batch apart from the one on screen")

    def test_a_batch_built_ahead_keeps_its_notice_until_the_feed_reaches_it(self):
        data = self.started()
        ahead = GameRound.objects.exclude(id=data["round"]).get()   # built ahead, not reached yet
        self.assertIn("Odd One Out", cache.get(game_views.AHEAD_NOTICE.format(ahead.id)))
        res = self.answer(data, self.answer_for(data["item"]))
        self.assertEqual(res["round"], str(ahead.id))
        self.assertIn("Odd One Out", res["notice"])

    def test_without_it_in_the_cache_the_feed_carries_on_without_a_notice(self):
        data = self.started()
        ahead = GameRound.objects.exclude(id=data["round"]).get()
        cache.delete(game_views.AHEAD_NOTICE.format(ahead.id))
        res = self.answer(data, self.answer_for(data["item"]))
        self.assertEqual((res["round"], res["notice"]), (str(ahead.id), ""))
