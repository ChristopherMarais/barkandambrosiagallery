"""One Game settings page for superusers (#568): reviewing the game's labels and granting unlocks, with one link to it."""
from django.conf import settings
from django.urls import reverse

from beetlesgallery.beetles_app import game_levels
from beetlesgallery.beetles_app.models import GamePreference, GameReport
from beetlesgallery.beetles_app.test_game import GameCase


def template(name):
    return (settings.BASE_DIR / "beetlesgallery" / "templates" / "beetles" / name).read_text(encoding="utf-8")


class GameSettingsPageTests(GameCase):
    def page(self, query=""):
        return self.client.get(reverse("game_settings") + query).content.decode()

    def test_superusers_only(self):
        url = reverse("game_settings")
        self.assertRedirectsToLogin(self.client.get(url))
        for account in (self.user, self.staff):
            self.client.force_login(account)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, {"player": self.user.id, "all": "1"}).status_code, 404)
        self.assertFalse(GamePreference.objects.filter(player=self.user).exclude(granted_perks=[]).exists())
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_both_sections_and_the_scoring_link_are_on_it(self):
        roi = self.roi(self.t_affinis)
        GameReport.objects.create(roi=roi, reporter=self.user, reason="bad_box")
        GamePreference.objects.create(player=self.user, granted_perks=["focus_genus"])
        self.client.force_login(self.superuser)
        page = self.page()
        self.assertIn("<h1 class=\"page-title\">Game settings</h1>", page)
        self.assertIn('id="review"', page)
        self.assertIn("Review game labels", page)
        self.assertIn("Open in annotator", page)                          # the open reports
        self.assertIn(reverse("game_export", args=["labels"]), page)      # the CSVs
        self.assertIn('id="unlocks"', page)
        self.assertIn('data-testid="unlock-row"', page)
        self.assertIn(f'id="p{self.user.id}"', page)                      # players with grants are listed
        nav = page[page.index('data-testid="settings-nav"'):page.index('id="review"')]
        self.assertIn(f'href="{reverse("game_scoring")}"', nav)           # Scoring, at the top
        self.assertLess(page.index('id="review"'), page.index('id="unlocks"'))

    def test_searching_players_keeps_the_review_tables_where_they_were(self):
        self.client.force_login(self.superuser)
        page = self.page(f"?q={self.user.username}&players_sort=player")
        form = page[page.index('id="unlocks"'):]
        self.assertIn('<input type="hidden" name="players_sort" value="player">', form)
        self.assertIn(f'value="{self.user.username}"', form)
        self.assertNotIn('name="q" value=""', form)

    def test_saving_unlocks_lands_back_on_that_player(self):
        self.client.force_login(self.superuser)
        res = self.client.post(reverse("game_settings"),
                               {"player": self.user.id, "perks": ["focus_genus"], "back": "q=pl&players_page=2"})
        self.assertRedirects(res, f"{reverse('game_settings')}?q=pl&players_page=2#p{self.user.id}",
                             fetch_redirect_response=False)
        self.assertEqual(game_levels.for_player(self.user)["perks"], {"focus_genus"})
        res = self.client.post(reverse("game_settings"), {"player": self.user.id, "all": "1", "back": ""})
        self.assertEqual(res["Location"], f"{reverse('game_settings')}#p{self.user.id}")
        self.assertEqual(game_levels.for_player(self.user)["perks"], set(game_levels.PERKS))

    def test_the_back_field_cannot_send_you_elsewhere(self):
        self.client.force_login(self.superuser)
        res = self.client.post(reverse("game_settings"), {"player": self.user.id, "back": "//evil.example/x"})
        self.assertTrue(res["Location"].startswith(reverse("game_settings") + "?"))

    def test_the_review_tables_still_page_and_filter(self):
        self.client.force_login(self.superuser)
        page = self.page("?trusted=1")
        self.assertIn('id="labels" open', page)
        self.assertIn("No proposals backed by experts yet.", page)


class OldAddressTests(GameCase):
    def test_the_label_review_redirects_to_its_section(self):
        self.client.force_login(self.superuser)
        res = self.client.get(reverse("game_review"))
        self.assertRedirects(res, reverse("game_settings") + "#review", fetch_redirect_response=False)
        res = self.client.get(reverse("game_review") + "?players_page=2&trusted=1")
        self.assertEqual(res["Location"], reverse("game_settings") + "?players_page=2&trusted=1#review")

    def test_the_unlocks_redirect_to_their_section(self):
        self.client.force_login(self.superuser)
        res = self.client.get(reverse("game_staff_unlocks") + "?q=amy")
        self.assertEqual(res["Location"], reverse("game_settings") + "?q=amy#unlocks")

    def test_an_old_open_tab_can_still_save_unlocks(self):
        self.client.force_login(self.superuser)
        res = self.client.post(reverse("game_staff_unlocks"), {"player": self.user.id, "all": "1", "q": "pl"})
        self.assertEqual(res["Location"], f"{reverse('game_settings')}?q=pl#p{self.user.id}")
        self.assertEqual(game_levels.for_player(self.user)["perks"], set(game_levels.PERKS))
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse("game_staff_unlocks"), {"player": self.user.id}).status_code, 404)

    def test_the_csv_exports_keep_their_addresses(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("game_export", args=["labels"]))["Content-Type"], "text/csv")


class OneLinkTests(GameCase):
    def test_the_game_home_has_one_settings_link_for_superusers(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("game_home")).content.decode()
        self.assertEqual(page.count('data-testid="game-settings-link"'), 1)
        self.assertIn(f'href="{reverse("game_settings")}"', page)
        self.assertNotIn(f'href="{reverse("game_review")}"', page)
        self.assertNotIn(f'href="{reverse("game_staff_unlocks")}"', page)
        self.client.force_login(self.user)
        self.assertNotIn('data-testid="game-settings-link"', self.client.get(reverse("game_home")).content.decode())

    def test_no_template_links_to_the_old_pages(self):
        root = settings.BASE_DIR / "beetlesgallery" / "templates"
        for path in root.rglob("*.html"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotIn("'game_review'", source)
                self.assertNotIn("'game_staff_unlocks'", source)

    def test_the_scoring_page_goes_back_to_the_settings(self):
        # the shared back link (site-back, #618): {% url 'game_settings' as back_url %} + back_link.html
        self.assertIn("{% url 'game_settings' as back_url %}", template("game_scoring.html"))
