"""
Select all (#370): 4, 9 or 16 beetles (the grid ladder, #489) and a group ("Tap every Platypodinae"). Opens at level 3;
a perfect grid earns twice a Similarity answer times its size factor, a wrong tap costs 1.5 times a right one, and taps
on unchecked beetles are only recorded.
"""
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_feedback, game_grid_ladder, game_levels, game_scoring
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, GridStep, ModelPrediction, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.testing import make_taxon


class SelectCase(GameCase):
    def setUp(self):
        super().setUp()
        self.t_xylo = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xylosandrus",
                                 species="crassiusculus", scientific_name="Xylosandrus crassiusculus")
        self.rois = {t.species: [self.roi(t) for _ in range(6)] for t in (self.t_affinis, self.t_ferr, self.t_plat, self.t_xylo)}
        self.client.force_login(self.user)

    def level(self, score, rating=0.0):
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": score, "rating": rating})

    def name_at(self, roi_id, rank):
        return game.lineage(game.Beetles.objects.select_related("taxon").get(id=roi_id).taxon, rank)[rank]

    def at(self, rank, size=9):
        """Level 3, and the player on the grid ladder's step for ``size`` beetles at ``rank``."""
        self.level(200, 0.4)
        GridStep.objects.update_or_create(player=self.user, game="select",
                                          defaults={"step": game_grid_ladder.step_for(size, rank)})

    def grid(self, rank="species"):
        self.at(rank)
        with override_settings(GAME_ROUND_SIZE=2):   # beetles for two grids of nine; later ones would be smaller
            return self.play("select")

    def answer(self, rnd, item, **body):
        return self.post("game_answer", dict(body, index=item["index"]), rnd.id)

    def members(self, rnd, item):
        it = rnd.items[item["index"]]
        return [i for i, t in enumerate(it["tiles"]) if self.name_at(t, it["rank"]).lower() == it["group"][it["rank"]].lower()]


class LadderTests(SelectCase):
    def test_select_all_opens_at_level_three(self):
        self.assertNotIn("select", game_levels.games(game_levels.describe(60, 0)["perks"]))
        self.assertIn("select", game_levels.games(game_levels.describe(200, 0.4)["perks"]))
        self.assertEqual(game_levels.game_level("select"), 3)
        self.level(60)
        res = self.post("game_prefs", {"play_mode": "select"})
        self.assertEqual((res.status_code, res.json()["error"]), (403, "Find Them All unlocks at level 3."))


class BuildTests(SelectCase):
    def test_every_grid_has_three_or_four_members_and_at_least_as_many_others(self):
        for rank in game.RANKS:
            with self.subTest(rank=rank):
                self.at(rank)
                item = game.build_select_items(self.user, 1)[0]
                self.assertEqual((item["mode"], item["rank"], len(item["tiles"]), len(set(item["tiles"]))), ("select", rank, 9, 9))
                named = [self.name_at(t, rank).lower() == item["group"][rank].lower() for t in item["tiles"]]
                self.assertIn(sum(named), (3, 4))
                self.assertGreaterEqual(9 - sum(named), sum(named))   # tapping everything never pays
                photos = game.Beetles.objects.filter(id__in=item["tiles"]).values_list("image_asset_id", flat=True)
                self.assertEqual(len(set(photos)), 9)

    def test_every_grid_has_a_sure_and_an_unsure_ai_beetle_besides_validated_ones(self):
        self.at("species")
        # only Xyleborus affinis has three validated beetles, so only it can be the group of nine; two each of the others
        for roi in self.rois["affinis"][3:] + self.rois["ferrugineus"][2:] + self.rois["cylindrus"][2:] + self.rois["crassiusculus"][2:]:
            roi.delete()
        guesses = {}
        for conf in (0.95, 0.8, 0.3):   # sure, in between, unsure
            guesses[conf] = self.roi(self.t_affinis, validated=False)
            ModelPrediction.objects.create(roi=guesses[conf], valid_species_id=self.t_affinis.valid_species_id,
                                           taxon=self.t_affinis, confidence=conf, model_name="m", model_version="1")
        items = game.build_select_items(self.user, 1)
        tiles = items[0]["tiles"]
        self.assertEqual((items[0]["group"]["species"], len(tiles)), ("Xyleborus affinis", 9))
        self.assertIn(str(guesses[0.95].id), tiles)
        self.assertIn(str(guesses[0.3].id), tiles)
        self.assertGreaterEqual(game.Beetles.objects.filter(id__in=tiles, bbox_is_validated=True).count(), 6)

    def test_without_an_unsure_ai_beetle_there_is_still_a_grid(self):   # the staging bug (#489)
        self.at("species")
        sure = self.roi(self.t_affinis, validated=False)
        ModelPrediction.objects.create(roi=sure, valid_species_id=self.t_affinis.valid_species_id, taxon=self.t_affinis,
                                       confidence=0.95, model_name="m", model_version="1")
        self.assertTrue(game.build_select_items(self.user, 1))


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class ScoringTests(SelectCase):
    def grid_answer(self, picks, unchecked=False, rank="species"):
        rnd = GameRound.objects.create(player=self.user, mode="select", items=[])
        tiles = self.rois["affinis"][:3] + self.rois["ferrugineus"][:3] + self.rois["cylindrus"][:3]
        if unchecked:
            tiles[0] = self.roi(self.t_affinis, validated=False)
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="select", index=0, roi=tiles[1], is_check=True, picks=picks,
            tiles=[str(t.id) for t in tiles], grid_rank=rank, grid_group=game.lineage(self.t_affinis, rank))

    def points(self, ans):
        return game_scoring.score(ans, lambda rid: [], game_scoring._NoJudges())[0]

    def test_a_perfect_grid_earns_twice_a_similarity_answer_at_that_rank_times_its_size_factor(self):
        self.assertAlmostEqual(self.points(self.grid_answer([0, 1, 2])),
                               2 * game_scoring.PAIR_POINTS[2] * game_scoring.size_factor(9))

    def test_a_wrong_tap_costs_one_and_a_half_right_ones_and_a_missed_one_nothing(self):
        share = 2 * game_scoring.PAIR_POINTS[2] * game_scoring.size_factor(9) / 3
        self.assertAlmostEqual(self.points(self.grid_answer([0, 1, 3])), share * (2 - 1.5))
        self.assertAlmostEqual(self.points(self.grid_answer([0, 1])), share * 2)
        self.assertLess(self.points(self.grid_answer(list(range(9)))), 0)   # tapping everything loses

    def test_taps_on_unchecked_beetles_are_recorded_not_scored(self):
        ans = self.grid_answer([0, 1, 2], unchecked=True)
        result = game.score_select(game_scoring.grid_tiles(ans), ans.picks, "species", ans.grid_group)
        self.assertEqual((result["tiles"][0], result["members"], result["right"]), ("vote", 2, 2))
        self.assertEqual(ans.picks, [0, 1, 2])

    def test_a_grid_counts_once_in_the_reliability_rating(self):   # #381: once per grid, not per tile
        ans = self.grid_answer([0, 1, 2])
        GameAnswer.objects.filter(pk=ans.pk).update(correct_species=True)   # a perfect grid, as the game stores it
        self.assertEqual(game_scoring.ratings()[self.user.id][2], 1)

    def test_skip_earns_a_little_like_odd_one_out(self):
        rnd, item = self.grid()
        self.answer(rnd, item, skipped=True)
        points = AnswerPoints.objects.get(answer__round=rnd)
        self.assertEqual((points.points, points.basis), (0.25, AnswerPoints.Basis.UNSURE))


