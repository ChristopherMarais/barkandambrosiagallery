"""Game settings: the Unlocks list is paged (25 a page), and search and sorting keep their place across pages."""
import re

from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app.models import GamePreference
from beetlesgallery.beetles_app.test_game import GameCase

ROW = re.compile(r'id="p(\d+)"[^>]*data-testid="unlock-row"')


class GameSettingsPagingTests(GameCase):
    def setUp(self):
        super().setUp()
        for i in range(30):   # thirty players with a grant, so each is listed under Unlocks
            user = get_user_model().objects.create_user(username=f"pl{i:02d}", password="not-a-real-password")
            GamePreference.objects.create(player=user, granted_perks=["focus_genus"])
        self.client.force_login(self.superuser)

    def page(self, query=""):
        return self.client.get(reverse("game_settings") + query).content.decode()

    def rows(self, html):
        return [int(i) for i in ROW.findall(html)]

    def pager(self, html):
        return html[html.index('data-testid="pager-unlocks"'):]

    def test_a_long_list_is_split_across_pages(self):
        first, second = self.page(), self.page("?unlocks_page=2")
        page_one, page_two = self.rows(first), self.rows(second)
        self.assertEqual(len(page_one), 25)
        self.assertTrue(page_two)
        self.assertEqual(set(page_one) & set(page_two), set())             # no player on both pages
        names = dict(get_user_model().objects.filter(username__startswith="pl").values_list("id", "username"))
        self.assertEqual(names[page_one[0]], "pl00")                        # still sorted by username
        self.assertEqual(sorted(names[i] for i in page_one + page_two), [f"pl{i:02d}" for i in range(30)])
        self.assertIn("unlocks_page=2#unlocks", self.pager(first))

    def test_the_pager_says_where_you_are(self):
        second = self.page("?unlocks_page=2")
        self.assertIn('<span class="digit-group">26</span>&ndash;', self.pager(second))   # numbers grouped in threes
        self.assertIn("Page 2 of", self.pager(second))
        self.assertIn("unlocks_page=1#unlocks", self.pager(second))        # back to the first page

    def test_search_and_the_page_links_keep_the_filter(self):
        html = self.page("?q=pl&unlocks_page=2")
        self.assertIn('value="pl"', html)                                   # the search box keeps its text
        self.assertIn("q=pl", self.pager(html))
        self.assertIn("unlocks_page=1", self.pager(html))
        self.assertEqual(len(self.rows(html)), 5)                           # pl25 to pl29 on page two

    def test_searching_starts_again_on_the_first_page_but_keeps_other_tables(self):
        html = self.page("?unlocks_page=2&players_page=3&players_sort=player")
        start = html.index('action="#unlocks"')
        form = html[start:html.index("</form>", start)]
        self.assertNotIn('name="unlocks_page"', form)                       # a new search does not keep the page number
        self.assertIn('name="players_page" value="3"', form)
        self.assertIn('name="players_sort" value="player"', form)

    def test_saving_from_a_later_page_goes_back_to_that_page(self):
        target = get_user_model().objects.get(username="pl00")
        res = self.client.post(reverse("game_settings"), {"player": target.id, "perks": ["focus_genus"],
                                                          "back": "unlocks_page=2"})
        self.assertRedirects(res, f"{reverse('game_settings')}?unlocks_page=2#p{target.id}",
                             fetch_redirect_response=False)
