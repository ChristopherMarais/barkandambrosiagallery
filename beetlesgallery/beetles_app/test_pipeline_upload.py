# Tests for the upload pipeline: process_single_upload -> validate_uploads -> import_validated (issue #256, phase 3).
import csv
import hashlib
import io
import itertools
import json
import os
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from functools import partial
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from beetlesgallery.beetles_app.management.commands import process_single_upload as pipeline_command
from beetlesgallery.beetles_app.models import Beetles, ImageAsset, UploadBatch
from beetlesgallery.beetles_app.tasks import process_upload_task
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_image, make_taxon

_shade = itertools.count(1)


def image_bytes(size=(8, 8), fmt="JPEG"):
    # Channels move in big steps so no two images in a run share JPEG bytes (and so a sha256).
    n = next(_shade)
    buffer = io.BytesIO()
    Image.new("RGB", size, (n * 40 % 256, n * 90 % 256, n * 150 % 256)).save(buffer, format=fmt)
    return buffer.getvalue()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def make_csv(rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def make_zip(images):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in images.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def read_media(name):
    with default_storage.open(name) as fh:
        return fh.read()


class UploadPipelineCase(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        # The real lock file is tracked in git at the repo root; keep test runs out of it.
        lock_path = os.path.join(self.media_root, "upload_pipeline.lock")
        self.enterContext(mock.patch.object(
            pipeline_command, "upload_pipeline_lock",
            partial(pipeline_command.upload_pipeline_lock, lock_path),
        ))

    def stage_batch(self, rows, images, csv_bytes=None):
        batch = UploadBatch.objects.create(uploaded_by=self.staff, original_filename="metadata.csv")
        batch.file.save("metadata.csv", ContentFile(csv_bytes or make_csv(rows)), save=False)
        batch.zip_file.save("images.zip", ContentFile(make_zip(images)), save=False)
        batch.save()
        return batch

    def quietly(self, *args, **options):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            call_command(*args, stdout=io.StringIO(), stderr=io.StringIO(), **options)

    def run_pipeline(self, batch):
        self.quietly("process_single_upload", id=str(batch.id))
        batch.refresh_from_db()
        return batch

    def assertRejected(self, batch, message):
        self.assertEqual(batch.status, UploadBatch.Status.REJECTED)
        self.assertIn(message, batch.error_message)
        self.assertTrue(batch.error_message.endswith("(see error log for full details)"), batch.error_message)
        self.assertTrue(batch.file.name.startswith("uploads/rejected/"), batch.file.name)
        self.assertTrue(batch.error_report_file.name.startswith("uploads/rejected/"), batch.error_report_file.name)
        self.assertIn(message, read_media(batch.error_report_file.name).decode())

    def assertImportFailed(self, batch, message):
        self.assertEqual(batch.status, UploadBatch.Status.IMPORT_FAILED)
        self.assertIsNotNone(batch.validated_at)
        self.assertIsNone(batch.imported_at)
        self.assertIn(message, batch.error_message)
        self.assertEqual(batch.error_report_file.name, f"upload_error_logs/import_errors_{batch.id}.txt")
        report = read_media(batch.error_report_file.name).decode()
        self.assertIn(message, report)
        self.assertIn("Original filename: metadata.csv", report)
        self.assertTrue(batch.file.name.startswith("uploads/validated/"), batch.file.name)

    def assertNothingImported(self):
        self.assertEqual(Beetles.objects.count(), 0)
        self.assertEqual(ImageAsset.objects.count(), 0)


class ValidBatchImportTests(UploadPipelineCase):

    # --- the happy path -----------------------------------------------------

    def test_valid_batch_is_imported_with_its_csv_metadata(self):
        taxon = make_taxon(valid_species_id="TAX-1")
        photo = image_bytes()
        batch = self.stage_batch([{
            "full_path_at_import": "field/2024/beetle_1.jpg",
            "depicts_valid_name_id": "TAX-1",
            "depicts_described_name_id": "DN-7",
            "depicts_specimen": "SPEC-001",
            "depicts_name_verbatim": "Xyleborus sp.",
            "alias_id": "ALT-1",
            "aspect": "dorsal",
            "collection_country": "Costa Rica",
            "collection_stateProvince": "Heredia",
            "specimen_sex": "Female",
            "specimen_type_status": "paratype",
            "specimen_notes": "under bark",
            "photographer": "A. Person",
            "image_institution": "Museum",
            "image_email": "a@example.org",
            "photo_usage_statement": "CC BY 4.0",
            "image_notes": "stacked",
            "image_has_multiple_individuals": "no",
            "resolution_in_ppmm": "123.45678",
            "bbox_x": 0.1, "bbox_y": 0.2, "bbox_width": 0.3, "bbox_height": 0.4,
            "bbox_is_validated": "yes",
        }], {"beetle_1.jpg": photo})

        self.run_pipeline(batch)

        self.assertEqual(batch.status, UploadBatch.Status.IMPORTED)
        self.assertEqual(batch.error_message, "")
        self.assertIsNotNone(batch.validated_at)
        self.assertIsNotNone(batch.imported_at)

        beetle = Beetles.objects.get()
        self.assertEqual(beetle.taxon, taxon)
        self.assertEqual(
            (beetle.depicts_valid_name_id, beetle.depicts_described_name_id, beetle.depicts_specimen,
             beetle.depicts_name_verbatim, beetle.alias_id, beetle.aspect),
            ("TAX-1", "DN-7", "SPEC-001", "Xyleborus sp.", "ALT-1", "dorsal"),
        )
        self.assertEqual(
            (beetle.collection_country, beetle.collection_stateProvince, beetle.specimen_sex,
             beetle.specimen_type_status, beetle.specimen_notes),
            ("Costa Rica", "Heredia", "f", "paratype", "under bark"),
        )
        self.assertEqual(
            (beetle.bbox_x, beetle.bbox_y, beetle.bbox_width, beetle.bbox_height, beetle.bbox_is_validated),
            (0.1, 0.2, 0.3, 0.4, False),   # uploads never arrive validated
        )
        history = beetle.history.first()
        self.assertEqual(history.history_user, self.staff)
        self.assertIn(str(batch.id), history.history_change_reason)

        asset = beetle.image_asset
        self.assertEqual(asset.full_path_at_import, "field/2024/beetle_1.jpg")
        self.assertEqual(asset.image_sha256, sha256(photo))
        self.assertEqual(asset.image_size_bytes, len(photo))
        self.assertEqual(
            (asset.photographer, asset.image_institution, asset.image_email,
             asset.photo_usage_statement, asset.image_notes),
            ("A. Person", "Museum", "a@example.org", "CC BY 4.0", "stacked"),
        )
        self.assertIs(asset.image_has_multiple_individuals, False)
        self.assertEqual(asset.resolution_in_ppmm, Decimal("123.4568"))

    def test_import_writes_the_original_and_a_thumbnail_and_archives_the_batch(self):
        photo = image_bytes(size=(16, 8))
        batch = self.stage_batch([{"full_path_at_import": "wide.jpg"}], {"wide.jpg": photo})

        self.run_pipeline(batch)

        asset = ImageAsset.objects.get()
        sha = sha256(photo)
        self.assertEqual(asset.image_file.name, Beetles.path_for_original(sha, "jpg"))
        self.assertEqual(read_media(asset.image_file.name), photo)
        self.assertEqual((asset.image_width, asset.image_height), (16, 8))
        self.assertEqual(asset.thumb_small.name, Beetles.path_for_thumb96(sha, webp=True))
        with default_storage.open(asset.thumb_small.name) as fh, Image.open(fh) as thumb:
            self.assertEqual(thumb.format, "WEBP")
            self.assertLessEqual(max(thumb.size), 96)

        for field in (batch.file, batch.zip_file):
            self.assertTrue(field.name.startswith("uploads/archived/"), field.name)
            self.assertTrue(os.path.exists(field.path))
        archive_dir = os.path.dirname(batch.file.path)
        with open(os.path.join(archive_dir, f"manifest_{batch.id}.json")) as fh:
            self.assertEqual([row["sha256"] for row in json.load(fh)["rows"]], [sha])
        with open(os.path.join(archive_dir, f"archive_{batch.id}.json")) as fh:
            archive = json.load(fh)
        self.assertEqual((archive["batch_id"], archive["imported_count"]), (str(batch.id), 1))

    def test_image_listed_on_several_rows_becomes_one_image_with_several_specimens(self):
        shared, single = image_bytes(), image_bytes()
        batch = self.stage_batch([
            {"full_path_at_import": "group.jpg", "depicts_specimen": "A", "bbox_x": 0.1, "bbox_y": 0.1, "bbox_width": 0.2, "bbox_height": 0.2},
            {"full_path_at_import": "group.jpg", "depicts_specimen": "B", "bbox_x": 0.5, "bbox_y": 0.5, "bbox_width": 0.2, "bbox_height": 0.2},
            {"full_path_at_import": "alone.jpg", "depicts_specimen": "C"},
        ], {"group.jpg": shared, "alone.jpg": single})

        self.run_pipeline(batch)

        self.assertEqual(batch.status, UploadBatch.Status.IMPORTED)
        self.assertEqual(ImageAsset.objects.count(), 2)
        group = ImageAsset.objects.get(image_sha256=sha256(shared))
        self.assertEqual(
            sorted(group.specimens.values_list("depicts_specimen", "bbox_x")),
            [("A", 0.1), ("B", 0.5)],
        )
        alone = ImageAsset.objects.get(image_sha256=sha256(single)).specimens.get()
        self.assertEqual(alone.depicts_specimen, "C")
        self.assertIsNone(alone.bbox_x)
        self.assertIsNone(alone.taxon)

    def test_tiff_image_also_gets_a_display_jpeg(self):
        scan = image_bytes(fmt="TIFF")
        batch = self.stage_batch([{"full_path_at_import": "scan.tif"}], {"scan.tif": scan})

        self.run_pipeline(batch)

        asset = ImageAsset.objects.get()
        display = ImageAsset.path_for_display(sha256(scan))
        self.assertEqual(asset.image_file.name, Beetles.path_for_original(sha256(scan), "tiff"))
        self.assertTrue(asset.display_url.endswith(display), asset.display_url)
        with default_storage.open(display) as fh, Image.open(fh) as jpeg:
            self.assertEqual((jpeg.format, jpeg.size), ("JPEG", (8, 8)))

    def test_upload_page_hands_the_batch_to_the_worker_which_imports_it(self):
        self.client.force_login(self.staff)
        photo = image_bytes()
        with mock.patch("beetlesgallery.beetles_app.views.process_upload_task") as task:
            task.delay.side_effect = process_upload_task
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                response = self.client.post("/upload/", {
                    "csv_file": SimpleUploadedFile("metadata.csv", make_csv([{"full_path_at_import": "img.jpg"}])),
                    "zip": SimpleUploadedFile("images.zip", make_zip({"img.jpg": photo})),
                })

        self.assertRedirects(response, reverse("data_management"), fetch_redirect_response=False)
        batch = UploadBatch.objects.get()
        task.delay.assert_called_once_with(batch.id)
        self.assertEqual(batch.status, UploadBatch.Status.IMPORTED)
        beetle = Beetles.objects.get()
        self.assertEqual(beetle.image_asset.image_sha256, sha256(photo))
        self.assertEqual(beetle.history.first().history_user, self.staff)

    def test_image_date_taken_from_the_csv_is_saved(self):
        batch = self.stage_batch([
            {"full_path_at_import": "dated.jpg", "image_date_taken": "2024-05-17"},
            {"full_path_at_import": "timed.jpg", "image_date_taken": "2024-05-18 10:30:00"},
            {"full_path_at_import": "impossible.jpg", "image_date_taken": "2024-13-45"},
        ], {"dated.jpg": image_bytes(), "timed.jpg": image_bytes(), "impossible.jpg": image_bytes()})

        self.run_pipeline(batch)

        self.assertEqual(batch.status, UploadBatch.Status.IMPORTED)
        self.assertEqual(
            dict(ImageAsset.objects.values_list("full_path_at_import", "image_date_taken")),
            {"dated.jpg": date(2024, 5, 17), "timed.jpg": date(2024, 5, 18), "impossible.jpg": None},
        )

    def test_new_photo_with_an_already_imported_path_gets_its_own_image(self):
        first, second = image_bytes(), image_bytes()
        self.run_pipeline(self.stage_batch([{"full_path_at_import": "IMG_0001.jpg"}], {"IMG_0001.jpg": first}))
        batch = self.run_pipeline(self.stage_batch([{"full_path_at_import": "IMG_0001.jpg"}], {"IMG_0001.jpg": second}))

        self.assertEqual(batch.status, UploadBatch.Status.IMPORTED)
        new_specimen = Beetles.objects.order_by("-last_updated_at").first()
        self.assertEqual(new_specimen.image_asset.image_sha256, sha256(second))


class ValidationRejectionTests(UploadPipelineCase):

    # --- the validator refuses the batch; nothing is imported ----------------

    def test_rows_whose_images_are_missing_from_the_zip_are_rejected(self):
        batch = self.stage_batch([
            {"full_path_at_import": "present.jpg"},
            {"full_path_at_import": "missing_1.jpg"},
            {"full_path_at_import": "missing_2.jpg"},
        ], {"present.jpg": image_bytes()})

        self.run_pipeline(batch)

        self.assertRejected(batch, "Row 3: image 'missing_1.jpg' not found in ZIP.")
        self.assertEqual(
            batch.error_message,
            "Row 3: image 'missing_1.jpg' not found in ZIP. (+1 more) (see error log for full details)",
        )
        self.assertIn("Row 4: image 'missing_2.jpg' not found in ZIP.", read_media(batch.error_report_file.name).decode())
        self.assertNothingImported()
        self.assertFalse(os.path.exists(os.path.join(self.media_root, "originals")))

    def test_image_already_in_the_database_is_rejected(self):
        photo = image_bytes()
        existing = make_image(full_path_at_import="old.jpg", image_sha256=sha256(photo))
        batch = self.stage_batch([{"full_path_at_import": "again.jpg"}], {"again.jpg": photo})

        self.run_pipeline(batch)

        self.assertRejected(batch, "Duplicate images already exist in DB")
        self.assertIn("again.jpg", batch.error_message)
        self.assertEqual(list(ImageAsset.objects.all()), [existing])
        self.assertEqual(Beetles.objects.count(), 0)

    def test_validator_rejects_sheets_that_do_not_match_the_zip_or_the_taxonomy(self):
        make_taxon(valid_species_id="TAX-1")
        same = image_bytes()
        cases = [
            ("no full_path_at_import column",
             [{"filename": "a.jpg"}], {"a.jpg": image_bytes()},
             "Missing required columns: ['full_path_at_import']"),
            ("blank full_path_at_import",
             [{"full_path_at_import": "", "collection_country": "USA"}], {"a.jpg": image_bytes()},
             "Row 2: 'full_path_at_import' must be present."),
            ("unknown valid name id",
             [{"full_path_at_import": "a.jpg", "depicts_valid_name_id": "NO-SUCH-ID"}], {"a.jpg": image_bytes()},
             "Row 2: 'depicts_valid_name_id' not found in reference database: 'NO-SUCH-ID'."),
            ("zip without images",
             [{"full_path_at_import": "a.jpg"}], {"notes.txt": b"hello"},
             "ZIP contains no image files."),
            ("image the sheet does not list",
             [{"full_path_at_import": "a.jpg"}], {"a.jpg": image_bytes(), "stray.jpg": image_bytes()},
             "ZIP contains extra images not referenced by sheet: ['stray.jpg']"),
            ("two files with the same content",
             [{"full_path_at_import": "a.jpg"}, {"full_path_at_import": "b.jpg"}], {"a.jpg": same, "b.jpg": same},
             "ZIP contains duplicate images by hash"),
        ]
        for label, rows, images, message in cases:
            with self.subTest(label):
                batch = self.run_pipeline(self.stage_batch(rows, images))
                self.assertRejected(batch, message)
                self.assertNothingImported()


class ImportFailureTests(UploadPipelineCase):

    # --- validation passes, import fails and rolls back ----------------------

    def test_invalid_sex_fails_the_import_and_rolls_back_earlier_rows(self):
        # Row 3 fails, so row 2 was already inserted inside the same transaction.
        batch = self.stage_batch([
            {"full_path_at_import": "ok.jpg", "specimen_sex": "M"},
            {"full_path_at_import": "bad.jpg", "specimen_sex": "juvenile"},
        ], {"ok.jpg": image_bytes(), "bad.jpg": image_bytes()})

        self.run_pipeline(batch)

        self.assertImportFailed(batch, "Row 3: Invalid sex 'juvenile'. Allowed: M, Male, F, Female.")
        self.assertNothingImported()

    def test_file_that_is_not_a_readable_image_fails_the_import(self):
        batch = self.stage_batch([
            {"full_path_at_import": "good.jpg"},
            {"full_path_at_import": "broken.jpg"},
        ], {"good.jpg": image_bytes(), "broken.jpg": b"not really a jpeg"})

        self.run_pipeline(batch)

        self.assertImportFailed(batch, f"{batch.id}: Row 3 failed image save/thumbnail")
        self.assertNothingImported()


class PipelineOrderTests(UploadPipelineCase):

    # --- process_single_upload and the batch commands ------------------------

    def test_import_runs_only_after_validation_passes(self):
        good = self.stage_batch([{"full_path_at_import": "a.jpg"}], {"a.jpg": image_bytes()})
        bad = self.stage_batch([{"full_path_at_import": "missing.jpg"}], {"other.jpg": image_bytes()})
        cases = [
            (good, UploadBatch.Status.IMPORTED, ["validate_uploads", "import_validated"]),
            (bad, UploadBatch.Status.REJECTED, ["validate_uploads"]),
        ]
        for batch, status, commands in cases:
            with self.subTest(status):
                with mock.patch.object(pipeline_command, "call_command", wraps=call_command) as spy:
                    self.run_pipeline(batch)
                self.assertEqual([c.args[0] for c in spy.call_args_list], commands)
                self.assertEqual(batch.status, status)
        self.assertTrue(os.path.exists(os.path.join(self.media_root, "upload_pipeline.lock")))

    def test_two_batches_validated_together_each_import_their_own_images(self):
        first, second = image_bytes(), image_bytes()
        batch_a = self.stage_batch([{"full_path_at_import": "a.jpg"}], {"a.jpg": first})
        batch_b = self.stage_batch([{"full_path_at_import": "b.jpg"}], {"b.jpg": second})

        self.quietly("validate_uploads")
        self.quietly("import_validated")

        batch_a.refresh_from_db()
        batch_b.refresh_from_db()
        self.assertEqual(ImageAsset.objects.get(full_path_at_import="a.jpg").image_sha256, sha256(first))
        self.assertEqual([batch_a.status, batch_b.status], [UploadBatch.Status.IMPORTED] * 2)
