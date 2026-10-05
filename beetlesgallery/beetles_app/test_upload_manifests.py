"""
Each upload batch keeps its own manifest and archive record (#350). Batches validated in the same month no longer
overwrite each other's; an import never reads another batch's manifest; and a batch validated before the change
still imports from the folder's shared manifest.json when that one is its own.
"""
import json
import os
import shutil

from beetlesgallery.beetles_app.models import ImageAsset, UploadBatch
from beetlesgallery.beetles_app.test_pipeline_upload import UploadPipelineCase, image_bytes, sha256


class ManifestPerBatchTests(UploadPipelineCase):
    def staged(self, name, photo=None):
        return self.stage_batch([{"full_path_at_import": name}], {name: photo or image_bytes()})

    def sidecar(self, batch, name):
        with open(os.path.join(os.path.dirname(batch.file.path), name), encoding="utf-8") as fh:
            return json.load(fh)

    def test_each_batch_keeps_its_own_manifest_and_archive_record(self):
        a, b = self.staged("a.jpg"), self.staged("b.jpg")
        self.quietly("validate_uploads")
        self.quietly("import_validated")
        for batch in (a, b):
            batch.refresh_from_db()
            self.assertEqual(batch.status, UploadBatch.Status.IMPORTED)
            self.assertEqual(self.sidecar(batch, f"manifest_{batch.id}.json")["batch_id"], str(batch.id))
            self.assertEqual(self.sidecar(batch, f"archive_{batch.id}.json")["batch_id"], str(batch.id))

    def test_a_batch_validated_before_the_change_imports_from_the_shared_manifest(self):
        photo = image_bytes()
        batch = self.staged("old.jpg", photo)
        self.quietly("validate_uploads")
        batch.refresh_from_db()
        folder = os.path.dirname(batch.file.path)
        os.replace(os.path.join(folder, f"manifest_{batch.id}.json"), os.path.join(folder, "manifest.json"))

        self.quietly("import_validated")

        batch.refresh_from_db()
        self.assertEqual(batch.status, UploadBatch.Status.IMPORTED)
        self.assertEqual(ImageAsset.objects.get().image_sha256, sha256(photo))
        self.assertEqual(self.sidecar(batch, "manifest.json")["batch_id"], str(batch.id))   # archived with it

    def test_an_import_refuses_a_manifest_that_belongs_to_another_batch(self):
        a, b = self.staged("a.jpg"), self.staged("b.jpg")
        self.quietly("validate_uploads")
        a.refresh_from_db()
        b.refresh_from_db()
        # What the shared manifest.json did before: the batch validated last wrote over the other's.
        folder = os.path.dirname(a.file.path)
        os.remove(os.path.join(folder, f"manifest_{a.id}.json"))
        shutil.copy(os.path.join(os.path.dirname(b.file.path), f"manifest_{b.id}.json"), os.path.join(folder, "manifest.json"))

        self.quietly("import_validated")

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertImportFailed(a, f"belongs to batch {b.id}")
        self.assertFalse(ImageAsset.objects.filter(full_path_at_import="a.jpg").exists())
        self.assertEqual(b.status, UploadBatch.Status.IMPORTED)   # the other batch is not held up
