"""
Metadata updates (process_single_update), beyond the three tests in test_pipeline_update that were known bugs (#350):
an image id that isn't a UUID fails the batch with a reason; the row counts are kept when a batch fails, and a row
that changes only its image counts as changed.
"""
from beetlesgallery.beetles_app.models import UpdateBatch
from beetlesgallery.beetles_app.test_pipeline_update import ProcessSingleUpdateTests, download_row
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image


class UpdateFixesTests(PageBehaviourCase):
    # the helpers only (inheriting would run that class's tests twice)
    make_batch = ProcessSingleUpdateTests.make_batch
    process = ProcessSingleUpdateTests.process
    run_rows = ProcessSingleUpdateTests.run_rows

    def test_an_image_id_that_is_not_a_uuid_fails_the_batch_with_a_reason(self):
        batch = self.run_rows({"record_id": "NEW", "image_id": "not-a-uuid", "collection_country": "Peru"})

        self.assertEqual(batch.status, UpdateBatch.Status.APPLY_FAILED)
        self.assertIn("Row 2: Image ID 'not-a-uuid' not found.", batch.error_message)

    def test_the_row_count_is_kept_when_the_batch_fails(self):
        beetle = make_beetle()
        batch = self.run_rows(download_row(beetle), {"record_id": "not-a-uuid", "collection_country": "Peru"})

        self.assertEqual(batch.status, UpdateBatch.Status.APPLY_FAILED)
        self.assertEqual(batch.rows_total, 2)

    def test_a_row_that_changes_only_its_image_counts_as_changed(self):
        beetle = make_beetle(image=make_image(photographer="A. Person"))

        batch = self.run_rows(download_row(beetle, photographer="B. Person"))

        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED)
        self.assertEqual((batch.rows_total, batch.rows_matched, batch.rows_changed), (1, 1, 1))
