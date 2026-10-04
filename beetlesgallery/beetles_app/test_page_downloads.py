"""
Behaviour tests for downloads (issue #206, part 3): the "Download" button on the
Image Browser (/downloads/start/) and the taxonomy reference CSV downloads.

The background worker that builds the files is replaced by a mock; these tests
cover the job the view records for it.
"""
import json
import uuid
from unittest import mock

from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse

from beetlesgallery.beetles_app.models import DownloadJob
from beetlesgallery.beetles_app.testing import PageBehaviourCase

START = "/downloads/start/"


class StartDownloadTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        patcher = mock.patch("beetlesgallery.beetles_app.views.build_downloads_task")
        self.task = patcher.start()
        self.addCleanup(patcher.stop)
        self.ids = [str(uuid.uuid4()) for _ in range(3)]

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_requires_login(self):
        self.client.logout()
        self.assertRedirectsToLogin(self.client.post(START, {"selection_mode": "ids"}))
        self.assertEqual(DownloadJob.objects.count(), 0)

    def test_get_sends_you_back_to_the_gallery(self):
        response = self.client.get(START)
        self.assertRedirects(response, reverse("beetles_image_browser"), fetch_redirect_response=False)
        self.assertEqual(DownloadJob.objects.count(), 0)

    def test_unknown_selection_mode_is_rejected(self):
        for mode in ("", "everything"):
            with self.subTest(mode=mode):
                response = self.client.post(START, {"selection_mode": mode})
                self.assertRedirects(response, reverse("beetles_image_browser"), fetch_redirect_response=False)
        self.assertEqual(DownloadJob.objects.count(), 0)
        self.task.delay.assert_not_called()

    # --- selected rows ---------------------------------------------------------

    def test_selected_ids_create_a_pending_job_and_queue_the_worker(self):
        response = self.client.post(START, {"selection_mode": "ids", "selected_ids": ",".join(self.ids)})

        self.assertRedirects(response, reverse("data_management"), fetch_redirect_response=False)
        job = DownloadJob.objects.get()
        self.assertEqual(job.requested_by, self.user)
        self.assertEqual(job.selection_mode, "ids")
        self.assertEqual(job.status, DownloadJob.Status.PENDING)
        self.assertEqual(job.get_ids(), self.ids)
        self.assertEqual(job.total_requested, 3)
        self.task.delay.assert_called_once_with(job.id)

    def test_ids_can_be_sent_as_a_repeated_field(self):
        self.client.post(START, {"selection_mode": "ids", "selected_ids": self.ids[:2]})
        self.assertEqual(DownloadJob.objects.get().get_ids(), self.ids[:2])

    def test_duplicate_and_malformed_ids_are_dropped(self):
        raw = f"{self.ids[0]}, not-a-uuid ,{self.ids[0]},{self.ids[1]}"
        self.client.post(START, {"selection_mode": "ids", "selected_ids": raw})
        job = DownloadJob.objects.get()
        self.assertEqual(job.get_ids(), self.ids[:2])
        self.assertEqual(job.total_requested, 2)

    def test_no_usable_ids_records_a_failed_job_and_does_not_queue_it(self):
        response = self.client.post(START, {"selection_mode": "ids", "selected_ids": "junk, more junk"})

        self.assertRedirects(response, reverse("data_management"), fetch_redirect_response=False)
        job = DownloadJob.objects.get()
        self.assertEqual(job.status, DownloadJob.Status.FAILED)
        self.assertIn("No valid rows", job.error_message)
        self.task.delay.assert_not_called()
        self.assertTrue(any("No valid rows" in m for m in self.messages(response)))

    def test_ids_are_found_even_under_an_unexpected_field_name(self):
        self.client.post(START, {"selection_mode": "ids", "picked": self.ids[0]})
        self.assertEqual(DownloadJob.objects.get().get_ids(), [self.ids[0]])

    # --- everything matching the current search --------------------------------

    def test_query_mode_stores_the_search_filters_and_ranges_as_json(self):
        self.client.post(START, {
            "selection_mode": "query", "q": "genus:Ips", "country": ["USA", "Brazil"],
            "size_min": "5", "res_max": " 10 ", "total_matches": "42",
        })

        job = DownloadJob.objects.get()
        self.assertEqual(job.selection_mode, "query")
        self.assertEqual(job.total_requested, 42)
        self.assertEqual(json.loads(job.query_string), {
            "q": "genus:Ips",
            "filters": {"country": ["USA", "Brazil"]},
            "ranges": {"size_min": "5", "size_max": "", "res_min": "", "res_max": "10"},
        })
        self.task.delay.assert_called_once_with(job.id)

    def test_query_mode_ignores_blank_filters_and_bad_totals(self):
        self.client.post(START, {"selection_mode": "query", "country": "", "total_matches": "lots"})
        job = DownloadJob.objects.get()
        self.assertEqual(json.loads(job.query_string)["filters"], {})
        self.assertEqual(job.total_requested, 0)

    def test_query_job_has_a_readable_description(self):
        self.client.post(START, {"selection_mode": "query", "q": "ips", "country": "USA"})
        self.assertEqual(DownloadJob.objects.get().get_readable_query(), "Text: “ips”; Country: USA")

    # --- metadata-only downloads -------------------------------------------------

    def test_images_are_included_by_default(self):
        self.client.post(START, {"selection_mode": "query"})
        self.assertTrue(DownloadJob.objects.get().include_images)

    def test_staff_can_ask_for_metadata_only(self):
        self.client.force_login(self.staff)
        self.client.post(START, {"selection_mode": "query", "download_type": "metadata_only"})
        self.assertFalse(DownloadJob.objects.get().include_images)

    def test_regular_users_cannot_opt_out_of_images(self):
        self.client.post(START, {"selection_mode": "query", "download_type": "metadata_only"})
        self.assertTrue(DownloadJob.objects.get().include_images)


