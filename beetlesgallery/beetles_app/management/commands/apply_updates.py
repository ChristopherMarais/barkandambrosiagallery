from django.core.management.base import BaseCommand, CommandError
from django.db.utils import DataError
from django.db import transaction
from django.utils import timezone

from beetlesgallery.beetles_app.models import Beetles, UpdateBatch
from beetlesgallery.beetles_app.utils import get_system_user

import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime

try:
    import pandas as pd
except ImportError:
    raise CommandError("Please install pandas and openpyxl: pip install pandas openpyxl")

# ---- Must match validator/import semantics ----
UPDATE_ALLOWED_FIELDS = [
    "alternative_id",
    "image_institution",
    "photographer",
    "image_email",
    "photo_usage_statement",
    "aspect",
    "resolution_in_ppmm",
    "image_notes",
    "image_date_taken",
    "image_has_multiple_individuals",
    "depicts_specimen",
    "depicts_valid_name_id",
    "depicts_described_name_id",
    "depicts_name_verbatim",
    "collection_country",
    "collection_stateProvince",
    "specimen_sex",
    "specimen_type_status",
    "specimen_notes",
]

REQUIRED_COLS = {"record_id"} | set(UPDATE_ALLOWED_FIELDS)
OPTIONAL_COLS = {"update_notes"}

# ---- Coercions (same as your validator) ----
def _none(v):
    if v is None: return None
    if isinstance(v, float) and math.isnan(v): return None
    if isinstance(v, str) and v.strip() == "": return None
    return v

def _clean_str(v):
    """
    Normalize strings:
      - convert to str
      - replace non-breaking spaces, newlines, tabs
      - strip surrounding whitespace
      - return None if empty after cleaning
    """
    v = _none(v)
    if v is None:
        return None
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
    if len(digits_only) > 12:
        return None
    return d

