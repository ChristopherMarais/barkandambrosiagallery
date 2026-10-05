"""
Big uploads arrive in pieces through the normal address (Cloudflare refuses a request over 100 MB), so nobody is sent
to direct.barkandambrosiagallery.org any more, and the staging site's visitors stay on staging.
"""
import uuid
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import chunked_upload
from beetlesgallery.beetles_app.models import UploadBatch
from beetlesgallery.beetles_app.test_page_uploads import GOOD_CSV, make_zip
from beetlesgallery.beetles_app.testing import PageBehaviourCase

TEMPLATES = settings.BASE_DIR / "beetlesgallery" / "templates"


class ChunkedUploadTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.id = str(uuid.uuid4())
        self.data = make_zip(("img_1.jpg",)) * 1   # a real ZIP, cut into pieces below
        patcher = mock.patch("beetlesgallery.beetles_app.views.process_upload_task")
        self.task = patcher.start()
        self.addCleanup(patcher.stop)

    def piece(self, offset, size, upload_id=None, total=None):
        return self.client.post(reverse("upload_chunk"), {
            "upload_id": upload_id or self.id, "offset": offset, "total": total or len(self.data),
            "chunk": SimpleUploadedFile("chunk", self.data[offset:offset + size]),
        })

    def send_all(self, size=40):
        for offset in range(0, len(self.data), size):
            res = self.piece(offset, size)
            self.assertEqual(res.status_code, 200, res.content)
        return res

    def test_pieces_join_into_the_file_and_a_retried_piece_replaces_itself(self):
        last = self.send_all()
        self.assertTrue(last.json()["complete"])
        self.assertEqual(self.piece(0, 40).status_code, 200)            # sent again after a dropped connection
        self.piece(40, len(self.data))                                     # ... and the rest again
        path = chunked_upload.part_path(self.staff, self.id)
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(path.parent, Path(settings.MEDIA_ROOT) / "tmp_uploads")   # swept by the nightly cleanup

    def test_a_gap_or_an_oversized_file_is_refused(self):
        self.assertEqual(self.piece(40, 40).status_code, 409)              # piece 0 never arrived
        with override_settings(MAX_UPLOAD_SIZE_ZIP=10):
            self.assertEqual(self.piece(0, 5).status_code, 400)
        self.assertEqual(self.piece(0, 40, upload_id="../../etc/passwd").status_code, 400)

    def test_the_upload_takes_the_joined_zip_and_tidies_up(self):
        self.send_all()
        res = self.client.post(reverse("upload"), {"csv_file": SimpleUploadedFile("metadata.csv", GOOD_CSV),
                                                   "zip_upload_id": self.id, "zip_total": len(self.data),
                                                   "zip_name": "my images.zip"})
        self.assertRedirects(res, reverse("data_management"), fetch_redirect_response=False)
        batch = UploadBatch.objects.get()
        with batch.zip_file.open("rb") as fh:
            self.assertEqual(fh.read(), self.data)
        self.assertFalse(chunked_upload.part_path(self.staff, self.id).exists())
        self.task.delay.assert_called_once_with(batch.id)

    def test_an_incomplete_or_someone_elses_file_is_not_taken(self):
        self.piece(0, 40)
        res = self.client.post(reverse("upload"), {"csv_file": SimpleUploadedFile("metadata.csv", GOOD_CSV),
                                                   "zip_upload_id": self.id, "zip_total": len(self.data)})
        self.assertRedirects(res, reverse("data_management"), fetch_redirect_response=False)
        self.assertFalse(UploadBatch.objects.exists())
        self.assertIsNone(chunked_upload.take(self.superuser, self.id, len(self.data)))   # stored per user

    def test_only_accounts_that_may_upload_can_send_pieces(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_user("basic", password="pw"))   # no grants
        self.assertEqual(self.piece(0, 40).status_code, 403)


class NoDirectHostTests(PageBehaviourCase):
    def test_nobody_is_sent_to_the_direct_address_and_the_form_sends_its_zip_in_pieces(self):
        page = (TEMPLATES / "beetles" / "data_management.html").read_text()
        self.assertNotIn("DIRECT_HOST", page)
        self.assertIn('data-chunk-input="zip"', page)
        base = (TEMPLATES / "base.html").read_text()
        # only a visitor still on the old direct. address is moved, and only to the same page of the main site
        self.assertIn('window.location.hostname === "direct.barkandambrosiagallery.org"', base)
        self.assertNotIn("allowedPaths", base)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("data_management")), f"const CHUNK_BYTES = {chunked_upload.CHUNK_BYTES};")
