"""
Recommended: create migrations and migrate before importing:
python manage.py makemigrations beetles_app
python manage.py migrate

Import:
    python manage.py import_beetles /path/to/data.xlsx

Full path: beetles_app/management/commands/import_beetles.py

Dry-run (no DB write):
    python manage.py import_beetles /path/to/data.xlsx --dry-run
"""

import math
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from beetlesgallery.beetles_app.models import Beetles, UploadBatch, ImageAsset, Taxon

try:
    import pandas as pd
except ImportError:
    raise CommandError("Please install pandas and openpyxl: pip install pandas openpyxl")

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
    """
    Convert to Decimal with max_digits=12, decimal_places=4.
    Rounds half-up to 4 places and ensures total digits fit.
    """
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
    # enforce max_digits=12 (strip '-' and '.')
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

def _enforce_maxlen(field_name: str, value, maxlen: int, row_num: int):
    """
    Raise a clear error if a non-null value exceeds maxlen.
    Do not clip; fail fast so the user can fix the spreadsheet.
    """
    val = _none(value)
    if val is None:
        return
    s = str(val)
    if len(s) > maxlen:
        raise CommandError(
            f"Row {row_num}: value in '{field_name}' exceeds max length {maxlen} "
            f"(got {len(s)}). Please shorten it in the spreadsheet."
        )


