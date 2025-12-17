import zipfile
import hashlib
import json
import os
import unicodedata

from beetles_app import species_ref
from beetles_app.models import Beetles, UploadBatch
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

try:
    import pandas as pd
except ImportError:
    pd = None

import math

from beetles_app.schema import REQUIRED_COLS, MAX_ROWS, IMAGE_EXTENSIONS, MANIFEST_NAME, MANIFEST_VERSION

def _is_blank(v):
    """Treat NaN/None/''/whitespace as blank."""
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False

def _normalize_valid_id(v):
    """
    Normalize depicts_valid_name_id into a canonical string form.

    - Blank (NaN/None/empty) -> None
    - Floats that are whole numbers (e.g. 123.0) -> "123"
    - Int-like values -> "123"
    - Everything else -> stripped string
    """
    if _is_blank(v):
        return None

    # Handle floats like 123.0 -> "123"
    if isinstance(v, float):
        if math.isnan(v):
            return None
        if v.is_integer():
            return str(int(v))
        return str(v).strip()

    # Handle ints (including numpy integers if you want to be fancy)
    try:
        import numpy as np  # optional
        integer_types = (int, np.integer)
    except ImportError:
        integer_types = (int,)

    if isinstance(v, integer_types):
        return str(int(v))

    # Fallback: just a stripped string
    return str(v).strip()

def _normalize_basename(name: str) -> str:
    """
    Cross-OS basename:
      - normalize Unicode
      - strip whitespace
      - treat both '/' and '\\' as separators
      - case-fold for matching
    """
    if not name:
        return ""

    # Normalize and strip
    s = unicodedata.normalize("NFC", str(name).strip())

    # Replace backslashes with forward slashes, then take last component
    s = s.replace("\\", "/")
    base = s.split("/")[-1]

    return base.casefold()

def _sha256_zip_member(zf: zipfile.ZipFile, member_name: str, chunk_size: int = 1024 * 1024) -> str | None:
    """
    Stream a member from a ZipFile and compute its SHA-256
    (reads the file inside the ZIP in chunks (memory-safe)) 
    Returns SHA-256 hex digest or None on error.
    """
    try:
        h = hashlib.sha256()
        with zf.open(member_name, "r") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

def _is_image_filename(name: str) -> bool:
    """Checks for allowed image extensions."""
    name = (name or "").lower()
    return name.endswith(IMAGE_EXTENSIONS)

def _is_os_cruft(path: str) -> bool:
    """
    Ignore cross-OS junk:
      - macOS AppleDouble & metadata: __MACOSX/..., files starting with '._', '.DS_Store'
      - Windows metadata: Thumbs.db, Desktop.ini
    """
    if not path:
        return True
    # __MACOSX tree (Finder zips)
    if path.startswith("__MACOSX/"):
        return True
    name = os.path.basename(path)
    if not name:
        return True
    lname = name.lower()
    if name.startswith("._"):               
        return True
    if lname == ".ds_store":
        return True
    if lname in {"thumbs.db", "desktop.ini", ".ds_store"}:
        return True
    return False

