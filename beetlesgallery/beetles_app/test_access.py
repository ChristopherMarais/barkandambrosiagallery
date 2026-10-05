"""
Asking for an account: choosing a username and password, confirming the email (which opens a Basic account at once),
a superuser granting extra areas one by one, signing in and password reset.
"""
import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import access
from beetlesgallery.beetles_app.models import AccessRequest
from beetlesgallery.beetles_app.testing import PageBehaviourCase

User = get_user_model()
APPROVERS = ["gmarais@example.org", "hulcr@example.org"]
STRONG = "Correct-Horse-9-Staple"
LINK = r"http://testserver/accounts/[a-z-]+/\S+"


def form_data(**changes):
    data = {
        "name": "Ada Lovelace", "email": "Ada@Example.org", "affiliation": "University of Somewhere",
        "areas": ["details", "download"], "reason": "I study ambrosia beetles.", "leave_blank": "",
        "username": "ada", "password1": STRONG, "password2": STRONG,
    }
    data.update(changes)
    return data


@override_settings(ACCESS_REQUEST_RECIPIENTS=APPROVERS)
class AccessCase(PageBehaviourCase):
    def send(self, **changes):
        return self.client.post(reverse("request_access"), form_data(**changes))

    def confirm(self):
        """Open the confirmation link from the last email to the applicant."""
        link = re.search(LINK, mail.outbox[-1].body).group(0)
        self.client.logout()
        return self.client.get(link)

    def apply_and_confirm(self, **changes):
        self.send(**changes)
        self.confirm()
        return AccessRequest.objects.get(user__username=changes.get("username", "ada"))

    def decide(self, access_request, decision, note="", user=None, areas=()):
        self.client.force_login(user or self.superuser)
        return self.client.post(reverse("access_requests"), {
            "request_id": access_request.id, "decision": decision, "note": note, "areas": list(areas),
        }, follow=True)

    def to(self, address):
        """The newest email to this address (or list of addresses)."""
        wanted = sorted(address) if isinstance(address, list) else [address]
        return [m for m in mail.outbox if sorted(m.to) == wanted][-1]


