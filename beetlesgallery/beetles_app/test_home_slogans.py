"""
The website's home page says what the site is for, one line at a time (#660, #665): better bark and ambrosia beetle
data, open to everyone, and each of the things the site does, each line a link to it, the game included. They take
turns, pause while pointed at or focused, and the game home has none.
"""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.test_pages import NO_WHITENOISE, PLAIN_STATIC


@override_settings(STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class HomeSloganTests(TestCase):
    def home(self):
        return self.client.get(reverse("image_browser")).content.decode()   # "/", the landing page

    def lines(self):
        block = re.search(r'data-testid="home-slogans">(.*?)</div>', self.home(), re.S).group(1)
        return re.findall(r'<p( data-on)?><a href="([^"]+)">(.*?)</a></p>', block, re.S)

    def test_each_line_links_to_what_it_names(self):
        links = [href for _, href, _ in self.lines()]
        self.assertEqual(links, [reverse("team"), reverse("beetles_image_browser"), reverse("tool_classify"),
                                 reverse("taxonomy_browser"), reverse("interactions_preview"), reverse("game_home")])

    def test_it_opens_on_what_the_site_is_for_and_ends_on_the_game(self):
        texts = [text for _, _, text in self.lines()]
        self.assertIn("open to everyone who studies bark and ambrosia beetles", texts[0])
        self.assertIn("IBBI-AI", texts[2])
        self.assertIn("pathogens", texts[4])
        self.assertIn("Bark &amp; Ambrosia Detective", texts[5])   # the game, by its name (GAME_DISPLAY_NAME)

    def test_only_the_first_shows_until_the_script_swaps_them(self):
        self.assertEqual([bool(on) for on, _, _ in self.lines()], [True, False, False, False, False, False])

    def test_the_script_takes_turns_and_holds_still_when_pointed_at(self):
        page = self.home()
        for part in ('document.querySelector(".home-slogans")', 'box.addEventListener("mouseenter"',
                     'box.addEventListener("focusin"', 'document.addEventListener("visibilitychange"'):
            self.assertIn(part, page)

    def test_the_game_home_has_no_slogans(self):
        user = get_user_model().objects.create_user("player", password="pw")
        self.client.force_login(user)
        page = self.client.get(reverse("game_home")).content.decode()
        self.assertNotIn("slogans", page)
