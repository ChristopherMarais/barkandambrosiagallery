# Tests for the metadata update pipeline: process_single_update run on an UpdateBatch (issue #256, phase 3).
import csv
import io
import os
import uuid
from datetime import date
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management import call_command

from beetlesgallery.beetles_app.models import AreaGrant, Beetles, UpdateBatch
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon
from beetlesgallery.beetles_app.views import UPDATE_ALLOWED_FIELDS

BEETLE_FIELDS = {field.name for field in Beetles._meta.get_fields()}


def fresh(beetle):
    return Beetles.objects.select_related("image_asset", "taxon").get(pk=beetle.pk)


def download_row(beetle, **changes):
    # The record as the gallery download writes it, with the exact column set update_upload requires.
    beetle = fresh(beetle)
    row = {"record_id": str(beetle.id)}
    for field in UPDATE_ALLOWED_FIELDS:
        owner = beetle if field in BEETLE_FIELDS else beetle.image_asset
        value = getattr(owner, field)
        row[field] = "" if value is None else value
    row.update(changes)
    return row


def to_csv(rows):
    columns = list(dict.fromkeys(column for row in rows for column in row))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


class ProcessSingleUpdateTests(PageBehaviourCase):

    def make_batch(self, content, filename="updates.csv"):
        AreaGrant.objects.get_or_create(user=self.staff, area="bulk_validate")   # these rows may change validation
        batch = UpdateBatch.objects.create(
            uploaded_by=self.staff,
            original_filename=filename,
            status=UpdateBatch.Status.STAGING,
        )
        batch.file.save(filename, ContentFile(content), save=False)
        batch.size_bytes = batch.file.size
        batch.compute_sha256_from_disk()
        batch.save()
        return batch

    def process(self, batch):
        call_command("process_single_update", id=batch.id, stdout=io.StringIO(), stderr=io.StringIO())
        batch.refresh_from_db()
        return batch

    def run_rows(self, *rows):
        return self.process(self.make_batch(to_csv(rows)))

    def assertFailed(self, batch, text):
        self.assertEqual(batch.status, UpdateBatch.Status.APPLY_FAILED)
        self.assertTrue(batch.error_message.startswith("APPLY ERROR: "), batch.error_message)
        self.assertIn(text, batch.error_message)
        self.assertIsNone(batch.applied_at)
        self.assertFalse(batch.report_file)
        self.assertTrue(batch.file.name.startswith("updates/staging/"), batch.file.name)

    # --- a valid batch ------------------------------------------------------

    def test_valid_batch_changes_only_the_named_record_and_is_archived(self):
        target = make_beetle(collection_country="USA", specimen_sex="Male", image=make_image(photographer="A. Old"))
        other = make_beetle(collection_country="USA", specimen_sex="Male", image=make_image(photographer="A. Old"))
        other_before = download_row(other)

        batch = self.run_rows(download_row(
            target,
            collection_country="Peru", specimen_sex="Female", alias_id="ALT-9",
            photographer="B. New", image_notes="retouched",
        ))

        target = fresh(target)
        self.assertEqual(
            (target.collection_country, target.specimen_sex, target.alias_id),
            ("Peru", "Female", "ALT-9"),
        )
        self.assertEqual((target.image_asset.photographer, target.image_asset.image_notes), ("B. New", "retouched"))
        self.assertEqual(download_row(other), other_before)
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED)
        self.assertIsNotNone(batch.applied_at)
        self.assertEqual(batch.error_message, "")
        self.assertTrue(batch.file.name.startswith("updates/archived/"), batch.file.name)
        self.assertTrue(os.path.exists(batch.file.path))

    def test_cells_are_converted_to_the_field_types(self):
        beetle = make_beetle()

        self.run_rows(download_row(
            beetle,
            resolution_in_ppmm="12.34565", image_has_multiple_individuals="yes", is_validated="True",
            bbox_x="0.25", bbox_y="0.5", bbox_width="0.125", bbox_height="0.2", bbox_is_validated="true",
            specimen_sex="F?",
        ))

        beetle = fresh(beetle)
        self.assertEqual(beetle.image_asset.resolution_in_ppmm, Decimal("12.3457"))
        self.assertIs(beetle.image_asset.image_has_multiple_individuals, True)
        self.assertIs(beetle.image_asset.is_validated, True)
        self.assertEqual((beetle.bbox_x, beetle.bbox_y, beetle.bbox_width, beetle.bbox_height), (0.25, 0.5, 0.125, 0.2))
        self.assertIs(beetle.bbox_is_validated, True)
        self.assertEqual(beetle.specimen_sex, "F?", "sex is stored as written, without validation")

    def test_dates_are_applied_and_unchanged_dates_are_kept(self):
        dated = make_beetle(image=make_image(image_date_taken=date(2020, 1, 1)))
        undated = make_beetle(image=make_image())

        self.run_rows(download_row(dated), download_row(undated, image_date_taken="2024-05-01 08:00:00"))

        self.assertEqual(
            (fresh(dated).image_asset.image_date_taken, fresh(undated).image_asset.image_date_taken),
            (date(2020, 1, 1), date(2024, 5, 1)),
        )

    def test_valid_name_id_relinks_the_taxon(self):
        old, new = make_taxon(valid_species_id="17"), make_taxon(valid_species_id="4521")
        moved, cleared = make_beetle(taxon=old), make_beetle(taxon=old)

        # The blank cell makes pandas read the column as floats (4521.0); the command must strip the ".0".
        self.run_rows(
            download_row(moved, depicts_valid_name_id="4521"),
            download_row(cleared, depicts_valid_name_id=""),
        )

        moved, cleared = fresh(moved), fresh(cleared)
        self.assertEqual((moved.depicts_valid_name_id, moved.taxon), ("4521", new))
        self.assertEqual((cleared.depicts_valid_name_id, cleared.taxon), (None, None))

    def test_blank_cell_clears_a_value_and_a_missing_column_leaves_it(self):
        beetle = make_beetle(specimen_notes="old note", collection_country="USA", image=make_image(photographer="A. Old"))

        self.run_rows({"record_id": str(beetle.id), "specimen_notes": "", "collection_country": "Chile"})

        beetle = fresh(beetle)
        self.assertIsNone(beetle.specimen_notes)
        self.assertEqual(beetle.collection_country, "Chile")
        self.assertEqual(beetle.image_asset.photographer, "A. Old")

    def test_only_changed_rows_get_a_history_record_credited_to_the_uploader(self):
        changed, unchanged = make_beetle(collection_country="USA"), make_beetle(collection_country="USA")

        batch = self.run_rows(download_row(changed, collection_country="Peru"), download_row(unchanged))

        latest = changed.history.first()
        self.assertEqual(changed.history.count(), 2)
        self.assertEqual(
            (latest.history_type, latest.history_user, latest.history_change_reason),
            ("~", self.staff, f"Updated via Batch {batch.id}"),
        )
        self.assertEqual(latest.prev_record.collection_country, "USA")
        self.assertEqual(unchanged.history.count(), 1)
        self.assertEqual(unchanged.image_asset.history.count(), 1)

    def test_csv_with_a_byte_order_mark_is_applied(self):
        # Excel writes CSVs as utf-8-sig, so the record_id header starts with a byte-order mark. (This was tested
        # through the specimen page's own edit form, which wrote one too; editing is on the annotation page now, #505.)
        beetle = make_beetle(collection_country="USA", specimen_notes="keep", image=make_image(photographer="A. Old"))

        content = to_csv([download_row(beetle, collection_country="Peru")]).decode().encode("utf-8-sig")
        batch = self.process(self.make_batch(content))

        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        beetle = fresh(beetle)
        self.assertEqual((beetle.collection_country, beetle.specimen_notes), ("Peru", "keep"))
        self.assertEqual(beetle.image_asset.photographer, "A. Old")

    def test_new_row_creates_a_specimen_on_the_linked_image(self):
        image = make_image()
        existing = make_beetle(image=image)

        batch = self.run_rows({
            "record_id": "NEW",
            "link_image_uuid": str(image.id),
            "image_has_multiple_individuals": "True",
            "depicts_specimen": "second individual",
            "specimen_sex": "Female",
        })

        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        created = fresh(Beetles.objects.exclude(pk=existing.pk).get(image_asset=image))
        self.assertEqual((created.depicts_specimen, created.specimen_sex), ("second individual", "Female"))
        self.assertIs(created.image_asset.image_has_multiple_individuals, True)
        record = created.history.get()
        self.assertEqual(
            (record.history_type, record.history_user, record.history_change_reason),
            ("+", self.staff, f"Created via Batch {batch.id}"),
        )

    def test_soft_deleted_record_is_still_updated_and_stays_deleted(self):
        beetle = make_beetle(collection_country="USA")
        beetle.delete()

        batch = self.run_rows(download_row(beetle, collection_country="Peru"))

        beetle = fresh(beetle)
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED)
        self.assertEqual(beetle.collection_country, "Peru")
        self.assertTrue(beetle.is_deleted)

    # --- invalid input: the batch fails and nothing is written --------------

    def test_unknown_record_id_fails_the_batch_and_writes_nothing(self):
        known = make_beetle(collection_country="USA")
        known_before = download_row(known)
        missing_id = uuid.uuid4()

        batch = self.run_rows(
            download_row(known, collection_country="Peru"),
            {**download_row(known, collection_country="Chile"), "record_id": str(missing_id)},
        )

        self.assertFailed(batch, f"Row 3: Record ID '{missing_id}' not found.")
        self.assertEqual(download_row(known), known_before)
        self.assertEqual(known.history.count(), 1)

    def test_csv_without_a_record_id_column_fails_the_batch(self):
        beetle = make_beetle(collection_country="USA")
        row = download_row(beetle, collection_country="Peru")
        del row["record_id"]

        batch = self.run_rows(row)

        self.assertFailed(batch, "Row 2: Must provide valid 'record_id' or 'image_id'.")
        self.assertEqual(fresh(beetle).collection_country, "USA")

    def test_database_error_on_a_later_row_rolls_back_the_earlier_rows(self):
        first, second = make_beetle(collection_country="USA"), make_beetle(collection_country="USA")

        # A value the column cannot hold gets past the row checks and fails in the database.
        batch = self.run_rows(
            download_row(first, collection_country="Peru"),
            download_row(second, collection_country="Peru", specimen_sex="x" * 60),
        )

        self.assertFailed(batch, "Database error during apply")
        self.assertIn("value too long", batch.error_message)
        self.assertEqual(fresh(first).collection_country, "USA")
        self.assertEqual(first.history.count(), 1)

    def test_non_uuid_record_id_fails_the_batch_with_a_reason(self):
        batch = self.run_rows({"record_id": "not-a-uuid", "collection_country": "Peru"})

        self.assertFailed(batch, "Row 2: Record ID 'not-a-uuid' not found.")

    # --- known gaps in what gets written ------------------------------------

    # Fixed (was a KNOWN BUG: a typo cleared the stored coordinate and the batch still ended "applied").
    # More box cases are in test_pipeline_bbox.py.
    def test_non_numeric_bbox_value_does_not_clear_the_box(self):
        beetle = make_beetle(bbox="validated")

        batch = self.run_rows(download_row(beetle, bbox_x="0,3"))

        self.assertFailed(batch, "Row 2: bbox_x '0,3' is not a number")
        self.assertEqual(fresh(beetle).bbox_x, 0.1)

    def test_row_counts_are_saved_on_the_batch(self):
        changed, unchanged = make_beetle(collection_country="USA"), make_beetle()

        batch = self.run_rows(download_row(changed, collection_country="Peru"), download_row(unchanged))

        self.assertEqual((batch.rows_total, batch.rows_matched, batch.rows_changed), (2, 2, 1))

    def test_changed_record_records_who_updated_it_and_why(self):
        beetle = make_beetle(collection_country="USA")

        self.run_rows({**download_row(beetle, collection_country="Peru"), "update_notes": "fixed country"})

        beetle = fresh(beetle)
        self.assertEqual((beetle.last_updated_by, beetle.update_notes), (self.staff, "fixed country"))
