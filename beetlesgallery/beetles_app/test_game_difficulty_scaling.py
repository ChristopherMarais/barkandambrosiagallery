"""
Points follow how hard a beetle is, and the difficulty target follows how a player is doing (#492): the target eases
off after a run of misses and pushes up after a run of right answers, per game and within bounds; each Identification
and Similarity answer keeps its beetle's difficulty percentile, and its points are a gain × m or a loss × (2 − m).
"""
from django.test import override_settings

from beetlesgallery.beetles_app import game, game_difficulty, game_feedback, game_scoring as scoring
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, PlayerScore, RoiDifficulty
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import PLAT, ScoringCase

DEFAULT_TARGET = dict(GAME_DIFFICULTY_START=0.15, GAME_DIFFICULTY_PER_ROUND=0.005, GAME_DIFFICULTY_SKILL_WEIGHT=0.7,
                      GAME_DIFFICULTY_MAX=0.9)


@override_settings(**DEFAULT_TARGET)
class TargetFollowsRecentAnswersTests(ScoringCase):
    def run_of(self, mode, right, n=10):
        for _ in range(n):
            if mode == "classify":
                self.answer(self.user, self.roi(self.t_affinis), AFFINIS if right else PLAT)
            else:
                self.answer(self.user, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr),
                            pair="genus" if right else "different")

    def test_a_run_of_misses_eases_it_off_in_that_game_only(self):
        self.assertAlmostEqual(game.target_difficulty(self.user, "classify"), 0.15)
        self.run_of("classify", right=False)
        self.assertAlmostEqual(game.target_difficulty(self.user, "classify"), 0.05)   # 0.15 - 0.15, but not below 0.05
        self.assertAlmostEqual(game.target_difficulty(self.user, "pair"), 0.15)
        self.assertAlmostEqual(game.target_difficulty(self.user), 0.15)               # the grid games: as before

    def test_easing_off_takes_a_strong_player_down_by_the_set_step(self):
        PlayerScore.objects.create(player=self.user, rating=0.5)                       # 0.15 + 0.35 = 0.5
        self.run_of("pair", right=False)
        self.assertAlmostEqual(game.target_difficulty(self.user, "pair"), 0.35)

    def test_a_run_of_right_answers_pushes_it_up_but_never_past_the_top(self):
        PlayerScore.objects.create(player=self.user, rating=0.5)
        self.run_of("pair", right=True)
        self.assertAlmostEqual(game.target_difficulty(self.user, "pair"), 0.55)
        PlayerScore.objects.filter(player=self.user).update(rating=1.0)               # 0.85 + 0.05 = 0.9, the top
        self.assertAlmostEqual(game.target_difficulty(self.user, "pair"), 0.9)
        PlayerScore.objects.filter(player=self.user).update(rating=1.2)               # already at the top
        self.assertAlmostEqual(game.target_difficulty(self.user, "pair"), 0.9)

    def test_a_middling_run_or_too_few_answers_change_nothing(self):
        self.run_of("classify", right=False, n=9)
        self.assertAlmostEqual(game.target_difficulty(self.user, "classify"), 0.15)   # 9 of 10: not enough to judge
        self.run_of("classify", right=True, n=6)                                       # last 10: 6 right, 4 wrong
        self.assertAlmostEqual(game.target_difficulty(self.user, "classify"), 0.15)

    def test_skips_count_as_not_right_and_reported_photos_not_at_all(self):
        for _ in range(10):
            self.answer(self.user, self.roi(self.t_affinis), skipped=True)
        self.assertEqual(game.recent_share_right(self.user, "classify"), 0.0)
        GameAnswer.objects.update(score_hold=True)
        self.assertIsNone(game.recent_share_right(self.user, "classify"))

    @override_settings(GAME_ROUND_SIZE=2)
    def test_the_builders_pass_their_game(self):
        from unittest import mock

        self.roi(self.t_affinis)
        self.roi(self.t_ferr)
        with mock.patch.object(game, "target_difficulty", wraps=game.target_difficulty) as target:
            game.start_round(self.user, "classify")
            game.start_round(self.user, "pair")
        self.assertEqual([c.args[1] for c in target.call_args_list], ["classify", "pair"])


class PercentileTests(ScoringCase):
    def beetle(self, difficulty, validated=True):
        roi = self.roi(self.t_affinis, validated=validated)
        if difficulty is not None:
            RoiDifficulty.objects.create(roi=roi, model_difficulty=difficulty)
        return roi

    def test_a_beetle_sits_in_the_middle_of_its_ties(self):
        rois = [self.beetle(d) for d in (0.1, 0.2, 0.3, 0.4)]
        self.beetle(None)                                   # unknown: not part of the distribution
        self.assertEqual(game_difficulty.percentile(rois[2].id), 0.625)    # 2 below, itself half
        self.assertEqual(game_difficulty.percentile(rois[0].id), 0.125)
        self.assertEqual(game_difficulty.percentile(self.beetle(None).id), 0.5)

    def test_validated_and_not_count_alike_and_nothing_known_is_the_middle(self):
        self.assertEqual(game_difficulty.percentile_of(0.9), 0.5)
        game_difficulty.forget()
        self.beetle(0.2, validated=False)
        hard = self.beetle(0.8)
        self.assertEqual(game_difficulty.percentile(hard.id), 0.75)

    def test_the_table_is_kept_for_a_while(self):
        self.beetle(0.2)
        game_difficulty.histogram()
        self.beetle(0.8)
        with self.assertNumQueries(0):
            game_difficulty.histogram()
        game_difficulty.forget()
        self.assertEqual(sum(game_difficulty.histogram()["validated"].values()), 2)

    @override_settings(GAME_ROUND_SIZE=1)
    def test_an_answer_keeps_its_beetles_percentile_from_when_it_was_given(self):
        self.beetle(0.1, validated=False)
        hard = self.beetle(0.9)
        rnd, item = self.play("classify")
        self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id)
        answer = GameAnswer.objects.get(roi=hard)
        self.assertEqual(answer.difficulty, 0.75)
        detail = AnswerPoints.objects.get(answer=answer).detail
        self.assertEqual((detail["difficulty"], detail["multiplier"]), (0.75, 1.125))
        RoiDifficulty.objects.filter(roi=hard).update(model_difficulty=0.0)   # later changes don't move it
        game_difficulty.forget()
        scoring.recompute([self.user.id])
        self.assertEqual(GameAnswer.objects.get(roi=hard).difficulty, 0.75)
        self.assertEqual(AnswerPoints.objects.get(answer=answer).detail["multiplier"], 1.125)


