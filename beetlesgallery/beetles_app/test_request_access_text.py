"""
The request-access pages say what really happens (#393, #385): confirming the email opens the account at once; only
the extra areas asked for are reviewed by a curator.
"""
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase


class RequestAccessTextTests(PageBehaviourCase):
    def test_the_form_says_the_account_works_once_the_email_is_confirmed(self):
        page = self.client.get(reverse("request_access")).content.decode()
        self.assertIn("Confirm your email and your account works straight away.", page)
        self.assertNotIn("nothing is approved until", page)
        self.assertNotIn("Accounts are given by approval", page)

    def test_the_sent_page_does_not_say_you_cannot_sign_in(self):
        page = render_to_string("accounts/request_access_sent.html", {"user": AnonymousUser()})
        self.assertIn("your account works straight away", page)
        self.assertNotIn("Until then you cannot sign in", page)
