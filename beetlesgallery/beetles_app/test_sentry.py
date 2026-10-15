"""
Sentry error reports (beetlesgallery/sentry_setup.py): off without SENTRY_DSN, and when on, no personal data, the
web app and the Celery worker both reported, tagged with the release and environment. The privacy notice mentions it
only while it is on.
"""
from pathlib import Path

import sentry_sdk
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from beetlesgallery import sentry_setup
from beetlesgallery.beetles_app.test_pages import NO_WHITENOISE, PLAIN_STATIC

ROOT = Path(__file__).resolve().parents[2]


class SentrySetupTests(SimpleTestCase):
    def tearDown(self):
        sentry_sdk.init()   # back to no client, so no other test can send anything

    def test_nothing_starts_without_a_dsn(self):
        self.assertFalse(sentry_setup.init(""))
        self.assertFalse(sentry_setup.init(None))
        self.assertFalse(sentry_sdk.get_client().is_active())

    def test_with_a_dsn_it_reports_errors_without_personal_data(self):
        self.assertTrue(sentry_setup.init("https://public@sentry.example.invalid/1", "staging", "v9.9.9"))
        client = sentry_sdk.get_client()
        self.assertTrue(client.is_active())
        options = client.options
        self.assertFalse(options["send_default_pii"])
        self.assertEqual((options["environment"], options["release"]), ("staging", "v9.9.9"))
        self.assertEqual(options["traces_sample_rate"], 0.0)   # errors only unless the server asks for tracing
        self.assertTrue({"django", "celery"} <= set(client.integrations))

    def test_settings_start_it_only_from_the_environment_and_never_in_tests(self):
        settings_py = (ROOT / "beetlesgallery" / "settings.py").read_text(encoding="utf-8")
        self.assertIn('SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()', settings_py)
        self.assertIn('if SENTRY_DSN and "test" not in sys.argv:', settings_py)

    def test_the_package_is_in_the_pixi_environment(self):
        self.assertIn('sentry-sdk = ">=2,<3"', (ROOT / "pixi.toml").read_text(encoding="utf-8"))


@override_settings(STORAGES=PLAIN_STATIC, MIDDLEWARE=NO_WHITENOISE)
class PrivacyNoticeTests(TestCase):
    def test_no_error_report_section_while_sentry_is_off(self):
        with override_settings(SENTRY_DSN=""):
            page = self.client.get(reverse("privacy")).content.decode()
        self.assertNotIn('data-testid="privacy-error-reports"', page)

    def test_the_section_says_what_a_report_holds_while_sentry_is_on(self):
        with override_settings(SENTRY_DSN="https://public@sentry.example.invalid/1"):
            page = self.client.get(reverse("privacy")).content.decode()
        self.assertIn('data-testid="privacy-error-reports"', page)
        self.assertIn("no IP address", page)
