"""The game home shows five slogans that take turns, saying why playing matters."""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase

SLOGANS = [
    "Contribute to Scientific Discovery through UF’s Entomology and Nematology Program—ranked No. 1 in the world "
    "by the Center for World University Rankings.",
    "Play with Purpose. Advance Science.",
    "Classify Beetles. Support Research.",
    "Every Classification Makes a Difference.",
    "Help Scientists Protect Agriculture—One Beetle at a Time.",
]


class GameHomeSloganTests(GameCase):
    def home(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_home")).content.decode()

    def lines(self):
        block = re.search(r'data-testid="slogan">(.*?)</div>', self.home(), re.S).group(1)
        return re.findall(r"<p( data-on)?>(.*?)</p>", block, re.S)

    def test_the_five_slogans_are_on_the_page_in_order(self):
        self.assertEqual([text for _, text in self.lines()], SLOGANS)

    def test_only_the_first_shows_until_the_script_swaps_them(self):
        self.assertEqual([bool(on) for on, _ in self.lines()], [True, False, False, False, False])

    def test_the_first_names_the_program_and_the_ranking_source(self):
        text = self.lines()[0][1]
        self.assertIn("Entomology and Nematology Program", text)
        self.assertIn("Center for World University Rankings", text)

    def test_the_swap_script_is_there(self):
        self.assertIn('querySelectorAll("#game-home .slogans > p")', self.home())
