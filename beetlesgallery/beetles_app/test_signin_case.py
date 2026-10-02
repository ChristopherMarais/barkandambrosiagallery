"""
Sign-in ignores the case of the username (phones capitalise the first letter), and the sign-in boxes tell
phones not to capitalise or correct the username in the first place.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase

PASSWORD = "a-long-test-password-9"


class SignInCaseTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user("chris", password=PASSWORD)

    def login(self, username, password=PASSWORD):
        return self.client.post(reverse("login"), {"username": username, "password": password})

    def test_any_case_signs_in(self):
        for username in ("chris", "Chris", "CHRIS", " Chris "):
            with self.subTest(username=username):
                self.assertEqual(self.login(username).status_code, 302)
                self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
                self.client.logout()

    def test_wrong_password_still_fails(self):
        self.assertContains(self.login("Chris", "wrong-password-1"), "enter a correct username and password")

    def test_old_accounts_that_clash_by_case_only_sign_in_with_the_exact_spelling(self):
        other = get_user_model().objects.create_user("Chris", password="another-long-password-8")
        self.assertEqual(self.login("Chris", "another-long-password-8").status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), other.pk)
        self.client.logout()
        self.assertEqual(self.login("chris").status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.client.logout()
        self.assertContains(self.login("CHRIS"), "enter a correct username and password")

    def test_the_username_box_is_not_capitalised_or_corrected_on_phones(self):
        page = self.client.get(reverse("login")).content.decode()
        self.assertIn('autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false"', page)
        self.assertIn('autocomplete="current-password"', page)
