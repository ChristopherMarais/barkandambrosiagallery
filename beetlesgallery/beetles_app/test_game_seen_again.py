"""
Beetles whose names a player has been shown come back (owner decision with #541): "users learn to classify these beetles
by playing". Not in the same sitting and not before GAME_REVEAL_COOLDOWN_HOURS; in another game first when there is
one; then at full points, but left out of accuracy and expertise (GameAnswer.seen_before), so those still mean naming
beetles unseen.
"""
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_tuning
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase


class SeenAgainCase(ReviewCase):
    def shown(self, roi, mode="classify", minutes_ago=0, as_partner=False):
        """An answer of this player's that showed ``roi``'s names, ``minutes_ago``."""
        rnd = GameRound.objects.create(player=self.user, mode=mode, items=[])
        # as the validated partner of a beetle nobody has validated, or as the beetle answered
        fields = ({"roi": self.roi(validated=False), "roi_b": roi, "is_check": False} if as_partner
                  else {"roi": roi, "is_check": True})
        answer = GameAnswer.objects.create(round=rnd, player=self.user, mode=mode, index=0, **fields)
        GameAnswer.objects.filter(pk=answer.pk).update(answered_at=timezone.now() - timedelta(minutes=minutes_ago))
        return answer


class CooldownTests(SeenAgainCase):
    def test_held_back_in_the_same_sitting_and_free_after_the_wait(self):
        roi = self.roi(self.t_affinis)
        self.shown(roi)
        self.assertIn(roi.id, game.held_back_ids(self.user))
        GameAnswer.objects.update(answered_at=timezone.now() - timedelta(hours=3))
        self.assertNotIn(roi.id, game.held_back_ids(self.user))
        self.assertIn(roi.id, game.revealed_ids(self.user))   # still shown: it just isn't held back any more

    def test_a_long_sitting_holds_it_back_however_long_ago(self):
        roi = self.roi(self.t_affinis)
        self.shown(roi, minutes_ago=200)
        for minutes in range(180, -1, -20):   # answers every 20 minutes since: one sitting
            self.shown(self.roi(self.t_ferr), minutes_ago=minutes)
        self.assertIn(roi.id, game.held_back_ids(self.user))

    @override_settings(GAME_REVEAL_COOLDOWN_HOURS=5)
    def test_the_wait_is_a_setting(self):
        roi = self.roi(self.t_affinis)
        self.shown(roi, minutes_ago=180)   # an earlier sitting, but only 3 hours ago
        self.assertIn(roi.id, game.held_back_ids(self.user))
        self.assertIn("GAME_REVEAL_COOLDOWN_HOURS", {t["key"] for _, group in game_tuning.GROUPS for t in group})

    def test_comes_back_in_another_game_first(self):
        here, elsewhere = self.roi(self.t_affinis), self.roi(self.t_affinis)
        self.shown(here, mode="classify", minutes_ago=300)
        self.shown(elsewhere, mode="pair", minutes_ago=300, as_partner=True)
        picked = {i["a"] for _ in range(5) for i in game.build_classify_items(self.user, 1) if i["check"]}
        self.assertEqual(picked, {str(elsewhere.id)})

    def test_but_never_starves_the_feed(self):
        only = self.roi(self.t_affinis)
        self.shown(only, mode="classify", minutes_ago=300)
        checks = [i["a"] for i in game.build_classify_items(self.user, 1) if i["check"]]
        self.assertEqual(checks, [str(only.id)])


class SeenBeforeTests(SeenAgainCase):
    def test_full_points_but_not_in_accuracy(self):
        fresh = self.classify(self.roi(self.t_affinis), AFFINIS)
        roi = self.roi(self.t_affinis)
        self.shown(roi, minutes_ago=300)
        again = self.classify(roi, AFFINIS)
        answer = GameAnswer.objects.filter(roi=roi).latest("answered_at")
        self.assertTrue(answer.seen_before)
        self.assertFalse(answer.is_retry)
        self.assertEqual(again["points"]["earned"], fresh["points"]["earned"])   # not the retry factor
        species = game.player_reliability([self.user.id])[self.user.id]["classify"]["species"]
        self.assertEqual(species["n"], 1)   # only the beetle named unseen

    def test_an_unseen_beetle_is_not_marked(self):
        self.classify(self.roi(self.t_affinis), AFFINIS)
        self.assertFalse(GameAnswer.objects.get().seen_before)

    def test_another_photo_of_a_specimen_counts_as_seen(self):
        dorsal, lateral = self.roi(self.t_affinis), self.roi(self.t_affinis)
        type(dorsal).objects.filter(pk__in=[dorsal.pk, lateral.pk]).update(depicts_specimen="SPEC-1")
        self.shown(dorsal, minutes_ago=300)
        self.assertTrue(game.was_shown(self.user, [lateral.id]))
        self.assertFalse(game.was_shown(self.user, [self.roi(self.t_affinis).id]))

    def test_how_it_works_says_so(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_how")).content.decode()
        self.assertIn('data-testid="how-seen-again"', page)
        self.assertIn("at least 2 hours on, usually in another game", page)
        self.assertIn("don&rsquo;t count towards your accuracy or expertise", page)
