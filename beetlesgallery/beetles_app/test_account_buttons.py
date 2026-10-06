"""
The account page and the request-access pages (#501).

* Superusers find Create New User beside Review Access Requests, in the Access Requests section, each with its own
  icon. Nobody else sees either button. The page has no stray closing tags.
* The request-access form and the page after sending say what happens: signed out, the account works once the email
  is confirmed (#535: Sign up says nothing about reviews); signed in, more access is reviewed and given once approved.
  The AI is called IBBI-AI and the game goes by its name. Only people not signed in get the Sign in link at the top of
  the form.
"""
from html.parser import HTMLParser

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import access
from beetlesgallery.beetles_app.areas import KEYS
from beetlesgallery.beetles_app.models import AccessRequest, AreaGrant
from beetlesgallery.beetles_app.testing import PageBehaviourCase

OPENS_CREATE_USER = "openModal('modal-create-user')"
# Elements that never have a closing tag
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Element:
    """One element of a page: its tag, its attributes and what is inside it."""

    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []   # Elements and text, in page order

    def find_all(self, tag=None, **attrs):
        """The elements inside this one with this tag and these attributes (data_testid= means data-testid)."""
        attrs = {name.replace("_", "-"): value for name, value in attrs.items()}
        found = []
        for child in self.children:
            if isinstance(child, Element):
                if (tag is None or child.tag == tag) and all(child.attrs.get(k) == v for k, v in attrs.items()):
                    found.append(child)
                found += child.find_all(tag, **attrs)
        return found

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    @property
    def words(self):
        """The text inside, with the spacing collapsed."""
        parts = [child if isinstance(child, str) else child.words for child in self.children]
        return " ".join(" ".join(parts).split())


class MainContent(HTMLParser):
    """The <main> part of a page as a tree of Elements, plus every closing tag that closed nothing."""

    def __init__(self, html):
        super().__init__()
        self.root = self.open = Element("(page)", {})
        self.stray = []
        self.feed(html[html.index("<main"):html.rindex("</main>") + len("</main>")])
        self.close()

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs, self.open)
        self.open.children.append(element)
        if tag not in VOID:
            self.open = element

    def handle_startendtag(self, tag, attrs):
        self.open.children.append(Element(tag, attrs, self.open))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag == self.open.tag and self.open is not self.root:
            self.open = self.open.parent
        else:
            self.stray.append(f"</{tag}> on line {self.getpos()[0]} of <main>, inside <{self.open.tag}>")

    def handle_data(self, data):
        self.open.children.append(data)


class ReadsPages:
    """For the test cases below: open a page and pick elements out of it."""

    def one(self, within, tag=None, **attrs):
        """The only element inside ``within`` that matches (fails the test if there are none or several)."""
        found = within.find_all(tag, **attrs)
        self.assertEqual(len(found), 1, f"expected one <{tag or '*'} {attrs}>, found {len(found)}")
        return found[0]

    def main(self, name):
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, 200)
        return MainContent(response.content.decode())