class Command(BaseCommand):
    help = "Validate uploaded XLSX files in staging and promote to validated/rejected."

    def add_arguments(self, parser):
        """Command line options for targeting a single batch and for dry-runs"""
        parser.add_argument(
            "--id",
            help="Validate a specific UploadBatch UUID. If omitted, validates all 'staging' batches.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run checks but do not change status or move files.",
        )

    def handle(self, *args, **opts):
        if pd is None:
            self.stderr.write(self.style.ERROR(
                "pandas is required. Install with: pip install pandas openpyxl"
            ))
            return

        batch_id = opts.get("id")
        dry_run = opts.get("dry_run")

        # If --id given, validate that one; else all in 'staging'
        if batch_id:
            qs = UploadBatch.objects.filter(id=batch_id)
        else:
            qs = UploadBatch.objects.filter(status=UploadBatch.Status.STAGING)

        if not qs.exists():
            self.stdout.write("No batches to validate.")
            return

        # Validate batches FIFO by created_at
        for batch in qs.order_by("created_at"):
            self._validate_one(batch, dry_run=dry_run)


    # --------------------------------
    # Per batch core validation logic
    # --------------------------------
    def _validate_one(self, batch: UploadBatch, dry_run: bool = False):
        self.stdout.write(f"Validating {batch.id} ({batch.original_filename})")

        # When not a dry-run, mark this batch as "validating" in the DB
        if not dry_run:
            batch.mark_validating()

        errors = []
        excel_path = batch.file.path


        # --- 1) XLSX Checks: Can the workbook be opened as a single sheet? ---
        try:
            # Use the first worksheet by default
            df = pd.read_excel(excel_path)
        except Exception as e:
            errors.append(f"Cannot open workbook: {e}")
            return self._finalize(batch, errors, dry_run)

        # Normalize headers
        df.columns = [c.strip() for c in df.columns]

        # Required columns
        missing = REQUIRED_COLS - set(df.columns)
        if missing:
            errors.append(f"Missing required columns: {sorted(missing)}")
            return self._finalize(batch, errors, dry_run)

        # Row cap (guard against accidental huge files)
        if MAX_ROWS is not None and len(df) > MAX_ROWS:
            errors.append(f"Sheet has {len(df)} rows (max {MAX_ROWS}).")
            return self._finalize(batch, errors, dry_run)

        # --- Preload reference membership for any non-blank depicts_valid_name_id in the sheet ---
        try:
            candidate_ids = {
                _normalize_valid_id(v)
                for v in df.get("depicts_valid_name_id", [])
                if not _is_blank(v)
            }
            
            candidate_ids = {vid for vid in candidate_ids if vid is not None}

            # Build a membership map via bulk_lookup (IDs not found will be absent)
            ref_map = species_ref.bulk_lookup(candidate_ids) if candidate_ids else {}
        
        except Exception as e:
            errors.append(f"Could not access taxonomy reference: {e}")
            return self._finalize(batch, errors, dry_run)

        # --- 2) Paired ZIP Lookup: Find the paired ZIP (same dir + same stem + .zip) ---
        if not batch.zip_file:
            errors.append("No ZIP file attached to this batch.")
            return self._finalize(batch, errors, dry_run)

        zip_path = batch.zip_file.path
        if not os.path.exists(zip_path):
            errors.append(f"ZIP file missing on disk: {zip_path}")
            return self._finalize(batch, errors, dry_run)

        # --- 3) Open ZIP and index image files ---
        try:
            zf = zipfile.ZipFile(zip_path, "r")
        except Exception as e:
            errors.append(f"Cannot open ZIP: {e}")
            return self._finalize(batch, errors, dry_run)

        # Image members, excluding OS cruft and directories
        zip_images = [
            m for m in zf.namelist()
            if not m.endswith("/")
            and _is_image_filename(m)
            and not _is_os_cruft(m)
        ]

        # Build a normalized basename -> list[members] map for robust matching
        basename_map: dict[str, list[str]] = {}
        for m in zip_images:
            key = _normalize_basename(m)  # normalized, case-folded basename
            basename_map.setdefault(key, []).append(m)

        if not zip_images:
            errors.append("ZIP contains no image files (after ignoring OS metadata files).")
            zf.close()
            return self._finalize(batch, errors, dry_run)

        # --- 4a) Row-by-row Mapping and Hashing: Validate 1:1 mapping and compute hashes ---
        manifest = []  # one entry per row: {row_num, excel_index, filename, zip_member, sha256, size}
        row_errors = 0

        for i, row in df.iterrows():
            # Excel-like row number (header at 1)
            row_num = i + 2

            full_path = row.get("full_path_at_import")
            valid_id = row.get("depicts_valid_name_id")  # may be blank

            # Required values must be present and non-blank
            if _is_blank(full_path):
                errors.append(f"Row {row_num}: 'full_path_at_import' must be present.")
                row_errors += 1
                continue

            # If an ID is provided, it must exist in the taxonomy reference
            if not _is_blank(valid_id):
                vid = _normalize_valid_id(valid_id)
                if vid not in ref_map:
                    errors.append(f"Row {row_num}: 'depicts_valid_name_id' not found in reference: '{vid}'.")
                    row_errors += 1
                    continue

            # Expected image filename inside ZIP (basename of full_path_at_import)
            expected_name_raw = os.path.basename(str(full_path).strip())
            expected_key = _normalize_basename(expected_name_raw)

            if expected_key not in basename_map:
                errors.append(f"Row {row_num}: image '{expected_name_raw}' not found in ZIP.")
                row_errors += 1
                continue

            members = basename_map[expected_key]
            if len(members) > 1:
                # Same normalized basename appears in multiple ZIP paths — ambiguous
                errors.append(f"Row {row_num}: multiple files named '{expected_name_raw}' in ZIP (ambiguous).")
                row_errors += 1
                continue

            member = members[0]

            # Compute SHA-256 streamingly, capture size for debugging/reporting
            sha = _sha256_zip_member(zf, member)
            if not sha:
                errors.append(f"Row {row_num}: failed to read/hash '{member}' in ZIP.")
                row_errors += 1
                continue

            # Stash record for manifest
            try:
                info = zf.getinfo(member)
                size = info.file_size
            except Exception:
                size = None

            manifest.append({
                "row_num": row_num,
                "excel_index": int(i),
                "filename": expected_name_raw,   # keep the original basename from the sheet
                "zip_member": member,            # exact path inside ZIP
                "sha256": sha,
                "size": size,
            })

        # If any per-row errors, stop now
        if row_errors:
            zf.close()
            return self._finalize(batch, errors, dry_run)


        # --- 4b) Within-batch duplicate detection by hash (same content twice in the ZIP) ---
        seen_by_hash = {}
        dups = []
        for m in manifest:
            h = m["sha256"]
            if h in seen_by_hash:
                dups.append((seen_by_hash[h]["filename"], m["filename"]))
            else:
                seen_by_hash[h] = m

        if dups:
            preview = dups[:10]
            more = "..." if len(dups) > 10 else ""
            errors.append(f"ZIP contains duplicate images by hash: {preview}{more}")
            zf.close()
            return self._finalize(batch, errors, dry_run)


        # --- 5) Duplicate image detection against DB; no overwrite policy ---
        hashes = [m["sha256"] for m in manifest]

        # Hashes are compared through single indexed look-up, not pairwise comparisons
        # Refuse to validate an upload that would create duplicate images (by content hash)
        existing = set(
            Beetles.objects.filter(image_sha256__in=hashes).values_list("image_sha256", flat=True)
        )

        if existing:
            # Show up to 10 collisions with their filenames for clarity
            collisions = [m for m in manifest if m["sha256"] in existing][:10]
            sample = [{"filename": m["filename"], "sha256": m["sha256"]} for m in collisions]
            errors.append(f"Duplicate images already exist (by sha256). Examples: {sample}")
            zf.close()
            return self._finalize(batch, errors, dry_run)


        # --- 6) Strictness: Fail if ZIP includes extra images not referenced in metadata ---
        # Compare using normalized basenames on both sides
        sheet_expected_norm = {_normalize_basename(m["filename"]) for m in manifest}
        zip_basenames_norm = set(basename_map.keys())

        extras_norm = sorted(zip_basenames_norm - sheet_expected_norm)
        if extras_norm:
            # Show user-friendly (original) names for preview when possible
            preview_members = []
            for k in extras_norm[:10]:
                # pick one representative member path for display
                reps = basename_map.get(k, [])
                if reps:
                    preview_members.append(os.path.basename(reps[0]))
                else:
                    preview_members.append(k)
            more = "..." if len(extras_norm) > 10 else ""
            errors.append(f"ZIP contains extra images not referenced by the sheet: {preview_members}{more}")

        zf.close()

        # Success: hand off to _finalize
        return self._finalize(batch, errors, dry_run, manifest=manifest, zip_path=zip_path)

    # --------------------------------------------------
    # Finalization: State Transition and Manifest Write
    # --------------------------------------------------
    def _finalize(self, batch: UploadBatch, errors, dry_run: bool, manifest=None, zip_path=None):
        if errors:
            # Build full multi-line error log text
            full_error_text = "\n".join(str(e) for e in errors)

            # Build a SHORT reason for the DB / UI
            # (so the My Uploads table doesn’t get a huge snippet)
            if errors:
                first = str(errors[0])
            else:
                first = "Validation failed."
            if len(errors) > 1:
                first = f"{first} (+{len(errors) - 1} more)"
            short_reason = (first[:1800] + "…") if len(first) > 1800 else first
            short_reason = f"{short_reason} (see error log for full details)"

            if dry_run:
                self.stdout.write(self.style.WARNING(
                    f"[DRY-RUN] Would REJECT {batch.id}: {short_reason}"
                ))
            else:
                # Mark rejected with the SHORT message
                batch.mark_rejected_and_move(short_reason)

                # Write the full error log to a text file in the same folder as the XLSX
                try:
                    # Use the same directory as the XLSX file (relative name)
                    base_dir_rel = os.path.dirname(batch.file.name)  # e.g. "uploads/rejected/..."
                    log_filename = f"{os.path.splitext(os.path.basename(batch.file.name))[0]}_errors.txt"
                    error_rel_path = os.path.join(base_dir_rel, log_filename)

                    default_storage.save(error_rel_path, ContentFile(full_error_text))

                    # Attach to the new FileField
                    batch.error_report_file.name = error_rel_path
                    batch.save(update_fields=["error_message", "error_report_file"])

                    self.stdout.write(self.style.ERROR(
                        f"REJECTED {batch.id}: {short_reason} (log: {error_rel_path})"
                    ))
                except Exception as e:
                    # If log writing fails, still rejected – just warn on stdout.
                    self.stdout.write(self.style.ERROR(
                        f"REJECTED {batch.id}: {short_reason} (failed to write error log: {e})"
                    ))

        else:
            # Validated path
            if dry_run:
                self.stdout.write(self.style.WARNING(
                    f"[DRY-RUN] Would VALIDATE {batch.id}"
                ))
            else:
                # Moves the XLSX out of staging
                batch.mark_validated_and_move()

                # Write manifest.json next to the moved XLSX (now under uploads/validated/...)
                try:
                    base_dir = os.path.dirname(batch.file.path)
                    manifest_path = os.path.join(base_dir, MANIFEST_NAME)
                    if manifest is not None:
                        with open(manifest_path, "w", encoding="utf-8") as fh:
                            json.dump(
                                {
                                    "batch_id": str(batch.id),
                                    "xlsx": batch.file.name,     # relative path via storage
                                    "zip": os.path.basename(zip_path) if zip_path else None,
                                    "count": len(manifest),
                                    "manifest_version": MANIFEST_VERSION,
                                    "rows": manifest,
                                },
                                fh,
                                ensure_ascii=False,
                                indent=2,
                            )
                    self.stdout.write(self.style.SUCCESS(
                        f"VALIDATED {batch.id} -> {batch.file.name} (manifest: {os.path.basename(manifest_path)})"
                    ))
                except Exception as e:

                    # Validation passed, but manifest write failed — keep validated status, just warn.
                    self.stdout.write(self.style.WARNING(
                        f"VALIDATED {batch.id}, but failed to write manifest: {e}"
                    ))