class RequestFormTests(AccessCase):
    def test_form_is_public_and_asks_for_a_username_and_password(self):
        response = self.client.get(reverse("request_access"))
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password1"')
        for _, label, _ in access.AREAS:
            self.assertContains(response, label)
        self.assertContains(response, 'data-testid="basic-summary"')

    def test_submitting_makes_an_inactive_account_and_only_emails_the_applicant(self):
        response = self.send()
        self.assertRedirects(response, reverse("request_access_sent"))
        user = User.objects.get(username="ada")
        self.assertEqual((user.is_active, user.is_staff, user.email), (False, False, "ada@example.org"))
        self.assertTrue(user.check_password(STRONG))
        saved = AccessRequest.objects.get()
        self.assertEqual((saved.user, saved.status, saved.email_verified_at), (user, "pending", None))
        [message] = mail.outbox
        self.assertEqual(message.to, ["ada@example.org"])
        self.assertIn("confirm", message.subject.lower())
        self.assertNotIn(STRONG, message.body)

    def test_the_form_reports_what_is_wrong_and_creates_nothing(self):
        User.objects.create_user("taken", email="other@example.org", password="pw")
        User.objects.create_user("owner", email="owner@example.org", password="pw")
        for changes, message in [
            ({"name": ""}, "This field is required"),
            ({"email": "not-an-email"}, "valid email"),
            ({"username": ""}, "Choose a username"),
            ({"username": "TAKEN"}, "taken"),
            ({"username": "bad name!"}, "valid username"),
            ({"password1": STRONG, "password2": "different"}, "do not match"),
            ({"password1": "12345678", "password2": "12345678"}, "entirely numeric"),
            ({"password1": "ada", "password2": "ada"}, "too short"),
            ({"email": "OWNER@example.org"}, "already exists"),
        ]:
            with self.subTest(changes=changes):
                self.assertContains(self.send(**changes), message)
        self.assertFalse(AccessRequest.objects.exists())
        self.assertFalse(User.objects.filter(username="ada").exists())
        self.assertEqual(mail.outbox, [])

    def test_a_name_cannot_break_the_email_subject(self):
        self.send(name="Ada\r\nBcc: someone@example.org")
        self.assertEqual(mail.outbox[0].bcc, [])
        self.assertNotIn("\n", mail.outbox[0].subject)

    def test_the_hidden_field_catches_bots(self):
        self.assertRedirects(self.send(leave_blank="http://spam.example"), reverse("request_access_sent"))
        self.assertFalse(User.objects.filter(username="ada").exists())

    def test_asking_again_with_the_same_email_makes_no_second_account_and_resends_the_link(self):
        self.send()
        response = self.send(username="ada2", email="ADA@example.org")
        self.assertRedirects(response, reverse("request_access_sent"))
        self.assertEqual((AccessRequest.objects.count(), User.objects.filter(username="ada2").count()), (1, 0))
        self.assertEqual([m.to for m in mail.outbox], [["ada@example.org"], ["ada@example.org"]])

    def test_too_many_requests_from_one_address_are_turned_away(self):
        for n in range(access.THROTTLE_PER_IP):
            self.send(username=f"person{n}", email=f"person{n}@example.org")
        self.assertContains(self.send(username="one-more", email="more@example.org"), "too many requests")

    def test_the_request_is_kept_when_the_confirmation_email_cannot_be_sent(self):
        with mock.patch("beetlesgallery.beetles_app.access.EmailMultiAlternatives.send", side_effect=OSError("no route")):
            self.assertRedirects(self.send(), reverse("request_access_sent"))
        self.assertIn("no route", AccessRequest.objects.get().notify_error)

    def test_someone_who_already_has_an_account_asks_without_a_new_one(self):
        self.user.email = "user@example.org"
        self.user.save()
        self.client.force_login(self.user)
        page = self.client.get(reverse("request_access"))
        self.assertContains(page, "user@example.org")
        self.assertNotContains(page, 'name="password1"')
        self.client.post(reverse("request_access"), {k: v for k, v in form_data(email="user@example.org", areas=["annotate"]).items()
                                                      if k not in ("username", "password1", "password2")})
        saved = AccessRequest.objects.get()
        self.assertEqual((saved.user, saved.email_verified_at is not None), (self.user, True))
        self.assertEqual(sorted(mail.outbox[0].to), sorted(APPROVERS))   # already confirmed: the approvers hear at once


    def test_a_resent_link_clears_the_old_email_error(self):
        with mock.patch("beetlesgallery.beetles_app.access.EmailMultiAlternatives.send", side_effect=OSError("no route")):
            self.send()
        self.assertIn("no route", AccessRequest.objects.get().notify_error)
        self.send(username="ada2")
        self.assertEqual(AccessRequest.objects.get().notify_error, "")


class EmailFormatTests(AccessCase):
    def html(self, message):
        [(content, mimetype)] = message.alternatives
        self.assertEqual(mimetype, "text/html")
        return content

    def test_every_email_has_a_plain_and_a_formatted_version(self):
        self.send(name="Ada <b>Lovelace</b>")
        verify = mail.outbox[-1]
        self.assertIn("Hello Ada <b>Lovelace</b>", verify.body)          # plain text is not escaped
        self.assertIn("Ada &lt;b&gt;Lovelace&lt;/b&gt;", self.html(verify))  # the formatted one is
        self.assertIn("Confirm my email", self.html(verify))
        waiting = AccessRequest.objects.get()
        self.confirm()
        self.assertIn("Review the request", self.html(self.to(APPROVERS)))
        welcome = self.to("ada@example.org")
        self.assertEqual(welcome.subject, "Welcome to the Bark & Ambrosia Beetle Gallery!")
        for text in ("Sign in", "<strong>ada</strong>", "Specimen pages", reverse("password_reset")):
            self.assertIn(text, self.html(welcome))
        self.decide(waiting, "grant", note="Glad to have you", areas=["details"])
        granted = mail.outbox[-1]
        self.assertEqual(granted.subject, "Your Bark & Ambrosia Beetle Gallery access")
        for text in ("Specimen pages", "Glad to have you"):
            self.assertIn(text, self.html(granted))

    def test_the_password_reset_email_is_formatted_too(self):
        self.user.email = "user@example.org"
        self.user.save()
        self.client.post(reverse("password_reset"), {"email": "user@example.org"})
        html = self.html(mail.outbox[-1])
        self.assertIn("Choose a new password", html)
        self.assertRegex(html, r'href="http://testserver/accounts/set-password/[^"]+"')


