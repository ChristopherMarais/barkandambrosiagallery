from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.core.files.base import ContentFile

from beetlesgallery.beetles_app.models import Beetles, UpdateBatch, ImageAsset

import uuid as _uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime
import math
import io
import csv

try:
    import pandas as pd
except ImportError:
    pd = None


# ---- Field Separation ----
IMAGE_FIELDS = {
    "image_institution", "photographer", "image_email", "photo_usage_statement",
    "resolution_in_ppmm", "image_notes", "image_date_taken", "image_has_multiple_individuals",
    "is_validated"
}

BEETLE_FIELDS = {
    "alternative_id", "aspect", "depicts_specimen", "depicts_valid_name_id",
    "depicts_described_name_id", "depicts_name_verbatim", "collection_country",
    "collection_stateProvince", "specimen_sex", "specimen_type_status", "specimen_notes",
    "bbox_x", "bbox_y", "bbox_width", "bbox_height", "bbox_is_validated"
}

UPDATE_ALLOWED_FIELDS = list(IMAGE_FIELDS | BEETLE_FIELDS)
UPDATE_REQUIRED_COLS = {"record_id"} | set(UPDATE_ALLOWED_FIELDS)
UPDATE_OPTIONAL_COLS = {"update_notes"}
UPDATE_IGNORED_COLS = {
    "image_id", "taxonomy_scientific_name", "taxonomy_subfamily", 
    "taxonomy_tribe", "taxonomy_genus", "taxonomy_species"
}

# ---- Coercion helpers ----
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

def _to_decimal_12_4(v):
    v = _none(v)
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        try:
            d = Decimal(str(float(v)))
        except Exception:
            return None
    d = d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    digits_only = str(d).replace("-", "").replace(".", "")
    if len(digits_only) > 12:
        return None
    return d

def _to_date(v):
    v = _none(v)
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return v.date()  # pandas Timestamp
    except Exception:
        return None

def _normalize_sex(v):
    v = _none(v)
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"m", "male"}:
        return "m"
    if s in {"f", "female"}:
        return "f"
    # Reject anything else
    raise ValueError(f"Invalid sex '{v}'. Allowed: M, Male, F, Female.")

def _to_bool(v):
    v = _none(v)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "t", "yes", "y"}:
        return True
    if s in {"0", "false", "f", "no", "n"}:
        return False
    return None

def _is_blank(v):
    s = "" if v is None else str(v).strip()
    return s == "" or s.lower() == "nan"


