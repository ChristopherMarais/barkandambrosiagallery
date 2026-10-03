"""The phone menu's first link, the beetle logo, goes home and says so (it used to show the route name)."""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase


class MobileMenuTests(PageBehaviourCase):
    def test_the_home_link_is_labelled_home(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("beetles_image_browser")).content.decode()
        menu = page[page.index('id="mobile-menu"'):]
        first_link = re.search(r'<a href="([^"]*)".*?</a>', menu, re.S)
        self.assertEqual(first_link.group(1), reverse("image_browser"))
        self.assertIn(">Home</span>", first_link.group(0))
        self.assertNotIn(">image_browser<", page)
        self.assertNotIn('alt="image_browser"', page)
