"""
The recap after a session comes at once. It worked the player's badges out twice for every badge there is (once
for now, once for before the session), some seventy times: eleven seconds for a regular player, during which the
page said "Saving your answers…" though every answer was saved already. Now twice in all. If the request fails
the page asks again once, so the player gets the recap rather than none.
"""
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game_rewards
from beetlesgallery.beetles_app.test_game import GameCase


class RecapTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_the_badges_are_worked_out_twice_whatever_their_number(self):
        with mock.patch.object(game_rewards, "earned_badges", wraps=game_rewards.earned_badges) as earned:
            game_rewards.recap(self.user, timezone.now())
        self.assertEqual(earned.call_count, 2)
        self.assertGreater(len(game_rewards.BADGES), 2)

    def test_a_badge_counts_as_new_only_if_it_came_in_the_session(self):
        before, now = {"first", "ten"}, {"first", "ten", "hundred"}
        with mock.patch.object(game_rewards, "earned_badges", side_effect=lambda player, before=None: (
                now if before is None else {"first", "ten"} if before else set())):
            recap = game_rewards.recap(self.user, timezone.now())
        self.assertEqual([b["key"] for b in recap["badges"]], sorted(now - before, key=list(game_rewards.BADGES).index))

    def test_the_page_adds_up_and_asks_again_once_if_it_fails(self):
        page = self.page()
        start = page.index('$("exit").addEventListener("click"')
        handler = page[start:page.index('$("recap-more")', start)]
        self.assertIn('"Adding up your session…"', handler)
        self.assertNotIn("Saving your answers", handler)
        self.assertIn(".catch(() => api(root.dataset.exitUrl, goodbye)).catch(() => null)", handler)
