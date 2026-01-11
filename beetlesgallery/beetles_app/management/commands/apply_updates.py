from django.core.management.base import BaseCommand, CommandError
from django.db.utils import DataError
from django.db import transaction
from django.utils import timezone

from beetlesgallery.beetles_app.models import Beetles, UpdateBatch, ImageAsset
from beetlesgallery.beetles_app.utils import get_system_user

import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime

try:
    import pandas as pd
except ImportError:
    raise CommandError("Please install pandas and openpyxl: pip install pandas openpyxl")

# ---- Field Separation (Must match validate_updates) ----
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
REQUIRED_COLS = {"record_id"} | set(UPDATE_ALLOWED_FIELDS)
OPTIONAL_COLS = {"update_notes"}

# ---- Coercions ----
def _none(v):
    if v is None: return None
    if isinstance(v, float) and math.isnan(v): return None
    if isinstance(v, str) and v.strip() == "": return None
    return v

def _clean_str(v):
    v = _none(v)
    if v is None: return None
    s = str(v).replace("\u00a0", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
    return s if s != "" else None

def _to_decimal_12_4(v):
    v = _none(v)
    if v is None: return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        try:
            d = Decimal(str(float(v)))
        except Exception:
            return None
    d = d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    digits_only = str(d).replace("-", "").replace(".", "")
    if len(digits_only) > 12: return None
    return d

def _to_date(v):
    v = _none(v)
    if v is None: return None
    if isinstance(v, date) and not isinstance(v, datetime): return v
    if isinstance(v, datetime): return v.date()
    try: return v.date() 
    except Exception: return None

def _normalize_sex(v):
    v = _none(v)
    if v is None: return None
    s = str(v).strip().lower()
    if s in {"m", "male"}: return "m"
    if s in {"f", "female"}: return "f"
    return None

def _to_bool(v):
    v = _none(v)
    if v is None: return None
    if isinstance(v, bool): return v
    s = str(v).strip().lower()
    if s in {"1", "true", "t", "yes", "y"}: return True
    if s in {"0", "false", "f", "no", "n"}: return False
    return None

def _is_blank(v):
    s = "" if v is None else str(v).strip()
    return s == "" or s.lower() == "nan"

class Command(BaseCommand):
    help = "Apply a VALIDATED UpdateBatch to Beetles/ImageAssets."

    def add_arguments(self, parser):
        parser.add_argument("--id", help="Apply a specific UpdateBatch UUID.")
        parser.add_argument("--dry-run", action="store_true", help="Print summary, no write.")
        parser.add_argument("--force", action="store_true", help="Apply even if no changes.")

    def handle(self, *args, **opts):
        batch_id = opts.get("id")
        dry_run = bool(opts.get("dry_run"))
        force = bool(opts.get("force"))

        if batch_id:
            qs = UpdateBatch.objects.filter(id=batch_id, status=UpdateBatch.Status.VALIDATED)
        else:
            qs = UpdateBatch.objects.filter(status=UpdateBatch.Status.VALIDATED).order_by("created_at")

        batch = qs.first()
        if not batch:
            raise CommandError("No VALIDATED update batches found.")

        try:
            df = pd.read_excel(batch.file.path)
            df.columns = [str(c).strip() for c in df.columns]
        except Exception as e:
            self._fail_apply(batch, f"Cannot open workbook: {e}")
            return

        # Defensive header check
        cols = set(df.columns)
        if (REQUIRED_COLS - cols) or (cols - (REQUIRED_COLS | OPTIONAL_COLS)):
            self._fail_apply(batch, "Header mismatch during apply.")
            return

        # Prepare records
        record_ids = [str(x).strip() for x in df["record_id"].tolist() if _none(x) is not None]
        # Optimizing with select_related for images
        beetles_qs = Beetles.objects.filter(id__in=record_ids).select_related('image_asset')
        beetles_by_id = {str(b.id): b for b in beetles_qs}

        changes = []  
        rows_changed = 0

        # Max lengths
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
        def _enforce_len(name, val):
            if val is None: return
            s = str(val).strip()
            lim = MAXLEN.get(name)
            if lim and len(s) > lim:
                raise ValueError(f"{name} exceeds max length {lim}")

        # Compute Changes
        for i, row in df.iterrows():
            excel_row = i + 2
            rid = str(row.get("record_id", "")).strip()
            b_preview = beetles_by_id.get(rid)
            if not rid or not b_preview:
                self._fail_apply(batch, f"Record not found: {rid}")
                return

            proposed = {}
            for fname in UPDATE_ALLOWED_FIELDS:
                raw = row.get(fname)
                if fname == "resolution_in_ppmm": v = _to_decimal_12_4(raw)
                elif fname == "image_date_taken": v = _to_date(raw)
                elif fname == "specimen_sex": v = _normalize_sex(raw)
                elif fname == "image_has_multiple_individuals": v = _to_bool(raw)
                elif fname == "depicts_valid_name_id":
                    if _is_blank(raw): v = None
                    else: v = _clean_str(raw)
                else:
                    v = _clean_str(raw)

                if v is not None: _enforce_len(fname, v)
                proposed[fname] = v

            # Change detection with routing
            changed = {}
            for k, new_val in proposed.items():
                if k in IMAGE_FIELDS:
                    if b_preview.image_asset:
                        old_val = getattr(b_preview.image_asset, k)
                    else:
                        old_val = None
                else:
                    old_val = getattr(b_preview, k)
                
                if old_val != new_val:
                    changed[k] = new_val

            update_notes = _none(row.get("update_notes"))
            if changed:
                rows_changed += 1
                changes.append((rid, changed, update_notes, excel_row))

        if rows_changed == 0 and not force and not dry_run:
            with transaction.atomic():
                batch.status = UpdateBatch.Status.APPLIED
                batch.applied_at = timezone.now()
                batch.error_message = "Applied (no changes)."
                batch.save()
            self.stdout.write(self.style.SUCCESS(f"{batch.id}: Applied (no changes)"))
            return

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"[DRY-RUN] Would update {rows_changed} records."))
            for (rid, changed, _, _) in changes[:5]:
                self.stdout.write(f"  - {rid}: {list(changed.keys())}")
            return

        # ---- APPLY ----
        try:
            history_user = batch.uploaded_by or get_system_user("admin")
        except Exception:
            history_user = None

        try:
            with transaction.atomic():
                # Re-fetch with locks
                beetles_by_id = {str(b.id): b for b in beetles_qs.select_for_update()}
                now = timezone.now()

                for rid, changed, update_notes, excel_row in changes:
                    b = beetles_by_id.get(rid)
                    if not b: raise CommandError(f"Record {rid} disappeared.")

                    b_changes = {}
                    i_changes = {}

                    # Route fields
                    for k, v in changed.items():
                        if k in IMAGE_FIELDS:
                            i_changes[k] = v
                        else:
                            b_changes[k] = v

                    # Apply Beetle Changes
                    for k, v in b_changes.items():
                        setattr(b, k, v)
                    
                    # Apply Image Changes
                    if i_changes and b.image_asset:
                        for k, v in i_changes.items():
                            setattr(b.image_asset, k, v)
                        try:
                            b.image_asset.save()
                        except Exception as e:
                            raise CommandError(f"Row {excel_row}: Image update failed: {e}")

                    # Save Beetle
                    b.last_updated_at = now
                    b.last_updated_by = history_user
                    if update_notes is not None:
                        b.update_notes = update_notes

                    try:
                        b.save()
                    except DataError as e:
                        raise CommandError(f"Row {excel_row} update failed (likely overflow): {e}")

                    # History
                    h = getattr(b, "history", None)
                    if h:
                        latest = h.first()
                        if latest:
                            latest.history_user = history_user
                            latest.history_change_reason = f"Batch {batch.id}; {', '.join(changed.keys())}"
                            latest.save()

                batch.status = UpdateBatch.Status.APPLIED
                batch.applied_at = timezone.now()
                batch.error_message = f"Applied {len(changes)} records."
                batch.save()

        except Exception as e:
            self._fail_apply(batch, f"Apply failed: {e}")
            return

        self.stdout.write(self.style.SUCCESS(f"APPLIED {batch.id}: {len(changes)} updated"))

    def _fail_apply(self, batch, reason):
        batch.status = UpdateBatch.Status.APPLY_FAILED
        batch.error_message = str(reason)[:2000]
        batch.save()
        self.stderr.write(self.style.ERROR(f"{batch.id}: {reason}"))