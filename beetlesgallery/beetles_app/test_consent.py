"""
The cookie choice for Google Analytics, and the privacy notice (docs/analytics.md, "Consent").

* The notice is on public pages while analytics is on; Accept and Reject look and size the same.
* The footer has "Cookie settings" (reopens the choice) and "Privacy notice" (/privacy/).
* Nothing from Google loads before Accept, and the script only ever asks for consent on the first visit.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.test_pages import NO_WHITENOISE, PLAIN_STATIC

GA_ID = "G-TEST12345"
STATIC_JS = Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "js" / "analytics.js"
BANNER = re.compile(r'<div id="consent-banner".*?</div>\s*</div>', re.S)
BUTTON = re.compile(r'<button type="button" class="([^"]*)" data-consent="(granted|denied)">([^<]*)</button>')
PUBLIC = ("/", "/accounts/login/", "/accounts/signup/", "/tools/classify/")


def page(client, url):
    return client.get(url).content.decode()


@override_settings(GA_MEASUREMENT_ID=GA_ID, STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class ChoiceTests(TestCase):
    def test_the_notice_is_on_public_pages_with_two_equal_buttons(self):
        for url in PUBLIC:
            html = page(self.client, url)
            banner = BANNER.search(html)
            self.assertIsNotNone(banner, url)
            buttons = BUTTON.findall(banner.group(0))
            self.assertEqual(sorted(label for _, _, label in buttons), ["Accept", "Reject"], url)
            # same style for both, and neither is the main (default-looking) button
            self.assertEqual({cls for cls, _, _ in buttons}, {"btn-secondary"}, url)
            self.assertNotIn("btn-main", banner.group(0), url)
            self.assertNotIn("No thanks", banner.group(0), url)

    def test_no_pre_ticked_or_default_choice_is_stored(self):
        html = page(self.client, "/")
        banner = BANNER.search(html).group(0)
        self.assertNotIn("checked", banner)
        self.assertNotIn("selected", banner)

    def test_the_footer_has_cookie_settings_and_the_privacy_notice(self):
        html = page(self.client, "/")
        footer = re.search(r'<footer[^>]*data-testid="site-footer".*?</footer>', html, re.S).group(0)
        self.assertIn("data-open-consent", footer)
        self.assertIn("Cookie settings", footer)
        self.assertIn(f'href="{reverse("privacy")}"', footer)

    def test_the_gtag_script_is_not_in_the_page_before_accept(self):
        for url in PUBLIC:
            html = page(self.client, url)
            self.assertNotIn("googletagmanager.com", html, url)
            self.assertIn('src="/static/js/analytics.js"', html, url)

    def test_the_script_asks_for_consent_before_loading_gtag(self):
        source = STATIC_JS.read_text(encoding="utf-8")
        default = source.index('gtag("consent", "default"')
        allow = source.index("function allow()")
        load = source.index("googletagmanager.com/gtag/js")
        self.assertLess(default, allow)
        self.assertLess(allow, load)
        self.assertIn('analytics_storage: "denied"', source)

    def test_withdrawing_removes_the_google_cookies(self):
        source = STATIC_JS.read_text(encoding="utf-8")
        withdraw = source[source.index("function withdraw()"):source.index("function track(")]
        self.assertIn('analytics_storage: "denied"', withdraw)
        self.assertIn("Max-Age=0", withdraw)
        self.assertIn("_ga", withdraw)

    def test_the_game_stops_above_the_notice(self):
        html = page(self.client, "/")
        self.assertIn("body.consent-showing #game { bottom: var(--consent-h, 0px); }", html)


@override_settings(GA_MEASUREMENT_ID="", STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class NoAnalyticsTests(TestCase):
    def test_no_notice_and_no_cookie_settings_link_without_the_id(self):
        html = page(self.client, "/")
        self.assertNotIn("consent-banner", html)
        self.assertNotIn("data-open-consent", html)
        self.assertIn(f'href="{reverse("privacy")}"', html)   # the notice itself is always there


@override_settings(GA_MEASUREMENT_ID=GA_ID, STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class PrivacyPageTests(TestCase):
    def test_the_privacy_notice_renders_for_everyone(self):
        response = self.client.get(reverse("privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(reverse("privacy"), "/privacy/")
        html = response.content.decode()
        for text in ("Privacy notice", "Who we are", "Google Analytics", "Your rights", "How long we keep it",
                     "Cookie settings", "Reject"):
            self.assertIn(text, html)

    @override_settings(PRIVACY_CONTACT_EMAIL="privacy@example.org")
    def test_the_contact_address_is_shown_when_set(self):
        self.assertIn("privacy@example.org", self.client.get("/privacy/").content.decode())

    @override_settings(PRIVACY_CONTACT_EMAIL="")
    def test_no_contact_line_when_the_address_is_not_set(self):
        html = self.client.get("/privacy/").content.decode()
        self.assertNotIn("mailto:", html)
