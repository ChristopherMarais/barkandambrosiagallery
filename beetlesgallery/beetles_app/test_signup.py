"""
Request access becomes a friendly Sign up (#535).

* /accounts/signup/ is the one sign-up page; the old /accounts/request-access/ links (and its "sent" page) still work.
* Signed out, the page, the page after sending and the confirmation email say "Sign up" and never mention a review:
  the extra-access ticks are offered quietly ("I'd also like"), and the decision email tells people the outcome.
* Signed in, the same page is "Ask for more access".
* The superuser's own "make an account for someone" page moved to /accounts/create-account/.
"""
import re

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import access
from beetlesgallery.beetles_app.models import AccessRequest
from beetlesgallery.beetles_app.test_access import APPROVERS, form_data
from beetlesgallery.beetles_app.testing import PageBehaviourCase

REVIEW_WORDS = re.compile(r"\breview|\bapprov|\bcurator|decision", re.IGNORECASE)


def words(response):
    return words_of(response.content.decode())


def words_of(html):
    """The visible text of the page's main content, on one line."""
    main = html[html.index("<main"):] if "<main" in html else html
    main = re.sub(r"<(script|style)\b.*?</\1>", " ", main, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", main).split())


@override_settings(ACCESS_REQUEST_RECIPIENTS=APPROVERS)
class SignUpTests(PageBehaviourCase):
    def test_the_page_lives_at_signup_and_the_old_links_still_work(self):
        self.assertEqual(reverse("signup"), "/accounts/signup/")
        for url in ("/accounts/signup/", "/accounts/request-access/", "/accounts/signup/sent/",
                    "/accounts/request-access/sent/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_signed_out_it_says_sign_up_and_nothing_about_a_review(self):
        page = self.client.get(reverse("signup"))
        self.assertContains(page, '<h1 class="page-title">Sign up</h1>')
        self.assertNotContains(page, "Request access")
        # what the page says, leaving out the ticks' own descriptions (one is "Review proposed interactions")
        text = re.sub(r"<fieldset\b.*?</fieldset>", " ", page.content.decode(), flags=re.S)
        self.assertIsNone(REVIEW_WORDS.search(words_of(text)), words_of(text))

    def test_the_extra_access_ticks_are_still_there_quietly(self):
        page = self.client.get(reverse("signup"))
        self.assertContains(page, "I'd also like")
        self.assertContains(page, 'type="checkbox" name="areas"', count=len(access.AREAS))

    def test_the_main_button_says_sign_up(self):
        page = self.client.get(reverse("signup")).content.decode()
        button = re.search(r'<button[^>]*data-testid="signup-submit"[^>]*>(.*?)</button>', page, re.S)
        self.assertIn('class="btn-main"', button.group(0))
        self.assertEqual(" ".join(re.sub(r"<[^>]+>", " ", button.group(1)).split()), "Sign up")

    def test_signing_up_still_makes_the_request_and_sends_a_friendly_email(self):
        response = self.client.post(reverse("signup"), form_data(areas=["upload"]))
        self.assertRedirects(response, reverse("request_access_sent"))
        saved = AccessRequest.objects.get()
        self.assertEqual((saved.user.username, saved.areas), ("ada", ["upload"]))
        email = mail.outbox[-1]
        self.assertIn("To finish signing up", email.body)
        self.assertIsNone(REVIEW_WORDS.search(email.body), email.body)
        sent = words(self.client.get(reverse("request_access_sent")))
        self.assertIn("Check your email", sent)
        self.assertIsNone(REVIEW_WORDS.search(sent), sent)

    def test_the_sign_in_page_offers_sign_up(self):
        page = self.client.get(reverse("login"))
        self.assertContains(page, f'href="{reverse("signup")}"')
        self.assertContains(page, ">Sign up</a>")
        self.assertNotContains(page, "Request access")
        self.assertNotContains(page, "we approve you")

    def test_signed_in_it_is_ask_for_more_access(self):
        self.client.force_login(self.user)
        for name in ("signup", "request_access"):
            with self.subTest(page=name):
                page = self.client.get(reverse(name))
                self.assertContains(page, '<h1 class="page-title">Ask for more access</h1>')
                self.assertNotContains(page, 'name="password1"')
                self.assertContains(page, "Send request")

    def test_the_superusers_create_account_page_moved(self):
        self.client.force_login(self.superuser)
        self.assertEqual(reverse("create_account"), "/accounts/create-account/")
        self.assertContains(self.client.get(reverse("create_account")), "Create User")
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse("create_account")), reverse("login"), fetch_redirect_response=False)
