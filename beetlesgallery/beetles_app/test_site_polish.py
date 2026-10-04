"""Issue #418: quieter site messages, no home Request access, the curators notice shown once."""
from django.conf import settings
from django.urls import reverse

from beetlesgallery.beetles_app import game_levels
from beetlesgallery.beetles_app.models import GamePreference
from beetlesgallery.beetles_app.test_pages import PageTestCase


class SitePolishTests(PageTestCase):
    def test_the_home_page_has_no_request_access_button(self):
        self.assertNotContains(self.client.get(reverse("image_browser")), "landing-request-access")

    def test_being_sent_to_sign_in_shows_no_bubble(self):
        res = self.client.get(reverse("login") + "?next=/game/")
        self.assertNotContains(res, "Please log in to continue")

    def test_site_messages_are_grey_and_only_errors_are_red(self):
        base = (settings.BASE_DIR / "beetlesgallery" / "templates" / "base.html").read_text()
        block = base[base.index("{% for message in messages %}"):base.index("{% endfor %}", base.index("{% for message in messages %}"))]
        self.assertNotIn("green", block)
        self.assertIn("bg-red-50", block)


class ProposalsNoticeTests(PageTestCase):
    def setUp(self):
        super().setUp()
        GamePreference.objects.create(player=self.user, granted_perks=[game_levels.PROPOSALS])
        self.client.force_login(self.user)

    def test_the_game_home_shows_it_once(self):
        first = self.client.get(reverse("game_home"))
        self.assertContains(first, 'data-testid="proposals-banner"')
        self.assertNotContains(first, "bg-green-50")
        self.assertNotContains(self.client.get(reverse("game_home")), 'data-testid="proposals-banner"')

    def test_the_unlocks_box_lights_up_only_when_opened_from_the_notice(self):
        self.assertContains(self.client.get(reverse("game_unlocks") + "?new=labels"), "labels-new")
        plain = self.client.get(reverse("game_unlocks"))
        self.assertNotContains(plain, 'class="p-4 border border-gray-200 bg-white rounded-2xl mb-8 labels-new"')
        self.assertNotContains(plain, "border-green-200 bg-green-50")