def _to_date(v):
    v = _none(v)
    if v is None: return None
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
    help = "Apply a VALIDATED UpdateBatch to Beetles (all-or-nothing overwrite of metadata fields)."

    def add_arguments(self, parser):
        parser.add_argument("--id", help="Apply a specific UpdateBatch UUID. If omitted, applies the oldest VALIDATED batch.")
        parser.add_argument("--dry-run", action="store_true", help="Parse + diff and print a summary, but do not write any changes.")
        parser.add_argument("--force", action="store_true", help="Apply even if the validator reported zero changed rows (no-op safe).")

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
            raise CommandError("No VALIDATED update batches to apply (or specified batch not VALIDATED).")

        # Load workbook (single-sheet)
        try:
            df = pd.read_excel(batch.file.path)
            df.columns = [str(c).strip() for c in df.columns]
        except Exception as e:
            self._fail_apply(batch, f"Cannot open workbook during apply: {e}")
            return

        # Guard headers (defensive; validator already enforced)
        cols = set(df.columns)
        missing = REQUIRED_COLS - cols
        extras = cols - (REQUIRED_COLS | OPTIONAL_COLS)
        if missing:
            self._fail_apply(batch, f"Apply aborted: sheet missing required columns: {sorted(missing)}")
            return
        if extras:
            self._fail_apply(batch, f"Apply aborted: sheet has unexpected extra columns: {sorted(extras)}")
            return

        # Build record map
        record_ids = [str(x).strip() for x in df["record_id"].tolist() if _none(x) is not None]
        beetles_qs = Beetles.objects.filter(id__in=record_ids)

        # Preview map (no locks) for diff/dry-run outside the transaction
        beetles_by_id = {str(b.id): b for b in beetles_qs}

        # Prepare changes (compute a second time to be source-of-truth at apply)
        changes = []  # list of (beetle_obj, dict_of_field_updates, update_notes, excel_row)
        rows_total = len(df)
        rows_matched = 0
        rows_changed = 0

        # same length limits used earlier
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
                raise ValueError(f"{name} exceeds max length {lim} (got {len(s)})")

        for i, row in df.iterrows():
            excel_row = i + 2
            rid = str(row.get("record_id", "")).strip()
            b_preview = beetles_by_id.get(rid)
            if not rid or not b_preview:
                self._fail_apply(batch, f"Apply aborted: record_id missing or not found at Excel row {excel_row}")
                return

            rows_matched += 1

            # Build proposed values with coercion
            proposed = {}
            for fname in UPDATE_ALLOWED_FIELDS:
                raw = row.get(fname)

                include_even_if_none = False  # default: None means "no change" for most fields

                if fname == "resolution_in_ppmm":
                    v = _to_decimal_12_4(raw)
                elif fname == "image_date_taken":
                    v = _to_date(raw)
                elif fname == "specimen_sex":
                    v = _normalize_sex(raw)
                elif fname == "image_has_multiple_individuals":
                    v = _to_bool(raw)
                elif fname == "depicts_valid_name_id":
                    # Special policy: blank means "clear to NULL"
                    if _is_blank(raw):
                        v = None
                        include_even_if_none = True   # we WANT to apply None for this field
                    else:
                        v = _clean_str(raw)           # non-blank stays as cleaned string
                else:
                    v = _clean_str(raw)

                # Length checks only if we actually have a string value
                if v is not None:
                    _enforce_len(fname, v)

                # For most fields, None means "no change"; for depicts_valid_name_id, None means "clear"
                if v is None and not include_even_if_none:
                    continue

                proposed[fname] = v

            # Detect change using the *preview* object
            changed = {k: v for k, v in proposed.items() if getattr(b_preview, k) != v}
            update_notes = _none(row.get("update_notes"))

            if changed:
                rows_changed += 1
                changes.append((rid, changed, update_notes, excel_row))


        # If validator said zero changed rows and we detect zero too,
        # allow no-op completion only when --force is passed (default: still OK to apply; nothing changes)
        if rows_changed == 0 and not force and not dry_run:
            # Soft-success: nothing to do; mark as Applied with a note
            with transaction.atomic():
                batch.status = UpdateBatch.Status.APPLIED
                batch.applied_at = timezone.now()
                batch.error_message = "Applied (no changes required)."
                batch.save(update_fields=["status", "applied_at", "error_message"])
            self.stdout.write(self.style.SUCCESS(f"{batch.id}: Applied (no changes)"))
            return

        # DRY RUN summary
        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"[DRY-RUN] Would APPLY {batch.id}: total={rows_total}, matched={rows_matched}, changed={rows_changed}"
            ))

            for (rid, changed, notes, r) in changes[:5]:
                self.stdout.write(f"  - {rid}: {sorted(changed.keys())}")
            return

        # ---- APPLY (all-or-nothing) ----
        try:
            history_user = batch.uploaded_by or get_system_user("admin")
        except Exception:
            history_user = None

        try:
            with transaction.atomic():
                
                # Lock the involved Beetles rows
                beetles_by_id = {
                    str(b.id): b
                    for b in beetles_qs.select_for_update()
                }
                now = timezone.now()

                for rid, changed, update_notes, excel_row in changes:
                    b = beetles_by_id.get(rid)
                    if not b:
                        # Extremely defensive: the set should match validator; this guards against odd races
                        raise CommandError(f"Apply aborted: record {rid} disappeared before apply.")

                    # Write fields
                    for k, v in changed.items():
                        try:
                            setattr(b, k, v)
                        except Exception as e:
                            raise Exception(f"Failed setting {k} (value={v!r}, type={type(v)}) at Excel row {excel_row}: {e}")

                    # Stamp per-record attribution fields
                    b.last_updated_at = now
                    b.last_updated_by = history_user
                    # Store notes on the record if Beetles.update_notes
                    if hasattr(b, "update_notes") and update_notes is not None:
                        b.update_notes = update_notes

                    try:
                        b.save()
                    
                    except DataError as e:
                        def ln(x):
                            return None if x is None else len(str(x).strip())

                        lens = {
                            "aspect": ln(getattr(b, "aspect", None)),
                            "collection_country": ln(getattr(b, "collection_country", None)),
                            "collection_stateProvince": ln(getattr(b, "collection_stateProvince", None)),
                            "specimen_type_status": ln(getattr(b, "specimen_type_status", None)),
                            "image_email": ln(getattr(b, "image_email", None)),
                            "image_institution": ln(getattr(b, "image_institution", None)),
                            "photographer": ln(getattr(b, "photographer", None)),
                            "depicts_specimen": ln(getattr(b, "depicts_specimen", None)),
                            "depicts_valid_name_id": ln(getattr(b, "depicts_valid_name_id", None)),
                            "depicts_described_name_id": ln(getattr(b, "depicts_described_name_id", None)),
                            "depicts_name_verbatim": ln(getattr(b, "depicts_name_verbatim", None)),
                            "alternative_id": ln(getattr(b, "alternative_id", None)),
                            "specimen_sex": ln(getattr(b, "specimen_sex", None)),
                        }

                        changed_list = ", ".join(sorted(changed.keys()))
                        raise CommandError(
                            f"Apply failed at Excel row {excel_row} for record {b.id}: "
                            f"likely char-field overflow. Changed fields: [{changed_list}]. "
                            f"Observed lengths={lens}. Original error: {e}"
                        ) from e


                    # Attach history user + reason to the most recent history row
                    h = getattr(b, "history", None)
                    if h:
                        latest = h.first()
                        if latest:
                            latest.history_user = history_user
                            # concise but descriptive reason
                            changed_list = ", ".join(sorted(changed.keys()))
                            latest.history_change_reason = (
                                f"Batch update {batch.id}; changed: {changed_list}"
                                + (f"; notes: {update_notes}" if update_notes else "")
                            )
                            latest.save()

                # Mark batch applied
                batch.status = UpdateBatch.Status.APPLIED
                batch.applied_at = timezone.now()
                # Friendly summary for UI
                batch.error_message = f"Applied {len(changes)} record(s)."
                batch.save(update_fields=["status", "applied_at", "error_message"])

        except Exception as e:
            self._fail_apply(batch, f"Apply failed: {e}")
            return

        self.stdout.write(self.style.SUCCESS(
            f"APPLIED {batch.id}: updated {len(changes)} record(s)"
        ))

    # ---- helpers ----
    def _fail_apply(self, batch: UpdateBatch, reason: str):
        batch.status = UpdateBatch.Status.APPLY_FAILED
        batch.error_message = str(reason)[:2000]
        batch.save(update_fields=["status", "error_message"])
        self.stderr.write(self.style.ERROR(f"{batch.id}: {reason}"))
