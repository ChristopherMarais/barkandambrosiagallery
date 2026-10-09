"""
After a session, the button that takes the player back to the game home just says Exit (owner): on the recap and on
the "all caught up" screen at the end of the feed. It still goes to the game home.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase


class ExitButtonTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_the_recap_says_exit_and_goes_home(self):
        page = self.page()
        link = page[page.rindex("<a", 0, page.index('data-testid="recap-home"')):]
        link = link[:link.index("</a>")]
        self.assertIn(f'href="{reverse("game_home")}"', link)
        self.assertTrue(link.endswith(">Exit"), link)

    def test_so_does_the_end_of_the_feed(self):
        page = self.page()
        link = page[page.rindex("<a", 0, page.index('data-testid="caughtup-exit"')):]
        link = link[:link.index("</a>")]
        self.assertIn(f'href="{reverse("game_home")}"', link)
        self.assertTrue(link.endswith(">Exit"), link)
        self.assertNotIn("Back to the game home", page)
