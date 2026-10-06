"""The Scoring page: superusers see how scoring works with the live numbers, and tune it without a deploy."""
from unittest import mock

from django.urls import reverse

from beetlesgallery.beetles_app import game_scoring, game_tuning
from beetlesgallery.beetles_app.models import GameTuning
from beetlesgallery.beetles_app.test_game import GameCase


class ScoringPageTests(GameCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)

    def test_superusers_only(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("game_scoring")).status_code, 404)
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("game_scoring"))
        self.assertContains(page, "How scoring works")
        self.assertContains(page, 'data-testid="scoring-examples"')
        self.assertContains(page, 'data-testid="tunable-GAME_POINTS_CONSENSUS_CAP"')
        self.assertContains(self.client.get(reverse("game_review")), 'data-testid="to-scoring"')

    def test_the_defaults_pass_every_balance_check(self):
        self.assertTrue(all(ok for _, ok, _ in game_tuning.checks()), game_tuning.checks())
        examples = {what: p for _, what, p in game_tuning.examples()}
        self.assertEqual(examples["Species correct (every rank)"], 45.0)
        self.assertLess(examples["Correct genus, wrong species"], examples["Stopped at a correct genus"])

    def test_a_change_applies_to_scoring_and_can_be_put_back(self):
        self.client.force_login(self.superuser)
        res = self.client.post(reverse("game_scoring"), {"GAME_POINTS_CLASSIFY_WEIGHT": "4",
                                                         "GAME_POINTS_RANK.subfamily": "1", "GAME_POINTS_RANK.tribe": "2",
                                                         "GAME_POINTS_RANK.genus": "4", "GAME_POINTS_RANK.species": "10"})
        self.assertRedirects(res, reverse("game_scoring"))
        self.assertEqual(game_scoring.classify_weight(), 4.0)
        self.assertEqual(game_scoring.RANK_POINTS["species"], 10.0)
        self.assertEqual(GameTuning.objects.get(key="GAME_POINTS_CLASSIFY_WEIGHT").updated_by, self.superuser)
        self.client.post(reverse("game_scoring"), {"reset-GAME_POINTS_CLASSIFY_WEIGHT": "1"})
        self.assertEqual(game_scoring.classify_weight(), 3.0)
        self.assertFalse(GameTuning.objects.filter(key="GAME_POINTS_CLASSIFY_WEIGHT").exists())
        page = self.client.get(reverse("game_scoring")).content.decode()
        self.assertIn('data-testid="scoring-log"', page)

    def test_out_of_range_values_are_refused(self):
        self.client.force_login(self.superuser)
        self.client.post(reverse("game_scoring"), {"GAME_POINTS_CONSENSUS_CAP": "5"})
        self.assertFalse(GameTuning.objects.exists())
        self.assertEqual(game_tuning.clean("GAME_POINTS_CONSENSUS_CAP", "x")[1], "must be a number")

    def test_a_tuning_that_rewards_guessing_is_flagged(self):
        GameTuning.objects.create(key="GAME_POINTS_ODD_WRONG_FACTOR", value=0.1)
        failing = [rule for rule, ok, _ in game_tuning.checks() if not ok]
        self.assertEqual(failing, ["A blind guess in Imposter Picker loses on average"])

    def test_rescore_everyone_runs_in_the_background(self):
        self.client.force_login(self.superuser)
        with mock.patch("beetlesgallery.beetles_app.tasks.recompute_all_scores_task.delay") as delay:
            self.client.post(reverse("game_scoring_rescore"))
        delay.assert_called_once_with()
