"""
A sign-up that goes through ends on its own page (#560): only the "check your email" message, no form and no
username or password fields, with Sign in and Sign up again. It is reached by a redirect (post, redirect, get), so
refreshing it never sends the sign-up twice, and the form page itself never shows a success message.
"""
import re

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import access
from beetlesgallery.beetles_app.models import AccessRequest
from beetlesgallery.beetles_app.test_access import APPROVERS, form_data
from beetlesgallery.beetles_app.testing import PageBehaviourCase

FIELDS = ("<form", 'name="username"', 'name="password1"', 'name="password2"', 'type="password"')


def main_of(response):
    html = response.content.decode()
    return html[html.index("<main"):] if "<main" in html else html


@override_settings(ACCESS_REQUEST_RECIPIENTS=APPROVERS)
class SignUpDoneTests(PageBehaviourCase):
    def sign_up(self, **changes):
        return self.client.post(reverse("signup"), form_data(**changes))

    def assert_no_fields(self, response):
        main = main_of(response)
        for field in FIELDS:
            self.assertNotIn(field, main)

    def test_a_good_sign_up_redirects_to_the_done_page(self):
        response = self.sign_up()
        self.assertRedirects(response, reverse("request_access_sent"))
        self.assertEqual(AccessRequest.objects.count(), 1)
        self.assertEqual([m.to for m in mail.outbox], [["ada@example.org"]])

    def test_the_done_page_shows_only_the_message_with_sign_in_and_sign_up_again(self):
        self.sign_up()
        page = self.client.get(reverse("request_access_sent"))
        self.assertContains(page, '<h1 class="page-title">Check your email</h1>')
        self.assert_no_fields(page)
        main = main_of(page)
        sign_in = re.search(r'<a href="([^"]+)" class="btn-main" data-testid="signup-done-signin">', main)
        again = re.search(r'<a href="([^"]+)" class="btn-secondary" data-testid="signup-done-again">', main)
        self.assertEqual(sign_in.group(1), reverse("login"))
        self.assertEqual(again.group(1), reverse("signup"))
        self.assertEqual(main.count("btn-main"), 1)   # one main action per page

    def test_following_the_redirect_lands_on_the_message_not_the_form(self):
        page = self.client.post(reverse("signup"), form_data(), follow=True)
        self.assertEqual(page.redirect_chain, [(reverse("request_access_sent"), 302)])
        self.assertContains(page, "Check your email")
        self.assert_no_fields(page)

    def test_refreshing_the_done_page_does_not_sign_up_again(self):
        self.sign_up()
        for _ in range(3):
            self.assertEqual(self.client.get(reverse("request_access_sent")).status_code, 200)
        self.assertEqual(AccessRequest.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_sign_up_again_gives_a_blank_form_without_any_success_message(self):
        self.sign_up()
        page = self.client.get(reverse("signup"))
        self.assertContains(page, '<h1 class="page-title">Sign up</h1>')
        self.assertContains(page, 'name="password1"')
        for leftover in ('value="ada"', "Ada Lovelace", "Check your email", "created successfully", "We sent a link"):
            self.assertNotContains(page, leftover)

    def test_the_form_is_not_kept_by_the_browser_so_back_shows_it_blank(self):
        cache_control = self.client.get(reverse("signup"))["Cache-Control"]
        self.assertIn("no-store", cache_control)

    def test_coming_back_to_the_form_clears_what_the_browser_refilled(self):
        page = self.client.get(reverse("signup")).content.decode()
        self.assertIn('data-testid="signup-form"', page)
        script = re.search(r"addEventListener\('pageshow'.*?\}\);", page, re.S).group(0)
        self.assertIn("back_forward", script)
        self.assertIn("event.persisted", script)
        self.assertIn('querySelector(\'[data-testid="signup-form"]\').reset()', script)

    def test_a_form_with_mistakes_stays_on_the_form(self):
        page = self.sign_up(password2="something-else")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="username"')
        self.assertNotContains(page, "Check your email")
        self.assertEqual(AccessRequest.objects.count(), 0)

    def test_throttling_still_keeps_people_on_the_form(self):
        for n in range(access.THROTTLE_PER_IP):
            self.sign_up(username=f"person{n}", email=f"person{n}@example.org")
        page = self.sign_up(username="one-more", email="more@example.org")
        self.assertContains(page, "too many requests")
        self.assertContains(page, 'name="username"')

    def test_signed_in_ask_for_more_access_ends_on_request_sent_without_sign_up_links(self):
        self.user.email = "user@example.org"
        self.user.save()
        self.client.force_login(self.user)
        data = {k: v for k, v in form_data(email="user@example.org", areas=["annotate"]).items()
                if k not in ("username", "password1", "password2")}
        page = self.client.post(reverse("request_access"), data, follow=True)
        self.assertEqual(page.redirect_chain, [(reverse("request_access_sent"), 302)])
        self.assertContains(page, '<h1 class="page-title">Request sent</h1>')
        self.assert_no_fields(page)
        self.assertNotContains(page, 'data-testid="signup-done-again"')
        self.assertEqual(sorted(mail.outbox[0].to), sorted(APPROVERS))
