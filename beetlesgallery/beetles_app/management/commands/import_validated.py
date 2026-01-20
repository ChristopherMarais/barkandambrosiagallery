from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError
from django.db.utils import DataError
from django.core.files.base import ContentFile
# from django.utils import timezone

from beetlesgallery.beetles_app.models import Beetles, UploadBatch, ImageAsset
from beetlesgallery.beetles_app.schema import REQUIRED_COLS
from beetlesgallery.beetles_app.utils import get_system_user

import os
import json
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import zipfile
import io
from PIL import Image, ImageOps
from beetlesgallery.beetles_app.image_pipeline import write_original_and_thumb96
from .validate_uploads import _normalize_valid_id

try:
    import pandas as pd
except ImportError:
    raise CommandError("Please install pandas: pip install pandas")


# -----------------------
# Helpers (type coercion)
# -----------------------

def _none(v):
    """Convert 'empty' values (NaN, '', etc.) to None."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v

def _to_bool(v):
    """Coerce common truthy/falsey strings/numbers to bool or None."""
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

def _to_decimal_12_4(v):
    """Decimal with max_digits=12, decimal_places=4 (round half-up)."""
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
        return v.date()  # pandas Timestamp -> date
    except Exception:
        return None

def _enforce_maxlen(field_name: str, value, maxlen: int, row_num: int):
    """Fail-fast (no clipping) if a non-null value exceeds maxlen."""
    val = _none(value)
    if val is None:
        return
    s = str(val).strip()
    if len(s) > maxlen:
        raise CommandError(
            f"Row {row_num}: value in '{field_name}' exceeds max length {maxlen} "
            f"(got {len(s)}). Please shorten it in the spreadsheet."
        )


class Command(BaseCommand):
    help = "Import rows from VALIDATED UploadBatch CSV + manifest.json into Beetles (create-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--id",
            help="Import a specific UploadBatch UUID. If omitted, imports all batches with status=validated.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse + check + count, but do not write Beetles rows or change batch status.",
        )

    def _write_import_error_report(self, batch, errors):
        """
        Write a text error report for an import failure to batch.error_report_file
        and set batch.error_message to a concise summary.

        errors: list[str] of human-readable error messages.
        """
        if not errors:
            errors = ["Import failed for unknown reasons."]

        # Full text for the downloadable log
        lines = [
            f"Import error report for UploadBatch {batch.id}",
            f"Original filename: {batch.original_filename}",
            "",
            "Errors:",
        ]
        for e in errors:
            lines.append(f"- {e}")

        content = "\n".join(lines)

        # Short summary for the DB field / UI column
        summary = "; ".join(errors)
        summary = summary[:2000]

        # Name the log file in a consistent way
        filename = f"import_errors_{batch.id}.txt"

        # Save the file without immediately saving the whole model
        batch.error_report_file.save(filename, ContentFile(content), save=False)
        batch.error_message = summary
        # status should already be set by the caller (IMPORT_FAILED)
        batch.save(update_fields=["status", "error_message", "error_report_file"])

    def handle(self, *args, **opts):
        batch_id = opts.get("id")
        dry_run = bool(opts.get("dry_run"))

        if batch_id:
            qs = UploadBatch.objects.filter(id=batch_id, status=UploadBatch.Status.VALIDATED)
        else:
            qs = UploadBatch.objects.filter(status=UploadBatch.Status.VALIDATED)

        if not qs.exists():
            self.stdout.write("No validated batches to import.")
            return

        total_created = 0
        for batch in qs.order_by("created_at"):
            self.stdout.write(f"Importing batch {batch.id}...")

            try:
                # This may raise CommandError for controlled failures
                created = self._import_one_batch(batch, dry_run=dry_run)
                total_created += created
    
            except CommandError as e:
                # Controlled failure from _import_one_batch (e.g., duplicate image_sha256)
                msg = str(e)

                if dry_run:
                    # Don't mutate the DB in dry-run mode
                    self.stderr.write(
                        self.style.ERROR(f"[DRY RUN] Import would fail for {batch.id}: {msg}")
                    )
                else:
                    batch.status = UploadBatch.Status.IMPORT_FAILED
                    # Write a detailed import error log + summary message
                    self._write_import_error_report(batch, [msg])
                    self.stderr.write(
                        self.style.ERROR(f"Import failed for {batch.id}: {msg}")
                    )

                # Skip to the next batch
                continue

            except Exception as e:
                # Unexpected error; still surface as IMPORT_FAILED with a generic message
                msg = f"Unexpected error during import: {e}"

                if dry_run:
                    self.stderr.write(
                        self.style.ERROR(f"[DRY RUN] Import would fail for {batch.id}: {msg}")
                    )
                else:
                    batch.status = UploadBatch.Status.IMPORT_FAILED
                    self._write_import_error_report(batch, [msg])
                    self.stderr.write(
                        self.style.ERROR(f"Import failed for {batch.id}: {msg}")
                    )

                # Skip to the next batch
                continue

        suffix = " (dry-run)" if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"Done. Beetles created: {total_created}{suffix}"))

    # --------------------------------
    # Per-batch import
    # --------------------------------
    def _import_one_batch(self, batch: UploadBatch, dry_run: bool) -> int:
        """
        For a single VALIDATED batch:
          - read manifest.json (row -> sha256, zip member, filename),
          - read the CSV,
          - create Beetles rows (create-only) and set image_sha256,
          - mark batch imported (unless dry-run).
        """
        base_dir = os.path.dirname(batch.file.path)
        manifest_path = os.path.join(base_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            raise CommandError(f"{batch.id}: manifest.json not found next to CSV ({manifest_path}).")

        # Read manifest
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as e:
            raise CommandError(f"{batch.id}: cannot read manifest.json: {e}")

        # Locate the paired ZIP (same dir as XLSX but filename recorded during validation)
        zip_name = manifest.get("zip")
        if not zip_name:
            raise CommandError(f"{batch.id}: manifest missing zip name.")
        zip_path = os.path.join(base_dir, zip_name)
        if not os.path.exists(zip_path):
            raise CommandError(f"{batch.id}: ZIP not found at {zip_path}")

        # Open once; close after import completes
        with zipfile.ZipFile(zip_path, "r") as zf:

            rows = manifest.get("rows", [])
            if not isinstance(rows, list) or not rows:
                raise CommandError(f"{batch.id}: manifest has no 'rows' entries.")

            # Build index -> manifest entry map
            by_index = {int(r["csv_index"]): r for r in rows if "csv_index" in r and "sha256" in r}

            # Read CSV (single sheet)
            try:
                df = pd.read_csv(batch.file.path)
            except Exception as e:
                raise CommandError(f"{batch.id}: cannot open CSV: {e}")

            df.columns = [c.strip() for c in df.columns]
            missing = REQUIRED_COLS - set(df.columns)
            if missing:
                raise CommandError(f"{batch.id}: CSV missing required columns: {sorted(missing)}")

            # Hard sanity checks: 1:1 row alignment with manifest
            n_manifest = len(by_index)
            n_sheet = len(df)
            if n_manifest != n_sheet:
                raise CommandError(
                    f"{batch.id}: manifest rows ({n_manifest}) != sheet rows ({n_sheet}). "
                    "Re-run validation to regenerate the manifest."
                )

            # Ensure every sheet row index is present in manifest and has a hash
            expected_idx = set(range(n_sheet))         
            missing_idx = expected_idx - set(by_index.keys())
            if missing_idx:
                raise CommandError(
                    f"{batch.id}: manifest missing csv_index entries for rows: "
                    f"{sorted(list(missing_idx))[:10]}{'...' if len(missing_idx) > 10 else ''}"
                )

            # Ensure all manifest rows have sha256
            no_hash = [k for k, v in by_index.items() if not v.get('sha256')]
            if no_hash:
                raise CommandError(
                    f"{batch.id}: manifest rows without sha256: "
                    f"{no_hash[:10]}{'...' if len(no_hash) > 10 else ''}"
                )

            # Limits copied from your model
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

            created_beetles = 0

            # Attribution in simple_history
            history_user = batch.uploaded_by or get_system_user("admin")
            if batch.uploaded_by is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Batch {batch.id}: no uploaded_by; attributing changes to 'admin' system user."
                    )
                )

            # Create-only import; validation already enforced uniqueness-by-hash.
            with transaction.atomic():

                for i, row in df.iterrows():
                    row_num = i + 2  # +2 for 0-based index + header row

                    # Required
                    full_path_at_import = _none(row.get("full_path_at_import"))

                    # Normalize valid_name_id using the same helper as validate_uploads
                    raw_valid_id = row.get("depicts_valid_name_id")
                    depicts_valid_name_id = _normalize_valid_id(raw_valid_id)

                    if full_path_at_import is None:
                        raise CommandError(
                            f"{batch.id}: Row {row_num} missing required field 'full_path_at_import'."
                        )
                    
                    # Enforce length only when an ID is present
                    if depicts_valid_name_id is not None:
                        _enforce_maxlen("depicts_valid_name_id", depicts_valid_name_id, MAXLEN["depicts_valid_name_id"], row_num)

                    # Manifest mapping (must exist; validator guaranteed 1:1)
                    m = by_index.get(int(i))
                    if not m:
                        raise CommandError(f"{batch.id}: Row {row_num} has no manifest entry (excel_index={i}).")
                    image_sha256 = m.get("sha256")
                    if not image_sha256:
                        raise CommandError(f"{batch.id}: Row {row_num} missing sha256 in manifest.")

                    size_bytes = m.get("size")
                    if size_bytes is None:
                        try:
                            size_bytes = zf.getinfo(m.get("zip_member")).file_size
                        except Exception:
                            size_bytes = None

                    # CharFields (no clipping)
                    char_fields = [
                        ("alternative_id", MAXLEN["alternative_id"]),
                        ("image_institution", MAXLEN["image_institution"]),
                        ("photographer", MAXLEN["photographer"]),
                        ("image_email", MAXLEN["image_email"]),
                        ("aspect", MAXLEN["aspect"]),
                        ("depicts_specimen", MAXLEN["depicts_specimen"]),
                        ("depicts_described_name_id", MAXLEN["depicts_described_name_id"]),
                        ("depicts_name_verbatim", MAXLEN["depicts_name_verbatim"]),
                        ("collection_country", MAXLEN["collection_country"]),
                        ("collection_stateProvince", MAXLEN["collection_stateProvince"]),
                        ("specimen_sex", MAXLEN["specimen_sex"]),
                        ("specimen_type_status", MAXLEN["specimen_type_status"]),
                    ]
                    values = {}
                    for fname, maxlen in char_fields:
                        val = _none(row.get(fname))
                        _enforce_maxlen(fname, val, maxlen, row_num)
                        values[fname] = val

                    # Non-char fields
                    photo_usage_statement = _none(row.get("photo_usage_statement"))
                    resolution_in_ppmm = _to_decimal_12_4(row.get("resolution_in_ppmm"))
                    image_notes = _none(row.get("image_notes"))
                    image_date_taken = _to_date(row.get("image_date_taken"))
                    image_has_multiple_individuals = _to_bool(row.get("image_has_multiple_individuals"))
                    specimen_notes = _none(row.get("specimen_notes"))

                    # Final check for fields lengths
                    pending = {
                        "aspect": values.get("aspect"),
                        "collection_country": values.get("collection_country"),
                        "collection_stateProvince": values.get("collection_stateProvince"),
                        "specimen_type_status": values.get("specimen_type_status"),
                    }
                    too_long = []
                    for fname, v in pending.items():
                        if v is None:
                            continue
                        s = str(v).strip()
                        limit = MAXLEN[fname]
                        if len(s) > limit:
                            too_long.append(f"{fname} len={len(s)} > {limit}")

                    if too_long:
                        raise CommandError(
                            f"{batch.id}: Row {row_num} has overlong values: {', '.join(too_long)}"
                        )

                    # Get the ZIP member path for this row (from manifest)
                    zip_member = m.get("zip_member")
                    if not zip_member:
                        raise CommandError(f"{batch.id}: Row {row_num} missing zip_member in manifest.")

                    if dry_run:
                        # Probe image decodability ONLY (no writes)
                        with zf.open(zip_member, "r") as f:
                            try:
                                with Image.open(f) as img:
                                    ImageOps.exif_transpose(img)
                                    img.verify()  # basic integrity check
                            except Exception as e:
                                raise CommandError(f"{batch.id}: Row {row_num} image failed probe: {e}")
                        # Skip saving paths/dimensions in dry-run; just use placeholders
                        orig_rel = None
                        thumb_rel = None
                        img_w, img_h = None, None
                        tw, th = 96, 96
                    
                    else:
                        # Read and persist original + thumb (content-addressed)
                        try:
                            with zf.open(zip_member, "r") as f:
                                saved = write_original_and_thumb96(image_sha256, f)
                        except Exception as e:
                            raise CommandError(f"{batch.id}: Row {row_num} failed image save/thumbnail: {e}")

                        orig_rel = saved["original_path"]
                        thumb_rel = saved["thumb_path"]
                        img_w, img_h = saved["image_size"]
                        tw, th = saved["thumb_size"]

                        try:
                            # 1. Prepare ImageAsset Defaults
                            image_defaults = {
                                'image_institution': values.get('image_institution'),
                                'photographer': values.get('photographer'),
                                'image_email': values.get('image_email'),
                                'photo_usage_statement': photo_usage_statement,
                                'image_date_taken': image_date_taken,
                                'image_notes': image_notes,
                                'image_has_multiple_individuals': image_has_multiple_individuals,
                                'resolution_in_ppmm': resolution_in_ppmm,
                                'image_sha256': image_sha256,
                                'image_size_bytes': size_bytes,
                                # Physical file fields
                                'image_file': orig_rel,
                                'thumb_small': thumb_rel,
                                'image_width': img_w,
                                'image_height': img_h,
                                'thumb_width': tw,
                                'thumb_height': th,
                            }

                            # 2. Get or Create ImageAsset
                            # We use full_path_at_import as the unique key. 
                            # If it exists, we link to it (and do NOT overwrite metadata).
                            image_asset, created = ImageAsset.objects.get_or_create(
                                full_path_at_import=full_path_at_import,
                                defaults=image_defaults
                            )

                            # 3. Create Beetle Record
                            new_obj = Beetles.objects.create(
                                image_asset=image_asset,
                                depicts_valid_name_id=depicts_valid_name_id,
                                depicts_described_name_id=values.get('depicts_described_name_id'),
                                depicts_specimen=values.get('depicts_specimen'),
                                depicts_name_verbatim=values.get('depicts_name_verbatim'),
                                alternative_id=values.get('alternative_id'),
                                aspect=values.get('aspect'),
                                collection_country=values.get('collection_country'),
                                collection_stateProvince=values.get('collection_stateProvince'),
                                specimen_sex=values.get('specimen_sex'),
                                specimen_type_status=values.get('specimen_type_status'),
                                specimen_notes=specimen_notes,
                            )
                            
                            created_beetles += 1

                            # one per batch
                            reason_text = (
                                f"Import from VALIDATED batch {batch.id} "
                            )

                            # Attach history user + reason to the most recent history row
                            h = new_obj.history.first()  # most recent history entry
                            if h:
                                h.history_user = history_user 
                                h.history_change_reason = reason_text
                                h.save()

                        except DataError as e:
                            # Likely a varchar overflow (e.g., 100-char fields) or other DB-level constraint issue.
                            # Report lengths of the constrained CharFields to pinpoint the culprit.
                            def ln(x): 
                                return None if x is None else len(str(x).strip())

                            lens = {
                                "aspect": ln(values.get("aspect")),
                                "collection_country": ln(values.get("collection_country")),
                                "collection_stateProvince": ln(values.get("collection_stateProvince")),
                                "specimen_type_status": ln(values.get("specimen_type_status")),
                                # Include a few other bounded fields for completeness:
                                "image_email": ln(values.get("image_email")),
                                "image_institution": ln(values.get("image_institution")),
                                "photographer": ln(values.get("photographer")),
                                "depicts_specimen": ln(values.get("depicts_specimen")),
                                "depicts_valid_name_id": ln(depicts_valid_name_id),
                                "depicts_described_name_id": ln(values.get("depicts_described_name_id")),
                                "depicts_name_verbatim": ln(values.get("depicts_name_verbatim")),
                                "alternative_id": ln(values.get("alternative_id")),
                            }

                             raise CommandError(f"{batch.id}: Row {row_num} failed DB insert: {e}") from e

                        except IntegrityError as e:
                            # Most likely the UNIQUE(image_sha256) constraint fired
                            # -> treat as a hard failure for the entire batch.
                            filename = os.path.basename(full_path_at_import) if full_path_at_import else "<unknown file>"
                            raise CommandError(
                                f"{batch.id}: Row {row_num} duplicate image content for "
                                f"'{filename}' (image_sha256={image_sha256} already exists in the database)."
                            ) from e

                if dry_run:
                    # don’t persist any of the created rows
                    transaction.set_rollback(True)

                # End-of-import actions (only when not a dry run)
                if not dry_run:
                    # 1) Write archive.json next to the CSV in validated/
                    batch.write_archive_json(
                        imported_count=created_beetles,
                        records_summary=[], 
                        notes="initial import"
                    )
                    # 2) Now move CSV/ZIP + sidecars (manifest.json, archive.json) to archived/
                    batch.mark_imported_and_archive()


            self.stdout.write(self.style.SUCCESS(
                f"Imported batch {batch.id}: created {created_beetles} Beetles"
            ))

        return created_beetles
