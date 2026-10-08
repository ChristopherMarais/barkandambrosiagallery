"""Leaving the game shows a turning spinner while the answers save (owner: it looked frozen), and the loading facts
include that all the art on the site is drawn by hand."""
from pathlib import Path

from django.test import SimpleTestCase

PAGE = (Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


class ExitSavingTests(SimpleTestCase):
    def test_the_recap_has_a_spinner_that_shows_while_saving(self):
        self.assertIn('<p id="recap-saving" class="hidden', PAGE)
        self.assertIn('<i class="fi fi-rr-spinner animate-spin text-2xl"', PAGE)
        handler = PAGE[PAGE.index('$("exit").addEventListener("click"'):]
        handler = handler[:handler.index("});")]
        self.assertLess(handler.index('$("recap-saving").classList.remove("hidden")'), handler.index("await leaving"))
        self.assertGreater(handler.index('$("recap-saving").classList.add("hidden")'), handler.index("await leaving"))

    def test_the_hand_drawn_art_fact(self):
        facts = PAGE[PAGE.index("const LOADING_FACTS = ["):]
        facts = facts[:facts.index("];")]
        self.assertIn('"All the art on this website is drawn by hand, by people."', facts)


class RecapBackLinkTests(SimpleTestCase):
    def test_the_finishing_screen_has_the_sites_back_link_to_the_game_home(self):
        recap = PAGE[PAGE.index('<div id="recap"'):PAGE.index('id="recap-title"')]
        self.assertIn('data-testid="recap-back"', recap)
        self.assertIn("{% url 'game_home' as back_url %}", recap)
        self.assertIn('{% include "beetles/includes/back_link.html" with url=back_url label=game_name %}', recap)
