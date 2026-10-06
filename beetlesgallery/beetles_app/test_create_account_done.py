"""
A superuser who makes an account on Create User (#567) ends on the confirmation only: "Username <name> created
successfully." and a "Create another account" button, no username or password boxes. It is reached by a redirect
(post, redirect, get), so refreshing never makes the account twice, and the page is never cached, so Back fetches
a blank form. Mistakes still show on the form. The Username box has a visible border like the password boxes.
"""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app.forms import TAILWIND_INPUT, TailwindUserCreationForm
from beetlesgallery.beetles_app.testing import PageBehaviourCase

PASSWORD = "Correct-Horse-9-Staple"
FIELDS = ('name="username"', 'name="password1"', 'name="password2"', 'type="password"', 'data-testid="create-account-form"')
TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "accounts"


def content_of(response):
    """What the page shows: after the site's sidebar (the sign-out form lives there), before the page's script."""
    html = response.content.decode()
    shown = html[html.index("Create User</h2>"):]
    return shown[:shown.index("<script")]


class CreateAccountDoneTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.User = get_user_model()
        self.client.force_login(self.superuser)

    def create(self, username="newbie", password2=PASSWORD, **extra):
        data = {"username": username, "password1": PASSWORD, "password2": password2, **extra}
        return self.client.post(reverse("create_account"), data)

    def test_a_good_create_redirects_to_the_confirmation_naming_the_user(self):
        response = self.create()
        self.assertRedirects(response, reverse("create_account") + "?created=newbie")
        self.assertTrue(self.User.objects.filter(username="newbie").exists())

    def test_the_confirmation_shows_only_the_message_and_create_another(self):
        page = self.client.get(self.create().url)
        main = content_of(page)
        self.assertIn("Username <strong>newbie</strong> created successfully.", main)
        for field in FIELDS:
            self.assertNotIn(field, main)
        self.assertIn(f'<a href="{reverse("create_account")}" class="btn-secondary" data-testid="create-account-again">', main)
        self.assertIn("Create another account", main)
        self.assertEqual(list(get_messages(page.wsgi_request)), [])  # the page says it; no second banner on top

    def test_refreshing_the_confirmation_makes_nothing(self):
        url = self.create().url
        before = self.User.objects.count()
        for _ in range(2):
            self.assertContains(self.client.get(url), "created successfully")
        self.assertEqual(self.User.objects.count(), before)

    def test_create_another_opens_an_empty_form(self):
        self.client.get(self.create().url)
        main = content_of(self.client.get(reverse("create_account")))
        for field in FIELDS:
            self.assertIn(field, main)
        self.assertNotIn("newbie", main)
        self.assertNotIn("created successfully", main)

    def test_an_unknown_name_in_the_address_shows_the_form(self):
        main = content_of(self.client.get(reverse("create_account") + "?created=nobody"))
        self.assertIn('data-testid="create-account-form"', main)
        self.assertNotIn("created successfully", main)

    def test_the_name_is_escaped(self):
        self.User.objects.create_user("<b>x</b>", password="pw")
        page = self.client.get(reverse("create_account") + "?created=%3Cb%3Ex%3C%2Fb%3E")
        self.assertContains(page, "&lt;b&gt;x&lt;/b&gt;")
        self.assertNotContains(page, "<strong><b>x</b></strong>")

    def test_mistakes_stay_on_the_form_with_the_error(self):
        self.User.objects.create_user("taken", password="pw")
        for response in (self.create("taken"), self.create("fresh", password2="something-else-9")):
            self.assertEqual(response.status_code, 200)
            main = content_of(response)
            self.assertIn('data-testid="create-account-form"', main)
            self.assertNotIn("created successfully", main)
        self.assertFalse(self.User.objects.filter(username="fresh").exists())

    def test_the_form_and_the_confirmation_are_never_cached(self):
        for response in (self.client.get(reverse("create_account")), self.client.get(self.create().url)):
            self.assertIn("no-store", response["Cache-Control"])

    def test_going_back_to_the_form_clears_what_the_browser_refilled(self):
        script = (TEMPLATES / "signup.html").read_text(encoding="utf-8")
        self.assertIn("addEventListener('pageshow'", script)
        self.assertIn("nav.type === 'back_forward'", script)
        self.assertIn("form.reset()", script)


class OnlySuperusersCreateAccountsTests(PageBehaviourCase):
    def test_others_are_sent_away_and_make_nothing(self):
        User = get_user_model()
        for user in (None, self.user, self.staff):
            self.client.logout()
            if user:
                self.client.force_login(user)
            before = User.objects.count()
            post = self.client.post(reverse("create_account"), {"username": "sneaky", "password1": PASSWORD, "password2": PASSWORD})
            self.assertRedirects(post, reverse("login"), fetch_redirect_response=False)
            self.assertEqual(User.objects.count(), before)
            User.objects.create_user("sneaky2", password="pw")
            get = self.client.get(reverse("create_account") + "?created=sneaky2")
            self.assertRedirects(get, reverse("login"), fetch_redirect_response=False)
            User.objects.filter(username="sneaky2").delete()


class MyAccountCreateUserTests(PageBehaviourCase):
    """The Create New User window on My Account already redirects after a success; Back now starts it empty and shut."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.superuser)

    def test_a_good_create_redirects_with_the_window_shut(self):
        data = {"action_create_user": "1", "username": "newbie", "password1": PASSWORD, "password2": PASSWORD}
        page = self.client.post(reverse("my_account"), data, follow=True)
        self.assertEqual(page.redirect_chain, [(reverse("my_account"), 302)])
        self.assertContains(page, "User &#x27;newbie&#x27; created successfully.")
        self.assertEqual(page.context["active_modal"], None)
        self.assertNotContains(page, 'value="newbie"')

    def test_a_mistake_reopens_the_window_with_the_error(self):
        data = {"action_create_user": "1", "username": "newbie", "password1": PASSWORD, "password2": "nope-9-nope"}
        page = self.client.post(reverse("my_account"), data)
        self.assertEqual(page.context["active_modal"], "modal-create-user")
        self.assertFalse(get_user_model().objects.filter(username="newbie").exists())

    def test_my_account_is_never_cached_and_back_empties_the_window(self):
        self.assertIn("no-store", self.client.get(reverse("my_account"))["Cache-Control"])
        script = (TEMPLATES / "my_account.html").read_text(encoding="utf-8")
        self.assertIn('id="create-user-form"', script)
        self.assertIn("addEventListener('pageshow'", script)
        self.assertIn("closeModal('modal-create-user')", script)


class UsernameBoxTests(SimpleTestCase):
    def test_every_box_has_a_grey_border_that_stays_visible_while_typing(self):
        classes = TAILWIND_INPUT.split()
        self.assertIn("border-gray-300", classes)
        self.assertIn("focus:border-gray-500", classes)
        for gone in ("border-stroke", "focus:border-primary"):   # no colour / white: the box vanished when focused
            self.assertNotIn(gone, classes)

    def test_username_and_password_boxes_look_the_same(self):
        form = TailwindUserCreationForm()
        looks = {name: form.fields[name].widget.attrs["class"] for name in ("username", "password1", "password2")}
        self.assertEqual(len(set(looks.values())), 1, looks)

    def test_the_classes_are_in_templates_so_tailwind_builds_them(self):
        templates = Path(__file__).resolve().parent.parent / "templates"
        html = "".join(p.read_text(encoding="utf-8") for p in templates.rglob("*.html"))
        for name in ("border-gray-300", "focus:border-gray-500"):
            self.assertIn(name, html)
