"""Issue #500: the sidebar's game entry always shows its icon, and a signed-in player's level badge, level
and points sit on a line under the game's name. The closed sidebar's icons sit in the middle of their shading, and the
staging bar is red.

The icon is the play icon, not the gamepad (nav-play-icon, #618): the game itself already uses a play button, so the
sidebar and home card match it."""
import re

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.models import PlayerScore
from beetlesgallery.beetles_app.templatetags.beetle_tags import digit_groups_text
from beetlesgallery.beetles_app.testing import PageBehaviourCase

BASE = settings.BASE_DIR / "beetlesgallery" / "templates" / "base.html"
PLAY_ICON = '<i class="fi fi-rr-play text-xl text-gray-700 w-8 text-center"></i>'


def game_entries(page):
    """The game's link in the phone menu and in the desktop sidebar (not the game's card on the home page)."""
    phone = page[page.index('id="mobile-menu"'):page.index('id="sidenav"')]
    desktop = page[page.index('id="sidenav"'):page.index("</aside>")]
    link = re.compile(r'<a href="%s".*?</a>' % re.escape(reverse("game_home")), re.S)
    return [link.search(part).group(0) for part in (phone, desktop)]


class GameEntryTests(PageBehaviourCase):
    def entries(self):
        return game_entries(self.client.get(reverse("image_browser")).content.decode())

    def sign_in(self, score, rating):
        PlayerScore.objects.create(player=self.user, score=score, rating=rating)
        self.client.force_login(self.user)

    def assert_play_icon_first(self, entry):
        self.assertTrue(entry[entry.index(">") + 1:].lstrip().startswith(PLAY_ICON), entry)

    def test_signed_out_it_is_the_play_icon_and_the_games_name(self):
        for entry in self.entries():
            self.assert_play_icon_first(entry)
            self.assertIn("Ambrosia Archive", entry)
            self.assertNotIn('data-testid="level-badge"', entry)
            self.assertNotIn('data-testid="sidebar-player"', entry)

    def test_signed_in_the_level_badge_level_and_points_sit_under_the_games_name(self):
        self.sign_in(1234, 0.65)   # level 5, Tunnel master
        phone, desktop = self.entries()
        for entry in (phone, desktop):
            self.assert_play_icon_first(entry)   # the badge is no longer the icon
            name = entry.index("Ambrosia Archive")
            badge = entry.index('data-testid="level-badge"')
            level = entry.index(">Tunnel master</span>")
            points = entry.index(" pts</span>")
            self.assertTrue(name < badge < level < points, entry)
            self.assertIn('title="Level 5: Tunnel master"', entry)
            self.assertEqual(entry.count('data-testid="sidebar-player"'), 1)
        self.assertIn(f"&middot; {digit_groups_text(1234)} pts", desktop)
        self.assertIn('<span class="digit-group">1</span><span class="digit-group" style="margin-left:0.4em">234</span> pts',
                      phone)
        self.assertIn(f'title="Ambrosia Archive: user, level 5 (Tunnel master), {digit_groups_text(1234)} pts"', desktop)
        self.assertNotIn(">Beta</span>", phone)   # out of beta (#538): the phone menu's pill is gone too

    def test_a_long_level_name_is_shortened_but_never_the_points(self):
        self.sign_in(30000, 0.95)   # level 10, King of Bark and Ambrosia
        for entry in self.entries():
            self.assertIn('<span class="truncate">King of Bark and Ambrosia</span><span class="shrink-0">&nbsp;&middot; ',
                          entry)


class StagingBarTests(PageBehaviourCase):
    @override_settings(STAGING=True)
    def test_the_staging_bar_is_red(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("image_browser")).content.decode()
        bar = re.search(r'<div class="([^"]*)"[^>]*data-testid="staging-banner"', page)
        self.assertIsNotNone(bar)
        self.assertIn("bg-red-700", bar.group(1).split())
        self.assertIn("text-white", bar.group(1).split())
        self.assertNotIn("bg-black", bar.group(1).split())

    def test_the_real_site_has_no_staging_bar(self):
        self.assertNotContains(self.client.get(reverse("image_browser")), 'data-testid="staging-banner"')


class ClosedSidebarTests(SimpleTestCase):
    """The rules that keep the closed sidebar's icons in the middle of their shading. Measured in a browser for #500:
    every row's icon has 15.5px of shading either side when closed, and does not move when the sidebar opens."""

    def setUp(self):
        self.css = " ".join(BASE.read_text().split())

    def test_rows_are_padded_so_the_icon_is_centred_and_the_padding_is_the_same_open(self):
        # #sidenav-footer > a joined the list when Account was pinned to the footer (nav-drawer-account, #618):
        # its row is padded the same as every other closed-rail row.
        self.assertIn("#sideItems > a:not(#sidenav-logo), #sideItems > form > button, #sidenav-footer > div, "
                      "#sidenav-footer > a { padding-inline: calc((5rem - 1px - 2 * 0.5rem - 2rem) / 2); }", self.css)
        self.assertIn("#sidenav i.fi { flex-shrink: 0; }", self.css)

    def test_the_padding_is_worked_out_from_the_rails_sizes(self):
        # the terms of the calc: a 5rem rail with a 1px border, a list padded 0.5rem (p-2) a side, 2rem (w-8) icons
        self.assertRegex(self.css, r"#sidenav \{[^}]*width: 5rem;")
        self.assertRegex(self.css, r'<aside id="sidenav" class="[^"]*\bborder-r\b')
        self.assertRegex(self.css, r'<div id="sideItems" class="[^"]*\bp-2\b')
        sidebar = self.css[self.css.index('<aside id="sidenav"'):self.css.index("</aside>")]
        icons = re.findall(r'<i class="fi fi-rr-[^"]*"', sidebar)
        self.assertGreaterEqual(len(icons), 8)
        self.assertTrue(all(" w-8 " in icon for icon in icons), icons)

    def test_no_scrollbar_narrows_the_closed_rows(self):
        # :not(:focus-within) joined :not(:hover) (nav-focus, #618): a keyboard user tabbing the rail open gets the
        # same scrollbar treatment a mouse hovering it would.
        self.assertIn("#sidenav:not(:hover):not(:focus-within) #sideItems { scrollbar-width: none; }", self.css)
        self.assertIn("#sidenav:not(:hover):not(:focus-within) #sideItems::-webkit-scrollbar { display: none; }",
                      self.css)

    def test_only_the_labels_are_indented_not_the_spans_inside_them(self):
        self.assertIn("#sidenav:hover span:not(span span), #sidenav:focus-within span:not(span span) { "
                      "opacity: 1; width: auto; margin-left: 0.75rem;", self.css)
        self.assertNotIn("#sidenav:hover span {", self.css)
        self.assertNotIn("sidebar-icon", self.css)   # the level badge left the icon column, and its special case with it