class PointsFollowDifficultyTests(ScoringCase):
    def at(self, p, fields=None, **kw):
        ans = self.answer(kw.pop("player", self.user), self.roi(self.t_affinis), fields, **kw)
        GameAnswer.objects.filter(pk=ans.pk).update(difficulty=p)
        ans.refresh_from_db()
        return ans

    def test_hard_beetles_pay_more_and_cost_less_easy_ones_the_reverse(self):
        self.assertEqual(self.points(self.at(1.0, AFFINIS)).points, 18.75)        # 15 × 1.25
        self.assertEqual(self.points(self.at(0.0, AFFINIS)).points, 11.25)        # 15 × 0.75
        self.assertEqual(self.points(self.at(1.0, PLAT)).points, -8.438)          # -11.25 × 0.75
        self.assertEqual(self.points(self.at(0.0, PLAT)).points, -14.062)         # -11.25 × 1.25
        self.assertEqual(self.points(self.at(0.5, AFFINIS)).points, 15.0)         # the middle: as before
        self.assertEqual(self.points(self.at(1.0, skipped=True)).points, -0.188)  # a skip is a loss too

    def test_one_factor_per_answer_keeps_the_order_of_answers(self):
        right_genus_wrong_species = self.points(self.at(1.0, FERR)).points
        stopped_at_genus = self.points(self.at(1.0, dict(AFFINIS, species=""))).points
        self.assertEqual((right_genus_wrong_species, stopped_at_genus), (5.25, 8.75))   # 4.2 and 7, × 1.25

    def test_similarity_uses_the_anchor(self):
        ans = self.answer(self.user, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr), pair="genus")
        GameAnswer.objects.filter(pk=ans.pk).update(difficulty=1.0)
        self.assertEqual(self.points(ans).points, 6.25)                           # 5 × 1.25

    @override_settings(GAME_POINTS_DIFFICULTY_SPREAD=0)
    def test_spread_zero_gives_the_old_points(self):
        self.assertEqual(self.points(self.at(1.0, AFFINIS)).points, 15.0)
        self.assertEqual(self.points(self.at(0.0, PLAT)).points, -11.25)

    def test_old_answers_keep_their_points(self):
        old = self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        points = self.points(old)
        self.assertEqual(points.points, 15.0)
        self.assertNotIn("multiplier", points.detail)

    @override_settings(GAME_POINTS_PARTICIPATION=0.5)
    def test_taking_part_is_not_scaled(self):
        self.assertEqual(self.points(self.at(1.0, AFFINIS)).points, 19.25)        # 15 × 1.25 + 0.5

    def test_grid_answers_are_not_scaled(self):
        odd = GameAnswer(mode="odd", difficulty=1.0, score_hold=False)
        self.assertEqual(scoring._by_difficulty(odd, 6.0, {}), (6.0, {}))
        grid = GameAnswer(mode="select", roi=self.roi(self.t_affinis))
        scoring.note_difficulty(grid)
        self.assertIsNone(grid.difficulty)

    def test_the_answer_as_it_comes_matches_the_full_recompute_and_never_goes_below_zero(self):
        answers = [self.at(0.9, PLAT), self.at(0.9, AFFINIS), self.at(0.1, PLAT), self.at(0.2, FERR),
                   self.at(0.6, skipped=True), self.answer(self.user, self.roi(self.t_affinis), AFFINIS)]
        for ans in answers:
            scoring.score_new_answer(ans)
        live = {a.answer_id: (a.points, a.detail) for a in AnswerPoints.objects.all()}
        total = PlayerScore.objects.get(player=self.user).score
        scoring.recompute([self.user.id])
        self.assertEqual({a.answer_id: (a.points, a.detail) for a in AnswerPoints.objects.all()}, live)
        self.assertAlmostEqual(PlayerScore.objects.get(player=self.user).score, total, places=2)
        self.assertEqual(live[answers[0].id][0], -9.0)     # -11.25 × 0.8, the first answer: the total stays at 0
        # 0, then +18 (15 × 1.2), -13.5 (-11.25 × 1.2), +3.57 (4.2 × 0.85), -0.24 (skip × 0.95), +15 (an old answer)
        self.assertAlmostEqual(total, 22.83, places=2)

    def test_where_points_went_follows_the_multiplier(self):
        ans = self.at(1.0, FERR)                    # right genus, wrong species on the hardest beetle
        self.points(ans)
        loss = game_feedback.answer_losses(GameAnswer.objects.select_related("points").get(pk=ans.pk))
        self.assertEqual((loss["earned"], loss["multiplier"]), (5.2, 1.25))
        self.assertEqual(loss["ranks"]["genus"]["points"], 5.0)            # 4 × 1.25
        self.assertEqual(loss["lost"], 13.5)                                # (8 + 2.8) × 1.25: worth plus the penalty
