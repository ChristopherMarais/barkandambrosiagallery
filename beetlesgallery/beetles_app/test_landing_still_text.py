"""The home page fits on one screen: no second full-screen team section, no "Team and Sponsors" / "Top" scroll
buttons. The team and institutions live on /team/ (#618 home-team)."""
from django.urls import reverse

from beetlesgallery.beetles_app.test_pages import PageTestCase


class LandingOneScreenTests(PageTestCase):
    def test_no_scroll_buttons_between_two_screen_high_sections(self):
        page = self.client.get(reverse("image_browser")).content.decode()
        self.assertNotIn("Team and Sponsors", page)
        self.assertNotIn(">Top<", page)
        self.assertNotIn("lg:snap-start", page)

    def test_the_team_page_holds_the_team_and_institutions(self):
        page = self.client.get(reverse("team")).content.decode()
        self.assertIn('id="team-section"', page)
        self.assertIn('id="institutions-list"', page)
