"""
Metadata updates (process_single_update), beyond the three tests in test_pipeline_update that were known bugs (#350):
an image id that isn't a UUID fails the batch with a reason; the row counts are kept when a batch fails, and a row
that changes only its image counts as changed; who changed a record or image, and why, is kept.
"""
from beetlesgallery.beetles_app.models import UpdateBatch
from beetlesgallery.beetles_app.test_pipeline_update import ProcessSingleUpdateTests, download_row, fresh
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

    def test_a_blank_note_leaves_the_old_one_and_still_records_who(self):
        beetle = make_beetle(collection_country="USA", update_notes="checked in 2023")

        self.run_rows({**download_row(beetle, collection_country="Peru"), "update_notes": ""})

        beetle = fresh(beetle)
        self.assertEqual((beetle.collection_country, beetle.update_notes), ("Peru", "checked in 2023"))
        self.assertEqual(beetle.last_updated_by, self.staff)

    def test_a_new_note_alone_is_a_change(self):
        beetle = make_beetle()

        batch = self.run_rows({**download_row(beetle), "update_notes": "label re-read"})

        self.assertEqual((fresh(beetle).update_notes, batch.rows_changed), ("label re-read", 1))

    def test_an_image_change_records_who_made_it(self):
        beetle = make_beetle(image=make_image(photographer="A. Person"))

        self.run_rows(download_row(beetle, photographer="B. Person"))

        self.assertEqual(fresh(beetle).image_asset.last_updated_by, self.staff)

    def test_an_unchanged_row_leaves_who_alone(self):
        beetle = make_beetle()

        self.run_rows(download_row(beetle))

        self.assertIsNone(fresh(beetle).last_updated_by)
