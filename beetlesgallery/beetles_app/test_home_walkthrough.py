"""The walkthrough can be replayed from the game home (#392), not only from How it works."""
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase


class HomeWalkthroughTests(GameCase):
    def test_the_home_links_to_the_walkthrough_and_it_starts_on_the_play_page(self):
        self.client.force_login(self.user)
        home = self.client.get(reverse("game_home")).content.decode()
        tour = f'{reverse("game_play", args=["mixed"])}?tour=1'
        self.assertIn(f'href="{tour}"', home)
        self.assertIn('data-first="1"', self.client.get(tour).content.decode())
