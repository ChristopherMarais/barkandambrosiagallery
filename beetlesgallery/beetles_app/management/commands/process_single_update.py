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
    "alternative_id", "aspect", "depicts_specimen", 
    "depicts_valid_name_id", "depicts_described_name_id", 
    "depicts_name_verbatim", "collection_country", 
    "collection_stateProvince", "specimen_sex", 
    "specimen_type_status", "specimen_notes",
    "bbox_x", "bbox_y", "bbox_width", "bbox_height", "bbox_label",
    "bbox_is_validated"
}
UPDATE_IGNORED_COLS = {
    "image_id", "taxonomy_scientific_name", "taxonomy_subfamily", 
    "taxonomy_tribe", "taxonomy_genus", "taxonomy_species", "update_notes"
}

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
            df.columns = [str(c).strip() for c in df.columns]
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
                    beetle_obj = Beetles.objects.get(pk=raw_id)
                except (Beetles.DoesNotExist, ValueError):
                    errors.append(f"Row {row_num}: Record ID '{raw_id}' not found.")
                    continue
            elif target_image_id:
                # CREATION MODE
                try:
                    image_asset = ImageAsset.objects.get(pk=target_image_id)
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
            
            updates_plan.append({
                "beetle": beetle_obj,
                "b_data": b_updates,
                "i_data": i_updates,
                "is_new": is_new,
                "row_num": row_num
            })

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
                for plan in updates_plan:
                    obj = plan["beetle"]
                    b_data = plan["b_data"]
                    i_data = plan["i_data"]
                    
                    # 1. Update/Create Beetle
                    has_b_change = False
                    for k, v in b_data.items():
                        # Type conversion
                        if k == "specimen_sex": 
                            val = _none(v)
                        elif k in ["bbox_x", "bbox_y", "bbox_width", "bbox_height"]: 
                            val = _to_float(v)
                        elif k == "bbox_is_validated":
                            val = _to_bool(v)
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
                    
                    if plan["is_new"]:
                        obj.save() # Insert
                        changed_count += 1
                        # History attribution
                        h = obj.history.first()
                        if h:
                            h.history_user = batch.uploaded_by
                            h.history_change_reason = f"Created via Batch {batch.id}"
                            h.save()
                    elif has_b_change:
                        obj.save()
                        changed_count += 1
                        # History attribution
                        h = obj.history.first()
                        if h:
                            h.history_user = batch.uploaded_by
                            h.history_change_reason = f"Updated via Batch {batch.id}"
                            h.save()

                    # 2. Update ImageAsset
                    # We update image fields if provided. Note: this affects ALL specimens linked to this image.
                    if i_data and obj.image_asset:
                        img = obj.image_asset
                        has_i_change = False
                        for k, v in i_data.items():
                            if k == "image_has_multiple_individuals": val = _to_bool(v)
                            elif k == "is_validated": val = _to_bool(v)
                            elif k == "resolution_in_ppmm": val = _to_decimal(v)
                            elif k == "image_date_taken": val = _to_date(v)
                            else: val = _none(v)

                            current = getattr(img, k, None)
                            if str(val) != str(current):
                                setattr(img, k, val)
                                has_i_change = True
                        
                        if has_i_change:
                            img.save()

            batch.rows_changed = changed_count
            batch.mark_applied_and_archive()
            self.stdout.write(self.style.SUCCESS(f"Batch {batch.id} applied successfully."))

        except Exception as e:
            self._fail(batch, f"Database error during apply: {e}")

    def _fail(self, batch, reason):
        self.stderr.write(self.style.ERROR(reason))
        batch.mark_apply_failed(reason)