class ConfirmEmailTests(AccessCase):
    def test_confirming_tells_the_approvers_once(self):
        self.send()
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(LINK, mail.outbox[0].body).group(0)
        response = self.confirm()
        self.assertContains(response, "Email confirmed")
        self.assertContains(response, "Your account is ready")
        self.assertIsNotNone(AccessRequest.objects.get().email_verified_at)
        self.assertTrue(User.objects.get(username="ada").is_active)        # Basic straight away
        approvers_mail = self.to(APPROVERS)
        self.assertEqual(approvers_mail.reply_to, ["ada@example.org"])
        for text in ("Ada Lovelace", "University of Somewhere", "Specimen pages", "I study ambrosia beetles.", "/tools/access-requests/"):
            self.assertIn(text, approvers_mail.body)
        self.assertIn("A curator will review", self.to("ada@example.org").body)
        self.client.get(link)  # opening the link again changes nothing
        self.assertEqual(len(mail.outbox), 3)   # the link, the approvers, the welcome

    def test_asking_for_nothing_more_is_approved_as_basic_without_bothering_anyone(self):
        self.send(areas=[])
        self.confirm()
        done = AccessRequest.objects.get()
        self.assertEqual((done.status, done.granted_role, done.decided_by), ("approved", "basic", None))
        self.assertTrue(User.objects.get(username="ada").is_active)
        self.assertEqual([m.to for m in mail.outbox], [["ada@example.org"], ["ada@example.org"]])   # no approvers
        self.client.force_login(self.superuser)
        self.assertNotContains(self.client.get(reverse("access_requests")), 'name="request_id" value="' + str(done.id))

    def test_a_bad_link_does_nothing(self):
        self.send()
        self.client.logout()
        self.assertContains(self.client.get("/accounts/verify-email/zz/not-a-token/"), "has expired")
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(User.objects.get(username="ada").is_active)

    def test_an_unconfirmed_request_is_not_shown_to_the_approvers(self):
        self.send()
        self.client.force_login(self.superuser)
        self.assertNotContains(self.client.get(reverse("access_requests")), "Ada Lovelace")
        self.assertNotContains(self.client.get(reverse("my_account")), "waiting")

    @override_settings(ACCESS_REQUEST_RECIPIENTS=[])
    def test_with_no_approvers_configured_the_request_is_still_listed(self):
        self.send()
        self.confirm()
        self.assertIn("No approvers", AccessRequest.objects.get().notify_error)
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("access_requests")), "Ada Lovelace")

    def test_every_superuser_with_an_email_is_told_too(self):
        self.superuser.email = "boss@example.org"
        self.superuser.save()
        User.objects.create_superuser("gone", email="gone@example.org", password="pw", is_active=False)
        User.objects.create_superuser("dup", email="HULCR@example.org", password="pw")
        self.send()
        self.confirm()
        self.assertEqual(sorted(mail.outbox[-2].to), sorted(APPROVERS + ["boss@example.org"]))


class ReviewPageTests(AccessCase):
    def test_only_superusers_can_open_it_or_decide(self):
        waiting = self.apply_and_confirm()
        url = reverse("access_requests")
        self.assertRedirectsToLogin(self.client.get(url))
        for account in (self.user, self.staff):
            self.client.force_login(account)
            self.assertRedirectsToLogin(self.client.get(url))
            self.assertRedirectsToLogin(self.client.post(url, {"request_id": waiting.id, "decision": "grant", "areas": ["upload"]}))
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "pending")
        self.assertFalse(User.objects.get(username="ada").area_grants.exists())

    def test_it_shows_the_request_the_account_they_chose_and_what_they_want(self):
        self.apply_and_confirm()
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("access_requests"))
        for text in ("Ada Lovelace", "<strong>ada</strong>", "Specimen pages", "(asked)", "I study ambrosia beetles.",
                     'value="details" class="mt-0.5 rounded border-gray-300" checked', "working as Basic"):
            self.assertContains(page, text)
        self.assertContains(page, 'value="upload" class="mt-0.5 rounded border-gray-300" >')   # not asked for: not ticked
        self.assertNotContains(page, "already has an account")   # their own new account is not a duplicate
        self.assertContains(self.client.get(reverse("my_account")), "1 waiting")
        self.assertNotContains(self.client.get(reverse("data_management")), "Review Access Requests")