class Command(BaseCommand):
    help = "Import data from a single-sheet Excel file into the unified Beetles table."

    def add_arguments(self, parser):
        parser.add_argument("excel_path", help="Path to the .xlsx file")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse/validate but do not write to the database.",
        )
        parser.add_argument(
            "--batch-id",
            help="UUID of UploadBatch for attribution (who/why).",
        )

    def handle(self, *args, **opts):
        excel_path = opts["excel_path"]
        dry_run = opts["dry_run"]

        batch_id = opts.get("batch_id")
        history_user = None
        reason = None
        if batch_id:
            try:
                batch = UploadBatch.objects.get(id=batch_id)
                history_user = batch.uploaded_by  # may be None
                reason = f"Import from batch {batch.id} ({os.path.basename(excel_path)})"
            except UploadBatch.DoesNotExist:
                raise CommandError(f"UploadBatch {batch_id} not found")
        else:
            reason = f"Import from file {os.path.basename(excel_path)}"

        # --- Read single sheet ---
        try:
            df = pd.read_excel(excel_path)
        except FileNotFoundError:
            raise CommandError(f"File not found: {excel_path}")
        except Exception as e:
            raise CommandError(f"Error reading Excel: {e}")

        # Normalize column names (trim)
        df.columns = [c.strip() for c in df.columns]
        
        # Required columns
        required_cols = {"full_path_at_import", "depicts_valid_name_id"}
        missing = required_cols - set(df.columns)
        if missing:
            raise CommandError(f"Missing required columns: {sorted(missing)}")

        # Convert NaNs/empties to None
        df = df.applymap(_none)

        created_beetles = 0
        new_images = 0

        # Field max lengths
        MAXLEN = {
            "alternative_id": 255,
            "image_institution": 255,
            "photographer": 255,
            "image_email": 254,  # EmailField default
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

        # Create-only import
        # Duplicates are supposed to be caught in validation
        RAISE_ON_DUP = False  # optional backstop on full_path_at_import

        # Pre-fetch taxon map to memory for fast foreign key linking during import
        taxon_map = {t.valid_species_id: t for t in Taxon.objects.all()}

        # All in one transaction for consistency
        with transaction.atomic():
            # enumerate gives 0-based index; Excel-like row number = idx+2 (row 1 is header)
            for row_num, (_, row) in enumerate(df.iterrows(), start=2):

                # --- Required ---
                full_path_at_import = _none(row.get("full_path_at_import"))
                depicts_valid_name_id = _none(row.get("depicts_valid_name_id"))
                if full_path_at_import is None or depicts_valid_name_id is None:
                    raise CommandError(
                        f"Error in row {row_num}: Each row must include non-empty 'full_path_at_import' and 'depicts_valid_name_id'."
                    )

                # max-length checks for required char fields
                _enforce_maxlen("depicts_valid_name_id", depicts_valid_name_id, MAXLEN["depicts_valid_name_id"], row_num)

                # --- 1. SEPARATE FIELDS: ImageAsset vs Beetles ---

                # A. Fields that belong to ImageAsset
                image_fields_map = [
                    ("image_institution", MAXLEN["image_institution"]),
                    ("photographer", MAXLEN["photographer"]),
                    ("image_email", MAXLEN["image_email"]),
                ]
                
                image_defaults = {}
                for field_name, maxlen in image_fields_map:
                    val = _none(row.get(field_name))
                    _enforce_maxlen(field_name, val, maxlen, row_num)
                    image_defaults[field_name] = val

                # Add non-char fields for ImageAsset
                image_defaults["photo_usage_statement"] = _none(row.get("photo_usage_statement"))
                image_defaults["resolution_in_ppmm"] = _to_decimal_12_4(row.get("resolution_in_ppmm"))
                image_defaults["image_notes"] = _none(row.get("image_notes"))
                image_defaults["image_date_taken"] = _to_date(row.get("image_date_taken"))
                image_defaults["image_has_multiple_individuals"] = _to_bool(row.get("image_has_multiple_individuals"))
                image_defaults["is_validated"] = _to_bool(row.get("is_validated")) or False
                if history_user:
                    image_defaults["last_updated_by"] = history_user


                # B. Fields that belong to Beetles
                beetle_fields_map = [
                    ("alternative_id", MAXLEN["alternative_id"]),
                    ("aspect", MAXLEN["aspect"]), # Kept on Beetles!
                    ("depicts_specimen", MAXLEN["depicts_specimen"]),
                    ("depicts_described_name_id", MAXLEN["depicts_described_name_id"]),
                    ("depicts_name_verbatim", MAXLEN["depicts_name_verbatim"]),
                    ("collection_country", MAXLEN["collection_country"]),
                    ("collection_stateProvince", MAXLEN["collection_stateProvince"]),
                    ("specimen_sex", MAXLEN["specimen_sex"]),
                    ("specimen_type_status", MAXLEN["specimen_type_status"]),
                ]

                beetle_values = {}
                for field_name, maxlen in beetle_fields_map:
                    val = _none(row.get(field_name))
                    _enforce_maxlen(field_name, val, maxlen, row_num)
                    beetle_values[field_name] = val
                
                # Add non-char fields for Beetles
                beetle_values["specimen_notes"] = _none(row.get("specimen_notes"))


                # --- 2. GET OR CREATE THE IMAGE ASSET ---
                # We use full_path_at_import as the unique key for the metadata import.
                # If an image with this path already exists, we reuse it (and ignore differing metadata in this row).
                # If it doesn't exist, we create it.
                image_asset, img_created = ImageAsset.objects.get_or_create(
                    full_path_at_import=full_path_at_import,
                    defaults=image_defaults
                )

                if img_created:
                    new_images += 1

                # --- 3. DUPLICATE CHECK (Beetle Level) ---
                # Check if this exact image already has a beetle record (if we are being strict)
                # Note: We now filter through the relationship 'image_asset__full_path_at_import'
                if RAISE_ON_DUP and Beetles.objects.filter(image_asset__full_path_at_import=full_path_at_import).exists():
                     raise CommandError(
                        f"Row {row_num}: a record for path '{full_path_at_import}' already exists. "
                        f"Refusing to overwrite."
                    )


                # --- 4. CREATE THE BEETLE RECORD ---
                obj = Beetles.objects.create(
                    image_asset=image_asset,  # LINKED HERE
                    depicts_valid_name_id=depicts_valid_name_id,
                    taxon=taxon_map.get(depicts_valid_name_id), # Link relational hierarchy
                    **beetle_values
                )
                created_beetles += 1

                # Tag history (django-simple-history)
                hist = obj.history.first()
                if hist:
                    if history_user is not None:
                        hist.history_user = history_user
                    hist.history_change_reason = reason
                    hist.save()

            if dry_run:
                self.stdout.write(self.style.WARNING("DRY-RUN enabled — rolling back transaction."))
                raise transaction.TransactionManagementError("Rollback for DRY-RUN")

        self.stdout.write(
            self.style.SUCCESS(f"Import complete. Beetles created: {created_beetles} | New ImageAssets: {new_images}")
        )