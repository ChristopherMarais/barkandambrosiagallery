import os
import sys
import json
import uuid
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.core.files.base import ContentFile

from beetlesgallery.beetles_app.models import UpdateBatch, Beetles, ImageAsset, Taxon
from beetlesgallery.beetles_app.areas import BULK_VALIDATE, has_area
from beetlesgallery.beetles_app.bbox_rules import BOX_COLUMNS, is_blank, parse_box
from beetlesgallery.beetles_app.csv_columns import modern_columns

try:
    import pandas as pd
except ImportError:
    pd = None

# --- Field Mapping ---
# Fields that live on the ImageAsset model
IMAGE_FIELDS = {
    "image_institution", "photographer", "image_email", 
    "photo_usage_statement", "image_date_taken", "image_notes",
    "image_has_multiple_individuals", "resolution_in_ppmm",
    "is_validated"
}

# Fields that live on the Beetles model
BEETLE_FIELDS = {
    "alias_id", "aspect", "depicts_specimen", 
    "depicts_valid_name_id", "depicts_described_name_id", 
    "depicts_name_verbatim", "collection_country", 
    "collection_stateProvince", "specimen_sex", 
    "specimen_type_status", "specimen_notes",
    "bbox_x", "bbox_y", "bbox_width", "bbox_height", "bbox_label",
    "bbox_is_validated", "label_source", "label_source_detail",
}
# label_source: an identification tier (key or label, any case, or an older source name); blank is No ID
from beetlesgallery.beetles_app.identification import parse_tier
UPDATE_IGNORED_COLS = {
    "image_id", "taxonomy_scientific_name", "taxonomy_subfamily", 
    "taxonomy_tribe", "taxonomy_genus", "taxonomy_species",
}   # update_notes is read on its own: why the record changed

# -----------------------
# Helpers
# -----------------------
def _none(v):
    if v is None: return None
    if isinstance(v, float) and math.isnan(v): return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s == "" or s == "nan": return None
    return v

def _same_cell(new, current):
    """Does this CSV cell hold what is already stored (None for blank)? Used to leave old data alone."""
    if is_blank(new):
        return current is None
    if current is None:
        return False
    try:
        return abs(float(str(new).strip()) - float(current)) < 1e-9
    except ValueError:
        return False


def _to_float(v):
    v = _none(v)
    if v is None: return None
    try: return float(v)
    except: return None

def _to_bool(v):
    v = _none(v)
    if v is None: return None
    if isinstance(v, bool): return v
    s = str(v).strip().lower()
    if s in {"1", "true", "t", "yes", "y"}: return True
    if s in {"0", "false", "f", "no", "n"}: return False
    return None

def _to_date(v):
    v = _none(v)
    if v is None: return None
    if isinstance(v, datetime): return v.date()
    if isinstance(v, date): return v
    # pd.read_csv leaves dates as text; [:10] drops a time part such as "2024-05-17 10:30:00".
    if isinstance(v, str):
        try: return date.fromisoformat(v.strip()[:10])
        except ValueError: return None
    # Pandas timestamp fallback
    try: return v.date() 
    except: return None

def _to_decimal(v):
    v = _none(v)
    if v is None: return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except:
        return None