class ApprovalTests(AccessCase):
    def test_granting_adds_the_ticked_areas_and_tells_them(self):
        waiting = self.apply_and_confirm()
        self.decide(waiting, "grant", note="Welcome aboard", areas=["details", "upload"])
        waiting.refresh_from_db()
        user = User.objects.get(username="ada")
        self.assertEqual((waiting.status, waiting.granted_role, waiting.granted_areas, waiting.decided_by),
                         ("approved", "areas", ["details", "upload"], self.superuser))
        self.assertEqual(set(user.area_grants.values_list("area", flat=True)), {"details", "upload"})
        self.assertEqual((user.is_active, user.is_staff, user.is_superuser), (True, False, False))
        message = mail.outbox[-1]
        self.assertEqual(message.to, ["ada@example.org"])
        for text in ("Specimen pages, Upload new images", "Welcome aboard", reverse("password_reset")):
            self.assertIn(text, message.body)
        self.assertNotIn(STRONG, message.body)
        self.client.logout()
        self.assertTrue(self.client.login(username="ada", password=STRONG))

    def test_granting_nothing_leaves_them_basic(self):
        waiting = self.apply_and_confirm()
        self.decide(waiting, "grant")
        waiting.refresh_from_db()
        self.assertEqual((waiting.status, waiting.granted_role), ("approved", "basic"))
        self.assertFalse(User.objects.get(username="ada").area_grants.exists())

    def test_a_request_never_makes_staff_or_a_superuser_and_never_takes_anything_away(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("request_access"), {"name": "S", "email": "s@example.org", "affiliation": "Y",
                                                     "areas": ["species_tables"], "reason": "Z", "leave_blank": ""})
        before = set(self.staff.area_grants.values_list("area", flat=True))
        self.decide(AccessRequest.objects.get(user=self.staff), "grant", areas=["species_tables"])
        self.staff.refresh_from_db()
        self.assertEqual(set(self.staff.area_grants.values_list("area", flat=True)), before | {"species_tables"})
        self.assertFalse(self.staff.is_superuser)

    def test_an_unconfirmed_request_cannot_be_approved(self):
        self.send()
        waiting = AccessRequest.objects.get()
        response = self.decide(waiting, "grant", areas=["details"])
        self.assertContains(response, "has not confirmed their email")
        self.assertFalse(User.objects.get(username="ada").is_active)

    def test_a_request_can_only_be_decided_once(self):
        waiting = self.apply_and_confirm()
        self.decide(waiting, "deny")
        response = self.decide(waiting, "grant", areas=["details"])
        self.assertContains(response, "already denied")

    def test_an_unknown_decision_or_request_changes_nothing(self):
        waiting = self.apply_and_confirm()
        self.assertContains(self.decide(waiting, "superuser"), "Choose what to grant, or Deny")
        self.assertContains(self.decide(mock.Mock(id="not-a-uuid"), "grant"), "no longer exists")
        self.decide(waiting, "grant", areas=["superuser", "is_staff"])   # unknown areas are ignored
        waiting.refresh_from_db()
        self.assertEqual(waiting.granted_role, "basic")

    def test_if_the_applicant_cannot_be_emailed_the_approver_is_told(self):
        waiting = self.apply_and_confirm()
        with mock.patch("beetlesgallery.beetles_app.access.EmailMultiAlternatives.send", side_effect=OSError("no route")):
            response = self.decide(waiting, "grant", areas=["details"])
        self.assertContains(response, "could not be emailed")
        self.assertContains(response, "username ada")


class DenialTests(AccessCase):
    def test_denying_keeps_their_basic_account_and_tells_them(self):
        waiting = self.apply_and_confirm()
        self.decide(waiting, "deny", note="Please apply with your institutional address.")
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "denied")
        user = User.objects.get(username="ada")
        self.assertTrue(user.is_active)
        self.assertFalse(user.area_grants.exists())
        self.assertIn("institutional address", mail.outbox[-1].body)
        self.assertIn("not able to give you the extra access", mail.outbox[-1].body)

    def test_an_old_unconfirmed_style_request_that_is_denied_frees_the_username(self):
        user = User.objects.create_user("old", email="old@example.org", password=STRONG, is_active=False)
        waiting = AccessRequest.objects.create(name="Old", email="old@example.org", user=user, areas=["details"],
                                               email_verified_at=timezone.now())
        self.decide(waiting, "deny")
        self.assertFalse(User.objects.filter(username="old").exists())
        self.assertIn("not able to give you an account", mail.outbox[-1].body)

    def test_denying_a_signed_in_persons_extra_access_keeps_their_account(self):
        self.client.force_login(self.user)
        self.client.post(reverse("request_access"), {"name": "U", "email": "u@example.org", "affiliation": "Y", "areas": ["upload"],
                                                     "reason": "Z", "leave_blank": ""})
        self.decide(AccessRequest.objects.get(user=self.user), "deny")
        self.assertTrue(User.objects.filter(pk=self.user.pk, is_active=True).exists())

    def test_an_older_request_with_no_account_is_replaced_by_a_new_one(self):
        AccessRequest.objects.create(name="Old", email="ada@example.org")
        self.send()
        self.assertEqual(AccessRequest.objects.get(name="Old").status, "denied")
        self.assertTrue(AccessRequest.objects.filter(user__username="ada", status="pending").exists())


