"""
Metadata updates (process_single_update), beyond the three tests in test_pipeline_update that were known bugs (#350):
an image id that isn't a UUID fails the batch with a reason.
"""
from beetlesgallery.beetles_app.models import UpdateBatch
from beetlesgallery.beetles_app.test_pipeline_update import ProcessSingleUpdateTests
from beetlesgallery.beetles_app.testing import PageBehaviourCase


class UpdateFixesTests(PageBehaviourCase):
    # the helpers only (inheriting would run that class's tests twice)
    make_batch = ProcessSingleUpdateTests.make_batch
    process = ProcessSingleUpdateTests.process
    run_rows = ProcessSingleUpdateTests.run_rows

    def test_an_image_id_that_is_not_a_uuid_fails_the_batch_with_a_reason(self):
        batch = self.run_rows({"record_id": "NEW", "image_id": "not-a-uuid", "collection_country": "Peru"})

        self.assertEqual(batch.status, UpdateBatch.Status.APPLY_FAILED)
        self.assertIn("Row 2: Image ID 'not-a-uuid' not found.", batch.error_message)
