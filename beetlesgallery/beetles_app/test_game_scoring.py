"""Points: truth first, agreement with stronger players second, a floor at zero, retries, retroactive re-scoring."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_relearn, game_scoring as scoring
from beetlesgallery.beetles_app.models import AnswerPoints, Beetles, GameAnswer, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR, FeedbackCase

PLAT = {"subfamily": "Platypodinae", "tribe": "Platypodini", "genus": "Platypus", "species": "cylindrus"}


# The rules below are easier to read without the participation point and the Identification weight; each has its own test
@override_settings(GAME_POINTS_PARTICIPATION=0, GAME_POINTS_CLASSIFY_WEIGHT=1)
class ScoringCase(FeedbackCase):
    def player(self, name):
        return get_user_model().objects.create_user(name, password="pw")

    def answer(self, player, roi, fields=None, check=None, mode="classify", roi_b=None, pair=None, skipped=False,
               retry=False, when=None):
        """One answer, scored the way the game scores it. Returns the saved GameAnswer."""
        rnd = GameRound.objects.create(player=player, mode=mode, items=[])
        check = roi.bbox_is_validated if check is None else check
        ans = GameAnswer(round=rnd, player=player, mode=mode, index=0, roi=roi, roi_b=roi_b, is_check=check,
                         skipped=skipped, is_retry=retry, pair_answer=pair or "", **(fields or {}))
        if check and not skipped:
            scores = game.score_classification(fields or {}, roi.taxon) if mode == "classify" else game.score_pair(pair, roi.taxon, roi_b.taxon)
            for r, ok in scores.items():
                setattr(ans, f"correct_{r}", ok)
            t = roi.taxon
            ans.ref_subfamily, ans.ref_tribe, ans.ref_genus, ans.ref_species = t.subfamily, t.tribe, t.genus, t.species
        ans.save()
        if when:
            GameAnswer.objects.filter(pk=ans.pk).update(answered_at=when)
        return ans

    def points(self, ans):
        scoring.recompute([ans.player_id])
        return AnswerPoints.objects.get(answer=ans)

    def strong(self, name, right=20, wrong=0):
        """A player with a good record on validated beetles."""
        p = self.player(name)
        for i in range(right):
            self.answer(p, self.roi(self.t_affinis), AFFINIS)
        for i in range(wrong):
            self.answer(p, self.roi(self.t_affinis), PLAT)
        return p


class TruthPointsTests(ScoringCase):
    def test_the_exact_species_earns_the_most(self):
        self.assertEqual(self.points(self.answer(self.user, self.roi(self.t_affinis), AFFINIS)).points, 15.0)

    def test_stopping_at_genus_when_right_still_earns_well(self):
        genus_only = dict(AFFINIS, species="")
        self.assertEqual(self.points(self.answer(self.user, self.roi(self.t_affinis), genus_only)).points, 7.0)

    def test_how_wrong_matters(self):
        near = self.points(self.answer(self.user, self.roi(self.t_affinis), FERR)).points      # right genus, wrong species
        far = self.points(self.answer(self.staff, self.roi(self.t_affinis), PLAT)).points      # everything wrong
        self.assertEqual(near, -11.667)   # 1 + 2 + 4 - 8 × 2⅓: the wrong species costs k times its points
        self.assertEqual(far, -35.0)      # -(1 + 2 + 4 + 8) × 2⅓: every rank claimed is wrong
        self.assertGreater(near, far)

    def test_partial_credit_is_less_than_being_right_and_less_than_stopping(self):
        right_tribe = dict(AFFINIS, genus="Ambrosiodmus", species="")
        tribe_wrong_genus = self.points(self.answer(self.user, self.roi(self.t_affinis), right_tribe)).points
        self.assertEqual(tribe_wrong_genus, -6.333)    # 1 + 2 - 4 × 2⅓
        tribe_only = dict(AFFINIS, genus="", species="")
        stopped = self.points(self.answer(self.staff, self.roi(self.t_affinis), tribe_only)).points
        self.assertEqual(stopped, 3.0)
        self.assertLess(tribe_wrong_genus, stopped)         # guessing past what you know costs
        self.assertLess(tribe_wrong_genus, 0)               # and a wrong rank never pays (#530)

    def test_not_sure_costs_a_little(self):
        p = self.points(self.answer(self.user, self.roi(self.t_affinis), skipped=True))
        self.assertEqual((p.points, p.basis), (-0.25, "unsure"))

    def test_a_retry_earns_half(self):
        self.assertEqual(self.points(self.answer(self.user, self.roi(self.t_affinis), AFFINIS, retry=True)).points, 7.5)


@override_settings(GAME_POINTS_CLASSIFY_WEIGHT=3)
class IdentificationWeightTests(ScoringCase):
    """Naming a beetle is worth several times a similarity answer, gains and losses alike."""

    def test_identification_counts_three_times(self):
        self.assertEqual(self.points(self.answer(self.user, self.roi(self.t_affinis), AFFINIS)).points, 45.0)
        genus_only = dict(AFFINIS, species="")
        self.assertEqual(self.points(self.answer(self.user, self.roi(self.t_affinis), genus_only)).points, 21.0)
        self.assertEqual(self.points(self.answer(self.player("x"), self.roi(self.t_affinis), PLAT)).points, -105.0)

    def test_a_perfect_identification_is_worth_many_similarity_answers(self):
        name = self.points(self.answer(self.user, self.roi(self.t_affinis), AFFINIS)).points
        b = self.roi(self.t_affinis)
        pair = self.points(self.answer(self.player("p"), self.roi(self.t_affinis), mode="pair", roi_b=b, pair="species")).points
        self.assertGreaterEqual(name / pair, 3)   # 45 against the best Similarity answer, 12

    def test_the_explanation_shows_the_weighted_points(self):
        self.client.force_login(self.user)
        page = self.client.get("/game/how-it-works/").content.decode()
        # players get the short version (#618 how-dup): Naming pays the most, without the point tables
        self.assertIn("Naming pays the most", page)
        self.assertNotIn("the exact species earns 45", page)


class PairPointsTests(ScoringCase):
    def pair(self, player, a_taxon, b_taxon, answer, **photo):
        a, b = self.roi(a_taxon), self.roi(b_taxon)
        for roi in (a, b):
            for field, value in photo.items():
                setattr(roi.image_asset, field, value)
            roi.image_asset.save()
        return self.points(self.answer(player, a, mode="pair", roi_b=b, pair=answer)).points

    def test_finer_lines_earn_more(self):
        same_genus = self.pair(self.user, self.t_affinis, self.t_ferr, "genus")
        different = self.pair(self.user, self.t_affinis, self.t_plat, "different")
        self.assertEqual((same_genus, different), (7.0, 1.0))

    def test_how_far_off_matters(self):
        one_step = self.pair(self.user, self.t_affinis, self.t_ferr, "species")       # same genus, said same species
        three_steps = self.pair(self.user, self.t_affinis, self.t_ferr, "different")  # same genus, said different subfamilies
        self.assertEqual(one_step, -4.667)     # same genus (7), less 2⅓ × (12 - 7) for the rung too far: wrong
        self.assertEqual(three_steps, -7.0)    # calling relatives strangers: 2⅓ per rung off

    def test_a_cautious_true_answer_earns_its_rung(self):
        cautious = self.pair(self.user, self.t_affinis, self.t_ferr, "tribe")    # true, but they share the genus
        exact = self.pair(self.user, self.t_affinis, self.t_ferr, "genus")
        self.assertEqual((cautious, exact), (4.0, 7.0))

    def test_alike_photos_earn_a_bonus(self):
        plain = self.pair(self.user, self.t_affinis, self.t_ferr, "genus")
        alike = self.pair(self.user, self.t_affinis, self.t_ferr, "genus", photographer="J. Hulcr", image_institution="UF")
        self.assertEqual(alike, 7.0 * 1.25)
        self.assertGreater(alike, plain)

    def test_not_sure(self):
        self.assertEqual(self.pair(self.user, self.t_affinis, self.t_ferr, "unsure"), -0.25)


class FloorTests(ScoringCase):
    def test_the_score_never_goes_below_zero_and_a_bad_start_leaves_no_debt(self):
        start = timezone.now() - timedelta(hours=1)
        self.answer(self.user, self.roi(self.t_affinis), PLAT, when=start)                       # -11.25
        self.answer(self.user, self.roi(self.t_affinis), dict(AFFINIS, species=""), when=start + timedelta(minutes=1))  # +7
        scoring.recompute([self.user.id])
        self.assertEqual(PlayerScore.objects.get(player=self.user).score, 7.0)

    def test_totals_and_counts(self):
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        self.answer(self.user, self.roi(self.t_affinis), skipped=True)
        scoring.recompute([self.user.id])
        score = PlayerScore.objects.get(player=self.user)
        self.assertEqual((score.score, score.viewed, score.labelled), (14.75, 2, 1))


class AgreementTests(ScoringCase):
    """Beetles nobody has validated: points from agreeing with players stronger than you."""

    def setUp(self):
        super().setUp()
        self.open_roi = self.roi(self.t_affinis, validated=False)
        self.weak = [self.strong(f"weak{i}", right=3, wrong=17) for i in range(3)]
        # strong players who say nothing about this beetle: they put the median among the strong
        self.bench = [self.strong(f"bench{i}") for i in range(4)]

    def test_agreeing_with_a_stronger_player_earns_points_but_less_than_the_truth(self):
        strong = self.strong("strong")
        self.answer(strong, self.open_roi, AFFINIS)
        mine = self.points(self.answer(self.user, self.open_roi, AFFINIS))
        self.assertEqual(mine.basis, "consensus")
        self.assertGreater(mine.points, 0)
        self.assertLess(mine.points, 15.0 * 0.6)

    def test_more_strong_players_agreeing_gets_closer_to_the_cap(self):
        one = self.strong("s1")
        self.answer(one, self.open_roi, AFFINIS)
        first = self.points(self.answer(self.user, self.open_roi, AFFINIS)).points
        for name in ("s2", "s3", "s4"):
            self.answer(self.strong(name), self.open_roi, AFFINIS)
        more = self.points(GameAnswer.objects.get(player=self.user, roi=self.open_roi)).points
        self.assertGreater(more, first)
        self.assertLessEqual(more, 15.0 * 0.6)

    def test_agreeing_only_with_weaker_players_earns_nothing(self):
        for w in self.weak:
            self.answer(w, self.open_roi, AFFINIS)
        self.assertEqual(self.points(self.answer(self.user, self.open_roi, AFFINIS)).points, 0.0)

    def test_siding_with_many_weak_players_against_a_strong_one_earns_nothing(self):
        strong = self.strong("strong")
        self.answer(strong, self.open_roi, PLAT)
        for w in self.weak:
            self.answer(w, self.open_roi, AFFINIS)
        mine = self.points(self.answer(self.user, self.open_roi, AFFINIS))
        self.assertEqual(mine.points, 0.0)
        self.assertLess(mine.detail["agreement"]["species"], 0)

    def test_it_is_judged_rank_by_rank(self):
        # a strong player says another species of the same genus: the genus and above earn, the disputed species
        # takes back k times what agreeing on it would earn (#530), never below zero in all
        self.answer(self.strong("strong"), self.open_roi, FERR)
        mine = self.points(self.answer(self.user, self.open_roi, AFFINIS))
        agreement = mine.detail["agreement"]
        self.assertGreater(agreement["genus"], 0)
        self.assertLess(agreement["species"], 0)
        k = scoring.wrong_cost()
        expected = 0.6 * ((1 + 2 + 4) * agreement["genus"] + 8 * k * agreement["species"])
        self.assertAlmostEqual(mine.points, max(0.0, expected), places=2)
        stopped = self.points(self.answer(self.staff, self.open_roi, dict(AFFINIS, species="")))
        self.assertGreater(stopped.points, mine.points)   # stopping at the genus beats guessing the species

    def test_disagreement_never_costs_points_on_an_unvalidated_beetle(self):
        self.answer(self.strong("strong"), self.open_roi, AFFINIS)
        self.assertEqual(self.points(self.answer(self.user, self.open_roi, PLAT)).points, 0.0)

    def test_a_proven_expert_counts_fully(self):
        expert = self.strong("expert", right=40)
        from beetlesgallery.beetles_app.game_trust import recompute_skills
        recompute_skills(expert)
        self.answer(expert, self.open_roi, AFFINIS)
        judges = scoring.Judges(scoring.ratings())
        labels = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "Xyleborus affinis"}
        self.assertEqual(judges.weight(expert.id, self.user.id, "genus", labels), 1.0)


class RetroactiveTests(ScoringCase):
    def test_validating_a_beetle_later_rescores_the_answers_on_it_up_or_down(self):
        roi = self.roi(self.t_affinis, validated=False)
        right = self.answer(self.user, roi, AFFINIS)
        wrong = self.answer(self.staff, roi, PLAT)
        self.assertEqual(self.points(right).basis, "consensus")
        Beetles.objects.filter(pk=roi.pk).update(bbox_is_validated=True)
        call_command("recompute_game_scores", stdout=open("/dev/null", "w"))
        self.assertEqual((AnswerPoints.objects.get(answer=right).points, AnswerPoints.objects.get(answer=right).basis), (15.0, "truth"))
        self.assertEqual(AnswerPoints.objects.get(answer=wrong).points, -35.0)

    def test_the_answer_api_scores_straight_away_and_moves_the_total(self):
        self.roi(self.t_affinis)
        self.client.force_login(self.user)
        rnd, item = self.play("classify")
        self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id)
        ans = GameAnswer.objects.get(player=self.user)
        self.assertEqual(AnswerPoints.objects.get(answer=ans).points, 15.0)
        self.assertEqual(PlayerScore.objects.get(player=self.user).score, 15.0)


class RetryTests(ScoringCase):
    """A beetle got wrong comes back in a later sitting (game_relearn; test_game_relearn has the rest)."""

    def test_a_beetle_got_wrong_comes_back_in_a_later_sitting(self):
        roi = self.roi(self.t_affinis)
        self.answer(self.user, roi, FERR, when=timezone.now() - timedelta(hours=3))
        self.assertEqual(list(game_relearn.due(self.user)), [roi.id])

    def test_not_in_the_same_sitting(self):
        roi = self.roi(self.t_affinis)
        self.answer(self.user, roi, FERR, when=timezone.now() - timedelta(minutes=5))
        self.assertEqual(game_relearn.due(self.user), {})

    def test_not_once_they_got_it_right(self):
        roi = self.roi(self.t_affinis)
        self.answer(self.user, roi, FERR, when=timezone.now() - timedelta(days=9))
        self.answer(self.user, roi, AFFINIS, retry=True, when=timezone.now() - timedelta(days=5))
        self.assertEqual(game_relearn.due(self.user), {})

    @override_settings(GAME_RETRY_MAX=2)
    def test_at_most_a_few_times(self):
        roi = self.roi(self.t_affinis)
        for days in (9, 6, 3):
            self.answer(self.user, roi, FERR, retry=days != 9, when=timezone.now() - timedelta(days=days))
        self.assertEqual(game_relearn.due(self.user), {})

    def test_retries_do_not_count_towards_accuracy(self):
        roi = self.roi(self.t_affinis)
        self.answer(self.user, roi, FERR)
        for _ in range(12):
            self.answer(self.user, roi, AFFINIS, retry=True)
        acc = game.player_summary(self.user)["accuracy"]
        self.assertTrue(acc is None or acc < 1.0)

    @override_settings(GAME_ROUND_SIZE=4)
    def test_the_feed_includes_one(self):
        roi = self.roi(self.t_affinis)
        self.answer(self.user, roi, FERR, when=timezone.now() - timedelta(hours=3))
        for _ in range(4):
            self.roi(self.t_affinis)
        rnd = game.start_round(self.user, "classify")
        retries = [i for i in rnd.items if i.get("retry")]
        self.assertEqual([i["a"] for i in retries], [str(roi.id)])


class PagesTests(ScoringCase):
    def test_the_how_it_works_page_explains_it(self):
        self.client.force_login(self.user)
        page = self.client.get("/game/how-it-works/").content.decode()
        for text in ("How the game works", "Naming pays the most", "agreeing with strong players", "never drops below zero"):
            self.assertIn(text, page)

    def test_the_game_home_shows_the_score_and_links_the_explanation(self):
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        self.client.force_login(self.user)
        page = self.client.get("/game/").content.decode()
        self.assertIn('data-testid="score"><span class="digit-group">15</span> pts<', page)
        self.assertIn("/game/how-it-works/", page)

    def test_the_recap_shows_the_points_of_the_sitting(self):
        from beetlesgallery.beetles_app import game_rewards
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        scoring.recompute([self.user.id])
        self.assertEqual(game_rewards.recap(self.user, timezone.now() - timedelta(hours=1))["points"], 15.0)

    def test_a_retry_is_marked_for_the_player(self):
        roi = self.roi(self.t_affinis)
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[{"a": str(roi.id), "b": None, "check": True, "retry": True}])
        from beetlesgallery.beetles_app.game_views import _item_payload
        self.assertTrue(_item_payload(rnd, 0)["again"])

    def test_the_command_can_do_one_player(self):
        from io import StringIO
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        out = StringIO()
        call_command("recompute_game_scores", "--player", self.user.username, stdout=out)
        self.assertIn("Re-scored 1 player.", out.getvalue())
        self.assertEqual(PlayerScore.objects.get(player=self.user).score, 15.0)

    def test_the_server_rescores_every_night(self):
        from django.conf import settings
        workflow = (settings.BASE_DIR / ".github" / "workflows" / "game-scores.yml").read_text()
        self.assertIn("cron:", workflow)
        self.assertIn("pixi run game-scores", workflow)