class Command(BaseCommand):
    help = "Process a single UpdateBatch (validate + apply) with split schema support."

    def add_arguments(self, parser):
        parser.add_argument("--id", required=True, help="UpdateBatch UUID")

    def handle(self, *args, **opts):
        batch_id = opts["id"]
        try:
            batch = UpdateBatch.objects.get(id=batch_id)
        except UpdateBatch.DoesNotExist:
            raise CommandError(f"Batch {batch_id} not found.")

        if pd is None:
            self._fail(batch, "Pandas not installed.")
            return

        self.stdout.write(f"Processing UpdateBatch {batch.id}...")
        batch.mark_validating()

        try:
            df = pd.read_csv(batch.file.path)
            df.columns = modern_columns(df.columns)
        except Exception as e:
            self._fail(batch, f"Cannot read CSV: {e}")
            return

        # --- VALIDATION PHASE ---
        errors = []
        updates_plan = [] # List of (beetle_obj, beetle_updates_dict, image_updates_dict, is_new)

        # We support two modes:
        # 1. Update existing: 'record_id' is present.
        # 2. Create new: 'record_id' is "NEW" (or empty) AND 'link_image_uuid' is present.
        
        row_count = len(df)
        batch.rows_total = row_count
        batch.save(update_fields=["rows_total"])   # the later saves name only their own fields (#350)
        # Validating or un-validating through a spreadsheet is bulk validation: its own permission. Batches with
        # no uploader were made by the owner on the server.
        may_validate = batch.uploaded_by is None or has_area(batch.uploaded_by, BULK_VALIDATE)
        
        for i, row in df.iterrows():
            row_num = i + 2
            
            raw_id = str(row.get("record_id", "")).strip().lower()
            if raw_id == "nan": raw_id = ""
            
            # FIX 2: Check BOTH 'image_id' (from downloads) and 'link_image_uuid' (from UI)
            target_image_id = str(row.get("image_id", row.get("link_image_uuid", ""))).strip().lower()
            if target_image_id == "nan": target_image_id = ""
            
            beetle_obj = None
            is_new = False

            # 1. Resolve Target
            if raw_id and raw_id != "new":
                try:
                    # uuid.UUID: an id that isn't a UUID at all is "not found" too, not a crash (#350)
                    beetle_obj = Beetles.objects.get(pk=uuid.UUID(raw_id))
                except (Beetles.DoesNotExist, ValueError):
                    errors.append(f"Row {row_num}: Record ID '{raw_id}' not found.")
                    continue
            elif target_image_id:
                # CREATION MODE
                try:
                    image_asset = ImageAsset.objects.get(pk=uuid.UUID(target_image_id))
                    beetle_obj = Beetles(image_asset=image_asset)
                    is_new = True
                except (ImageAsset.DoesNotExist, ValueError):
                    errors.append(f"Row {row_num}: Image ID '{target_image_id}' not found.")
                    continue
            else:
                errors.append(f"Row {row_num}: Must provide valid 'record_id' or 'image_id'.")
                continue

            # 2. Prepare Data Dictionaries
            b_updates = {}
            i_updates = {}

            # Iterate columns provided in the Excel
            for col in df.columns:
                if col in ["record_id", "image_id", "link_image_uuid", "update_notes"] or col in UPDATE_IGNORED_COLS: 
                    continue
                
                val = row[col]
                
                # Assign to correct bucket
                if col in BEETLE_FIELDS:
                    b_updates[col] = val
                elif col in IMAGE_FIELDS:
                    # Update image fields
                    i_updates[col] = val
            
            # A validation flag must be a readable true/false (blank leaves it alone). Otherwise a typo
            # would either be ignored or reach the database as a raw error.
            unreadable = [
                (col, raw) for col, raw in (
                    ("bbox_is_validated", b_updates.get("bbox_is_validated")),
                    ("is_validated", i_updates.get("is_validated")),
                ) if not is_blank(raw) and _to_bool(raw) is None
            ]
            if unreadable:
                col, raw = unreadable[0]
                errors.append(f"Row {row_num}: {col} '{raw}' must be true or false.")
                continue

            if "label_source" in b_updates and not is_blank(b_updates["label_source"]):
                source, ok = parse_tier(b_updates["label_source"])
                if not ok:
                    errors.append(f"Row {row_num}: label_source '{b_updates['label_source']}' must be one of "
                                  f"{', '.join(k for k, _ in Beetles.LabelSource.choices)} (or blank for No ID).")
                    continue
                b_updates["label_source"] = source

            # 3. Bounding box rules (same as the annotator API). Only what this row changes is checked,
            # so old data that predates the rules does not block unrelated edits.
            current_cells = [None if is_new else getattr(beetle_obj, c) for c in BOX_COLUMNS]
            final_cells = [b_updates.get(c, cur) for c, cur in zip(BOX_COLUMNS, current_cells)]
            box_touched = any(c in b_updates for c in BOX_COLUMNS)
            box_changed = is_new or (box_touched and not all(
                _same_cell(new, cur) for new, cur in zip(final_cells, current_cells)
            ))
            if box_touched and box_changed:
                final_box, box_error = parse_box(*final_cells)
                if box_error:
                    errors.append(f"Row {row_num}: {box_error}.")
                    continue
            else:
                stored_box, stored_error = parse_box(*current_cells)
                final_box = None if stored_error else stored_box

            if "bbox_is_validated" in b_updates:
                wants_validated = _to_bool(b_updates["bbox_is_validated"])
                was_validated = False if is_new else bool(beetle_obj.bbox_is_validated)
                if wants_validated and not was_validated and final_box is None:
                    errors.append(f"Row {row_num}: bbox_is_validated is true but the row has no box.")
                    continue

            if not may_validate:
                roi_wants = _to_bool(b_updates.get("bbox_is_validated"))
                image_wants = _to_bool(i_updates.get("is_validated"))
                image_was = bool(beetle_obj.image_asset.is_validated) if beetle_obj.image_asset_id else False
                changes = [col for col, wants, was in (
                    ("bbox_is_validated", roi_wants, False if is_new else bool(beetle_obj.bbox_is_validated)),
                    ("is_validated", image_wants, image_was),
                ) if wants is not None and wants != was and not (col == "bbox_is_validated" and final_box is None)]
                if changes:
                    errors.append(f"Row {row_num}: changing {' and '.join(changes)} needs the Bulk validate permission. "
                                  "Validate on the annotation page, or leave the cell as downloaded.")
                    continue

            notes = _none(row.get("update_notes"))
            updates_plan.append({
                "notes": None if is_blank(notes) else str(notes).strip(),   # why (optional column)
                "beetle": beetle_obj,
                "b_data": b_updates,
                "i_data": i_updates,
                "is_new": is_new,
                "row_num": row_num,
                "final_box": final_box,
            })

        # A new box on an image whose record has no box yet would leave that record behind, unboxed,
        # next to a duplicate. The box belongs on that record (use its record_id), as in the annotator.
        boxed_here = {p["beetle"].pk for p in updates_plan if not p["is_new"] and p["final_box"]}
        unboxed_by_image = {}
        for plan in updates_plan:
            if not (plan["is_new"] and plan["final_box"]):
                continue
            image_id = plan["beetle"].image_asset_id
            if image_id not in unboxed_by_image:
                unboxed_by_image[image_id] = list(
                    Beetles.objects.filter(image_asset_id=image_id, is_deleted=False, bbox_x__isnull=True)
                    .exclude(pk__in=boxed_here).values_list("id", flat=True)[:1]
                )
            if unboxed_by_image[image_id]:
                errors.append(
                    f"Row {plan['row_num']}: image {image_id} already has a record without a box "
                    f"(record_id {unboxed_by_image[image_id][0]}). Put the box on that record instead "
                    "of adding a new one; use 'NEW' only for an additional box."
                )

        if errors:
            self._fail(batch, "\n".join(errors[:20])) # Limit error msg size
            return

        # --- APPLY PHASE ---
        batch.rows_matched = len(updates_plan)
        changed_count = 0
        
        try:
            # Pre-fetch taxon map to memory for fast foreign key linking during import
            taxon_map = {t.valid_species_id: t for t in Taxon.objects.all()}
            
            with transaction.atomic():
                images_to_unvalidate = {}
                # An image's validated state as it was when the batch began. Saving a validated ROI makes
                # the image validated (models.update_image_asset_validation_status), so the row's
                # is_validated cell (usually the value at download time) must be compared with this,
                # not with the live value, or it would undo the validation this same batch just made.
                start_image_validated = dict(
                    ImageAsset.objects.filter(
                        pk__in={p["beetle"].image_asset_id for p in updates_plan if p["beetle"].image_asset_id}
                    ).values_list("id", "is_validated")
                )
                for plan in updates_plan:
                    obj = plan["beetle"]
                    b_data = plan["b_data"]
                    i_data = plan["i_data"]
                    
                    # 1. Update/Create Beetle
                    has_b_change = False
                    had_box = obj.bbox_x is not None
                    was_validated = bool(obj.bbox_is_validated)
                    for k, v in b_data.items():
                        # Type conversion
                        if k == "specimen_sex": 
                            val = _none(v)
                        elif k in ["bbox_x", "bbox_y", "bbox_width", "bbox_height"]: 
                            val = _to_float(v)
                        elif k == "bbox_is_validated":
                            val = _to_bool(v)
                            if val is None:
                                continue  # blank: leave it (a new record starts unvalidated)
                            if val is False:
                                obj.bbox_validated_by = None
                                obj.bbox_validated_at = None
                        elif k in ("label_source", "label_source_detail"):
                            val = "" if is_blank(v) else str(v).strip()[:255]   # these are never NULL
                        elif k in ["depicts_valid_name_id", "depicts_described_name_id"]:
                            val = _none(v)
                            if val is not None:
                                # Force to string and strip Pandas floating '.0' if present
                                val = str(val).replace('.0', '').strip()
                        else: 
                            val = _none(v)
                        
                        current = getattr(obj, k, None)
                        if str(val) != str(current):
                            setattr(obj, k, val)
                            has_b_change = True
                            
                            # Hydrate the relational Taxon Foreign Key
                            if k == "depicts_valid_name_id":
                                obj.taxon = taxon_map.get(val) if val else None
                    
                    # Who changed the record and why (#350); a blank update_notes leaves the old note
                    if plan["notes"] is not None and plan["notes"] != (obj.update_notes or ""):
                        obj.update_notes = plan["notes"]
                        has_b_change = True
                    if plan["is_new"] or has_b_change:
                        obj.last_updated_by = batch.uploaded_by

                    # Audit trail for boxes, as the annotator API keeps it.
                    now = timezone.now()
                    has_box = obj.bbox_x is not None
                    if has_box and not had_box:
                        obj.bbox_created_by, obj.bbox_created_at = batch.uploaded_by, now
                    elif had_box and not has_box:
                        obj.bbox_created_by = obj.bbox_created_at = None
                        obj.bbox_validated_by = obj.bbox_validated_at = None
                        obj.bbox_is_validated = False
                    if obj.bbox_is_validated and not was_validated:
                        obj.bbox_validated_by, obj.bbox_validated_at = batch.uploaded_by, now

                    if plan["is_new"]:
                        obj.save() # Insert
                        # History attribution
                        h = obj.history.first()
                        if h:
                            h.history_user = batch.uploaded_by
                            h.history_change_reason = f"Created via Batch {batch.id}"
                            h.save()
                    elif has_b_change:
                        obj.save()
                        # History attribution
                        h = obj.history.first()
                        if h:
                            h.history_user = batch.uploaded_by
                            h.history_change_reason = f"Updated via Batch {batch.id}"
                            h.save()

                    # 2. Update ImageAsset
                    # We update image fields if provided. Note: this affects ALL specimens linked to this image.
                    has_i_change = False
                    if i_data and obj.image_asset:
                        img = obj.image_asset
                        for k, v in i_data.items():
                            if k == "image_has_multiple_individuals": val = _to_bool(v)
                            elif k == "is_validated":
                                val = _to_bool(v)
                                if val is None:
                                    continue  # blank: leave it
                                # Devalidating a validated image cascades to all its ROIs (applied after the loop)
                                if val is False and start_image_validated.get(img.id, img.is_validated):
                                    images_to_unvalidate[img.id] = img
                            elif k == "resolution_in_ppmm": val = _to_decimal(v)
                            elif k == "image_date_taken": val = _to_date(v)
                            else: val = _none(v)

                            current = getattr(img, k, None)
                            if k == "is_validated":
                                current = start_image_validated.get(img.id, current)
                            if str(val) != str(current):
                                setattr(img, k, val)
                                has_i_change = True
                        
                        if has_i_change:
                            img.last_updated_by = batch.uploaded_by
                            img.save()

                    if plan["is_new"] or has_b_change or has_i_change:
                        changed_count += 1

                # Run after all ROI saves so stale in-memory ROIs can't re-validate the image
                for img in images_to_unvalidate.values():
                    img.unvalidate(user=batch.uploaded_by)

            batch.rows_changed = changed_count
            batch.save(update_fields=["rows_matched", "rows_changed"])
            batch.mark_applied_and_archive()
            self.stdout.write(self.style.SUCCESS(f"Batch {batch.id} applied successfully."))

        except Exception as e:
            self._fail(batch, f"Database error during apply: {e}")

    def _fail(self, batch, reason):
        self.stderr.write(self.style.ERROR(reason))
        batch.mark_apply_failed(reason)