"""
Issue #393: after the account reset, the sign-in page says so for a few weeks, and "Request access" is easy to find
on the sign-in and home pages.
"""
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.test_pages import PageTestCase


class LoginNoticeTests(PageTestCase):
    @override_settings(ACCOUNT_RESET_NOTICE_UNTIL="2999-01-01")
    def test_the_notice_shows_until_the_date(self):
        res = self.client.get(reverse("login"))
        self.assertContains(res, 'data-testid="reset-notice"')
        self.assertContains(res, "Had an account before? Please request access again.")

    @override_settings(ACCOUNT_RESET_NOTICE_UNTIL="2000-01-01")
    def test_it_goes_after_the_date(self):
        self.assertNotContains(self.client.get(reverse("login")), 'data-testid="reset-notice"')

    @override_settings(ACCOUNT_RESET_NOTICE_UNTIL="")
    def test_an_empty_setting_turns_it_off(self):
        self.assertNotContains(self.client.get(reverse("login")), 'data-testid="reset-notice"')

    @override_settings(ACCOUNT_RESET_NOTICE_UNTIL="not a date")
    def test_a_bad_date_does_not_break_the_page(self):
        self.assertNotContains(self.client.get(reverse("login")), 'data-testid="reset-notice"')

    def test_request_access_is_a_button_on_sign_in(self):
        res = self.client.get(reverse("login"))
        self.assertContains(res, 'data-testid="request-access"')
        self.assertContains(res, reverse("request_access"))

    def test_the_home_page_leaves_it_to_the_sign_in_page(self):
        # #418: sign-in appears whenever someone opens a members-only page, so the home page doesn't repeat it
        self.assertNotContains(self.client.get(reverse("image_browser")), 'data-testid="landing-request-access"')

    def test_a_wrong_password_still_says_nothing_about_the_account(self):
        res = self.client.post(reverse("login"), {"username": "nobody-here", "password": "x"})
        self.assertNotContains(res, "No account")
