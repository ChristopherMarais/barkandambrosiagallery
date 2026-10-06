"""Extra access areas on top of the three roles."""
from django.urls import reverse

from beetlesgallery.beetles_app.areas import has_area
from beetlesgallery.beetles_app.models import AreaGrant
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle

PAGES = {  # area -> pages that need it
    "annotate": ["tool_annotate"],
    "upload": [],
    "interactions": ["interaction_review", "upload_interactions"],
}


class AreaCase(PageBehaviourCase):
    def grant(self, user, *areas):
        for area in areas:
            AreaGrant.objects.create(user=user, area=area)
        if hasattr(user, "_granted_areas"):
            del user._granted_areas


class RoleDefaultsTests(AreaCase):
    def test_roles_keep_their_defaults(self):
        for area in PAGES:
            self.assertFalse(has_area(self.user, area))
            self.assertTrue(has_area(self.staff, area))
            self.assertTrue(has_area(self.superuser, area))

    def test_a_grant_adds_only_that_area(self):
        self.grant(self.user, "interactions")
        self.assertTrue(has_area(self.user, "interactions"))
        self.assertFalse(has_area(self.user, "annotate"))
        self.assertFalse(has_area(self.user, "upload"))

    def test_an_inactive_user_has_nothing(self):
        self.grant(self.user, "annotate")
        self.user.is_active = False
        self.assertFalse(has_area(self.user, "annotate"))


class EnforcementTests(AreaCase):
    def test_pages_need_their_area(self):
        for area, pages in PAGES.items():
            for name in pages:
                with self.subTest(area=area, page=name):
                    self.client.force_login(self.user)
                    self.assertRedirectsToLogin(self.client.get(reverse(name)))
        self.grant(self.user, "interactions")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("interaction_review")).status_code, 200)
        self.assertEqual(self.client.get(reverse("upload_interactions")).status_code, 200)
        self.assertRedirectsToLogin(self.client.get(reverse("tool_annotate")))

    def test_annotate_grant_opens_the_annotator_and_its_api(self):
        beetle = make_beetle(bbox="unvalidated")
        self.client.force_login(self.user)
        self.assertRedirectsToLogin(self.client.get(reverse("tool_annotate")))
        self.assertEqual(self.client.post(f"/api/v1/beetles/{beetle.id}/validate/").status_code, 403)
        self.grant(self.user, "annotate")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("tool_annotate")).status_code, 200)
        # validating is its own grant now
        self.assertEqual(self.client.post(f"/api/v1/beetles/{beetle.id}/validate/").status_code, 403)
        self.grant(self.user, "validate")
        self.client.force_login(self.user)
        self.assertNotEqual(self.client.post(f"/api/v1/beetles/{beetle.id}/validate/").status_code, 403)

    def test_upload_grant_opens_the_upload_handlers(self):
        self.client.force_login(self.user)
        self.assertRedirectsToLogin(self.client.post(reverse("upload")))
        self.assertRedirectsToLogin(self.client.post(reverse("update_upload")))
        self.grant(self.user, "upload")
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse("upload")), reverse("data_management"), fetch_redirect_response=False)

    def test_sidebar_and_data_management_show_only_what_is_allowed(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("data_management")).content.decode()
        self.assertNotIn("Image Annotation", page)
        self.assertNotIn("openModal('modal-upload-new')", page)
        self.assertNotIn("Review Proposed Interactions", self.client.get(reverse("interactions_preview")).content.decode())
        self.grant(self.user, "annotate", "interactions", "upload")
        self.client.force_login(self.user)
        page = self.client.get(reverse("data_management")).content.decode()
        self.assertIn("Image Annotation", page)
        self.assertIn("openModal('modal-upload-new')", page)
        self.assertIn("Review Proposed Interactions", self.client.get(reverse("interactions_preview")).content.decode())

    def test_superuser_only_pages_stay_superuser_only(self):
        self.grant(self.user, "annotate", "upload", "interactions")
        self.client.force_login(self.user)
        for name in ("upload_predictions", "access_requests", "upload_interaction_proposals"):
            self.assertRedirectsToLogin(self.client.get(reverse(name)))


