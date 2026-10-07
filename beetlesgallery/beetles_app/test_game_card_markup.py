"""The game home's per-game cards never nest a link in a link: the "Play" link inside an <a> card made browsers
split each unplayed card in two (an empty "·" / "Play" box next to it). The card is a div with the History link
stretched over it instead."""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.models import PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase


class GameCardMarkupTests(GameCase):
    def card(self, key):
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 60, "rating": 0.0})
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_home")).content.decode()
        marker = f'data-testid="game-card-{key}"'
        start = page.rindex("<div", 0, page.index(marker))
        end = page.find('data-testid="game-card-', page.index(marker) + len(marker))   # the next card
        return page[start:end if end != -1 else start + 1500]

    def test_the_card_is_a_div_with_an_empty_stretched_history_link(self):
        card = self.card("odd")
        self.assertTrue(card.startswith("<div"))
        self.assertRegex(card, rf'<a href="{re.escape(reverse("game_history"))}\?game=odd" class="absolute inset-0[^"]*"[^>]*></a>')

    def test_the_play_link_is_not_inside_another_link(self):
        card = self.card("odd")
        self.assertIn(f'href="{reverse("game_play", args=["odd"])}"', card)
        # every <a> in the card closes before the next one opens
        self.assertNotRegex(card, r"<a\b(?:(?!</a>).)*<a\b")
