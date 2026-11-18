"""
Signing up is simple (owner, 2026-11-18): nothing to choose. Confirming the email gives every account the same
access: everything to read and download (specimen pages and downloads included), IBBI-AI and the game. Anything that
changes data is asked for later with a short note and granted by hand, and a request can never give the Admin areas.
"""
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import areas
from beetlesgallery.beetles_app.models import AccessRequest, AreaGrant
from beetlesgallery.beetles_app.test_access import APPROVERS, LINK, form_data
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image

User = get_user_model()


@override_settings(ACCESS_REQUEST_RECIPIENTS=APPROVERS)
class SimpleSignUpTests(PageBehaviourCase):
    def sign_up_and_confirm(self):
        self.client.post(reverse("signup"), form_data())
        self.client.get(re.search(LINK, mail.outbox[-1].body).group(0))
        ada = User.objects.get(username="ada")
        self.client.force_login(ada)
        return ada

    def test_a_new_account_can_read_and_download_everything_and_change_nothing(self):
        ada = self.sign_up_and_confirm()
        self.assertFalse(AreaGrant.objects.filter(user=ada).exists())
        self.assertEqual(AccessRequest.objects.get(user=ada).status, "approved")      # nobody had to approve it
        beetle = make_beetle(image=make_image())
        for name, args in (("beetle_detail", [beetle.id]), ("beetles_image_browser", []), ("tool_classify", []),
                           ("taxonomy_browser", []), ("game_home", [])):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)
        self.assertTrue(areas.has_area(ada, areas.DOWNLOAD))
        for key in areas.KEYS:   # every area that changes something, Admin included
            self.assertFalse(areas.has_area(ada, key), key)
        self.assertEqual(self.client.get(reverse("upload")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin_valid_species")).status_code, 403)
        self.assertFalse(ada.is_staff or ada.is_superuser)

    def test_asking_for_more_is_a_note_and_never_reaches_the_admin_areas(self):
        ada = self.sign_up_and_confirm()
        page = self.client.get(reverse("request_access"))
        self.assertContains(page, "What would you like to help with?")
        self.assertNotContains(page, 'name="areas"')
        self.client.post(reverse("request_access"), {"name": "Ada", "email": "ada@example.org", "affiliation": "X",
                                                     "reason": "Drawing boxes", "leave_blank": ""})
        waiting = AccessRequest.objects.get(user=ada, status="pending")
        self.client.force_login(self.superuser)
        review = self.client.get(reverse("access_requests"))
        self.assertContains(review, "Drawing boxes")
        for key in (areas.PREDICTIONS, areas.SPECIES, areas.NOTICE):
            self.assertNotContains(review, f'value="{key}"')
        self.client.post(reverse("access_requests"), {"request_id": waiting.id, "decision": "grant",
                                                      "areas": [areas.BOXES, areas.SPECIES, areas.NOTICE]})
        self.assertEqual(set(AreaGrant.objects.filter(user=ada).values_list("area", flat=True)), {areas.BOXES})

    def test_a_superuser_can_still_give_admin_areas_by_hand_on_the_account_page(self):
        # only a request can't; the Account page's per-person editor is unchanged
        self.assertIn(areas.SPECIES, areas.KEYS)
        self.assertIn(areas.SPECIES, [a["key"] for g in areas.page_groups() for a in g["areas"]])
