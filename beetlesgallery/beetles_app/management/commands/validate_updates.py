from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.core.files.base import ContentFile

from beetlesgallery.beetles_app.models import Beetles, UpdateBatch, ImageAsset
from beetlesgallery.beetles_app import species_ref

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
    "image_institution",
    "photographer",
    "image_email",
    "photo_usage_statement",
    "resolution_in_ppmm",
    "image_notes",
    "image_date_taken",
    "image_has_multiple_individuals",
}

BEETLE_FIELDS = {
    "alternative_id",
    "aspect",
    "depicts_specimen",
    "depicts_valid_name_id",
    "depicts_described_name_id",
    "depicts_name_verbatim",
    "collection_country",
    "collection_stateProvince",
    "specimen_sex",
    "specimen_type_status",
    "specimen_notes",
}

UPDATE_ALLOWED_FIELDS = list(IMAGE_FIELDS | BEETLE_FIELDS)

UPDATE_REQUIRED_COLS = {"record_id"} | set(UPDATE_ALLOWED_FIELDS)
UPDATE_OPTIONAL_COLS = {"update_notes"}


# ---- Coercion helpers ----
def _none(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v

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
    return None

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
            df = pd.read_excel(batch.file.path)
            df.columns = [str(c).strip() for c in df.columns]
        except Exception as e:
            errors.append(f"Cannot open workbook: {e}")
            return self._finalize(batch, errors, dry_run)

        # Header checks
        cols = set(df.columns)
        missing = UPDATE_REQUIRED_COLS - cols
        extras = cols - (UPDATE_REQUIRED_COLS | UPDATE_OPTIONAL_COLS)
        if missing:
            errors.append(f"Missing required columns: {sorted(missing)}")
        if extras:
            errors.append(f"Unexpected extra columns: {sorted(extras)}")

        if errors:
            return self._finalize(batch, errors, dry_run)

        # Light UUID sanity check
        bad_uuid_examples = []
        for s in df["record_id"].dropna().astype(str).head(20):
            try:
                _uuid.UUID(str(s).strip())
            except Exception:
                bad_uuid_examples.append(s)
                if len(bad_uuid_examples) >= 3:
                    break
        if bad_uuid_examples:
            errors.append(f"'record_id' contains non-UUID values (examples: {bad_uuid_examples[:3]})")
            return self._finalize(batch, errors, dry_run)

        # Pin valid_species version
        try:
            species_version = species_ref.get_version() or ""
            species_label = species_ref.get_label() or ""
        except Exception:
            errors.append("valid_species reference is unavailable; cannot validate depicts_valid_name_id.")
            return self._finalize(batch, errors, dry_run)

        # Build quick index of beetles (prefetching image_asset)
        record_ids = [str(x).strip() for x in df["record_id"].tolist() if _none(x) is not None]
        
        # Duplicate record_id check
        from collections import Counter
        dups = [rid for rid, c in Counter(record_ids).items() if c > 1]
        if dups:
            examples = ", ".join(dups[:5])
            errors.append(f"Duplicate record_id values detected ({len(dups)}). Examples: {examples}.")
            return self._finalize(batch, errors, dry_run)

        # Fetch objects with relation
        beetles_map = {
            str(b.id): b
            for b in Beetles.objects.filter(id__in=record_ids).select_related('image_asset')
        }

        missing_ids = [rid for rid in record_ids if rid not in beetles_map]
        if missing_ids:
            examples = ", ".join(missing_ids[:5])
            errors.append(f"{len(missing_ids)} record_id value(s) do not exist. Examples: {examples}.")
            return self._finalize(batch, errors, dry_run)

        # Taxonomy Check
        candidate_ids = {
            str(v).strip()
            for v in df["depicts_valid_name_id"].tolist()
            if not _is_blank(v)
        }
        ref_map = species_ref.bulk_lookup(candidate_ids) if candidate_ids else {}

        row_missing = []
        if "depicts_valid_name_id" in df.columns:
            for i, v in df["depicts_valid_name_id"].items():
                if _is_blank(v): continue
                vid = str(v).strip()
                if vid not in ref_map:
                    row_missing.append((i + 2, vid))

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
            rid = str(row.get("record_id", "")).strip()
            if not rid:
                rows_failed += 1
                per_row.append({"record_id": "", "status": "failed", "error_message": f"Row {excel_row}: missing record_id"})
                continue

            b = beetles_map.get(rid)
            if not b:
                rows_failed += 1
                per_row.append({"record_id": rid, "status": "failed", "error_message": f"Row {excel_row}: record_id not found"})
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
                    elif fname == "image_has_multiple_individuals": v = _to_bool(v)
                    else: v = _none(v)

                    enforce_len(fname, v)
                    proposed[fname] = v

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
        report_bytes = report_buf.getvalue().encode("utf-8")

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