class AnswerTests(SelectCase):
    def test_a_perfect_grid_is_saved_revealed_and_celebrated(self):
        rnd, item = self.grid("species")
        self.assertEqual((item["mode"], item["rank"], len(item["images"])), ("select", "species", 9))
        self.assertEqual(item["target"], rnd.items[item["index"]]["group"]["species"])
        members = self.members(rnd, item)
        data = self.answer(rnd, item, picks=members).json()
        ans = GameAnswer.objects.get()
        self.assertEqual((ans.mode, ans.picks, ans.correct_species, ans.is_check), ("select", members, True, True))
        self.assertEqual(data["review"]["celebrate"]["kind"], "validated")
        self.assertEqual([i for i, t in enumerate(data["review"]["grid"]["tiles"]) if t["state"] == "right"], members)

    def test_missing_one_is_partly_correct_and_a_wrong_tap_shows(self):
        rnd, item = self.grid("species")
        members = self.members(rnd, item)
        other = next(i for i in range(9) if i not in members)
        data = self.answer(rnd, item, picks=members[1:] + [other]).json()
        self.assertEqual(data["review"]["grid"]["tiles"][members[0]]["state"], "missed")
        self.assertEqual(data["review"]["grid"]["tiles"][other]["state"], "wrong")
        self.assertFalse(GameAnswer.objects.get().correct_species)

    def test_taps_must_be_places_in_the_grid(self):
        rnd, item = self.grid()
        for bad in ([], [9], [0, 0], [True], "1", None):
            with self.subTest(picks=bad):
                self.assertEqual(self.answer(rnd, item, picks=bad).status_code, 400)

    def test_the_round_review_shows_each_beetle_and_only_names_it_as_far_as_the_grid_went(self):
        rnd, item = self.grid("genus")
        self.answer(rnd, item, picks=self.members(rnd, item))
        game.finish_round(rnd)
        fb = game_feedback.round_feedback(rnd)["items"][0]
        self.assertEqual((len(fb["sides"]), fb["verdict"], fb["truth_select"]["rank"]), (9, "right", "genus"))
        self.assertTrue(all(s["label"]["species"] == "" for s in fb["sides"]))
        self.assertEqual(self.client.get(reverse("game_round_review", args=[rnd.id])).status_code, 200)

    def test_a_species_grid_gives_every_beetle_away(self):
        rnd, item = self.grid("species")
        self.answer(rnd, item, skipped=True)
        self.assertTrue(set(rnd.items[item["index"]]["tiles"]) <= {str(i) for i in game.revealed_ids(self.user)})


class PageTests(SelectCase):
    def test_the_play_page_and_how_it_works_have_it(self):
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        for marker in ('data-play="select"', 'data-testid="select-panel"', 'id="select-prompt"'):
            self.assertIn(marker, page)
        how = self.client.get(reverse("game_how")).content.decode()
        self.assertIn('data-testid="how-select"', how)
        self.assertIn("Find Them All at level 3", how)