class SignInTests(AccessCase):
    def login(self, username="ada", password=STRONG):
        return self.client.post(reverse("login"), {"username": username, "password": password})

    def test_until_the_email_is_confirmed_they_are_told_why_they_cannot_sign_in(self):
        self.send()
        self.assertContains(self.login(), "confirm your email first")
        self.confirm()
        self.assertEqual(self.login().status_code, 302)   # confirmed: Basic, signed in

    def test_a_wrong_password_or_unknown_user_gets_the_usual_message(self):
        self.apply_and_confirm()
        self.client.logout()
        for username, password in (("ada", "wrong-password-1"), ("nobody", STRONG)):
            with self.subTest(username=username):
                self.assertContains(self.login(username, password), "enter a correct username and password")

    def test_the_sign_in_page_links_to_reset_and_to_the_request_form(self):
        page = self.client.get(reverse("login"))
        self.assertContains(page, reverse("password_reset"))
        self.assertContains(page, reverse("request_access"))


class PasswordResetTests(AccessCase):
    def setUp(self):
        super().setUp()
        self.user.email = "user@example.org"
        self.user.save()

    def ask(self, email):
        return self.client.post(reverse("password_reset"), {"email": email})

    def test_the_form_and_the_sent_page_are_public(self):
        self.assertEqual(self.client.get(reverse("password_reset")).status_code, 200)
        self.assertEqual(self.client.get(reverse("password_reset_done")).status_code, 200)

    def test_an_account_owner_gets_a_link_and_can_choose_a_new_password(self):
        self.assertRedirects(self.ask("USER@example.org"), reverse("password_reset_done"))
        [message] = mail.outbox
        self.assertEqual(message.to, ["user@example.org"])
        link = re.search(r"http://testserver/accounts/set-password/\S+", message.body).group(0)
        form_url = self.client.get(link, follow=True).redirect_chain[-1][0]
        done = self.client.post(form_url, {"new_password1": STRONG, "new_password2": STRONG})
        self.assertRedirects(done, reverse("login"), fetch_redirect_response=False)
        self.assertTrue(self.client.login(username="user", password=STRONG))
        self.assertContains(self.client.get(link, follow=True), "expired or was already used")  # single use

    def test_unknown_addresses_and_accounts_still_waiting_get_the_same_page_and_no_email(self):
        self.send()
        for email in ("nobody@example.org", "ada@example.org"):
            with self.subTest(email=email):
                before = len(mail.outbox)
                self.assertRedirects(self.ask(email), reverse("password_reset_done"))
                self.assertEqual(len(mail.outbox), before)

    def test_a_weak_new_password_is_refused(self):
        self.ask("user@example.org")
        link = re.search(r"http://testserver/accounts/set-password/\S+", mail.outbox[-1].body).group(0)
        form_url = self.client.get(link, follow=True).redirect_chain[-1][0]
        self.assertEqual(self.client.post(form_url, {"new_password1": "12345678", "new_password2": "12345678"}).status_code, 200)
        self.assertFalse(self.client.login(username="user", password="12345678"))

    def test_asking_too_often_sends_nothing_more_and_says_nothing_different(self):
        for _ in range(10):
            self.ask("user@example.org")
        self.assertEqual(len(mail.outbox), 10)
        self.assertRedirects(self.ask("user@example.org"), reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 10)


class ExtrasTests(AccessCase):
    def test_only_areas_beyond_basic_need_a_review(self):
        self.assertEqual(access.extras(["browse", "game", "details", "upload"]), ["details", "upload"])
        self.assertEqual(access.extras([]), [])


