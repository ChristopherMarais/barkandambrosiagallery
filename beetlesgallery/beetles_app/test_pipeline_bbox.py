"""
Bounding boxes through the two CSV pipelines: upload (with images) and update (by record).

The annotator API refuses boxes that are outside the image, and records who drew a box and who
validated it. The CSV pipelines must hold the same line, because the game and the classifier
treat a validated box as ground truth.
"""
import io
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from beetlesgallery.beetles_app.bbox_rules import parse_box
from beetlesgallery.beetles_app.models import AreaGrant, Beetles, ImageAsset, UpdateBatch
from beetlesgallery.beetles_app.test_pipeline_update import download_row, fresh, to_csv
from beetlesgallery.beetles_app.test_pipeline_upload import UploadPipelineCase, image_bytes
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


class ParseBoxTests(PageBehaviourCase):
    def test_all_blank_means_no_box(self):
        for blank in ("", None, float("nan"), "nan", "  "):
            with self.subTest(blank=blank):
                self.assertEqual(parse_box(blank, blank, blank, blank), (None, None))

    def test_a_valid_box(self):
        self.assertEqual(parse_box("0.1", 0.2, 0.5, "0.25"), ((0.1, 0.2, 0.5, 0.25), None))
        self.assertEqual(parse_box(0, 0, 1, 1)[1], None)  # the whole image is a box

    def test_rejections_say_what_is_wrong(self):
        cases = {
            "partial": ((0.1, "", "", ""), "all four"),
            "pixels": ((1200, 300, 400, 400), "fractions"),
            "negative": ((-0.1, 0.1, 0.2, 0.2), "between 0 and 1"),
            "zero width": ((0.1, 0.1, 0, 0.2), "bbox_width"),
            "past right edge": ((0.8, 0.1, 0.5, 0.2), "right edge"),
            "past bottom edge": ((0.1, 0.9, 0.2, 0.3), "bottom edge"),
            "comma decimal": ("0,3 0.1 0.2 0.2".split(" "), "dot"),
            "text": (("a", 0.1, 0.2, 0.2), "not a number"),
            "infinite": ((float("inf"), 0.1, 0.2, 0.2), "not a number"),
        }
        for name, (values, expected) in cases.items():
            with self.subTest(case=name):
                box, error = parse_box(*values)
                self.assertIsNone(box)
                self.assertIn(expected, error)

    def test_a_box_touching_the_edge_within_rounding_is_fine(self):
        self.assertIsNone(parse_box(0.8, 0.8, 0.2000001, 0.2)[1])


