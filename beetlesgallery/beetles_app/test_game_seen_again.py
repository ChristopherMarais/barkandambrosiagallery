"""
Beetles whose names a player has been shown come back (owner decision with #541): "users learn to classify these beetles
by playing". By the rule of 3 (game.SEEN_AGAIN): 3 minutes after the first time they were shown, then 3 hours, 3 days,
3 weeks and 3 months; in another game first when there is one; then at full points, but left out of accuracy and
expertise (GameAnswer.seen_before), so those still mean naming beetles unseen.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game
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


class RuleOfThreeTests(SeenAgainCase):
    def test_held_back_for_3_minutes_after_the_first_time(self):
        roi = self.roi(self.t_affinis)
        self.shown(roi, minutes_ago=2)
        self.assertIn(roi.id, game.held_back_ids(self.user))
        GameAnswer.objects.update(answered_at=timezone.now() - timedelta(minutes=4))
        self.assertNotIn(roi.id, game.held_back_ids(self.user))
        self.assertIn(roi.id, game.revealed_ids(self.user))   # still shown: it just isn't held back any more

    def test_each_time_it_is_shown_the_wait_grows(self):
        roi = self.roi(self.t_affinis)
        waits = [timedelta(minutes=3), timedelta(hours=3), timedelta(days=3), timedelta(weeks=3), timedelta(weeks=13)]
        self.assertEqual(list(game.SEEN_AGAIN), waits)
        for times, wait in enumerate(waits + [waits[-1]], start=1):   # and every 3 months from then on
            GameAnswer.objects.all().delete()
            for n in range(times):   # shown this many times, the last one ``ago``
                self.shown(roi, minutes_ago=wait.total_seconds() / 60 * 2 * (times - n))
            last = GameAnswer.objects.order_by("-answered_at").first()
            with self.subTest(times=times):
                for ago, held in ((wait - timedelta(minutes=1), True), (wait + timedelta(minutes=1), False)):
                    GameAnswer.objects.filter(pk=last.pk).update(answered_at=timezone.now() - ago)
                    self.assertEqual(roi.id in game.held_back_ids(self.user), held, (times, ago))

    def test_a_long_sitting_no_longer_holds_it_back_once_its_wait_is_over(self):
        roi = self.roi(self.t_affinis)
        self.shown(roi, minutes_ago=200)
        for minutes in range(180, -1, -20):   # answers every 20 minutes since: one sitting
            self.shown(self.roi(self.t_ferr), minutes_ago=minutes)
        self.assertNotIn(roi.id, game.held_back_ids(self.user))

    def test_another_photo_of_the_specimen_counts_its_showings(self):
        dorsal, lateral = self.roi(self.t_affinis), self.roi(self.t_affinis)
        type(dorsal).objects.filter(pk__in=[dorsal.pk, lateral.pk]).update(depicts_specimen="SPEC-1")
        self.shown(dorsal, minutes_ago=600)
        self.shown(lateral, minutes_ago=60)   # the second time this specimen is shown: 3 hours
        shown = game.reveals(self.user)
        self.assertEqual((shown[dorsal.id]["seen"], shown[lateral.id]["seen"]), (2, 2))
        self.assertTrue({dorsal.id, lateral.id} <= game.held_back_ids(self.user))

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
        self.assertIn("usually in another game", page)
        self.assertIn("The rule of 3: a beetle comes back 3 minutes after you first see it, then 3 hours, 3 days, "
                      "3 weeks and 3 months", page)
        self.assertIn("don&rsquo;t count towards your accuracy or expertise", page)
