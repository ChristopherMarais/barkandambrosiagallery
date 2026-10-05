"""
Permissions grouped by page: validation, AI recommendations, metadata updates, bulk validation, model predictions and
the site notice are each their own grant; uploads never arrive validated; "Staff" is a preset on the account page.
"""
import importlib
import io
import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from beetlesgallery.beetles_app import areas
from beetlesgallery.beetles_app.models import AreaGrant, Beetles, UpdateBatch
from beetlesgallery.beetles_app.test_pipeline_update import download_row, fresh, to_csv
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image

User = get_user_model()


def person(name, *grants):
    user = User.objects.create_user(name, password="pw")
    AreaGrant.objects.bulk_create([AreaGrant(user=user, area=a) for a in grants])
    return user


class GroupsTests(PageBehaviourCase):
    def test_every_permission_is_on_exactly_one_page(self):
        keys = [a["key"] for group in areas.page_groups() for a in group["areas"]]
        self.assertEqual(sorted(keys), sorted(areas.KEYS))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual([g["page"] for g in areas.page_groups()][:3], ["Image browser", "Image annotation", "Data management"])

    def test_bulk_validation_includes_validating_one_at_a_time(self):
        bulk = person("bulk", areas.BULK_VALIDATE)
        self.assertTrue(areas.has_area(bulk, areas.VALIDATE))
        self.assertFalse(areas.has_area(person("one", areas.VALIDATE), areas.BULK_VALIDATE))

    def test_the_migration_keeps_what_people_could_do(self):
        editor, uploader = person("editor", areas.ANNOTATE), person("uploader", areas.UPLOAD)
        migration = importlib.import_module("beetlesgallery.beetles_app.migrations.0038_split_validation_and_update_grants")
        migration.grant(apps, None)
        migration.grant(apps, None)   # running it twice adds nothing twice
        have = lambda u: sorted(AreaGrant.objects.filter(user=u).values_list("area", flat=True))   # noqa: E731
        self.assertEqual(have(editor), ["ai_recommend", "annotate", "validate"])
        self.assertEqual(have(uploader), ["update", "upload"])


class AnnotationPageTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.editor = person("editor", areas.ANNOTATE)
        self.beetle = make_beetle(bbox="unvalidated")

    def post(self, url, data=None):
        return self.client.post(url, json.dumps(data or {}), content_type="application/json")

    def test_editing_names_does_not_validate_or_run_the_ai(self):
        self.client.force_login(self.editor)
        page = self.client.get(reverse("tool_annotate")).content.decode()
        self.assertIn("const CAN_VALIDATE = false;", page)
        self.assertIn("const CAN_AI_RECOMMEND = false;", page)
        self.assertEqual(self.post(f"/api/v1/beetles/{self.beetle.id}/validate/").status_code, 403)
        self.assertEqual(self.post(f"/api/v1/image-assets/{self.beetle.image_asset_id}/validate/").status_code, 403)
        self.assertFalse(fresh(self.beetle).bbox_is_validated)

    def test_the_validate_grant_validates(self):
        AreaGrant.objects.create(user=self.editor, area=areas.VALIDATE)
        self.client.force_login(self.editor)
        self.assertIn("const CAN_VALIDATE = true;", self.client.get(reverse("tool_annotate")).content.decode())
        self.assertEqual(self.post(f"/api/v1/beetles/{self.beetle.id}/validate/").status_code, 200)
        self.assertTrue(fresh(self.beetle).bbox_is_validated)


class DataManagementTests(PageBehaviourCase):
    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("data_management")).content.decode()

    def test_each_button_follows_its_own_permission(self):
        uploader = person("uploader", areas.UPLOAD)
        page = self.page(uploader)
        self.assertIn("openModal('modal-upload-new')", page)
        self.assertNotIn("openModal('modal-update-existing')", page)
        self.assertNotIn(reverse("upload_predictions"), page)
        page = self.page(person("updater", areas.UPDATE, areas.PREDICTIONS))
        self.assertNotIn("openModal('modal-upload-new')", page)
        self.assertIn("openModal('modal-update-existing')", page)
        self.assertIn(reverse("upload_predictions"), page)

    def test_the_handlers_check_the_same_permissions(self):
        self.client.force_login(person("uploader", areas.UPLOAD))
        self.assertEqual(self.client.post(reverse("update_upload")).status_code, 403)
        self.assertEqual(self.client.get(reverse("upload_predictions")).status_code, 403)
        self.client.force_login(person("predictor", areas.PREDICTIONS))
        self.assertEqual(self.client.get(reverse("upload_predictions")).status_code, 200)


