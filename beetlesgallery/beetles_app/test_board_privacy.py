"""
Leaderboard privacy (#394): a player can show as "A player" to others, and can hide the boards from their own game
home. Their own name still shows to themselves, and a name search doesn't find someone who hid theirs.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app import game_board
from beetlesgallery.beetles_app.models import GamePreference, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase


class BoardPrivacyTests(GameCase):
    def setUp(self):
        super().setUp()
        self.shy = get_user_model().objects.create_user("shybeetle", password="pw")
        for player, score in ((self.shy, 900), (self.user, 500)):
            PlayerScore.objects.create(player=player, score=score, viewed=40)
        GamePreference.objects.create(player=self.shy, hide_name=True)

    def page(self, name, *args, user=None, query=""):
        self.client.force_login(user or self.user)
        return self.client.get(reverse(name, args=args) + query).content.decode()

    def test_others_see_a_player_who_hid_their_name_as_a_player_without_a_link(self):
        page = self.page("game_leaderboard", query="?period=all")
        self.assertIn("A player", page)
        self.assertNotIn("shybeetle", page)
        self.assertNotIn(reverse("game_profile", args=[self.shy.id]), page)

    def test_they_still_see_their_own_name(self):
        rows = game_board.board(period="all", viewer_id=self.shy.id)
        self.assertEqual([(r["username"], r["anonymous"]) for r in rows][0], ("shybeetle", False))
        self.assertIn("shybeetle", self.page("game_leaderboard", user=self.shy, query="?period=all"))

    def test_a_name_search_does_not_find_them(self):
        self.assertEqual(game_board.board(period="all", q="shy", viewer_id=self.user.id), [])

    def test_their_profile_and_expertise_show_a_player_to_others(self):
        for name, args in (("game_profile", [self.shy.id]), ("game_player_expertise", [self.shy.id])):
            self.assertNotIn("shybeetle", self.page(name, *args), name)
            self.assertIn("A player", self.page(name, *args), name)
        self.assertIn("shybeetle", self.page("game_profile", self.shy.id, user=self.shy))

    def test_hiding_the_boards_leaves_them_out_of_the_game_home(self):
        self.assertIn('data-testid="home-board"', self.page("game_home"))
        GamePreference.objects.update_or_create(player=self.user, defaults={"hide_boards": True})
        home = self.page("game_home")
        self.assertNotIn('data-testid="home-board"', home)
        self.assertNotIn(f'href="{reverse("game_leaderboard")}"', home)

    def test_the_unlocks_page_saves_both(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("game_unlocks"), {"boards": "1", "hide_name": "on", "hide_boards": "on"})
        self.assertRedirects(response, reverse("game_unlocks") + "#boards", fetch_redirect_response=False)
        pref = GamePreference.objects.get(player=self.user)
        self.assertEqual((pref.hide_name, pref.hide_boards), (True, True))
        self.client.post(reverse("game_unlocks"), {"boards": "1"})   # both unticked
        pref.refresh_from_db()
        self.assertEqual((pref.hide_name, pref.hide_boards), (False, False))
