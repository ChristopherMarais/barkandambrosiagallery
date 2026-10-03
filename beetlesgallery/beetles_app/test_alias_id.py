"""
alternative_id is now alias_id: the contributor's own ID for a record (#379). Files that still use the old column
name are accepted by uploads and updates; templates, downloads and pages use the new one and explain it.
"""
import io
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from beetlesgallery.beetles_app.csv_columns import modern_columns
from beetlesgallery.beetles_app.models import Beetles, UpdateBatch, UploadBatch
from beetlesgallery.beetles_app.test_pipeline_update import download_row, fresh, to_csv
from beetlesgallery.beetles_app.test_pipeline_upload import UploadPipelineCase, image_bytes
from beetlesgallery.beetles_app.testing import make_beetle


class AliasIdTests(UploadPipelineCase):
    def test_legacy_column_names_map_to_the_new_one(self):
        self.assertEqual(modern_columns(["﻿record_id", " alternative_id ", "alias_id"]),
                         ["record_id", "alias_id", "alias_id"])

    def test_an_upload_with_the_old_column_name_is_imported(self):
        batch = self.stage_batch([{"full_path_at_import": "a/beetle_1.jpg", "alternative_id": "ANIC-32-045871"}],
                                 {"beetle_1.jpg": image_bytes()})
        self.run_pipeline(batch)
        self.assertEqual(batch.status, UploadBatch.Status.IMPORTED, batch.error_message)
        self.assertEqual(Beetles.objects.get().alias_id, "ANIC-32-045871")

    def test_an_update_with_the_old_column_name_is_applied(self):
        beetle = make_beetle()
        row = download_row(beetle)
        row["alternative_id"] = "OLD-HEADER-1"
        del row["alias_id"]
        self.client.force_login(self.staff)
        upload = SimpleUploadedFile("update.csv", to_csv([row]), content_type="text/csv")
        with mock.patch("beetlesgallery.beetles_app.views.process_update_task"):
            self.client.post(reverse("update_upload"), {"csv_file": upload})
        batch = UpdateBatch.objects.get()
        self.assertEqual(batch.status, UpdateBatch.Status.STAGING, batch.error_message)
        call_command("process_single_update", id=batch.id, stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(fresh(beetle).alias_id, "OLD-HEADER-1")

    def test_templates_and_pages_use_alias_id_and_explain_it(self):
        downloads = Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "downloads"
        for name in ("beetles_upload_template.csv", "beetles_update_template.csv"):
            header = (downloads / name).read_text(encoding="utf-8-sig").splitlines()[0].split(",")
            self.assertIn("alias_id", header)
            self.assertNotIn("alternative_id", header)
        self.client.force_login(self.staff)
        beetle = make_beetle(alias_id="ANIC-1")
        page = self.client.get(reverse("beetle_detail", args=[beetle.id])).content.decode()
        self.assertIn(">Alias ID</dt>", page)
        self.assertIn("ANIC-1", page)