class UpdateValidationTests(PageBehaviourCase):
    """Changing validation through an update CSV is bulk validation."""

    def setUp(self):
        super().setUp()
        self.updater = person("updater", areas.UPDATE)

    def run_rows(self, *rows, user=None):
        batch = UpdateBatch.objects.create(uploaded_by=user or self.updater, original_filename="u.csv",
                                           status=UpdateBatch.Status.STAGING)
        batch.file.save("u.csv", ContentFile(to_csv(rows)), save=False)
        batch.size_bytes = batch.file.size
        batch.compute_sha256_from_disk()
        batch.save()
        call_command("process_single_update", id=batch.id, stdout=io.StringIO(), stderr=io.StringIO())
        batch.refresh_from_db()
        return batch

    def test_validating_needs_bulk_validate(self):
        beetle = make_beetle(bbox="unvalidated")
        batch = self.run_rows(download_row(beetle, bbox_is_validated="true"))
        self.assertEqual(batch.status, UpdateBatch.Status.APPLY_FAILED)
        self.assertIn("Row 2: changing bbox_is_validated needs the Bulk validate permission", batch.error_message)
        self.assertFalse(fresh(beetle).bbox_is_validated)

        AreaGrant.objects.create(user=self.updater, area=areas.BULK_VALIDATE)
        batch = self.run_rows(download_row(beetle, bbox_is_validated="true"))
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        self.assertTrue(fresh(beetle).bbox_is_validated)

    def test_unvalidating_an_image_needs_it_too(self):
        image = make_image(is_validated=True)
        beetle = make_beetle(image=image, bbox="validated")
        batch = self.run_rows(download_row(beetle, is_validated="false"))
        self.assertIn("changing is_validated needs the Bulk validate permission", batch.error_message)

    def test_rows_that_leave_validation_as_downloaded_apply(self):
        beetle = make_beetle(image=make_image(is_validated=True), bbox="validated")
        batch = self.run_rows(download_row(beetle, specimen_notes="under bark"))
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        self.assertEqual(fresh(beetle).specimen_notes, "under bark")

    def test_clearing_a_box_is_not_validating(self):
        beetle = make_beetle(bbox="validated")
        batch = self.run_rows(download_row(beetle, bbox_x="", bbox_y="", bbox_width="", bbox_height="",
                                           bbox_is_validated="false"))
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        self.assertIsNone(Beetles.objects.get(id=beetle.id).bbox_x)


class AccountPageTests(PageBehaviourCase):
    def test_permissions_are_grouped_by_page_and_staff_is_a_preset(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("my_account")).content.decode()
        self.assertEqual(page.count('data-testid="permission-page"'), len(areas.PAGES))
        self.assertIn('data-testid="role-help"', page)
        self.assertNotIn("already have all of these", page)
        self.assertIn(f"const CURATOR_AREAS = {json.dumps(areas.CURATOR_AREAS)};", page)
        self.assertIn('data-testid="edit-user-name"', page)

    def test_the_site_notice_has_its_own_permission(self):
        self.client.force_login(self.staff)
        self.assertNotIn('data-testid="site-notice-link"', self.client.get(reverse("my_account")).content.decode())
        self.assertEqual(self.client.get(reverse("site_notice")).status_code, 403)
        self.client.force_login(person("announcer", areas.NOTICE))
        self.assertIn('data-testid="site-notice-link"', self.client.get(reverse("my_account")).content.decode())
        self.assertEqual(self.client.get(reverse("site_notice")).status_code, 200)
