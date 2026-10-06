"""The Scoring page's difficulty controls, distributions, examples and checks (#492), and the line players read."""
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_difficulty, game_scoring, game_tuning
from beetlesgallery.beetles_app.models import GameTuning, PlayerScore, RoiDifficulty
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import PLAT, ScoringCase

DIFFICULTY_KEYS = ["GAME_DIFFICULTY_START", "GAME_DIFFICULTY_PER_ROUND", "GAME_DIFFICULTY_SKILL_WEIGHT",
                   "GAME_DIFFICULTY_MAX", "GAME_DIFFICULTY_RECENT", "GAME_DIFFICULTY_EASE_BELOW", "GAME_DIFFICULTY_EASE",
                   "GAME_DIFFICULTY_PUSH_ABOVE", "GAME_DIFFICULTY_PUSH", "GAME_POINTS_DIFFICULTY_SPREAD"]


@override_settings(GAME_POINTS_PARTICIPATION=0.5, GAME_POINTS_CLASSIFY_WEIGHT=3)   # the real numbers, not ScoringCase's
class ScoringPageDifficultyTests(ScoringCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)
        self.client.force_login(self.superuser)

    def page(self):
        return self.client.get(reverse("game_scoring")).content.decode()

    def test_the_difficulty_group_is_tunable_and_starts_unchanged(self):
        page = self.page()
        for key in DIFFICULTY_KEYS:
            self.assertIn(f'data-testid="tunable-{key}"', page)
            self.assertEqual(game_tuning.current(key), game_tuning.TUNABLES[key]["default"], key)   # settings.py agrees
        self.assertNotIn(">changed</span>", page)

    def test_edits_save_and_reach_the_game(self):
        res = self.client.post(reverse("game_scoring"), {"GAME_POINTS_DIFFICULTY_SPREAD": "0.4", "GAME_DIFFICULTY_RECENT": "20",
                                                         "GAME_DIFFICULTY_EASE": "0.2"})
        self.assertRedirects(res, reverse("game_scoring"))
        self.assertEqual(game_scoring.difficulty_spread(), 0.4)
        self.assertEqual(game.game_setting("GAME_DIFFICULTY_RECENT", 10), 20)
        self.assertEqual(GameTuning.objects.get(key="GAME_DIFFICULTY_EASE").value, 0.2)
        self.assertAlmostEqual(game_scoring.difficulty_multiplier(1.0), 1.4)

    def test_a_spread_of_one_or_more_is_refused(self):
        self.client.post(reverse("game_scoring"), {"GAME_POINTS_DIFFICULTY_SPREAD": "1"})
        self.assertFalse(GameTuning.objects.exists())

    def test_the_defaults_pass_every_check_and_a_wide_spread_is_flagged(self):
        self.assertTrue(all(ok for _, ok, _ in game_tuning.checks()), game_tuning.checks())
        GameTuning.objects.create(key="GAME_POINTS_DIFFICULTY_SPREAD", value=0.85)
        failing = [rule for rule, ok, _ in game_tuning.checks() if not ok]
        self.assertIn("A careless answer still loses on the hardest beetles", failing)
        GameTuning.objects.filter(key="GAME_POINTS_DIFFICULTY_SPREAD").update(value=1.0)
        game_tuning.forget()
        self.assertIn("Points by difficulty never make a mistake free", [r for r, ok, _ in game_tuning.checks() if not ok])

    def test_examples_show_an_easy_a_middling_and_a_hard_beetle(self):
        ex = game_tuning.difficulty_examples()
        self.assertEqual([c[:4] for c in ex["columns"]], [("Easy", 0.1, 0.8, 1.2), ("Middling", 0.5, 1.0, 1.0),
                                                           ("Hard", 0.9, 1.2, 0.8)])
        rows = {what: cells for _, what, cells in ex["rows"]}
        self.assertEqual(rows["Species correct (every rank)"], [36.0, 45.0, 54.0])
        self.assertEqual(rows["Wrong subfamily, claimed down to species"], [-126.0, -105.0, -84.0])
        self.assertIn('data-testid="difficulty-examples"', self.page())

    def test_distributions_with_no_data(self):
        page = self.page()
        self.assertIn('data-testid="scoring-distributions"', page)
        self.assertIn("No rated players yet.", page)
        self.assertIn("No answers in the last 30 days.", page)
        self.assertIn("No answers on validated beetles with a known difficulty yet.", page)

    def test_distributions_with_data(self):
        rois = []
        for d in (0.05, 0.15, 0.95):
            roi = self.roi(self.t_affinis)
            RoiDifficulty.objects.create(roi=roi, model_difficulty=d)
            rois.append(roi)
        self.roi(self.t_affinis, validated=False)            # no difficulty yet
        self.answer(self.user, rois[0], AFFINIS)
        self.answer(self.user, rois[2], PLAT)
        game_scoring.recompute([self.user.id])
        PlayerScore.objects.filter(player=self.user).update(judged=8, rating=0.42)
        game_difficulty.forget()

        dist = game_difficulty.distributions()
        validated, unvalidated = dist["beetles"]
        self.assertEqual((validated["total"], [b["count"] for b in validated["bars"]][:2]), (3, [1, 1]))
        self.assertEqual((unvalidated["total"], unvalidated["unknown"]), (0, 1))
        self.assertEqual([b["count"] for b in dist["ratings"]["bars"]][4], 1)
        [ident] = dist["points"]
        self.assertEqual((ident["game"], ident["answers"], ident["lost_share"]), ("Identification", 2, 0.5))
        deciles = dist["right"]["deciles"]
        self.assertEqual(dist["right"]["answers"], 2)
        self.assertEqual((deciles[1]["share"], deciles[8]["share"]), (1.0, 0.0))   # easiest of 3: decile 1, hardest: 8
        self.assertAlmostEqual(deciles[9]["multiplier"], 1.225, places=2)   # the middle of the hardest tenth

        page = self.page()
        for testid in ("dist-beetles", "dist-ratings", "dist-points", "dist-right"):
            self.assertIn(f'data-testid="{testid}"', page)
        self.assertIn("Identification and Similarity answers on validated beetles", page)

    def test_large_counts_are_grouped_in_threes(self):
        chart = {"label": "Validated", "total": 12345, "unknown": 0, "bars": game_difficulty._bars([12345] + [0] * 9)}
        fake = {"beetles": [chart], "ratings": {"total": 0}, "points": [], "right": {"answers": 0}}
        with mock.patch.object(game_difficulty, "distributions", return_value=fake):
            html = self.page()
        self.assertIn('<span class="digit-group">12</span><span class="digit-group" style="margin-left:0.4em">345</span>', html)

    def test_players_read_that_hard_beetles_are_worth_more(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_how")).content.decode()
        self.assertIn('data-testid="how-difficulty"', page)
        self.assertIn("harder beetles are worth more: up to 25% more", page)
        GameTuning.objects.create(key="GAME_POINTS_DIFFICULTY_SPREAD", value=0)
        game_tuning.forget()
        self.assertNotIn('data-testid="how-difficulty"', self.client.get(reverse("game_how")).content.decode())