class AccountPageTests(ReadsPages, PageBehaviourCase):
    def page(self, user):
        self.client.force_login(user)
        return self.main("my_account")

    def buttons(self, page):
        """(Review Access Requests, Create New User), both from the Access Requests section."""
        section = self.one(page.root, data_testid="access-requests")
        return self.one(section, "a", href=reverse("access_requests")), self.one(section, "button", onclick=OPENS_CREATE_USER)

    def test_superusers_find_create_user_beside_review_access_requests(self):
        page = self.page(self.superuser)
        review, create = self.buttons(page)
        self.assertEqual(review.words, "Review Access Requests")
        self.assertEqual(create.words, "Create New User")
        self.assertIs(review.parent, create.parent)                       # side by side, in one row
        self.assertEqual(len(page.root.find_all(onclick=OPENS_CREATE_USER)), 1)   # and nowhere else on the page

    def test_the_two_buttons_have_the_same_look_and_their_own_icons(self):
        review, create = self.buttons(self.page(self.superuser))
        self.assertNotEqual(self.one(review, "i").classes, self.one(create, "i").classes)
        for button in (review, create):
            self.assertIn("btn-secondary", button.classes)

    def test_the_waiting_count_stays_on_the_review_button(self):
        AccessRequest.objects.create(name="Ada", email="ada@example.org", email_verified_at=timezone.now())
        review, _ = self.buttons(self.page(self.superuser))
        self.assertEqual(self.one(review, data_testid="pending-access-count").words, "1 waiting")

    def test_nobody_else_sees_either_button_whatever_they_were_granted(self):
        for area in KEYS:
            AreaGrant.objects.get_or_create(user=self.staff, area=area)
        for user in (self.user, self.staff):
            with self.subTest(user=user.username):
                page = self.page(user)
                self.assertEqual(page.root.find_all(data_testid="access-requests"), [])
                self.assertEqual(page.root.find_all(onclick=OPENS_CREATE_USER), [])
                self.assertEqual(page.root.find_all(href=reverse("access_requests")), [])
                self.assertIn("Change Password", page.root.words)

    def test_every_tag_on_the_page_is_closed_exactly_once(self):
        for user in (self.superuser, self.user):
            with self.subTest(user=user.username):
                page = self.page(user)
                self.assertEqual(page.stray, [])
                self.assertIs(page.open, page.root, f"<{page.open.tag}> is never closed")


class RequestAccessExplainsTheReviewTests(ReadsPages, PageBehaviourCase):
    PAGES = ("request_access", "request_access_sent")

    def each_page(self, names=PAGES):
        """(label, html) for these pages (the form and the page after sending), signed out and then signed in."""
        for user in (None, self.user):
            if user:
                self.client.force_login(user)
            for name in names:
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                yield f"{name}, {'signed in' if user else 'signed out'}", response.content.decode()

    def test_signed_in_they_say_more_access_is_reviewed_and_given_once_approved(self):
        self.client.force_login(self.user)
        for name in self.PAGES:
            with self.subTest(page=name):
                words = self.main(name).root.words
                self.assertRegex(words, r"\b[Ww]e review\b")
                self.assertIn("once it is approved", words)
                self.assertIn("email you our decision", words)

    def test_signed_out_they_say_the_account_works_once_the_email_is_confirmed(self):
        for name in self.PAGES:
            with self.subTest(page=name):
                self.assertIn("your account works straight away", self.main(name).root.words)

    def test_they_never_say_you_cannot_sign_in_or_use_the_old_names(self):
        for label, html in self.each_page():
            with self.subTest(page=label):
                html = " ".join(html.split())
                for old in ("Until then you cannot sign in", "nothing is approved until", "Beetle ID game", "AI classifier"):
                    self.assertNotIn(old, html)

    def test_only_people_not_signed_in_get_the_sign_in_link(self):
        self.assertEqual(self.one(self.main("request_access").root, "a", href=reverse("login")).words, "← Sign in")
        self.client.force_login(self.user)
        self.assertEqual(self.main("request_access").root.find_all("a", href=reverse("login")), [])

    def test_the_form_names_ibbi_ai_and_the_game(self):
        for label, html in self.each_page(["request_access"]):
            with self.subTest(page=label):
                basic = self.one(MainContent(html).root, data_testid="basic-summary").words
                self.assertIn("IBBI-AI", basic)
                self.assertIn(settings.GAME_DISPLAY_NAME, basic)

    def test_the_review_page_and_the_emails_call_the_ai_ibbi_ai(self):
        self.client.force_login(self.superuser)
        intro = self.main("access_requests").root.words
        self.assertIn(f"IBBI-AI and {settings.GAME_DISPLAY_NAME}", intro)
        self.assertNotIn("AI classifier", intro)
        self.assertIn("IBBI-AI", access.BASIC_SUMMARY)                 # the welcome and decision emails
        self.assertIn(settings.GAME_DISPLAY_NAME, access.BASIC_SUMMARY)
        self.assertNotIn("classifier", access.BASIC_SUMMARY)
        self.assertEqual(access.AREA_LABELS["classify"], "IBBI-AI")    # what older requests asked for
