"""After about an hour of continuous play the game suggests a break, once an hour, never blocking (#394)."""
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase


class BreakNudgeTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_the_nudge_is_on_the_page_after_an_hour_by_default(self):
        page = self.page()
        self.assertIn('data-break-minutes="60"', page)
        self.assertIn('id="break-nudge" class="hidden"', page)
        self.assertIn('id="break-done">Done for today', page)
        self.assertIn('id="break-more">Keep going', page)

    @override_settings(GAME_BREAK_NUDGE_MINUTES=0)
    def test_it_can_be_turned_off(self):
        self.assertIn('data-break-minutes="0"', self.page())