class AccountPageTests(AreaCase):
    def edit(self, actor, target, **fields):
        self.client.force_login(actor)
        data = {"action_edit_user": "1", "user_id": target.id, "username": target.username, "role": "standard", "is_active": "on", **fields}
        return self.client.post(reverse("my_account"), data)

    def test_a_superuser_grants_and_removes_areas(self):
        self.edit(self.superuser, self.user, areas=["interactions", "annotate"])
        self.assertEqual(set(AreaGrant.objects.filter(user=self.user).values_list("area", flat=True)), {"interactions", "annotate"})
        self.assertEqual(AreaGrant.objects.get(user=self.user, area="annotate").granted_by, self.superuser)
        self.edit(self.superuser, self.user, areas=["interactions"])
        self.assertEqual(list(AreaGrant.objects.filter(user=self.user).values_list("area", flat=True)), ["interactions"])
        self.edit(self.superuser, self.user)
        self.assertFalse(AreaGrant.objects.filter(user=self.user).exists())

    def test_unknown_areas_are_ignored(self):
        self.edit(self.superuser, self.user, areas=["superuser", "annotate"])
        self.assertEqual(list(AreaGrant.objects.filter(user=self.user).values_list("area", flat=True)), ["annotate"])

    def test_staff_cannot_change_grants(self):
        before = set(AreaGrant.objects.filter(user=self.user).values_list("area", flat=True))
        self.edit(self.staff, self.user, areas=["annotate"])
        self.assertEqual(set(AreaGrant.objects.filter(user=self.user).values_list("area", flat=True)), before)

    def test_the_page_offers_the_choices_to_superusers_only(self):
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("my_account")), "Permissions</span>")
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(reverse("my_account")), "Permissions</span>")

    def test_a_users_grants_are_passed_to_the_edit_dialog(self):
        self.grant(self.user, "upload", "annotate")
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("my_account")), "'annotate,details,download,upload'")


class EditingUsersIsForSuperusersTests(AreaCase):
    def edit(self, actor, **fields):
        self.client.force_login(actor)
        data = {"action_edit_user": "1", "user_id": self.user.id, "username": self.user.username, "role": "standard", "is_active": "on", **fields}
        return self.client.post(reverse("my_account"), data)

    def test_staff_cannot_change_anyones_role_status_name_or_password(self):
        for fields in ({"role": "superuser"}, {"role": "staff"}, {"is_active": ""}, {"username": "renamed"}, {"new_password": "Changed-pw-99"}):
            with self.subTest(fields=fields):
                self.edit(self.staff, **fields)
                self.user.refresh_from_db()
                self.assertEqual((self.user.username, self.user.is_staff, self.user.is_superuser, self.user.is_active), ("user", False, False, True))
                self.assertTrue(self.user.check_password("pw"))

    def test_staff_cannot_promote_themselves(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("my_account"), {"action_edit_user": "1", "user_id": self.staff.id, "username": "staff", "role": "superuser", "is_active": "on"})
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_superuser)

    def test_a_superuser_still_can(self):
        self.edit(self.superuser, role="staff", username="renamed")
        self.user.refresh_from_db()
        self.assertEqual((self.user.username, self.user.is_staff, self.user.is_superuser), ("renamed", True, False))

    def test_the_edit_button_is_only_shown_to_superusers(self):
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(reverse("my_account")), "onclick=\"openEditUserModal(")
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("my_account")), "onclick=\"openEditUserModal(")


class StaffOnlySeeTheirOwnAccountTests(AreaCase):
    def test_staff_cannot_create_users_or_see_the_directory(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("my_account"))
        for text in ("Create New User", "User Directory", "modal-create-user"):
            self.assertNotContains(page, text)
        before = self.user.__class__.objects.count()
        self.client.post(reverse("my_account"), {"action_create_user": "1", "username": "newbie", "password1": "Correct-Horse-9-Staple", "password2": "Correct-Horse-9-Staple"})
        self.assertEqual(self.user.__class__.objects.count(), before)
        self.assertRedirectsToLogin(self.client.get(reverse("create_account")))

    def test_staff_can_still_change_their_own_password(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("my_account")), "Change Password")

    def test_superusers_see_and_can_do_all_of_it(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("my_account"))
        for text in ("Create New User", "User Directory"):
            self.assertContains(page, text)
        self.assertEqual(self.client.get(reverse("create_account")).status_code, 200)