class Command(BaseCommand):
    help = "Validate UpdateBatch XLSX and compute dry-run diffs. Sets status to VALIDATED or REJECTED. No DB updates are made."

    def add_arguments(self, parser):
        parser.add_argument("--id", help="Validate a specific UpdateBatch UUID. If omitted, validates all with status='staging'.")
        parser.add_argument("--dry-run", action="store_true", help="Run checks and print results, but do not change batch status or files.")

    def handle(self, *args, **opts):
        if pd is None:
            raise CommandError("pandas is required. Install with: pip install pandas openpyxl")

        batch_id = opts.get("id")
        dry_run = bool(opts.get("dry_run"))

        if batch_id:
            qs = UpdateBatch.objects.filter(id=batch_id)
        else:
            qs = UpdateBatch.objects.filter(status=UpdateBatch.Status.STAGING)

        if not qs.exists():
            self.stdout.write("No update batches to validate.")
            return

        for batch in qs.order_by("created_at"):
            self._validate_one(batch, dry_run=dry_run)

    def _validate_one(self, batch: UpdateBatch, dry_run: bool) -> None:
        self.stdout.write(f"Validating UpdateBatch {batch.id} ({batch.original_filename})")
        if not dry_run:
            batch.mark_validating()

        errors = []

        try:
            df = pd.read_csv(batch.file.path)
            df.columns = [str(c).strip() for c in df.columns]
        except Exception as e:
            errors.append(f"Cannot open CSV: {e}")
            return self._finalize(batch, errors, dry_run)

        # UI Compatibility (Merge 'link_image_uuid' into 'image_id')
        if "link_image_uuid" in df.columns and "image_id" not in df.columns:
            df["image_id"] = df["link_image_uuid"]

        # Header checks
        cols = set(df.columns)
        if "record_id" not in cols and "image_id" not in cols:
            errors.append("CSV must contain either 'record_id' or 'image_id' column.")
            return self._finalize(batch, errors, dry_run)

        extras = cols - (set(UPDATE_ALLOWED_FIELDS) | UPDATE_OPTIONAL_COLS | UPDATE_IGNORED_COLS | {"record_id", "link_image_uuid"})
        if extras:
            errors.append(f"Unexpected extra columns: {sorted(extras)}")

        if errors:
            return self._finalize(batch, errors, dry_run)

        # Clean IDs (Destroy Pandas "nan")
        if "record_id" in df.columns:
            df["record_id"] = df["record_id"].astype(str).replace(r"^(?i)nan$", "", regex=True).str.strip()
        else:
            df["record_id"] = ""
            
        if "image_id" in df.columns:
            df["image_id"] = df["image_id"].astype(str).replace(r"^(?i)nan$", "", regex=True).str.strip()
        else:
            df["image_id"] = ""

        record_ids = [r for r in df["record_id"].unique() if r and r.lower() != "new"]
        image_ids = [i for i in df["image_id"].unique() if i and i.lower() != "new"]

        # Pre-fetch Maps to avoid N+1 Queries
        from beetlesgallery.beetles_app.models import Taxon, Beetles, ImageAsset
        beetles_map = {str(b.id): b for b in Beetles.objects.filter(id__in=record_ids).select_related('image_asset')}
        image_map = {str(img.id): img for img in ImageAsset.objects.filter(id__in=image_ids)}

        latest_taxon = Taxon.objects.order_by("-updated_at").first()
        species_version = latest_taxon.updated_at.isoformat() if latest_taxon else "unknown"
        species_label = "Database Managed (v2.0)"

        # Taxonomy Check
        candidate_ids = {
            str(v).strip()
            for v in df.get("depicts_valid_name_id", pd.Series(dtype=str)).tolist()
            if not _is_blank(v)
        }
        valid_taxa_set = set(Taxon.objects.filter(valid_species_id__in=candidate_ids).values_list('valid_species_id', flat=True)) if candidate_ids else set()

        row_missing = []
        if "depicts_valid_name_id" in df.columns:
            for i, v in df["depicts_valid_name_id"].items():
                if _is_blank(v): continue
                vid = str(v).strip()
                if vid not in valid_taxa_set:
                    row_missing.append((i + 2, vid))
        # --- NEW CODE ENDS HERE ---

        if row_missing:
            examples = ", ".join([f"row {r}: '{val}'" for r, val in row_missing[:10]])
            errors.append(f"'depicts_valid_name_id' has values not found in valid_species: {examples}")
            return self._finalize(batch, errors, dry_run)

        # Diff computation
        rows_matched = 0
        rows_changed = 0
        rows_failed = 0
        per_row = []

        # Length Limits
        MAXLEN = {
            "alternative_id": 255, 
            "image_institution": 255, 
            "photographer": 255,
            "image_email": 254, 
            "aspect": 100, 
            "depicts_specimen": 255,
            "depicts_valid_name_id": 255, 
            "depicts_described_name_id": 255,
            "depicts_name_verbatim": 255, 
            "collection_country": 100,
            "collection_stateProvince": 100, 
            "specimen_sex": 50,
            "specimen_type_status": 100, 
        }

        def enforce_len(name, val):
            if val is None: return
            s = str(val).strip()
            limit = MAXLEN.get(name)
            if limit and len(s) > limit:
                raise ValueError(f"{name} exceeds max length {limit} (got {len(s)})")

        for i, row in df.iterrows():
            excel_row = i + 2
            rid = row.get("record_id", "")
            iid = row.get("image_id", "")
            
            b = None
            is_new = False
            
            # 1. Update Existing ROI
            if rid and rid.lower() != "new":
                b = beetles_map.get(rid)
                if not b:
                    rows_failed += 1
                    per_row.append({"record_id": rid, "status": "failed", "error_message": f"Row {excel_row}: record_id '{rid}' not found"})
                    continue
            
            # 2. Create New ROI
            elif iid:
                img = image_map.get(iid)
                if not img:
                    rows_failed += 1
                    per_row.append({"record_id": rid, "status": "failed", "error_message": f"Row {excel_row}: image_id '{iid}' not found"})
                    continue
                is_new = True
                b = Beetles(image_asset=img) # Temp object for diffing
            
            # 3. Invalid State
            else:
                rows_failed += 1
                per_row.append({"record_id": "", "status": "failed", "error_message": f"Row {excel_row}: Must provide 'record_id' or 'image_id'"})
                continue

            rows_matched += 1

            try:
                # Build proposed values
                proposed = {}
                for fname in UPDATE_ALLOWED_FIELDS:
                    v = row.get(fname)
                    if fname == "resolution_in_ppmm": v = _to_decimal_12_4(v)
                    elif fname == "image_date_taken": v = _to_date(v)
                    elif fname == "specimen_sex": v = _normalize_sex(v)
                    elif fname in ["image_has_multiple_individuals", "is_validated", "bbox_is_validated"]: 
                        v = _to_bool(v)
                    elif fname in ["bbox_x", "bbox_y", "bbox_width", "bbox_height"]: 
                        v = _to_float(v)
                    else: 
                        v = _none(v)

                # Diff against current DB values
                changed_fields = []
                for fname, new_val in proposed.items():
                    # ROUTING LOGIC: Check appropriate model
                    if fname in IMAGE_FIELDS:
                        if b.image_asset:
                            old_val = getattr(b.image_asset, fname)
                        else:
                            old_val = None
                    else:
                        old_val = getattr(b, fname)

                    if old_val != new_val:
                        changed_fields.append(fname)

                per_row.append({
                    "record_id": rid,
                    "excel_row": excel_row,
                    "status": "changed" if changed_fields else "unchanged",
                    "changed_fields": ", ".join(changed_fields),
                    "error_message": "",
                })

                if changed_fields:
                    rows_changed += 1

            except Exception as e:
                rows_failed += 1
                per_row.append({
                    "record_id": rid,
                    "status": "failed",
                    "changed_fields": "",
                    "error_message": f"Row {excel_row}: {e}",
                })
                continue

        rows_unchanged = max(0, rows_matched - rows_changed)

        # Write Report
        report_buf = io.StringIO()
        w = csv.writer(report_buf)
        w.writerow(["excel_row", "record_id", "status", "changed_fields", "error_message"])
        for r in per_row:
            w.writerow([r.get("excel_row",""), r.get("record_id",""), r.get("status",""), r.get("changed_fields",""), r.get("error_message","")])
        report_bytes = report_buf.getvalue().encode("utf-8-sig")

        if not dry_run:
            # We save=True here to ensure the file path is committed to the DB
            # before we potentially mark it as rejected in _finalize
            batch.report_file.save(f"{batch.id}_report.csv", ContentFile(report_bytes), save=True)

        if rows_failed > 0:
            errors.append(f"{rows_failed} row(s) failed validation. See report.")
            return self._finalize(batch, errors, dry_run)

        if errors:
            return self._finalize(batch, errors, dry_run)

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"[DRY-RUN] VALIDATE: {rows_changed} changed, {rows_unchanged} unchanged."))
            return

        with transaction.atomic():
            batch.rows_total = len(df)
            batch.rows_matched = rows_matched
            batch.rows_changed = rows_changed
            batch.rows_unchanged = rows_unchanged
            batch.rows_failed = rows_failed
            batch.species_version_at_validation = species_version or ""
            batch.species_label_at_validation = species_label or ""
            batch.report_file.save(f"{batch.id}.csv", ContentFile(report_bytes), save=False)
            batch.status = UpdateBatch.Status.VALIDATED
            batch.validated_at = timezone.now()
            batch.save()

        self.stdout.write(self.style.SUCCESS(f"VALIDATED {batch.id}"))

    def _finalize(self, batch: UpdateBatch, errors, dry_run: bool):
        reason = "; ".join(map(str, errors))[:2000]
        if dry_run:
            self.stdout.write(self.style.WARNING(f"[DRY-RUN] REJECT: {reason}"))
            return
        batch.mark_rejected_and_move(reason)
        self.stderr.write(self.style.ERROR(f"REJECTED {batch.id}: {reason}"))