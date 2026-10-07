"""The home page's "Team and Sponsors" and "Top" links stay in place but do not bounce or fade."""
from django.urls import reverse

from beetlesgallery.beetles_app.test_pages import PageTestCase


class LandingStillTextTests(PageTestCase):
    def test_the_team_and_top_links_are_still_there(self):
        page = self.client.get(reverse("image_browser")).content.decode()
        self.assertIn("Team and Sponsors", page)
        self.assertIn(">Top<", page)

    def test_the_team_and_top_links_do_not_animate(self):
        page = self.client.get(reverse("image_browser")).content.decode()
        for label, section in (("Team and Sponsors", 'id="landing-section"'), ("Top</span>", 'id="team-section"')):
            start = page.index(label, page.index(section))
            block = page[page.rindex("<button", 0, start):page.index("</button>", start)]
            self.assertNotIn("animate-", block)
            self.assertNotIn("transition-opacity", block)
