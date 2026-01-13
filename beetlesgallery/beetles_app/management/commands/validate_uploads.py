import zipfile
import hashlib
import json
import os
import unicodedata
import time
import math

from beetlesgallery.beetles_app import species_ref
from beetlesgallery.beetles_app.models import Beetles, UploadBatch, ImageAsset
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

try:
    import pandas as pd
except ImportError:
    pd = None

from beetlesgallery.beetles_app.schema import REQUIRED_COLS, MAX_ROWS, IMAGE_EXTENSIONS, MANIFEST_NAME, MANIFEST_VERSION

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
    if _is_blank(v):
        return None
    if isinstance(v, float):
        if math.isnan(v): return None
        if v.is_integer(): return str(int(v))
        return str(v).strip()
    try:
        import numpy as np
        integer_types = (int, np.integer)
    except ImportError:
        integer_types = (int,)
    if isinstance(v, integer_types):
        return str(int(v))
    return str(v).strip()

def _smart_basename(path: str) -> str:
    """Extract filename correctly regardless of OS separators."""
    if not path:
        return ""
    # Normalize separators to '/' then take the last part
    return str(path).replace("\\", "/").strip().split("/")[-1]

def _normalize_basename(name: str) -> str:
    """Normalize for matching (lowercase, unicode)."""
    if not name:
        return ""
    s = unicodedata.normalize("NFC", str(name).strip())
    # Robust basename extraction
    base = _smart_basename(s)
    return base.casefold()

def _sha256_zip_member(zf: zipfile.ZipFile, member_name: str, chunk_size: int = 1024 * 1024) -> str | None:
    try:
        h = hashlib.sha256()
        with zf.open(member_name, "r") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk: break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

def _is_image_filename(name: str) -> bool:
    name = (name or "").lower()
    return name.endswith(IMAGE_EXTENSIONS)

def _is_os_cruft(path: str) -> bool:
    if not path: return True
    if path.startswith("__MACOSX/"): return True
    name = _smart_basename(path)
    if not name: return True
    lname = name.lower()
    if name.startswith("._"): return True
    if lname in {".ds_store", "thumbs.db", "desktop.ini"}: return True
    return False

