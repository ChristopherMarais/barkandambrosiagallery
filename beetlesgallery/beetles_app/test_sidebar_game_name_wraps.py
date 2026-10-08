"""The game's name, Bark & Ambrosia Detective, was cut off in the sidebar ("Bark & Ambrosia Detect"): in Chromium it is
204px on one line, and the open sidebar leaves its label 164px (175px in the phone menu). It now wraps onto a second
line at a fixed 10rem, so it wraps the same while the rail opens and the rows under it never move."""
from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app.test_sidebar_game_entry import game_entries
from beetlesgallery.beetles_app.testing import PageBehaviourCase

BASE = settings.BASE_DIR / "beetlesgallery" / "templates" / "base.html"


class GameNameEntryTests(PageBehaviourCase):
    def test_the_name_wraps_in_the_phone_menu_and_the_sidebar(self):
        for entry in game_entries(self.client.get(reverse("image_browser")).content.decode()):
            self.assertIn('<span class="nav-game-name block font-medium text-gray-700">'
                          'Bark &amp; Ambrosia Detective</span>', entry)


class GameNameRuleTests(SimpleTestCase):
    def setUp(self):
        self.css = " ".join(BASE.read_text().split())

    def test_the_name_has_a_fixed_width_and_may_wrap(self):
        self.assertIn(".nav-game-name { width: 10rem; white-space: normal; }", self.css)

    def test_the_10rem_fits_the_open_sidebar_and_the_phone_menu(self):
        # open rail 16rem, less its 1px border, the list's 0.5rem padding a side, the row padding a side, the 2rem
        # icon and the label's 0.75rem margin: 10.25rem. The phone menu (w-64) leaves the label more.
        self.assertIn("#sidenav:hover, #sidenav:focus-within { width: 16rem; }", self.css)
        self.assertRegex(self.css, r'<div id="mobile-menu" class="[^"]*\bw-64\b')
        rem = 16
        row_padding = (5 * rem - 1 - 2 * 0.5 * rem - 2 * rem) / 2   # the closed-rail padding, kept when it opens
        label = 16 * rem - 1 - 2 * 0.5 * rem - 2 * row_padding - 2 * rem - 0.75 * rem
        self.assertGreaterEqual(label, 10 * rem)