class UpdateBoxTests(PageBehaviourCase):
    def make_batch(self, content):
        AreaGrant.objects.get_or_create(user=self.staff, area="bulk_validate")   # these rows may change validation
        batch = UpdateBatch.objects.create(
            uploaded_by=self.staff, original_filename="updates.csv", status=UpdateBatch.Status.STAGING
        )
        batch.file.save("updates.csv", ContentFile(content), save=False)
        batch.size_bytes = batch.file.size
        batch.compute_sha256_from_disk()
        batch.save()
        return batch

    def run_rows(self, *rows):
        batch = self.make_batch(to_csv(rows))
        call_command("process_single_update", id=batch.id, stdout=io.StringIO(), stderr=io.StringIO())
        batch.refresh_from_db()
        return batch

    def assertRejected(self, batch, text):
        self.assertEqual(batch.status, UpdateBatch.Status.APPLY_FAILED)
        self.assertIn(text, batch.error_message)

    BOX = dict(bbox_x="0.1", bbox_y="0.2", bbox_width="0.3", bbox_height="0.4")

    # --- adding boxes ---------------------------------------------------------

    def test_a_box_can_be_added_to_an_unboxed_record(self):
        record = make_beetle()  # the record every plain image upload leaves behind
        batch = self.run_rows(download_row(record, **self.BOX))

        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        record = fresh(record)
        self.assertEqual((record.bbox_x, record.bbox_y, record.bbox_width, record.bbox_height), (0.1, 0.2, 0.3, 0.4))
        self.assertFalse(record.bbox_is_validated)

    def test_a_new_box_records_who_added_it(self):
        record = make_beetle()
        self.run_rows(download_row(record, **self.BOX))
        record = fresh(record)
        self.assertEqual(record.bbox_created_by, self.staff)
        self.assertIsNotNone(record.bbox_created_at)

    def test_a_second_box_on_the_same_image_is_a_new_row(self):
        first = make_beetle(bbox="unvalidated")
        batch = self.run_rows({
            "record_id": "NEW", "image_id": str(first.image_asset_id),
            "depicts_specimen": "second", **self.BOX,
        })

        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        boxes = Beetles.objects.filter(image_asset=first.image_asset, bbox_x__isnull=False)
        self.assertEqual(boxes.count(), 2)
        created = boxes.exclude(pk=first.pk).get()
        self.assertEqual((created.depicts_specimen, created.bbox_created_by), ("second", self.staff))

    def test_boxes_for_two_records_of_one_image_in_one_batch(self):
        image = make_image()
        a, b = make_beetle(image=image), make_beetle(image=image)
        batch = self.run_rows(
            download_row(a, **self.BOX),
            download_row(b, bbox_x="0.5", bbox_y="0.5", bbox_width="0.2", bbox_height="0.2"),
        )
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        self.assertEqual((fresh(a).bbox_x, fresh(b).bbox_x), (0.1, 0.5))

    # --- refusing bad boxes: nothing is written --------------------------------

    def test_pixel_coordinates_are_refused(self):
        record, other = make_beetle(collection_country="USA"), make_beetle()
        batch = self.run_rows(
            download_row(record, collection_country="Peru"),
            download_row(other, bbox_x="1200", bbox_y="300", bbox_width="400", bbox_height="400"),
        )
        self.assertRejected(batch, "Row 3")
        self.assertIn("fractions", batch.error_message)
        self.assertEqual(fresh(record).collection_country, "USA")  # the good row was not applied either
        self.assertIsNone(fresh(other).bbox_x)

    def test_partial_and_out_of_range_boxes_are_refused(self):
        cases = {
            "only x": dict(bbox_x="0.1", bbox_y="", bbox_width="", bbox_height=""),
            "zero width": dict(bbox_x="0.1", bbox_y="0.1", bbox_width="0", bbox_height="0.2"),
            "past the edge": dict(bbox_x="0.8", bbox_y="0.1", bbox_width="0.5", bbox_height="0.2"),
        }
        for name, box in cases.items():
            with self.subTest(case=name):
                record = make_beetle()
                batch = self.run_rows(download_row(record, **box))
                self.assertRejected(batch, "Row 2")
                self.assertIsNone(fresh(record).bbox_x)

    def test_unreadable_text_fails_instead_of_clearing_the_box(self):
        record = make_beetle(bbox="validated")
        batch = self.run_rows(download_row(record, bbox_x="0,3"))
        self.assertRejected(batch, "not a number")
        self.assertEqual(fresh(record).bbox_x, 0.1)

    def test_validated_needs_a_box(self):
        record = make_beetle()
        batch = self.run_rows(download_row(record, bbox_is_validated="true"))
        self.assertRejected(batch, "no box")
        self.assertFalse(fresh(record).bbox_is_validated)

    def test_a_new_box_next_to_an_unboxed_record_is_refused_with_the_fix(self):
        unboxed = make_beetle()
        batch = self.run_rows({"record_id": "NEW", "image_id": str(unboxed.image_asset_id), **self.BOX})

        self.assertRejected(batch, "Row 2")
        self.assertIn(str(unboxed.id), batch.error_message)  # names the record that should get the box
        self.assertEqual(Beetles.objects.filter(image_asset=unboxed.image_asset).count(), 1)

    def test_that_check_does_not_trip_when_the_same_batch_boxes_the_record(self):
        unboxed = make_beetle()
        batch = self.run_rows(
            download_row(unboxed, **self.BOX),
            {"record_id": "NEW", "image_id": str(unboxed.image_asset_id),
             "bbox_x": "0.5", "bbox_y": "0.5", "bbox_width": "0.2", "bbox_height": "0.2"},
        )
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        self.assertEqual(Beetles.objects.filter(image_asset=unboxed.image_asset, bbox_x__isnull=False).count(), 2)

    # --- changing and clearing boxes -------------------------------------------

    def test_rows_that_do_not_touch_the_box_are_not_checked(self):
        # Existing data may not meet today's rules; only the columns a row changes are held to them.
        odd = make_beetle(collection_country="USA", bbox="validated")
        Beetles.objects.filter(pk=odd.pk).update(bbox_width=1.5)
        batch = self.run_rows(download_row(odd, collection_country="Peru"))
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        self.assertEqual(fresh(odd).collection_country, "Peru")

    def test_clearing_all_four_removes_the_box_and_its_validation(self):
        record = make_beetle(bbox="validated")
        Beetles.objects.filter(pk=record.pk).update(bbox_validated_by=self.staff, bbox_created_by=self.staff)
        batch = self.run_rows(download_row(
            record, bbox_x="", bbox_y="", bbox_width="", bbox_height="", bbox_is_validated="false"
        ))
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        record = fresh(record)
        self.assertIsNone(record.bbox_x)
        self.assertFalse(record.bbox_is_validated)
        self.assertIsNone(record.bbox_validated_by)
        self.assertIsNone(record.bbox_created_by)

    def test_validating_a_box_records_the_validator(self):
        record = make_beetle(bbox="unvalidated")
        self.run_rows(download_row(record, bbox_is_validated="true"))
        record = fresh(record)
        self.assertTrue(record.bbox_is_validated)
        self.assertEqual(record.bbox_validated_by, self.staff)
        self.assertIsNotNone(record.bbox_validated_at)


