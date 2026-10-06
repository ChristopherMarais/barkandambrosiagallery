"""
A big upload that stops part-way carries on where it stopped (issue #506). The server says how much of the file it
has (upload_chunk_status), the browser sends only the rest under the same upload id, asks before the page is left
while it is still sending, and once the server has the files the page says it can be closed.
"""
import re
import uuid
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from beetlesgallery.beetles_app import chunked_upload
from beetlesgallery.beetles_app.models import UploadBatch
from beetlesgallery.beetles_app.test_page_uploads import GOOD_CSV, make_zip
from beetlesgallery.beetles_app.testing import PageBehaviourCase

PAGE = settings.BASE_DIR / "beetlesgallery" / "templates" / "beetles" / "data_management.html"


class UploadStatusTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.id = str(uuid.uuid4())
        self.data = make_zip(("img_1.jpg", "img_2.jpg"))   # a real ZIP of a few hundred bytes, sent in pieces
        patcher = mock.patch("beetlesgallery.beetles_app.views.process_upload_task")
        self.task = patcher.start()
        self.addCleanup(patcher.stop)

    def piece(self, offset, size):
        return self.client.post(reverse("upload_chunk"), {
            "upload_id": self.id, "offset": offset, "total": len(self.data),
            "chunk": SimpleUploadedFile("chunk", self.data[offset:offset + size]),
        })

    def status(self, **params):
        return self.client.get(reverse("upload_chunk_status"), params or {"upload_id": self.id})

    def test_the_server_says_how_much_of_an_upload_it_has(self):
        self.assertEqual(self.status().json(), {"received": 0})   # nothing sent yet
        self.piece(0, 40)
        self.piece(40, 40)
        res = self.status()
        self.assertEqual(res.json(), {"received": 80})
        self.assertIn("no-store", res["Cache-Control"])            # asked afresh every time, never from a cache

    def test_an_upload_that_stopped_carries_on_from_what_the_server_has(self):
        for offset in (0, 30, 60):                                  # three pieces arrive, then the connection drops
            self.assertEqual(self.piece(offset, 30).status_code, 200)
        received = self.status().json()["received"]
        self.assertEqual(received, 90)
        for offset in range(received, len(self.data), 40):         # the rest from there, whatever the piece size
            self.assertEqual(self.piece(offset, 40).status_code, 200)
        self.assertEqual(self.status().json(), {"received": len(self.data)})
        upload = chunked_upload.take(self.staff, self.id, len(self.data))
        self.assertIsNotNone(upload)
        with upload:
            self.assertEqual(upload.read(), self.data)

    def test_a_piece_past_the_end_of_the_servers_copy_says_where_to_carry_on(self):
        self.piece(0, 40)
        gap = self.piece(80, 40)                                    # the piece at 40 never arrived
        self.assertEqual(gap.status_code, 409)
        self.assertEqual(gap.json()["received"], 40)
        for offset in range(40, len(self.data), 40):                # what the browser does with that answer
            self.assertEqual(self.piece(offset, 40).status_code, 200)
        self.assertEqual(self.status().json(), {"received": len(self.data)})

    def test_a_bad_or_missing_id_is_refused(self):
        for bad in ("../../etc/passwd", "../" * 12, "x" * 36, ""):
            with self.subTest(upload_id=bad):
                self.assertEqual(self.status(upload_id=bad).status_code, 400)
        self.assertEqual(self.client.get(reverse("upload_chunk_status")).status_code, 400)

    def test_another_accounts_upload_reads_as_nothing(self):
        self.piece(0, 40)
        self.client.force_login(self.superuser)                     # may upload too, but this upload is not theirs
        self.assertEqual(self.status().json(), {"received": 0})
        self.assertEqual(chunked_upload.part_path(self.staff, self.id).stat().st_size, 40)   # and it is left alone

    def test_only_accounts_that_may_upload_can_ask(self):
        self.client.force_login(self.user)                          # a member: no upload area
        self.assertEqual(self.status().status_code, 403)
        self.client.logout()
        self.assertRedirectsToLogin(self.status())

    def test_it_only_answers_get(self):
        self.assertEqual(self.client.post(reverse("upload_chunk_status"), {"upload_id": self.id}).status_code, 405)

    def test_once_the_form_is_in_the_page_says_it_can_be_closed(self):
        for offset in range(0, len(self.data), 40):
            self.piece(offset, 40)
        res = self.client.post(reverse("upload"), {"csv_file": SimpleUploadedFile("metadata.csv", GOOD_CSV),
                                                   "zip_upload_id": self.id, "zip_total": len(self.data),
                                                   "zip_name": "images.zip"})
        self.assertRedirects(res, reverse("data_management"), fetch_redirect_response=False)
        self.task.delay.assert_called_once_with(UploadBatch.objects.get().id)
        # The browser does not follow that redirect: it reloads the page, which shows the message
        self.assertContains(self.client.get(reverse("data_management")), "You can close this page")
        self.assertEqual(self.status().json(), {"received": 0})    # the pieces now live with the batch


class ResumeInThePageTests(PageBehaviourCase):
    """The page's upload script has no test runner of its own: these pin the paths the upload relies on."""

    def setUp(self):
        super().setUp()
        self.page = PAGE.read_text(encoding="utf-8")
        self.script = self.page.split("// --- UPLOAD PROGRESS HANDLERS ---", 1)[1].split("</script>", 1)[0]

    def test_the_page_asks_the_server_where_to_carry_on(self):
        self.client.force_login(self.staff)
        rendered = self.client.get(reverse("data_management"))
        self.assertContains(rendered, f'const CHUNK_STATUS_URL = "{reverse("upload_chunk_status")}";')
        for code in ("let uploadId = rememberedUploadId(file);", "offset = answer.data.received;",
                     "'Resuming: '", "answer.status === 409", "const CHUNK_ATTEMPTS = 5;", "forgetUploadId(file);"):
            self.assertIn(code, self.script)

    def test_every_use_of_browser_storage_is_guarded(self):
        uses = [line for line in self.script.splitlines() if "localStorage." in line]
        self.assertEqual(len(uses), 3)                              # read, remember, forget
        for line in uses:
            self.assertIn("try {", line)
            self.assertIn("catch (err)", line)

    def test_leaving_is_warned_while_sending_and_the_warning_goes_when_done_or_failed(self):
        self.assertIn("window.addEventListener('beforeunload', warnBeforeLeaving);", self.script)
        self.assertEqual(self.script.count("window.removeEventListener('beforeunload', warnBeforeLeaving);"), 2)
        done = self.script.index("forgetUploadId(file);")
        # gone before the reload, or the browser would ask about leaving its own reload
        self.assertLess(self.script.index("window.removeEventListener('beforeunload'", done),
                        self.script.index("window.location.reload();", done))

    def test_a_failure_says_how_to_carry_on_and_promises_only_what_the_cleanup_keeps(self):
        self.assertIn("again with the same ZIP within a day to continue where it stopped", self.script)
        # the nightly sweep_upload_temp_files keeps a stopped upload that long after its last piece
        self.assertGreaterEqual(settings.TEMP_FILE_KEEP_HOURS, 24)

    def test_the_forms_redirect_is_not_followed_so_the_reload_shows_the_message(self):
        self.assertIn("redirect: 'manual'", self.script)

    def test_only_the_new_data_form_sends_in_pieces(self):
        forms = dict(re.findall(r'<form id="([\w-]+)"([^>]*)>', self.page))
        self.assertIn('data-chunk-input="zip"', forms["form-upload-new"])
        self.assertNotIn("data-chunk-input", forms["form-update-existing"])   # its CSV goes up as before
