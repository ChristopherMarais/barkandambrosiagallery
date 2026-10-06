"""
The uploads page sends the Update Metadata and species-table forms in the background (XMLHttpRequest) and then
reloads. The background request followed the view's redirect, and the page it fetched out of sight used up the
server's message, so the reload showed none ("Update file received", "Update rejected: ..."). These views now
answer a background send with {"reload": true} and keep the message for the reloaded page (#506 follow-up).
"""
import uuid
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from beetlesgallery.beetles_app.models import UpdateBatch
from beetlesgallery.beetles_app.testing import PageBehaviourCase
from beetlesgallery.beetles_app.views import UPDATE_REQUIRED_COLS

PAGE = settings.BASE_DIR / "beetlesgallery" / "templates" / "beetles" / "data_management.html"
# Sent the way the uploads page sends it: an XMLHttpRequest, which follows any redirect it gets
BACKGROUND = {"follow": True, "HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def update_csv(columns=UPDATE_REQUIRED_COLS):
    columns = sorted(columns)
    row = [str(uuid.uuid4()) if column == "record_id" else "" for column in columns]
    return (",".join(columns) + "\n" + ",".join(row) + "\n").encode()


class BackgroundFormMessageTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        for target in ("process_update_task", "call_command"):   # the background update and the taxonomy rebuild
            patcher = mock.patch(f"beetlesgallery.beetles_app.views.{target}")
            setattr(self, target, patcher.start())
            self.addCleanup(patcher.stop)

    def reloaded(self):
        return self.client.get(reverse("data_management")).content.decode()

    def assertShownOnceAfterTheReload(self, response, text):
        self.assertIn(text, self.reloaded())
        self.assertNotIn(text, self.reloaded())                    # used up there, like any message
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"reload": True})

    def test_an_update_keeps_its_message_for_the_reload(self):
        self.client.force_login(self.staff)
        res = self.client.post(reverse("update_upload"), {"csv_file": SimpleUploadedFile("u.csv", update_csv())},
                               **BACKGROUND)
        self.assertShownOnceAfterTheReload(res, "Update file received.")
        self.process_update_task.delay.assert_called_once_with(UpdateBatch.objects.get().id)

    def test_a_rejected_update_says_why_after_the_reload(self):
        self.client.force_login(self.staff)
        csv = update_csv(UPDATE_REQUIRED_COLS - {"photographer"})
        res = self.client.post(reverse("update_upload"), {"csv_file": SimpleUploadedFile("u.csv", csv)}, **BACKGROUND)
        self.assertShownOnceAfterTheReload(res, "Update rejected: Missing required columns: [&#x27;photographer&#x27;]")
        self.process_update_task.delay.assert_not_called()

    def test_a_species_table_keeps_its_message_for_the_reload(self):
        self.client.force_login(self.superuser)
        for page, text in (("admin_valid_species", "Accepted species uploaded"),
                           ("admin_described_names", "Synonyms and old names uploaded")):
            with self.subTest(page=page):
                res = self.client.post(reverse(page), {"csv_file": SimpleUploadedFile("ref.csv", b"a,b\n1,2\n")},
                                       **BACKGROUND)
                self.assertShownOnceAfterTheReload(res, text)
        self.assertEqual(self.call_command.call_count, 2)

    def test_a_refused_species_table_keeps_its_message_although_its_page_was_drawn(self):
        # An empty file fails the form, and the view draws its own page with the message (using it up there)
        self.client.force_login(self.superuser)
        res = self.client.post(reverse("admin_valid_species"), {"csv_file": SimpleUploadedFile("ref.csv", b"")},
                               **BACKGROUND)
        self.assertShownOnceAfterTheReload(res, "Invalid file submission.")
        self.call_command.assert_not_called()

    def test_a_form_sent_the_normal_way_is_answered_as_before(self):
        self.client.force_login(self.superuser)
        res = self.client.post(reverse("update_upload"), {"csv_file": SimpleUploadedFile("u.csv", update_csv())})
        self.assertRedirects(res, reverse("data_management"), fetch_redirect_response=False)
        res = self.client.post(reverse("admin_valid_species"), {"csv_file": SimpleUploadedFile("ref.csv", b"a\n1\n")})
        self.assertRedirects(res, reverse("admin_valid_species"), fetch_redirect_response=False)

    def test_who_may_not_send_still_is_turned_away(self):
        self.client.force_login(self.user)                          # a member: no update or species-table area
        for page in ("update_upload", "admin_valid_species"):
            with self.subTest(page=page):
                res = self.client.post(reverse(page), {}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                self.assertEqual(res.status_code, 403)

    def test_the_page_reloads_on_that_answer(self):
        script = PAGE.read_text(encoding="utf-8").split("// --- UPLOAD PROGRESS HANDLERS ---", 1)[1]
        self.assertIn("if (response.success || response.reload) {", script)