class UploadBoxTests(UploadPipelineCase):
    def upload(self, *rows, photos=1):
        images = {r["full_path_at_import"]: image_bytes() for r in rows}
        return self.run_pipeline(self.stage_batch(list(rows), images))

    def test_boxes_are_imported_with_who_added_them(self):
        make_taxon(valid_species_id="T1")
        batch = self.upload({
            "full_path_at_import": "a.jpg", "depicts_valid_name_id": "T1",
            "bbox_x": 0.1, "bbox_y": 0.2, "bbox_width": 0.3, "bbox_height": 0.4,
        })
        self.assertEqual(batch.status, "imported", batch.error_message)
        beetle = Beetles.objects.get()
        self.assertEqual((beetle.bbox_x, beetle.bbox_height), (0.1, 0.4))
        self.assertEqual(beetle.bbox_created_by, self.staff)
        self.assertIsNotNone(beetle.bbox_created_at)
        self.assertFalse(beetle.bbox_is_validated)

    def test_a_box_marked_validated_still_arrives_unvalidated(self):
        batch = self.upload({
            "full_path_at_import": "a.jpg", "bbox_x": 0.1, "bbox_y": 0.2, "bbox_width": 0.3,
            "bbox_height": 0.4, "bbox_is_validated": "yes",
        })
        self.assertEqual(batch.status, "imported", batch.error_message)
        beetle = Beetles.objects.get()
        self.assertFalse(beetle.bbox_is_validated)
        self.assertIsNone(beetle.bbox_validated_by)

    def test_a_row_without_a_box_still_imports(self):
        batch = self.upload({"full_path_at_import": "a.jpg"})
        self.assertEqual(batch.status, "imported", batch.error_message)
        self.assertIsNone(Beetles.objects.get().bbox_x)

    def test_bad_boxes_reject_the_whole_batch_with_row_numbers(self):
        good = {"full_path_at_import": "good.jpg", "bbox_x": 0.1, "bbox_y": 0.1, "bbox_width": 0.2, "bbox_height": 0.2}
        cases = {
            "pixels": ({"bbox_x": 1200, "bbox_y": 300, "bbox_width": 400, "bbox_height": 400}, "fractions"),
            "partial": ({"bbox_x": 0.1}, "all four"),
            "past the edge": ({"bbox_x": 0.9, "bbox_y": 0.1, "bbox_width": 0.5, "bbox_height": 0.2}, "right edge"),
            "text": ({"bbox_x": "left", "bbox_y": 0.1, "bbox_width": 0.2, "bbox_height": 0.2}, "not a number"),
            "validated, no box": ({"bbox_is_validated": "yes"}, "no box"),
        }
        for name, (columns, expected) in cases.items():
            with self.subTest(case=name):
                Beetles.objects.all().delete()
                ImageAsset.objects.all().delete()
                batch = self.upload(good, {"full_path_at_import": f"bad_{name}.jpg", **columns})
                self.assertRejected(batch, "Row 3")
                self.assertIn(expected, batch.error_message)
                self.assertNothingImported()


class UpdateDialogTests(PageBehaviourCase):
    """The Update dialog checks the file before the pipeline runs; a NEW row must get through it."""

    BOX = UpdateBoxTests.BOX

    def post_csv(self, *rows):
        self.client.force_login(self.staff)
        upload = SimpleUploadedFile("boxes.csv", to_csv(rows), content_type="text/csv")
        with mock.patch("beetlesgallery.beetles_app.views.process_update_task") as task:
            self.client.post(reverse("update_upload"), {"csv_file": upload})
        return UpdateBatch.objects.get(), task

    def test_a_new_row_passes_the_dialog_check_and_creates_the_box(self):
        existing = make_beetle(bbox="unvalidated")
        row = download_row(existing, record_id="NEW", depicts_specimen="second", **self.BOX)
        row["image_id"] = str(existing.image_asset_id)

        batch, task = self.post_csv(row)

        self.assertEqual(batch.status, UpdateBatch.Status.STAGING, batch.error_message)
        task.delay.assert_called_once_with(batch.id)
        call_command("process_single_update", id=batch.id, stdout=io.StringIO(), stderr=io.StringIO())
        batch.refresh_from_db()
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        self.assertEqual(Beetles.objects.filter(image_asset=existing.image_asset, bbox_x__isnull=False).count(), 2)

    def test_a_malformed_record_id_is_still_refused(self):
        existing = make_beetle()
        batch, task = self.post_csv(download_row(existing, record_id="not-a-uuid"))
        self.assertEqual(batch.status, UpdateBatch.Status.REJECTED)
        task.delay.assert_not_called()
