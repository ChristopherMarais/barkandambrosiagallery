"""
Google Analytics (GA4) and its cookie notice (docs/analytics.md).

* With GA_MEASUREMENT_ID empty (local, staging, tests) nothing analytics-related is rendered.
* With the ID set, the loader and the notice appear on public pages, and nothing personal is in what is rendered.
* The page reported to Google is the URL pattern, never a real address with IDs or a query string.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from beetlesgallery.beetles_app.analytics import page_pattern
from beetlesgallery.beetles_app.test_pages import NO_WHITENOISE, PLAIN_STATIC

GA_ID = "G-TEST12345"
STATIC_JS = Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "js" / "analytics.js"
LOADER = re.compile(r"<script[^>]*analytics\.js[^>]*>")
BANNER = re.compile(r'<div id="consent-banner".*?</div>\s*</div>', re.S)
ANALYTICS_MARKERS = ("googletagmanager", "analytics.js", "consent-banner", "data-ga-", "ga_consent", "gaTrack")


def page(client, url):
    return client.get(url).content.decode()


@override_settings(GA_MEASUREMENT_ID="", STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class AnalyticsOffTests(TestCase):
    def test_nothing_is_rendered_when_the_id_is_empty(self):
        html = page(self.client, reverse("login"))
        for marker in ANALYTICS_MARKERS:
            self.assertNotIn(marker, html)

    @override_settings(GA_MEASUREMENT_ID="UA-12345", STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
    def test_an_id_that_is_not_a_ga4_measurement_id_is_ignored(self):
        html = page(self.client, reverse("login"))
        self.assertNotIn("googletagmanager", html)
        self.assertNotIn("consent-banner", html)


@override_settings(GA_MEASUREMENT_ID=GA_ID, STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class AnalyticsOnTests(TestCase):
    def test_the_loader_carries_the_id(self):
        html = page(self.client, reverse("login"))
        tag = LOADER.search(html)
        self.assertIsNotNone(tag)
        self.assertIn(f'data-ga-id="{GA_ID}"', tag.group(0))
        self.assertIn('data-ga-page="/accounts/login/"', tag.group(0))

    def test_the_cookie_notice_is_on_public_pages(self):
        for url in ("/", reverse("login"), reverse("signup")):
            html = page(self.client, url)
            self.assertIn('id="consent-banner"', html, url)
            self.assertIn('data-consent="granted"', html, url)
            self.assertIn('data-consent="denied"', html, url)
            self.assertIn(">Accept<", html, url)
            self.assertIn(">No thanks<", html, url)

    def test_nothing_personal_is_rendered_for_analytics(self):
        user = get_user_model().objects.create_user(
            username="zebra_collector", email="collector@example.com", password="not-a-real-password-123")
        self.client.force_login(user)
        html = page(self.client, "/")
        loader = LOADER.search(html).group(0)
        banner = BANNER.search(html).group(0)
        for part in (loader, banner):
            self.assertNotIn("zebra_collector", part)
            self.assertNotIn("collector@example.com", part)

    def test_the_script_sends_only_the_named_events(self):
        script = STATIC_JS.read_text(encoding="utf-8")
        for name in ("game_start", "game_round_done", "sign_up_started", "sign_up_done"):
            self.assertIn(f"{name}:", script)
        for personal in ("username", "email", "user_id", "beetle", "password"):
            self.assertNotIn(personal, script)

    def test_the_sign_up_pages_declare_their_events(self):
        self.assertIn('data-ga-start="sign_up_started"', page(self.client, reverse("signup")))
        self.assertIn('data-ga-event="sign_up_done"', page(self.client, reverse("request_access_sent")))


class PagePatternTests(TestCase):
    def pattern_for(self, path):
        request = RequestFactory().get(path)
        request.resolver_match = resolve(path)
        return page_pattern(request)

    def test_ids_in_the_path_become_placeholders(self):
        self.assertEqual(self.pattern_for("/game/players/42/"), "/game/players/:id/")
        self.assertEqual(
            self.pattern_for("/game/rounds/3f2c9a1e-5b7d-4c8e-9f10-aabbccddeeff/"),
            "/game/rounds/:id/",
        )

    def test_no_match_is_reported_as_not_found(self):
        request = RequestFactory().get("/no/such/page/")
        self.assertEqual(page_pattern(request), "/not-found")

    @override_settings(GA_MEASUREMENT_ID=GA_ID, STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
    def test_the_query_string_never_reaches_the_page(self):
        html = page(self.client, reverse("login") + "?next=/beetles/search/?q=Secret+Name")
        loader = LOADER.search(html).group(0)
        self.assertNotIn("Secret", loader)
        self.assertNotIn("?", re.search(r'data-ga-page="([^"]*)"', loader).group(1))
