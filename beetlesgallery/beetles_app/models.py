from django.db import models
from django.conf import settings
from django.utils import timezone
from django.db.models.functions import Upper

import uuid
import json, os
from simple_history.models import HistoricalRecords

# -----------------------------
# Unified Beetle record
# -----------------------------
class ImageAsset(models.Model):
    """
    Represents the physical image file and its technical/provenance metadata.
    One ImageAsset can be linked to multiple Beetles (specimens).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # --- Identification ---
    # We use SHA256 as the primary logic for deduplication
    image_sha256 = models.CharField(max_length=64, null=True, blank=True, db_index=True, unique=True)
    full_path_at_import = models.TextField(help_text="Original path/filename of the first import of this image.")

    # --- Provenance / Copyright ---
    image_institution = models.CharField(max_length=255, null=True, blank=True)
    photographer = models.CharField(max_length=255, null=True, blank=True)
    image_email = models.EmailField(null=True, blank=True)
    photo_usage_statement = models.TextField(null=True, blank=True)
    image_date_taken = models.DateField(null=True, blank=True)
    image_notes = models.TextField(null=True, blank=True)
    
    # --- Technical Metadata ---
    image_has_multiple_individuals = models.BooleanField(null=True, blank=True)
    # aspect = models.CharField(max_length=100, null=True, blank=True)
    resolution_in_ppmm = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    image_size_bytes = models.BigIntegerField(null=True, blank=True, db_index=True)

    # --- Files (Content-Addressed) ---
    image_file = models.ImageField(upload_to="", blank=True, null=True, max_length=500)
    thumb_small = models.ImageField(upload_to="", blank=True, null=True, max_length=500)

    # --- Dimensions ---
    image_width = models.PositiveIntegerField(null=True, blank=True)
    image_height = models.PositiveIntegerField(null=True, blank=True)
    thumb_width = models.PositiveIntegerField(null=True, blank=True)
    thumb_height = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Image {self.image_sha256[:8] if self.image_sha256 else 'NoSHA'} ({self.full_path_at_import})"

    # --- Helpers moved from Beetles ---
    @staticmethod
    def shard_from_sha(sha256: str) -> tuple[str, str]:
        s = (sha256 or "").lower()
        return s[:2], s[2:4]

    @staticmethod
    def path_for_display(sha256: str) -> str:
        a, b = ImageAsset.shard_from_sha(sha256)
        return f"display/{a}/{b}/{sha256}.jpg"

    @property
    def display_url(self):
        if not self.image_file:
            return ""
        name = self.image_file.name.lower()
        if name.endswith(".tif") or name.endswith(".tiff"):
            if self.image_sha256:
                path = ImageAsset.path_for_display(self.image_sha256)
                return self.image_file.storage.url(path)
        return self.image_file.url

class Beetles(models.Model):
    """
    Single wide table for image + specimen + context metadata.
    Only 'full_path_at_import' are required at upload.
    Everything else is optional (null/blank allowed) and can be validated in the pipeline.
    """

    # Stable internal primary key
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    image_asset = models.ForeignKey(
        ImageAsset, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True, 
        related_name='specimens'
    )

    aspect = models.CharField(max_length=100, null=True, blank=True)

    # --- What the image depicts ---
    depicts_specimen = models.CharField(max_length=255, null=True, blank=True)
    depicts_valid_name_id = models.CharField(max_length=255, null=True, blank=True)  
    depicts_described_name_id = models.CharField(max_length=255, null=True, blank=True)
    depicts_name_verbatim = models.CharField(max_length=255, null=True, blank=True)

    # --- Collection / specimen metadata ---
    collection_country = models.CharField(max_length=100, null=True, blank=True)
    collection_stateProvince = models.CharField(max_length=100, null=True, blank=True)
    specimen_sex = models.CharField(max_length=50, null=True, blank=True)
    specimen_type_status = models.CharField(max_length=100, null=True, blank=True)
    specimen_notes = models.TextField(null=True, blank=True)

    # --- Alternative identifiers ---
    alternative_id = models.CharField(max_length=255, null=True, blank=True)

    # --- Bulk update attribution & concurrency ---
    last_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="beetles_updates"
    )
    last_updated_at = models.DateTimeField(auto_now=True, db_index=True)
    update_notes = models.TextField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        db_table = "beetles"
        verbose_name = "Beetle Metadata"
        verbose_name_plural = "Beetles Metadata"
        indexes = [
            models.Index(fields=["depicts_valid_name_id"]),
            models.Index(fields=["collection_country", "collection_stateProvince"]),
            models.Index(Upper("depicts_specimen"), name="beetles_u_specimen_idx"),
            models.Index(Upper("collection_country"), name="beetles_u_country_idx"),
            models.Index(Upper("specimen_sex"), name="beetles_u_sex_idx"),
            models.Index(Upper("specimen_type_status"), name="beetles_u_type_status_idx"),
        ]

    def __str__(self):
        # Prefer a human-friendly alternative_id if present; else a short UUID
        label = self.alternative_id or str(self.id)[:8].strip()
        return f"{label} | {self.depicts_valid_name_id}"


    # ---------
    # Helpers: content-addressed relative paths based on sha256
    # ---------
    @staticmethod
    def path_for_display(sha256: str) -> str:
        """
        Path for a web-friendly JPEG version of the original image.
        Used primarily for displaying TIFFs.
        """
        a, b = Beetles.shard_from_sha(sha256)
        return f"display/{a}/{b}/{sha256}.jpg"

    @property
    def display_url(self):
        """Delegates display URL generation to the linked ImageAsset."""
        if self.image_asset: 
            return self.image_asset.display_url
        return ""

    @property
    def resolved_image_file(self):
        """Access the underlying ImageField from the asset."""
        if self.image_asset: 
            return self.image_asset.image_file
        return None

    @staticmethod
    def shard_from_sha(sha256: str) -> tuple[str, str]:
        return ImageAsset.shard_from_sha(sha256)

    @staticmethod
    def path_for_display(sha256: str) -> str:
        return ImageAsset.path_for_display(sha256)

    @staticmethod
    def path_for_original(sha256: str, ext: str) -> str:
        a, b = ImageAsset.shard_from_sha(sha256)
        ext = (ext or "").lstrip(".").lower() or "bin"
        return f"originals/{a}/{b}/{sha256}.{ext}"

    @staticmethod
    def path_for_thumb96(sha256: str, webp: bool = True) -> str:
        a, b = ImageAsset.shard_from_sha(sha256)
        suffix = "webp" if webp else "jpg"
        return f"thumbnails/{a}/{b}/{sha256}_96.{suffix}"


# -----------------------------
# UploadBatch
# -----------------------------

import uuid
import hashlib
import os
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

def staging_upload_path_csv(instance, filename):
    # uploads/staging/YYYY/MM/<batch-id>.csv
    return f"uploads/staging/{timezone.now():%Y/%m}/{instance.id}.csv"

# Alias for historical migrations
staging_upload_path_xlsx = staging_upload_path_csv

def staging_upload_path_zip(instance, filename):
    # uploads/staging/YYYY/MM/<batch-id>.zip
    return f"uploads/staging/{timezone.now():%Y/%m}/{instance.id}.zip"

# # Back-compat: old migrations import this by name
# def staging_upload_path(instance, filename):
#     return staging_upload_path_csv(instance, filename)


class UploadBatch(models.Model):
    """
    A single DB row = pair of uploaded files (CSV + ZIP).
    This table gives you observability and control across the pipeline:

      Step 1 (upload):      status='staging'
      Step 2 (validation):  status='validating' -> 'validated' or 'rejected'
      Step 3 (import):      status='imported' (after loading into domain tables)
    """

    class Status(models.TextChoices):
        STAGING    = "staging", "Staging"                          # just received / saved
        VALIDATING = "validating", "Validating"                    # validation job running
        VALIDATED  = "validated", "Validation passed"              # passed checks; safe to import
        REJECTED   = "rejected", "Validation failed"               # failed checks
        IMPORTED   = "imported", "Import successful"               # loaded into DB
        IMPORT_FAILED = "import_failed", "Import failed"           # import_beetles failed 

    # Stable, opaque identifier you can expose in URLs and logs
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Who uploaded it (optional)
    uploaded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="upload_batches"
    )

    # The actual file on disk (under MEDIA_ROOT). At upload time this will
    # be in uploads/staging/... via staging_upload_path_csv().
    file = models.FileField(upload_to=staging_upload_path_csv, max_length=500)
    zip_file = models.FileField(upload_to=staging_upload_path_zip, max_length=500, blank=True)

    # Snapshot of what the user picked, but *not* used for storage.
    original_filename = models.CharField(max_length=300)

    # Useful for sanity checks, dedupe heuristics, reporting
    size_bytes = models.BigIntegerField(default=0)

    # Integrity/dedup: set after saving the file (computed from disk)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)

    # Lifecycle flag for the pipeline
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.STAGING, db_index=True
    )

    # If validation fails, record the reason here for UI/admin
    error_message = models.TextField(blank=True)

    error_report_file = models.FileField(
        upload_to="upload_error_logs/",
        blank=True,
        null=True,
        help_text="Full validation error log for this batch.",
    )

    # Ops/audit timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Upload Batch"
        verbose_name_plural = "Upload Batches"

    def __str__(self):
        return f"UploadBatch({self.id}) {self.original_filename} [{self.status}]"

    # -----------------------------
    # Utilities used by validators/importers
    # -----------------------------

    def compute_sha256_from_disk(self) -> str:
        """
        Compute a SHA-256 checksum of the *stored* file in chunks
        (memory-safe for large files). Call this right after saving the file.
        """
        h = hashlib.sha256()
        with open(self.file.path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        self.sha256 = digest
        return digest


    def _relocate_files(self, target_subdir: str) -> None:
        """
        [FLOW: Used at Step 2 and Step 3]
        Atomically move the files on disk to a new lifecycle folder and update
        the FileFields' names so Django serves the new paths.
        """
        # Build destination path under MEDIA_ROOT preserving the existing filename.
        # file.name is a *relative* path like 'uploads/staging/YYYY/MM/<uuid>.csv'
        for f in (self.file, getattr(self, "zip_file", None)):
            if not f:
                continue
            base_dir = os.path.join(target_subdir, timezone.now().strftime("%Y/%m"))
            src_abs = f.path
            filename_only = os.path.basename(f.name)  # <uuid>.<ext>
            dst_rel = os.path.join(base_dir, filename_only)
            dst_abs = self.file.storage.path(dst_rel)
            
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            os.replace(src_abs, dst_abs)
            f.name = dst_rel
        self.save(update_fields=["file"] + (["zip_file"] if getattr(self, "zip_file", None) else []))


    # Convenience markers (keep heavy validation OUT of models.py; see notes below)
    def mark_validating(self) -> None:
        self.status = self.Status.VALIDATING
        self.save(update_fields=["status"])

    def mark_validated_and_move(self) -> None:
        # Move to validated/, set status + timestamp atomically from the DB perspective
        with transaction.atomic():
            self._relocate_files("uploads/validated")
            self.status = self.Status.VALIDATED
            self.validated_at = timezone.now()
            self.error_message = ""
            self.save(update_fields=["status", "validated_at", "error_message"])

    def mark_rejected_and_move(self, reason: str) -> None:
        with transaction.atomic():
            self._relocate_files("uploads/rejected")
            self.status = self.Status.REJECTED
            self.error_message = (reason or "")[:2000]
            self.save(update_fields=["status", "error_message"])

    def mark_imported_and_archive(self) -> None:
        """
        Move CSV/ZIP and known sidecars (manifest.json, archive.json) from validated->archived,
        then mark the batch as IMPORTED.
        """
        with transaction.atomic():
            # capture source & destination dirs so we can move sidecars too
            src_dir_abs = os.path.dirname(self.file.path)

            # move the primary files (updates self.file/.zip_file names)
            self._relocate_files("uploads/archived")

            dst_dir_abs = os.path.dirname(self.file.path)

            # best-effort move for known sidecars living alongside the CSV
            def _move_sidecar(fname: str):
                if not fname:
                    return
                src = os.path.join(src_dir_abs, fname)
                dst = os.path.join(dst_dir_abs, fname)
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except Exception:
                        # ignore; sidecar isn't critical for marking imported
                        pass

            _move_sidecar("manifest.json")
            _move_sidecar("archive.json")

            self.status = self.Status.IMPORTED
            self.imported_at = timezone.now()
            self.save(update_fields=["status", "imported_at"])

    def mark_import_failed(self, reason: str, move_to_failed_folder: bool = False) -> None:
        """
        Mark the batch as import_failed and record the reason.
        """
        from django.utils import timezone
        self.error_message = (f"IMPORT ERROR: {reason or ''}")[:2000]
        if move_to_failed_folder:
            self._relocate_files("uploads/failed_import")
        self.status = self.Status.IMPORT_FAILED
        self.save(update_fields=["status", "error_message"])

    def _current_dir_abs(self) -> str:
        """Return absolute directory containing the CSV for this batch."""
        return os.path.dirname(self.file.path)

    def write_archive_json(self, *, imported_count: int, records_summary: list[dict] | None = None, notes: str = "") -> str:
        """
        Create/overwrite archive.json next to the CSV that’s currently on disk.
        Call this AFTER a successful import, BEFORE archiving.
        Returns the absolute path written.
        """
        from django.utils import timezone

        payload = {
            "batch_id": str(self.id),
            "csv": self.file.name,                             # storage-relative
            "zip": self.zip_file.name if getattr(self, "zip_file", None) else None,
            "sha256": self.sha256,
            "imported_at": timezone.now().isoformat(),
            "imported_count": int(imported_count),
            "schema_version": 1,
            "notes": notes or "",
            "records": records_summary or [],                   # keep lean; per-row summaries if you have them
        }

        dir_abs = self._current_dir_abs()
        path_abs = os.path.join(dir_abs, "archive.json")
        with open(path_abs, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return path_abs

# -----------------------------
# UpdateBatch  (bulk metadata updates by UUID)
# -----------------------------

def updates_staging_path_csv(instance, filename):
    return f"updates/staging/{timezone.now():%Y/%m}/{instance.id}.csv"

# Alias for historical migrations
updates_staging_path_xlsx = updates_staging_path_csv

def updates_reports_path(instance, filename):
    # updates/reports/YYYY/MM/<batch-id>.csv (or .json)
    return f"updates/reports/{timezone.now():%Y/%m}/{instance.id}/{filename}"

class UpdateBatch(models.Model):
    """
    Staff-only CSV-based update batches. Validates against current DB + valid_species,
    supports dry-run diffing, and applies all-or-nothing in a single transaction.
    """

    class Status(models.TextChoices):
        STAGING      = "staging", "Staging"
        VALIDATING   = "validating", "Validating"
        VALIDATED    = "validated", "Validation passed"
        REJECTED     = "rejected", "Validation failed"
        APPLIED      = "applied", "Applied"
        APPLY_FAILED = "apply_failed", "Apply failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    uploaded_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="update_batches"
    )

    # CSV uploaded by staff
    file = models.FileField(upload_to=updates_staging_path_csv, max_length=500)
    original_filename = models.CharField(max_length=300)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)

    # Lifecycle/status
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.STAGING, db_index=True
    )
    error_message = models.TextField(blank=True)

    # Reference version pinning (valid_species at validation time)
    species_version_at_validation = models.CharField(max_length=64, blank=True)
    species_label_at_validation = models.CharField(max_length=200, blank=True)

    # Counts for audit/summary
    rows_total = models.PositiveIntegerField(default=0)
    rows_matched = models.PositiveIntegerField(default=0)    # UUIDs found
    rows_changed = models.PositiveIntegerField(default=0)
    rows_unchanged = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)

    # Ops/audit timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    # Validation/apply report artifact(s)
    report_file = models.FileField(upload_to=updates_reports_path, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Update Batch"
        verbose_name_plural = "Update Batches"
        indexes = [
            models.Index(fields=["uploaded_by", "status"]),
        ]

    def __str__(self):
        return f"UpdateBatch({self.id}) {self.original_filename} [{self.status}]"

    # ---------- Utilities ----------
    def compute_sha256_from_disk(self) -> str:
        """
        Compute SHA-256 checksum of the stored CSV (memory-safe).
        """
        h = hashlib.sha256()
        with open(self.file.path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        self.sha256 = digest
        return digest

    def _relocate_file(self, target_subdir: str) -> None:
        """
        Move the CSV to a lifecycle folder and update the FileField name accordingly.
        """
        base_dir = os.path.join(target_subdir, timezone.now().strftime("%Y/%m"))
        src_abs = self.file.path
        filename_only = os.path.basename(self.file.name)  # <uuid>.csv
        dst_rel = os.path.join(base_dir, filename_only)
        dst_abs = self.file.storage.path(dst_rel)

        os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
        os.replace(src_abs, dst_abs)
        self.file.name = dst_rel
        self.save(update_fields=["file"])

    # State transitions
    def mark_validating(self) -> None:
        self.status = self.Status.VALIDATING
        self.save(update_fields=["status"])

    def mark_validated_and_move(self) -> None:
        with transaction.atomic():
            self._relocate_file("updates/validated")
            self.status = self.Status.VALIDATED
            self.validated_at = timezone.now()
            self.error_message = ""
            self.save(update_fields=["status", "validated_at", "error_message"])

    def mark_rejected_and_move(self, reason: str) -> None:
        with transaction.atomic():
            self._relocate_file("updates/rejected")
            self.status = self.Status.REJECTED
            self.error_message = (reason or "")[:2000]
            self.save(update_fields=["status", "error_message"])

    def mark_applied_and_archive(self) -> None:
        with transaction.atomic():
            self._relocate_file("updates/archived")
            self.status = self.Status.APPLIED
            self.applied_at = timezone.now()
            self.save(update_fields=["status", "applied_at"])

    def mark_apply_failed(self, reason: str, move_to_failed_folder: bool = False) -> None:
        self.error_message = (f"APPLY ERROR: {reason or ''}")[:2000]
        if move_to_failed_folder:
            self._relocate_file("updates/failed_apply")
        self.status = self.Status.APPLY_FAILED
        self.save(update_fields=["status", "error_message"])


# -----------------------------
# DownloadJob
# -----------------------------
class DownloadJob(models.Model):
    class Status(models.TextChoices):
        PENDING   = "pending", "Pending"
        BUILDING  = "building", "Building"
        READY     = "ready", "Ready"
        FAILED    = "failed", "Failed"
        EXPIRED   = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    requested_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="download_jobs"
    )

    # Selection shape
    selection_mode = models.CharField(
        max_length=8, choices=[("ids", "IDs"), ("query", "Query")], db_index=True
    )
    query_string = models.TextField(blank=True)        # when selection_mode='query'
    selected_ids_json = models.TextField(blank=True)   # when selection_mode='ids' (JSON list of UUID strings)
    total_requested = models.PositiveIntegerField(default=0)

    # Lifecycle/status
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Results (filled in when READY)
    csv_file = models.FileField(upload_to="downloads/results/%Y/%m", blank=True)
    zip_file = models.FileField(upload_to="downloads/results/%Y/%m", blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["requested_by", "status"])]

    def __str__(self):
        return f"DownloadJob({self.id}) [{self.status}] {self.selection_mode}:{self.total_requested}"

    # Helpers for templates / workers
    def set_ids(self, ids_list):
        try:
            self.selected_ids_json = json.dumps([str(x) for x in ids_list])
        except Exception:
            self.selected_ids_json = "[]"

    def get_ids(self):
        try:
            return json.loads(self.selected_ids_json or "[]")
        except Exception:
            return []

    def get_readable_query(self):
        """
        Parses the JSON query string to return a human-readable summary of filters
        (e.g., 'Text: "bear"; Genus: Cyclommatus; Size >= 10MB').
        """
        qs = (self.query_string or "").strip()
        if not qs:
            return "—"
            
        # 1. Try parsing as JSON (New format used by start_batch_download)
        if qs.startswith("{"):
            try:
                data = json.loads(qs)
                parts = []
                
                # Text search
                if text := data.get("q"):
                    parts.append(f"Text: “{text}”")
                    
                # Facet filters
                filters = data.get("filters", {})
                for k, vals in filters.items():
                    # format key nicely (e.g. 'subfamily' -> 'Subfamily')
                    label = k.replace("_", " ").title()
                    # format vals
                    val_str = ", ".join(str(v) for v in vals)
                    parts.append(f"{label}: {val_str}")
                    
                # Ranges
                ranges = data.get("ranges", {})
                if r := ranges.get("size_min"): parts.append(f"Size ≥ {r}MB")
                if r := ranges.get("size_max"): parts.append(f"Size ≤ {r}MB")
                if r := ranges.get("res_min"): parts.append(f"Res ≥ {r}")
                if r := ranges.get("res_max"): parts.append(f"Res ≤ {r}")

                return "; ".join(parts) if parts else "All records"
            except json.JSONDecodeError:
                pass # Fall through to legacy plain text

        # 2. Legacy/Plain text (backwards compatibility)
        return qs