class EmailSetupTests(PageBehaviourCase):
    def test_the_test_email_command_reports_and_sends(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("send_test_email", "someone@example.org", stdout=out)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["someone@example.org"])
        self.assertIn("Sent to someone@example.org", out.getvalue())

    def test_the_test_email_command_reports_a_failure(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with mock.patch("beetlesgallery.beetles_app.management.commands.send_test_email.send_mail",
                        side_effect=ConnectionRefusedError("no server")):
            with self.assertRaisesRegex(CommandError, "ConnectionRefusedError: no server"):
                call_command("send_test_email", "someone@example.org", stdout=__import__("io").StringIO())


class ReminderTests(PageBehaviourCase):
    """The daily reminder to the approvers about requests nobody has decided."""

    def request(self, email, hours_ago=30, verified=True, status="pending"):
        r = AccessRequest.objects.create(name=email.split("@")[0].title(), email=email, areas=["browse"], status=status)
        if verified:
            AccessRequest.objects.filter(pk=r.pk).update(email_verified_at=timezone.now() - timedelta(hours=hours_ago))
        return r

    def run_command(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("remind_access_requests", *args, stdout=out)
        return out.getvalue()

    @override_settings(ACCESS_REQUEST_RECIPIENTS=APPROVERS, SITE_URL="https://example.org")
    def test_one_email_lists_everyone_who_has_waited_with_a_link_to_the_page(self):
        self.request("ada@example.org", hours_ago=30)
        self.request("grace@example.org", hours_ago=100)
        out = self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(set(message.to) >= set(APPROVERS), True)
        self.assertEqual(message.subject, "Reminder: 2 access requests waiting")
        self.assertIn("Ada <ada@example.org>", message.body)
        self.assertIn("Grace <grace@example.org>", message.body)
        self.assertIn("waiting 4 days", message.body)
        self.assertIn("https://example.org" + reverse("access_requests"), message.body)
        self.assertIn("Emailed", out)

    @override_settings(ACCESS_REQUEST_RECIPIENTS=APPROVERS)
    def test_nothing_is_sent_when_nobody_has_waited_long_enough_or_nobody_is_waiting(self):
        self.request("new@example.org", hours_ago=2)
        self.assertIn("No access requests are waiting", self.run_command())
        AccessRequest.objects.all().delete()
        self.assertIn("No access requests are waiting", self.run_command())
        self.assertEqual(mail.outbox, [])

    @override_settings(ACCESS_REQUEST_RECIPIENTS=APPROVERS)
    def test_decided_and_unconfirmed_requests_are_left_out(self):
        self.request("done@example.org", status="approved")
        self.request("unconfirmed@example.org", verified=False)
        self.request("waiting@example.org")
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("waiting@example.org", mail.outbox[0].body)
        self.assertNotIn("done@example.org", mail.outbox[0].body)
        self.assertNotIn("unconfirmed@example.org", mail.outbox[0].body)

    @override_settings(ACCESS_REQUEST_RECIPIENTS=APPROVERS)
    def test_the_wait_can_be_changed_and_a_dry_run_sends_nothing(self):
        self.request("ada@example.org", hours_ago=30)
        self.assertIn("No access requests", self.run_command("--older-than-hours", "48"))
        self.assertIn("Would email", self.run_command("--dry-run"))
        self.assertEqual(mail.outbox, [])

    @override_settings(ACCESS_REQUEST_RECIPIENTS=[])
    def test_superusers_with_an_email_are_reminded_too(self):
        User.objects.filter(pk=self.superuser.pk).update(email="boss@example.org")
        self.request("ada@example.org")
        self.run_command()
        self.assertEqual(mail.outbox[0].to, ["boss@example.org"])

    @override_settings(ACCESS_REQUEST_RECIPIENTS=[])
    def test_it_complains_when_nobody_can_be_emailed(self):
        from django.core.management.base import CommandError
        User.objects.all().update(email="")
        self.request("ada@example.org")
        with self.assertRaises(CommandError):
            self.run_command()

    def test_the_server_runs_it_every_day_after_the_deploy_task_exists(self):
        from django.conf import settings
        import tomllib
        workflow = (settings.BASE_DIR / ".github" / "workflows" / "access-reminders.yml").read_text()
        self.assertIn("cron:", workflow)
        self.assertIn("pixi run remind-access", workflow)
        tasks = tomllib.loads((settings.BASE_DIR / "pixi.toml").read_text())["tasks"]
        self.assertEqual(tasks["remind-access"], "python manage.py remind_access_requests")
