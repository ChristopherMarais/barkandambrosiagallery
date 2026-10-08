"""The game is called Bark & Ambrosia Detective; levels 5-9 follow a bark beetle's life with matching icons; the sidebar has
one entry for the game, showing the player's level and points under its name."""
from django.conf import settings

from beetlesgallery.beetles_app import game_levels
from beetlesgallery.beetles_app.models import PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase


class AmbrosiaArchiveTests(GameCase):
    def test_the_name_comes_from_one_setting(self):
        self.assertEqual(settings.GAME_DISPLAY_NAME, "Bark & Ambrosia Detective")
        self.client.force_login(self.user)
        page = self.client.get("/game/").content.decode()
        self.assertIn("Bark &amp; Ambrosia Detective", page)
        self.assertNotIn("Beetle ID Game", page)

    def test_levels_follow_a_bark_beetles_life_and_their_icons_match(self):
        names = [name for _, _, name, _ in game_levels.LEVELS]
        self.assertEqual(names, ["Egg", "Larva", "Pupa", "Teneral", "Tunnel master", "Gallery engineer", "Fungus farmer",
                                 "Brood guardian", "Colony founder", "King of Bark and Ambrosia"])
        self.assertNotIn("Taxonomist", names)   # kept for real taxonomists' identifications
        self.assertEqual([game_levels.level_icon(n) for n in range(5, 10)],
                         ["fi-rr-pickaxe", "fi-rr-route", "fi-rr-mushroom", "fi-rr-shield", "fi-rr-house-tree"])
        self.assertEqual(len(game_levels.LEVEL_ICONS), len(game_levels.LEVELS))

    def test_one_sidebar_entry_for_the_game_carries_the_players_level_and_points(self):
        PlayerScore.objects.create(player=self.user, score=1234, rating=0.65)
        self.client.force_login(self.user)
        page = self.client.get("/").content.decode()
        self.assertEqual(page.count('data-testid="sidebar-player"'), 2)   # phone menu and desktop sidebar, inside the game entry
        for chunk in page.split('data-testid="sidebar-player"')[1:]:
            self.assertIn("pts", chunk[:400])
        entry = page[page.rfind('<a href="/game/"', 0, page.index('data-testid="sidebar-player"')):]
        self.assertIn("Bark &amp; Ambrosia Detective", entry[:entry.index('data-testid="sidebar-player"')])