class Command(BaseCommand):
    help = "Validate uploaded XLSX files in staging and promote to validated/rejected."

    def add_arguments(self, parser):
        parser.add_argument("--id", help="Validate a specific UploadBatch UUID.")
        parser.add_argument("--dry-run", action="store_true", help="Run checks but do not change status.")

    def handle(self, *args, **opts):
        if pd is None:
            self.stderr.write(self.style.ERROR("pandas is required."))
            return

        batch_id = opts.get("id")
        dry_run = opts.get("dry_run")

        if batch_id:
            qs = UploadBatch.objects.filter(id=batch_id)
        else:
            qs = UploadBatch.objects.filter(status=UploadBatch.Status.STAGING)

        if not qs.exists():
            self.stdout.write("No batches to validate.")
            return

        for batch in qs.order_by("created_at"):
            self._validate_one(batch, dry_run=dry_run)

    def _validate_one(self, batch: UploadBatch, dry_run: bool = False):
        self.stdout.write(f"Validating {batch.id} ({batch.original_filename})")
        if not dry_run:
            batch.mark_validating()

        errors = []
        excel_path = batch.file.path

        # --- FIX: Wait for file to sync (Windows/Docker lag) ---
        retries = 5
        while not os.path.exists(excel_path) and retries > 0:
            self.stdout.write(f"Waiting for file sync: {excel_path}")
            time.sleep(1)
            retries -= 1
        # -------------------------------------------------------

        try:
            df = pd.read_excel(excel_path)
        except Exception as e:
            errors.append(f"Cannot open workbook: {e}")
            return self._finalize(batch, errors, dry_run)

        df.columns = [c.strip() for c in df.columns]
        missing = REQUIRED_COLS - set(df.columns)
        if missing:
            errors.append(f"Missing required columns: {sorted(missing)}")
            return self._finalize(batch, errors, dry_run)

        if MAX_ROWS is not None and len(df) > MAX_ROWS:
            errors.append(f"Sheet has {len(df)} rows (max {MAX_ROWS}).")
            return self._finalize(batch, errors, dry_run)

        try:
            candidate_ids = {
                _normalize_valid_id(v)
                for v in df.get("depicts_valid_name_id", [])
                if not _is_blank(v)
            }
            candidate_ids = {vid for vid in candidate_ids if vid is not None}
            ref_map = species_ref.bulk_lookup(candidate_ids) if candidate_ids else {}
        except Exception as e:
            errors.append(f"Could not access taxonomy reference: {e}")
            return self._finalize(batch, errors, dry_run)

        if not batch.zip_file:
            errors.append("No ZIP file attached to this batch.")
            return self._finalize(batch, errors, dry_run)

        zip_path = batch.zip_file.path
        if not os.path.exists(zip_path):
            errors.append(f"ZIP file missing on disk: {zip_path}")
            return self._finalize(batch, errors, dry_run)

        try:
            zf = zipfile.ZipFile(zip_path, "r")
        except Exception as e:
            errors.append(f"Cannot open ZIP: {e}")
            return self._finalize(batch, errors, dry_run)

        zip_images = [
            m for m in zf.namelist()
            if not m.endswith("/") and _is_image_filename(m) and not _is_os_cruft(m)
        ]

        basename_map: dict[str, list[str]] = {}
        for m in zip_images:
            key = _normalize_basename(m)
            basename_map.setdefault(key, []).append(m)

        if not zip_images:
            errors.append("ZIP contains no image files.")
            zf.close()
            return self._finalize(batch, errors, dry_run)

        manifest = []
        row_errors = 0

        for i, row in df.iterrows():
            row_num = i + 2
            full_path = row.get("full_path_at_import")
            valid_id = row.get("depicts_valid_name_id")

            if _is_blank(full_path):
                errors.append(f"Row {row_num}: 'full_path_at_import' must be present.")
                row_errors += 1
                continue

            if not _is_blank(valid_id):
                vid = _normalize_valid_id(valid_id)
                if vid not in ref_map:
                    errors.append(f"Row {row_num}: 'depicts_valid_name_id' not found in reference: '{vid}'.")
                    row_errors += 1
                    continue

            # Robust filename extraction (handles backslashes on Linux)
            expected_name_raw = _smart_basename(str(full_path))
            expected_key = _normalize_basename(expected_name_raw)

            if expected_key not in basename_map:
                errors.append(f"Row {row_num}: image '{expected_name_raw}' not found in ZIP.")
                row_errors += 1
                continue

            members = basename_map[expected_key]
            
            # --- AMBIGUITY RESOLVER ---
            member = None
            if len(members) == 1:
                member = members[0]
            else:
                # If duplicates exist (same filename in different folders), use full path to decide
                # Normalize Excel path: replace backslash with forward slash for comparison
                excel_norm_full = str(full_path).strip().replace("\\", "/").lower()
                
                # Check for exact suffix matches (e.g. "folder/img.jpg" matches "root/folder/img.jpg")
                matches = []
                for m in members:
                    # Check if the ZIP path ends with the Excel path (robust to relative root differences)
                    if m.lower().endswith(excel_norm_full):
                        matches.append(m)
                
                if len(matches) == 1:
                    member = matches[0]
                elif len(matches) > 1:
                    errors.append(f"Row {row_num}: multiple files match path '{full_path}' in ZIP: {matches}")
                    row_errors += 1
                    continue
                else:
                    # Fallback: if full path didn't match, maybe the Excel path was just a filename?
                    # If strictly ambiguous, error out.
                    errors.append(f"Row {row_num}: multiple files named '{expected_name_raw}' in ZIP. Could not disambiguate using '{full_path}'. Candidates: {members}")
                    row_errors += 1
                    continue
            # --------------------------

            sha = _sha256_zip_member(zf, member)
            if not sha:
                errors.append(f"Row {row_num}: failed to read/hash '{member}' in ZIP.")
                row_errors += 1
                continue

            try:
                info = zf.getinfo(member)
                size = info.file_size
            except Exception:
                size = None

            manifest.append({
                "row_num": row_num,
                "excel_index": int(i),
                "filename": expected_name_raw,
                "zip_member": member,
                "sha256": sha,
                "size": size,
            })

        if row_errors:
            zf.close()
            return self._finalize(batch, errors, dry_run)

        # Duplicate hash detection (batch internal)
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
            errors.append(f"ZIP contains duplicate images by hash: {preview}...")
            zf.close()
            return self._finalize(batch, errors, dry_run)

        # Duplicate hash detection (database)
        hashes = [m["sha256"] for m in manifest]
        existing = set(ImageAsset.objects.filter(image_sha256__in=hashes).values_list("image_sha256", flat=True))
        if existing:
            collisions = [m for m in manifest if m["sha256"] in existing][:10]
            sample = [{"filename": m["filename"], "sha256": m["sha256"]} for m in collisions]
            errors.append(f"Duplicate images already exist in DB. Examples: {sample}")
            zf.close()
            return self._finalize(batch, errors, dry_run)

        # Strictness check
        sheet_expected_norm = {_normalize_basename(m["filename"]) for m in manifest}
        zip_basenames_norm = set(basename_map.keys())
        extras_norm = sorted(zip_basenames_norm - sheet_expected_norm)
        if extras_norm:
            preview_members = []
            for k in extras_norm[:10]:
                reps = basename_map.get(k, [])
                preview_members.append(os.path.basename(reps[0]) if reps else k)
            errors.append(f"ZIP contains extra images not referenced by sheet: {preview_members}")

        zf.close()
        return self._finalize(batch, errors, dry_run, manifest=manifest, zip_path=zip_path)

    def _finalize(self, batch: UploadBatch, errors, dry_run: bool, manifest=None, zip_path=None):
        if errors:
            full_error_text = "\n".join(str(e) for e in errors)
            first = str(errors[0])
            if len(errors) > 1: first = f"{first} (+{len(errors) - 1} more)"
            short_reason = (first[:1800] + "…") if len(first) > 1800 else first
            short_reason = f"{short_reason} (see error log for full details)"

            if dry_run:
                self.stdout.write(self.style.WARNING(f"[DRY-RUN] REJECT {batch.id}: {short_reason}"))
            else:
                batch.mark_rejected_and_move(short_reason)
                try:
                    base_dir_rel = os.path.dirname(batch.file.name)
                    log_filename = f"{os.path.splitext(os.path.basename(batch.file.name))[0]}_errors.txt"
                    error_rel_path = os.path.join(base_dir_rel, log_filename)
                    default_storage.save(error_rel_path, ContentFile(full_error_text))
                    batch.error_report_file.name = error_rel_path
                    batch.save(update_fields=["error_message", "error_report_file"])
                    self.stdout.write(self.style.ERROR(f"REJECTED {batch.id}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"REJECTED {batch.id} (log write failed: {e})"))
        else:
            if dry_run:
                self.stdout.write(self.style.WARNING(f"[DRY-RUN] VALIDATE {batch.id}"))
            else:
                batch.mark_validated_and_move()
                try:
                    base_dir = os.path.dirname(batch.file.path)
                    manifest_path = os.path.join(base_dir, MANIFEST_NAME)
                    if manifest is not None:
                        with open(manifest_path, "w", encoding="utf-8") as fh:
                            json.dump({
                                    "batch_id": str(batch.id),
                                    "xlsx": batch.file.name,
                                    "zip": os.path.basename(zip_path) if zip_path else None,
                                    "count": len(manifest),
                                    "manifest_version": MANIFEST_VERSION,
                                    "rows": manifest,
                                }, fh, ensure_ascii=False, indent=2)
                    self.stdout.write(self.style.SUCCESS(f"VALIDATED {batch.id}"))
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"VALIDATED {batch.id} (manifest failed: {e})"))