"""
A beetle whose names a player was shown counts again after a long gap (#555): naming it more than
GAME_EXPERTISE_RECALL_DAYS after it was last shown is recall, not short-term memory, so the answer counts toward
expertise, accuracy and rating again (GameAnswer.seen_before stays False). Points are the same either way.
"""
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_tuning
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_seen_again import SeenAgainCase
from beetlesgallery.beetles_app.test_train2_joins import SeveralOddCase

DAY = 24 * 60


class RecallTests(SeenAgainCase):
    def answer_after(self, *days_ago):
        """Name a beetle whose names were shown ``days_ago`` (each); returns that answer."""
        roi = self.roi(self.t_affinis)
        for days in days_ago:
            self.shown(roi, minutes_ago=days * DAY)
        self.classify(roi, AFFINIS)
        return GameAnswer.objects.filter(roi=roi).latest("answered_at")

    def species_n(self):
        return game.player_reliability([self.user.id])[self.user.id]["classify"]["species"]["n"]

    def test_within_the_gap_it_is_seen_before(self):
        self.assertTrue(self.answer_after(10).seen_before)
        self.assertEqual(self.species_n(), 0)

    def test_after_the_gap_it_counts_again(self):
        answer = self.answer_after(31)
        self.assertFalse(answer.seen_before)
        self.assertEqual(self.species_n(), 1)   # in accuracy and expertise like a beetle named unseen

    def test_the_last_time_it_was_shown_is_what_counts(self):
        self.assertTrue(self.answer_after(60, 5).seen_before)

    def test_same_points_either_way(self):
        self.answer_after(10)
        recent = self.earned()
        self.answer_after(31)
        self.assertEqual(self.earned(), recent)

    @override_settings(GAME_EXPERTISE_RECALL_DAYS=5)
    def test_the_gap_is_a_setting(self):
        self.assertFalse(self.answer_after(10).seen_before)
        self.assertTrue(self.answer_after(3).seen_before)
        keys = {t["key"]: t for _, group in game_tuning.GROUPS for t in group}
        self.assertEqual(keys["GAME_EXPERTISE_RECALL_DAYS"]["default"], 30)
        self.assertTrue(keys["GAME_EXPERTISE_RECALL_DAYS"]["label"] and keys["GAME_EXPERTISE_RECALL_DAYS"]["help"])

    def test_on_the_scoring_page_next_to_the_mistakes_coming_back(self):
        group = next(g for _, g in game_tuning.GROUPS if any(t["key"] == "GAME_RETRY_MAX" for t in g))
        self.assertIn("GAME_EXPERTISE_RECALL_DAYS", {t["key"] for t in group})

    def test_another_photo_of_the_specimen_follows_the_same_gap(self):
        dorsal, lateral = self.roi(self.t_affinis), self.roi(self.t_affinis)
        type(dorsal).objects.filter(pk__in=[dorsal.pk, lateral.pk]).update(depicts_specimen="SPEC-1")
        self.shown(dorsal, minutes_ago=10 * DAY)
        self.assertTrue(game.seen_recently(self.user, [lateral.id]))
        GameAnswer.objects.update(answered_at=timezone.now() - timedelta(days=40))
        self.assertFalse(game.seen_recently(self.user, [lateral.id]))
        self.assertTrue(game.was_shown(self.user, [lateral.id]))   # still shown, just long ago

    def test_how_it_works_says_so(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_how")).content.decode()
        self.assertIn("Once you haven&rsquo;t seen a beetle for 30 days, naming it counts again.", page)


class RecallGridTests(SeveralOddCase):
    def shown_days_ago(self, roi_id, days):
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        answer = GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, is_check=True,
                                           roi_id=roi_id)
        GameAnswer.objects.filter(pk=answer.pk).update(answered_at=timezone.now() - timedelta(days=days))

    def test_a_grid_with_a_beetle_shown_recently_is_seen_before(self):
        self.start()
        self.shown_days_ago(self.odds[1], 10)
        self.answer(self.odd_places)
        self.assertTrue(GameAnswer.objects.get(mode="odd").seen_before)

    def test_a_grid_whose_beetles_were_shown_long_ago_counts(self):
        self.start()
        self.shown_days_ago(self.odds[1], 45)
        self.shown_days_ago(self.grid["tiles"][self.rest[0]], 45)
        self.answer(self.odd_places)
        self.assertFalse(GameAnswer.objects.get(mode="odd").seen_before)

    def test_an_earlier_grid_shown_long_ago_counts_too(self):
        self.start()
        rnd = GameRound.objects.create(player=self.user, mode="select", items=[])
        old = GameAnswer.objects.create(round=rnd, player=self.user, mode="select", index=0, roi_id=self.odds[0],
                                         tiles=[self.odds[0]])
        GameAnswer.objects.filter(pk=old.pk).update(answered_at=timezone.now() - timedelta(days=45))
        self.answer(self.odd_places)
        self.assertFalse(GameAnswer.objects.get(mode="odd").seen_before)
        GameAnswer.objects.filter(pk=old.pk).update(answered_at=timezone.now() - timedelta(days=2))
        self.assertTrue(game.seen_recently(self.user, [self.odds[0]]))