class MyDownloadsTests(PageBehaviourCase):
    def test_my_uploads_page_lists_only_your_own_jobs(self):
        mine = DownloadJob.objects.create(requested_by=self.user, selection_mode="query")
        DownloadJob.objects.create(requested_by=self.staff, selection_mode="query")
        self.client.force_login(self.user)

        response = self.client.get(reverse("data_management"))

        self.assertEqual(list(response.context["download_jobs"]), [mine])


class ReferenceDownloadTests(PageBehaviourCase):
    """The taxonomy reference CSVs served from storage, to people with the species tables area."""

    def setUp(self):
        super().setUp()
        from beetlesgallery.beetles_app.models import AreaGrant
        AreaGrant.objects.create(user=self.user, area="species_tables")

    def test_people_without_the_species_tables_area_are_turned_away(self):
        self.client.force_login(self.staff)
        for name in ("download_taxonomy_ref", "download_described_names_ref"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def store(self, path, content=b"id,name\n1,Ips\n"):
        default_storage.save(path, ContentFile(content))

    def test_reference_downloads_require_login(self):
        for name in ("download_taxonomy_ref", "download_described_names_ref"):
            with self.subTest(page=name):
                self.assertRedirectsToLogin(self.client.get(reverse(name)))

    def test_missing_reference_file_is_a_404(self):
        self.client.force_login(self.user)
        for name in ("download_taxonomy_ref", "download_described_names_ref"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)

    def test_valid_species_csv_is_served_as_an_attachment(self):
        self.store("reference/valid_species.csv")
        self.client.force_login(self.user)

        response = self.client.get(reverse("download_taxonomy_ref"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertRegex(response["Content-Disposition"], r'^attachment; filename="valid_species_\d{8}_\d{6}\.csv"$')
        self.assertEqual(b"".join(response.streaming_content), b"id,name\n1,Ips\n")

    def test_described_names_csv_is_served_as_an_attachment(self):
        self.store("reference/described_names.csv")
        self.client.force_login(self.user)

        response = self.client.get(reverse("download_described_names_ref"))

        self.assertEqual(response.status_code, 200)
        self.assertRegex(response["Content-Disposition"], r'filename="described_names_\d{8}_\d{6}\.csv"')


class ArchiveDownloadTests(PageBehaviourCase):
    def url(self, ref_type="valid_species", filename="old.csv"):
        return reverse("download_taxonomy_archive", args=[ref_type, filename])

    def test_only_superusers_can_download_archives(self):
        for user in (None, self.user, self.staff):
            with self.subTest(user=user):
                self.client.logout()
                if user:
                    self.client.force_login(user)
                self.assertRedirectsToLogin(self.client.get(self.url()))

    def test_superuser_gets_the_archived_file(self):
        default_storage.save("reference/archive/valid_species/old.csv", ContentFile(b"a,b\n"))
        self.client.force_login(self.superuser)

        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="old.csv"')
        self.assertEqual(b"".join(response.streaming_content), b"a,b\n")

    def test_missing_archive_is_a_404(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_unknown_reference_type_is_a_404(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.url(ref_type="passwords")).status_code, 404)

    def test_filenames_that_climb_out_of_the_folder_are_refused(self):
        default_storage.save("reference/secret.csv", ContentFile(b"secret"))
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.url(filename="..secret.csv")).status_code, 404)
