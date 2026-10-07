from django.db import models
from django.conf import settings
from django.utils import timezone
from django.db.models.functions import Lower, Trim, Upper
from django.contrib.postgres.indexes import GinIndex

import uuid
import json
import os
from simple_history.models import HistoricalRecords
from .schema import LEGACY_MANIFEST_NAME, archive_name, manifest_name
from treebeard.mp_tree import MP_Node

# -----------------------------
# Unified Beetle record
# -----------------------------
# Change reasons on the ROI history records written when a whole image is (un)validated (label_history.py)
IMAGE_VALIDATED = "Validated with the whole image"
IMAGE_UNVALIDATED = "Unvalidated with the whole image"


def record_roi_history(roi_ids, user, reason):
    """Bulk updates skip save() and so leave no history: write one record per changed ROI, so its history shows it."""
    if roi_ids:
        Beetles.history.bulk_history_create(list(Beetles.objects.filter(id__in=roi_ids)), update=True,
                                            default_user=user, default_change_reason=reason)


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

    is_validated = models.BooleanField(
        default=False, 
        help_text="Has this image been fully reviewed/validated?"
    )
    last_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_image_assets',
        help_text="User who last updated this image or its metadata"
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_images",
        help_text="Who added this photo to the gallery, when known (the AI page sets it for a signed-in visitor).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(
        default=False, 
        db_index=True,
        help_text="Soft deletion flag. If true, hidden from UI."
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    history = HistoricalRecords()

    def delete(self, using=None, keep_parents=False, deleted_by=None):
        """Soft delete the image and cascade soft-delete to all its ROIs."""
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        if deleted_by:
            self.last_updated_by = deleted_by
        self.save(update_fields=['is_deleted', 'deleted_at', 'last_updated_by', 'updated_at'])
        
        # Cascade soft-delete to associated Beetles/ROIs
        for beetle in self.specimens.filter(is_deleted=False):
            beetle.delete(deleted_by=deleted_by)

    def unvalidate(self, user=None):
        """
        Staff action: move the image and all of its active ROIs back to unvalidated.
        Clears the ROI validation audit stamps.
        """
        roi_updates = dict(bbox_is_validated=False, bbox_validated_by=None, bbox_validated_at=None)
        if user:
            roi_updates["last_updated_by"] = user
            self.last_updated_by = user
        changed = list(self.specimens.filter(is_deleted=False, bbox_is_validated=True).values_list("id", flat=True))
        self.specimens.filter(is_deleted=False).update(**roi_updates)
        record_roi_history(changed, user, IMAGE_UNVALIDATED)
        self.is_validated = False
        self.save(update_fields=['is_validated', 'last_updated_by', 'updated_at'])

    def validate(self, user=None):
        """
        Staff action: validate every active ROI that has a bounding box.
        Follows the same rule as the Beetles post_save signal: an image with no
        boxed ROIs cannot be validated. Returns the resulting is_validated.
        """
        boxed_rois = self.specimens.filter(is_deleted=False, bbox_x__isnull=False)
        roi_updates = dict(bbox_is_validated=True, bbox_validated_at=timezone.now())
        if user:
            roi_updates["bbox_validated_by"] = user
            roi_updates["last_updated_by"] = user
            self.last_updated_by = user
        changed = list(boxed_rois.filter(bbox_is_validated=False).values_list("id", flat=True))
        boxed_rois.filter(bbox_is_validated=False).update(**roi_updates)
        from beetlesgallery.beetles_app import identification
        identification.vouch(list(boxed_rois), user)   # validated names are Expert IDs at least
        record_roi_history(changed, user, IMAGE_VALIDATED)
        self.is_validated = boxed_rois.exists()
        self.save(update_fields=['is_validated', 'last_updated_by', 'updated_at'])
        return self.is_validated

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

    taxon = models.ForeignKey(
        'Taxon', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='specimens',
        help_text="Relational link to the materialized taxonomy tree."
    )

    aspect = models.CharField(max_length=100, null=True, blank=True)

    # --- What the image depicts ---
    depicts_specimen = models.CharField(max_length=255, null=True, blank=True)
    depicts_valid_name_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)  
    depicts_described_name_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    depicts_name_verbatim = models.CharField(max_length=255, null=True, blank=True)

    # --- How reliable the name is (#390): an identification tier, most reliable first ---
    #   Taxonomist ID  a taxonomist examined it, or the vial / specimen label says so
    #   Expert ID      a curator or proven expert named or validated it (game consensus a curator accepted, too)
    #   Community ID   players' answers, not yet checked
    #   External ID    an external database or website
    #   (blank)        No ID: not recorded
    # Only Taxonomist ID and Expert ID names can be validated. Every name an ROI was given is kept (RoiName); the
    # ROI shows the most reliable one (identification.py).
    class LabelSource(models.TextChoices):
        TAXONOMIST = "taxonomist", "Taxonomist ID"
        EXPERT = "expert", "Expert ID"
        COMMUNITY = "community", "Community ID"
        EXTERNAL = "external", "External ID"

    label_source = models.CharField(
        max_length=20, choices=LabelSource.choices, blank=True, default="",
        help_text="How reliable the name is (identification tier). Blank: No ID (not recorded).",
    )
    label_source_detail = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Who examined it, or which database or website (name or URL).",
    )

    # --- Collection / specimen metadata ---
    collection_country = models.CharField(max_length=100, null=True, blank=True)
    collection_stateProvince = models.CharField(max_length=100, null=True, blank=True)
    specimen_sex = models.CharField(max_length=50, null=True, blank=True)
    specimen_type_status = models.CharField(max_length=100, null=True, blank=True)
    specimen_notes = models.TextField(null=True, blank=True)

    # --- Alternative identifiers ---
    alias_id = models.CharField(
        max_length=255, null=True, blank=True,
        help_text="Your own ID for this record, e.g. a catalogue number or file name from your database. Optional; we keep it unchanged and include it in every download so you can link our records back to yours. (Was called alternative_id.)",
    )

    # --- Bulk update attribution & concurrency ---
    last_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="beetles_updates"
    )
    last_updated_at = models.DateTimeField(auto_now=True, db_index=True)
    update_notes = models.TextField(null=True, blank=True)

    # --- Bounding Box Coordinates (normalized 0-1) ---
    # When bbox_x is not NULL, this Beetle represents a specific annotation on the image
    bbox_x = models.FloatField(
        null=True, blank=True,
        help_text="Normalized left edge position (0 = left side, 1 = right side)"
    )
    bbox_y = models.FloatField(
        null=True, blank=True,
        help_text="Normalized top edge position (0 = top, 1 = bottom)"
    )
    bbox_width = models.FloatField(
        null=True, blank=True,
        help_text="Normalized width (as fraction of image width, 0-1)"
    )
    bbox_height = models.FloatField(
        null=True, blank=True,
        help_text="Normalized height (as fraction of image height, 0-1)"
    )

    # --- Bounding Box Validation Workflow ---
    bbox_is_validated = models.BooleanField(
        default=False,
        help_text="Whether this annotation has been verified by a staff member"
    )
    bbox_validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='validated_beetle_bboxes',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Staff member who validated this annotation"
    )
    bbox_validated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this annotation was validated"
    )

    # --- Bounding Box Audit Trail ---
    bbox_created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='created_beetle_bboxes',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who created this annotation"
    )
    bbox_created_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this annotation was created"
    )

    # --- Soft Delete ---
    is_deleted = models.BooleanField(
        default=False, 
        db_index=True,
        help_text="Soft deletion flag."
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    def delete(self, using=None, keep_parents=False, deleted_by=None):
        """Soft delete the beetle (ROI/specimen)."""
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        if deleted_by:
            self.last_updated_by = deleted_by
        self.save(update_fields=['is_deleted', 'deleted_at', 'last_updated_by', 'last_updated_at'])
    
    def save(self, *args, **kwargs):
        """
        Automatically keep the relational Taxon FK in sync with the string ID 
        when a single record is saved (e.g. from the Annotation Tool).
        """
        update_fields = kwargs.get('update_fields')
        # If doing a targeted save (like soft-delete), skip the taxonomy sync unless relevant
        if update_fields is None or 'depicts_valid_name_id' in update_fields or 'taxon' in update_fields:
            if self.depicts_valid_name_id:
                from beetlesgallery.beetles_app.models import Taxon
                needs_update = True
                
                # If a taxon is already linked, check if it matches the current string ID
                if self.taxon_id:
                    try:
                        if self.taxon and self.taxon.valid_species_id == self.depicts_valid_name_id:
                            needs_update = False
                    except Taxon.DoesNotExist:
                        pass
                
                if needs_update:
                    self.taxon = Taxon.objects.filter(valid_species_id=self.depicts_valid_name_id).first()
                    # Ensure taxon gets saved if update_fields is explicitly strictly defined
                    if update_fields is not None and 'taxon' not in update_fields:
                        kwargs['update_fields'] = list(update_fields) + ['taxon']
            else:
                self.taxon = None
                if update_fields is not None and 'taxon' not in update_fields:
                    kwargs['update_fields'] = list(update_fields) + ['taxon']

        # Identification tiers: keep the most reliable name, validated names are Expert IDs at least, and every
        # name given is recorded (identification.py)
        from beetlesgallery.beetles_app import identification
        before = kwargs.get('update_fields')
        fields = identification.before_save(self, before)
        if before is not None:
            kwargs['update_fields'] = fields
        super().save(*args, **kwargs)
        identification.after_save(self)

    class Meta:
        db_table = "beetles"
        verbose_name = "Region of Interest (ROI)"
        verbose_name_plural = "Regions of Interest (ROIs)"
        indexes = [
            models.Index(fields=["depicts_valid_name_id"]),
            models.Index(fields=["collection_country", "collection_stateProvince"]),
            models.Index(Upper("depicts_specimen"), name="beetles_u_specimen_idx"),
            models.Index(Upper("collection_country"), name="beetles_u_country_idx"),
            models.Index(Upper("specimen_sex"), name="beetles_u_sex_idx"),
            models.Index(Upper("specimen_type_status"), name="beetles_u_type_status_idx"),
            models.Index(fields=["image_asset", "id"], name="beetles_asset_id_idx"),
            # same_specimen (game, #386) looks photos up by Lower(Trim(depicts_specimen)) on every batch and review
            models.Index(Lower(Trim("depicts_specimen")), name="beetles_lower_specimen_idx"),
        ]

    def __str__(self):
        # Prefer a human-friendly alias_id if present; else a short UUID
        label = self.alias_id or str(self.id)[:8].strip()
        return f"{label} | {self.depicts_valid_name_id}"

    # ---------
    # Bounding Box Helper Methods
    # ---------
    def has_bbox(self) -> bool:
        """Check if this Beetle record has bounding box annotation data."""
        return self.bbox_x is not None

    def unvalidate(self, user=None):
        """
        Unvalidates this ROI. Its save will trigger the post_save signal,
        which automatically moves the parent ImageAsset to unvalidated as well.
        """
        self.bbox_is_validated = False
        self.bbox_validated_by = None
        self.bbox_validated_at = None
        if user:
            self.last_updated_by = user
        self.save(update_fields=['bbox_is_validated', 'bbox_validated_by', 'bbox_validated_at', 'last_updated_by', 'last_updated_at'])

    def validate(self, user=None):
        """
        Validates this ROI. Its save will trigger the post_save signal.
        If all other ROIs on the image are validated, the parent ImageAsset
        will automatically move to validated as well.
        """
        self.bbox_is_validated = True
        if user:
            self.bbox_validated_by = user
            self.last_updated_by = user
        self.bbox_validated_at = timezone.now()
        self.save(update_fields=['bbox_is_validated', 'bbox_validated_by', 'bbox_validated_at', 'last_updated_by', 'last_updated_at'])

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

    # ---------
    # Helpers: content-addressed relative paths based on sha256
    # ---------
    @staticmethod
    def shard_from_sha(sha256: str) -> tuple[str, str]:
        return ImageAsset.shard_from_sha(sha256)

    @staticmethod
    def path_for_display(sha256: str) -> str:
        """Path for a web-friendly JPEG version of the original image, used to display TIFFs."""
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

import hashlib
from django.db import transaction
from django.contrib.auth import get_user_model

User = get_user_model()

def staging_upload_path_csv(instance, filename):
    # uploads/staging/YYYY/MM/<batch-id>.csv
    return f"uploads/staging/{timezone.now():%Y/%m}/{instance.id}.csv"

# Alias for historical migrations
staging_upload_path_xlsx = staging_upload_path_csv

def staging_upload_path_zip(instance, filename):
    # uploads/staging/YYYY/MM/<batch-id>.zip
    return f"uploads/staging/{timezone.now():%Y/%m}/{instance.id}.zip"


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
        Move CSV/ZIP and the batch's sidecars (its manifest and archive record, see schema.py) from
        validated->archived, then mark the batch as IMPORTED.
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

            manifest = manifest_name(self.id)
            if not os.path.exists(os.path.join(src_dir_abs, manifest)):
                manifest = LEGACY_MANIFEST_NAME   # validated before manifests were kept per batch (#350)
            _move_sidecar(manifest)
            _move_sidecar(archive_name(self.id))

            self.status = self.Status.IMPORTED
            self.imported_at = timezone.now()
            self.save(update_fields=["status", "imported_at"])

    def mark_import_failed(self, reason: str, move_to_failed_folder: bool = False) -> None:
        """
        Mark the batch as import_failed and record the reason.
        """
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
        Create/overwrite this batch's archive record (archive_<id>.json) next to the CSV that’s currently on disk.
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
        path_abs = os.path.join(dir_abs, archive_name(self.id))
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

    include_images = models.BooleanField(default=True, help_text="If False, generate CSV only (no ZIP).")
    
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


# -----------------------------
# ImageLock (Session-based image viewing lock)
# -----------------------------
class ImageLock(models.Model):
    """
    Tracks which user is currently viewing/editing an image in the data annotator.
    Prevents concurrent editing conflicts by showing warnings when another user
    has the image open.

    Locks automatically expire after LOCK_TIMEOUT_MINUTES of inactivity.
    """
    LOCK_TIMEOUT_MINUTES = 5

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    image_asset = models.OneToOneField(
        ImageAsset,
        on_delete=models.CASCADE,
        related_name='active_lock',
        help_text="The image that is currently locked"
    )

    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='image_locks',
        help_text="User who has this image open"
    )

    locked_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When the lock was acquired"
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Last activity timestamp (for auto-expiry)"
    )

    class Meta:
        db_table = "image_locks"
        verbose_name = "Image Lock"
        verbose_name_plural = "Image Locks"
        indexes = [
            models.Index(fields=["locked_by", "updated_at"]),
        ]

    def __str__(self):
        return f"Lock on {self.image_asset.id} by {self.locked_by.username}"

    def is_expired(self) -> bool:
        """Check if this lock has expired based on last activity."""
        from datetime import timedelta
        from django.utils import timezone
        timeout = timedelta(minutes=self.LOCK_TIMEOUT_MINUTES)
        return timezone.now() - self.updated_at > timeout

    @classmethod
    def cleanup_expired_locks(cls):
        """Remove all expired locks. Can be called periodically or before checking locks."""
        from datetime import timedelta
        from django.utils import timezone
        timeout = timedelta(minutes=cls.LOCK_TIMEOUT_MINUTES)
        cutoff = timezone.now() - timeout
        expired = cls.objects.filter(updated_at__lt=cutoff)
        count = expired.count()
        expired.delete()
        return count


# -----------------------------
# Relational Taxonomy & Metadata
# -----------------------------
class Taxon(MP_Node):
    """
    Relational representation of valid_species.csv using a Materialized Path tree.
    Inheriting from MP_Node provides 'path', 'depth', and 'numchild' fields natively.
    """
    valid_species_id = models.CharField(
        max_length=255, 
        unique=True, 
        db_index=True,
        help_text="Original identifier mapping to valid_species.csv"
    )
    scientific_name = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    scientific_name_authority = models.CharField(max_length=255, blank=True, null=True)

    # Taxonomic Ranks
    subfamily = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    tribe = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    subtribe = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    genus = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    species = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    subspecies = models.CharField(max_length=100, blank=True, null=True)

    # Nomenclatural Data
    authority = models.CharField(max_length=255, blank=True, null=True)
    authority_year = models.CharField(max_length=50, blank=True, null=True)
    original_genus = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    # Audit Trail
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    node_order_by = ['scientific_name']

    class Meta:
        db_table = "taxon"
        verbose_name = "Taxon"
        verbose_name_plural = "Taxa"
        indexes = [
            models.Index(fields=["scientific_name"]),
            models.Index(fields=["genus", "species"]),
            GinIndex(
                name='taxon_orig_genus_gin', 
                fields=['original_genus'], 
                opclasses=['gin_trgm_ops']
            ),
        ]

    def __str__(self):
        return self.scientific_name or self.valid_species_id


class Synonym(models.Model):
    """
    Relational representation of described_names.csv.
    """
    taxon = models.ForeignKey(
        Taxon, 
        on_delete=models.CASCADE, 
        related_name='synonyms',
        db_index=True,
        help_text="The valid taxon this synonym maps to."
    )
    name_id = models.CharField(
        max_length=255, 
        unique=True, 
        db_index=True,
        help_text="Original identifier mapping to described_names.csv"
    )
    described_scientific_name = models.CharField(max_length=255, db_index=True)
    described_scientific_name_authority = models.CharField(max_length=255, blank=True, null=True)

    # Synonymous Ranks
    genus = models.CharField(max_length=100, blank=True, null=True)
    species = models.CharField(max_length=100, blank=True, null=True)
    subspecies = models.CharField(max_length=100, blank=True, null=True)
    
    authority = models.CharField(max_length=255, blank=True, null=True)
    year = models.CharField(max_length=50, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "synonym"
        verbose_name = "Synonym"
        verbose_name_plural = "Synonyms"
        indexes = [
            GinIndex(
                name='syn_desc_name_gin', 
                fields=['described_scientific_name'], 
                opclasses=['gin_trgm_ops']
            ),
        ]

    def __str__(self):
        return self.described_scientific_name


class CategoryMapping(models.Model):
    """
    Relational representation of category_mapping.json for annotations.
    """
    category_id = models.IntegerField(primary_key=True, help_text="Numeric ID used in YOLO/COCO.")
    name = models.CharField(max_length=255, db_index=True)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    supercategory = models.CharField(max_length=100, default='beetle', db_index=True)

    class Meta:
        db_table = "category_mapping"
        verbose_name = "Category Mapping"
        verbose_name_plural = "Category Mappings"

    def __str__(self):
        return self.full_name or self.name


from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

@receiver(post_save, sender=Beetles)
@receiver(post_delete, sender=Beetles)
def update_image_asset_validation_status(sender, instance, **kwargs):
    """
    Automatically orchestrates the validation status of the parent ImageAsset 
    whenever a child Beetles (ROI) record is saved, modified, or deleted.
    """
    # Defensive check: Ensure the ROI is attached to an image
    if not instance.image_asset_id:
        return

    # Use .first() rather than direct lookup to prevent crash if ImageAsset is being deleted
    image_asset = ImageAsset.objects.filter(id=instance.image_asset_id).first()
    if not image_asset:
        return

    # Get all active (non-deleted) ROIs for this image that actually have a bounding box drawn
    active_rois = Beetles.objects.filter(
        image_asset=image_asset,
        bbox_x__isnull=False,
        is_deleted=False
    )

    # Scenario A: No bounding boxes exist on the image
    if not active_rois.exists():
        if image_asset.is_validated:
            image_asset.is_validated = False
            # Use update_fields to bypass saving the whole model and avoid infinite loops
            image_asset.save(update_fields=['is_validated'])
        return

    # Scenario B: Bounding boxes exist. Are they ALL validated?
    unvalidated_count = active_rois.filter(bbox_is_validated=False).count()
    
    # If unvalidated_count is 0, all boxes are validated
    should_be_validated = (unvalidated_count == 0)

    # Only execute a database write if the state actually needs to change
    if image_asset.is_validated != should_be_validated:
        image_asset.is_validated = should_be_validated
        image_asset.save(update_fields=['is_validated'])


# -----------------------------
# Ecological Interaction Models (Isolated Dataset)
# -----------------------------
class PathogenInteraction(models.Model):
    """
    Standalone table for reported pathogens and parasites associated with bark and ambrosia beetles.
    Kept completely isolated from production ImageAsset and Beetles models.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record_block_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    record_number = models.CharField(max_length=32, blank=True, null=True)
    beetle_host = models.CharField(max_length=255, db_index=True, help_text="Scientific name of beetle host")
    beetle_host_id = models.CharField(max_length=64, blank=True, null=True)
    pathogen = models.CharField(max_length=255, db_index=True, help_text="Pathogen or parasite taxon")
    category = models.CharField(max_length=64, db_index=True, help_text="Fungi, Nematode, Microsporidia, etc.")
    organism_source = models.CharField(max_length=255, blank=True, null=True)
    infection_site = models.CharField(max_length=255, blank=True, null=True)
    ecological_relationship = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    identification_method = models.CharField(max_length=255, blank=True, null=True)
    validation_type = models.CharField(max_length=128, blank=True, null=True)
    experimental_conditions = models.CharField(max_length=255, blank=True, null=True)
    country_or_region = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    year = models.CharField(max_length=16, blank=True, null=True)
    source = models.CharField(max_length=255, blank=True, null=True)
    title = models.TextField(blank=True, null=True)
    doi_or_full_text = models.TextField(blank=True, null=True)
    full_text_status = models.CharField(max_length=64, blank=True, null=True)

    class Origin(models.TextChoices):
        DATASET = "dataset", "Published dataset (v1.0)"
        PROPOSAL = "proposal", "Accepted proposal"
        UPLOAD = "upload", "Uploaded by a curator"

    origin = models.CharField(
        max_length=10, choices=Origin.choices, default=Origin.DATASET, db_index=True,
        help_text="Where the row came from. Reloading the dataset only ever touches 'dataset' rows.",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Who accepted or uploaded it (empty for the published dataset).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pathogen_interactions"
        ordering = ["beetle_host", "pathogen"]
        verbose_name = "Pathogen Interaction"
        verbose_name_plural = "Pathogen Interactions"

    def __str__(self):
        return f"{self.beetle_host} - {self.pathogen} ({self.category})"



class InteractionProposal(models.Model):
    """
    A proposed ecological interaction for a beetle in the species list, with where it came from.

    Proposals come from the literature collector (collect_interactions.py) or a CSV. They are NOT part
    of the interactions dataset until an expert accepts them, which publishes a PathogenInteraction
    (``published_as``). Rejected proposals are kept so the same claim is not proposed again.

    A proposal is one claim from one source: the same beetle and partner found in two papers is two
    proposals, which is what lets an expert see how well supported a claim is.
    """

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Waiting for review"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # The claim
    beetle_name = models.CharField(max_length=255, help_text="The beetle's valid name in the species list (a source that uses a synonym is matched to it).")
    beetle_valid_species_id = models.CharField(
        max_length=64, blank=True, db_index=True, help_text="valid_species_id when the beetle is in the species list."
    )
    taxon = models.ForeignKey(
        "Taxon", on_delete=models.SET_NULL, null=True, blank=True, related_name="interaction_proposals"
    )
    partner_name = models.CharField(max_length=255, help_text="The other organism: a fungus, host tree, nematode...")
    category = models.CharField(max_length=64, blank=True, help_text="Fungi, Nematode, Host plant, Mite...")
    relationship = models.CharField(max_length=128, blank=True, help_text="pathogen, parasite, symbiont, host plant...")

    # Where it comes from
    source_key = models.CharField(
        max_length=300,
        help_text="What makes a source unique: its lower-case DOI, else 'pmid:...' or its URL.",
    )
    source_doi = models.CharField(max_length=255, blank=True)
    source_url = models.URLField(max_length=500, blank=True, help_text="Where a reviewer reads it.")
    source_title = models.TextField(blank=True)
    source_authors = models.CharField(max_length=500, blank=True, help_text="As the source lists them, e.g. 'Smith J, Jones K'.")
    source_journal = models.CharField(max_length=255, blank=True)
    source_year = models.PositiveSmallIntegerField(null=True, blank=True)
    source_db = models.CharField(max_length=30, blank=True, help_text="europepmc, globi, upload...")
    open_access = models.BooleanField(null=True, blank=True)
    evidence = models.TextField(blank=True, help_text="The sentence(s) in the source that state it.")
    evidence_location = models.CharField(max_length=30, blank=True, help_text="title, abstract, full text")
    score = models.FloatField(null=True, blank=True, help_text="The collector's confidence, 0 to 1.")
    collector = models.CharField(max_length=100, blank=True, help_text="What proposed it, and its version.")

    # The decision
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PROPOSED, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    published_as = models.ForeignKey(
        PathogenInteraction, on_delete=models.SET_NULL, null=True, blank=True, related_name="proposals",
        help_text="The dataset row created when this was accepted.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "interaction_proposal"
        ordering = ["-score", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                Lower("beetle_name"), Lower("partner_name"), "source_key",
                name="interaction_proposal_one_per_claim_and_source",
            ),
            models.CheckConstraint(
                condition=models.Q(score__isnull=True) | models.Q(score__gte=0, score__lte=1),
                name="interaction_proposal_score_0_1",
            ),
        ]
        indexes = [models.Index(fields=["status", "-score"], name="interaction_proposal_queue")]

    def __str__(self):
        return f"{self.beetle_name} - {self.partner_name} ({self.get_status_display()})"


# -----------------------------
# Beetle ID game
# -----------------------------
class GameRound(models.Model):
    """
    One round of the Beetle ID game for one player.

    The items are chosen when the round starts and stored in ``items`` so the
    client never picks what it is shown. Each item is a dict:
    {"a": <Beetles id>, "b": <Beetles id or None>, "check": bool, "flip": bool}.
    "check" items have a validated answer and are scored; the client is never told which.
    Odd One Out items also carry "tiles" (the Beetles ids shown, in order), "rank" (where the odd one differs) and
    "group" (the others' names down to that rank); their "a" is the odd one.
    """

    class Mode(models.TextChoices):
        CLASSIFY = "classify", "Classify"
        PAIR = "pair", "Compare pairs"
        ODD = "odd", "Odd One Out"
        SELECT = "select", "Find Them All"
        MIXED = "mixed", "All modes"   # one feed of several games; each item carries its own mode

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_rounds"
    )
    mode = models.CharField(max_length=10, choices=Mode.choices, db_index=True)
    items = models.JSONField(default=list)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "game_round"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.get_mode_display()} round by {self.player} ({self.started_at:%Y-%m-%d})"


class GameAnswer(models.Model):
    """
    One answered item of a round.

    Classify: ``roi`` is the region shown and the four rank fields hold the answer
    (blank = the player stopped before that rank).
    Pair: ``roi`` and ``roi_b`` are the two regions and ``pair_answer`` is the deepest
    rank the player says they share. On unvalidated pairs ``roi`` is the unvalidated one.
    Odd One Out: ``tiles`` are the regions shown, ``roi`` the one the player picked (the odd one itself when they
    skipped) and ``roi_b`` the odd one the round was built around; the pick says "``roi`` is not in ``grid_group`` at
    ``grid_rank``". ``is_check`` is set when the picked region is validated, and only ``correct_<grid_rank>`` is judged.
    Select all: ``tiles`` are the regions shown, ``picks`` the places of those the player tapped as ``grid_group`` at
    ``grid_rank``, and ``roi`` one validated member of the group; ``correct_<grid_rank>`` says whether the grid was
    perfect (every validated member tapped, nothing else), the taps themselves are scored in game_scoring.
    In both grid games ``flagged`` are the places the player flagged as a bad photo before answering: they are left
    out of scoring and of what the grid says about each beetle (#489).

    ``correct_<rank>`` is only filled for check items: True/False when that rank was
    judged, None when it was not answered or has no reference value.
    """

    class PairAnswer(models.TextChoices):
        DIFFERENT = "different", "Different subfamily"
        SUBFAMILY = "subfamily", "Same subfamily"
        TRIBE = "tribe", "Same tribe"
        GENUS = "genus", "Same genus"
        SPECIES = "species", "Same species"
        UNSURE = "unsure", "Not sure"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    round = models.ForeignKey(GameRound, on_delete=models.CASCADE, related_name="answers")
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_answers"
    )
    mode = models.CharField(max_length=10, choices=GameRound.Mode.choices, db_index=True)
    index = models.PositiveSmallIntegerField(help_text="Position of the item in its round.")
    is_check = models.BooleanField(default=False, db_index=True)
    skipped = models.BooleanField(default=False)

    roi = models.ForeignKey(Beetles, on_delete=models.CASCADE, related_name="game_answers")
    roi_b = models.ForeignKey(
        Beetles, on_delete=models.CASCADE, null=True, blank=True, related_name="game_answers_as_b"
    )

    subfamily = models.CharField(max_length=100, blank=True)
    tribe = models.CharField(max_length=100, blank=True)
    genus = models.CharField(max_length=100, blank=True)
    species = models.CharField(max_length=100, blank=True)
    pair_answer = models.CharField(max_length=10, choices=PairAnswer.choices, blank=True)
    tiles = models.JSONField(default=list, blank=True, help_text="Grid games (Odd One Out, Select all): the regions shown, in order.")
    picks = models.JSONField(default=list, blank=True, help_text="Grid games: the places in tiles the player tapped (Select all) or picked (Odd One Out, since #540).")
    grid_rank = models.CharField(max_length=10, blank=True, help_text="Grid games: the rank of the group (in Odd One Out, where one region differs).")
    grid_group = models.JSONField(
        default=dict, blank=True,
        help_text='Grid games: the names of the group, down to grid_rank, e.g. {"subfamily": "Scolytinae", "tribe": "Xyleborini"}.',
    )
    flagged = models.JSONField(
        default=list, blank=True,
        help_text="Grid games: the places in tiles the player flagged as a bad photo; left out of scoring and votes.",
    )
    grid_step = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Grid games: the player's step on the grid ladder (1-12) when the grid was built.",
    )

    correct_subfamily = models.BooleanField(null=True, blank=True)
    correct_tribe = models.BooleanField(null=True, blank=True)
    correct_genus = models.BooleanField(null=True, blank=True)
    correct_species = models.BooleanField(null=True, blank=True)

    # Reference label of ``roi`` when the answer was scored (check items only), kept so
    # per-branch expertise survives later taxonomy or label edits.
    ref_subfamily = models.CharField(max_length=100, blank=True)
    ref_tribe = models.CharField(max_length=100, blank=True)
    ref_genus = models.CharField(max_length=100, blank=True)
    ref_species = models.CharField(max_length=100, blank=True)

    response_ms = models.PositiveIntegerField(
        null=True, blank=True, help_text="Time from the item appearing to the answer, as reported by the browser."
    )
    score_hold = models.BooleanField(
        default=False,
        help_text="Left out of scores: the player reported this ROI and the report is open, "
                  "or the ROI stopped being a valid reference.",
    )
    is_retry = models.BooleanField(
        default=False,
        help_text="A validated beetle shown again so the player can learn it. Earns points, but is left out of "
                  "accuracy and expertise, which only count the first time a beetle is seen.",
    )
    seen_before = models.BooleanField(
        default=False,
        help_text="The player had been shown this beetle's names before (the review after an answer names every "
                  "beetle, #541), so it came back after a wait. Earns full points, but is left out of accuracy and "
                  "expertise, which count only beetles named unseen.",
    )
    validated_later = models.BooleanField(
        default=False, db_index=True,
        help_text="The beetle was not validated when answered but has been since. Its correct_* and ref_* fields are "
                  "then filled from the validated name, so the answer counts towards accuracy and expertise too.",
    )
    new_species = models.BooleanField(
        null=True, blank=True,
        help_text="The species the player named had no validated images when they named it. If the beetle is later "
                  "validated as that species, the player is credited with a new species (SpeciesDiscovery).",
    )
    difficulty = models.FloatField(
        null=True, blank=True,
        help_text="Identification and Similarity: how hard the beetle was when answered, as its percentile among all "
                  "playable beetles (0 easiest, 1 hardest). Its points follow it (game_scoring). Empty on older answers.",
    )
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "game_answer"
        constraints = [
            models.UniqueConstraint(fields=["round", "index"], name="game_answer_round_index_uniq"),
        ]
        indexes = [
            models.Index(fields=["player", "mode", "is_check"], name="game_answer_player_idx"),
            # a player's answers by day (game_rewards: today's count, the streak, the recap)
            models.Index(fields=["player", "answered_at"], name="game_answer_player_day_idx"),
            # grid games: "which grids showed this beetle" is a JSON containment test on tiles (game.showing)
            GinIndex(fields=["tiles"], name="game_answer_tiles_gin", opclasses=["jsonb_path_ops"]),
        ]

    def __str__(self):
        return f"{self.player} #{self.index} ({self.mode})"


class AnswerPoints(models.Model):
    """
    The points one game answer is worth (see game_scoring.py). Recomputed whenever what it depends on changes:
    a beetle being validated (or its label corrected) re-scores answers on it against the truth, and other
    players' answers move the consensus points of beetles that are not validated yet.
    """

    class Basis(models.TextChoices):
        TRUTH = "truth", "Scored against a validated label"
        CONSENSUS = "consensus", "Agreement with stronger players"
        UNSURE = "unsure", "Not sure / skipped"
        NONE = "none", "Not scored (yet)"

    answer = models.OneToOneField(GameAnswer, on_delete=models.CASCADE, primary_key=True, related_name="points")
    points = models.FloatField(default=0.0)
    basis = models.CharField(max_length=10, choices=Basis.choices, default=Basis.NONE)
    detail = models.JSONField(default=dict, blank=True, help_text="How the points were made up, for the player's feedback.")
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "game_answer_points"
        verbose_name = "Answer Points"
        verbose_name_plural = "Answer Points"


class PlayerScore(models.Model):
    """
    A player's running totals, recomputed from AnswerPoints (and their scored answers) so lists are fast.

    ``score`` is the sum of their points in the order they earned them, never allowed below zero.
    ``rating`` is how reliable they are on beetles we know the answer to (0 to 1, a cautious lower estimate
    of their accuracy). It decides whose agreement counts for others and how much.
    """

    player = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True, related_name="game_score")
    score = models.FloatField(default=0.0, db_index=True)
    rating = models.FloatField(default=0.0)
    accuracy = models.FloatField(null=True, blank=True)
    judged = models.PositiveIntegerField(default=0, help_text="Judged ranks on validated beetles (first sightings).")
    viewed = models.PositiveIntegerField(default=0, help_text="Beetles shown and answered or skipped.")
    labelled = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "game_player_score"


class GamePreference(models.Model):
    """
    A player's choices in the game, each a perk unlocked by levels: which game they play (both mixed, or only one
    of them) and their focus, only beetles of one subfamily, tribe or genus (blank means no focus).
    """

    class PlayMode(models.TextChoices):
        BOTH = "both", "All modes"   # every game the player has unlocked, mixed
        CLASSIFY = "classify", "Naming"
        PAIR = "pair", "Similarity"
        ODD = "odd", "Odd One Out"
        SELECT = "select", "Find Them All"

    class FocusRank(models.TextChoices):
        NONE = "", "Everything"
        SUBFAMILY = "subfamily", "Subfamily"
        TRIBE = "tribe", "Tribe"
        GENUS = "genus", "Genus"

    player = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True, related_name="game_preference")
    focus_rank = models.CharField(max_length=10, choices=FocusRank.choices, blank=True, default="")
    focus_value = models.CharField(max_length=100, blank=True, default="")
    play_mode = models.CharField(max_length=10, choices=PlayMode.choices, default=PlayMode.BOTH)
    granted_perks = models.JSONField(
        default=list, blank=True,
        help_text="Unlocks a superuser granted whatever the player's level (game_levels.PERKS keys, or \"all\"). "
                  "For people who need the features, and for testing.",
    )
    kept_perks = models.JSONField(
        default=list, blank=True,
        help_text="Unlocks the player keeps from before the levels changed (game_levels.PERKS keys), e.g. "
                  "Identification for players who had it when it moved from level 2 to level 4.",
    )
    proposals_notice_seen_at = models.DateTimeField(
        null=True, blank=True, help_text="When the player saw 'Your labels now go to the curators' (shown once)."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "game_preference"


class GridStep(models.Model):
    """
    Where a player is on the ladder of one grid game, Odd One Out or Select all (#489): the grids growing from 4 to 9,
    16 and 25 beetles (and in Odd One Out then hiding more odd ones, #540) before going a rank deeper, from subfamily to
    species (game_grid_ladder.steps). Up a step after a run of good grids, down one after a poor grid.
    """

    GAMES = [(GameRound.Mode.ODD.value, GameRound.Mode.ODD.label), (GameRound.Mode.SELECT.value, GameRound.Mode.SELECT.label)]

    player = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grid_steps")
    game = models.CharField(max_length=10, choices=GAMES)
    step = models.PositiveSmallIntegerField(default=1)
    good_run = models.PositiveSmallIntegerField(default=0, help_text="Good grids in a row since the step last moved.")
    last_answer = models.ForeignKey(
        GameAnswer, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The last answer that moved the ladder, so no answer ever counts twice.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "game_grid_step"
        constraints = [models.UniqueConstraint(fields=["player", "game"], name="game_grid_step_player_game_uniq")]

    def __str__(self):
        return f"{self.player} {self.game} step {self.step}"


class RetroCredit(models.Model):
    """
    An answer re-scored because the beetle was validated after the player answered it: what they said, what the
    beetle turned out to be, and the points before and after. Shown to the player once as a recap
    (``seen_at``), so they learn from it. Only validations make one, never agreement points.
    """

    answer = models.OneToOneField("GameAnswer", on_delete=models.CASCADE, related_name="retro_credit")
    player = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="retro_credits")
    points_before = models.FloatField(default=0.0)
    points_after = models.FloatField(default=0.0)
    validated_name = models.CharField(max_length=200, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "game_retro_credit"
        ordering = ["-created_at"]

    @property
    def change(self):
        return round(self.points_after - self.points_before, 2)


class SpeciesDiscovery(models.Model):
    """
    A player named a species for a beetle when the gallery had no validated images of that species, and a
    curator later validated the beetle as exactly that species. Shown once as a pop-up (``seen_at``) and as a
    badge on the player's profile.
    """

    player = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="species_discoveries")
    roi = models.ForeignKey("Beetles", on_delete=models.CASCADE, related_name="species_discoveries")
    answer = models.ForeignKey("GameAnswer", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    genus = models.CharField(max_length=100)
    species = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "game_species_discovery"
        ordering = ["-created_at"]
        verbose_name = "Species Discovery"
        verbose_name_plural = "Species Discoveries"
        constraints = [
            models.UniqueConstraint(fields=["player", "roi"], name="game_discovery_uniq"),
        ]

    def __str__(self):
        return f"{self.player}: {self.genus} {self.species}"


class PlayerSkill(models.Model):
    """
    How well a player identifies one rank within one branch of the taxonomy, from
    their scored "Name That Beetle" answers. Recomputed whenever they finish a round.

      rank=species,   branch=<genus>      species ID within that genus
      rank=genus,     branch=<tribe>      genus ID within that tribe
      rank=tribe,     branch=<subfamily>  tribe ID within that subfamily
      rank=subfamily, branch=""           subfamily ID overall

    ``proven`` means the player has covered the taxon and is accurate enough (see game_trust.is_proven):
    enough answers on most of its children with validated images, at least GAME_TRUST_MIN_ACCURACY right.
    ``required`` and ``covered`` are the answers that coverage needs and how many of them the player has;
    ``children_total`` counts the taxon's children with validated images (its species, genera or tribes),
    ``children_needed`` how many of them proof needs, and ``children_done`` those fully covered (#381).
    ``proven_at`` is when it last became proven.
    """

    player = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_skills"
    )
    rank = models.CharField(max_length=10)
    branch = models.CharField(max_length=100, blank=True)
    correct = models.PositiveIntegerField(default=0)
    judged = models.PositiveIntegerField(default=0)
    lower_bound = models.FloatField(default=0.0)
    required = models.PositiveIntegerField(default=0)
    covered = models.PositiveIntegerField(default=0)
    children_total = models.PositiveIntegerField(default=0)
    children_needed = models.PositiveIntegerField(default=0)
    children_done = models.PositiveIntegerField(default=0)
    proven = models.BooleanField(default=False, db_index=True)
    proven_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "game_player_skill"
        constraints = [
            models.UniqueConstraint(fields=["player", "rank", "branch"], name="game_skill_uniq"),
        ]

    def __str__(self):
        where = f" in {self.branch}" if self.branch else ""
        return f"{self.player} {self.rank}{where}: {self.correct}/{self.judged}"


class LabelReview(models.Model):
    """A staff decision on a game label proposal for one ROI (accepted or dismissed)."""

    class Decision(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        DISMISSED = "dismissed", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    roi = models.ForeignKey(Beetles, on_delete=models.CASCADE, related_name="label_reviews")
    decision = models.CharField(max_length=10, choices=Decision.choices)
    subfamily = models.CharField(max_length=100, blank=True)
    tribe = models.CharField(max_length=100, blank=True)
    genus = models.CharField(max_length=100, blank=True)
    species = models.CharField(max_length=100, blank=True)
    taxon = models.ForeignKey("Taxon", on_delete=models.SET_NULL, null=True, blank=True)
    trusted_rank = models.CharField(
        max_length=10, blank=True, help_text="Deepest rank backed by a proven expert when reviewed."
    )
    answers = models.PositiveIntegerField(default=0, help_text="Game answers on the ROI when reviewed.")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="game_label_reviews"
    )
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "game_label_review"
        ordering = ["-reviewed_at"]


class RoiDifficulty(models.Model):
    """
    How hard an ROI is to identify, used to match items to players.

    ``model_difficulty`` is for classifier output (e.g. 1 - top confidence), to be filled
    by a future upload or pipeline; when set it takes precedence. ``game_difficulty`` is
    learned from game answers: the error rate on scored items, or disagreement between
    players on unvalidated ones. Both run from 0 (easy) to 1 (hard).
    """

    roi = models.OneToOneField(Beetles, on_delete=models.CASCADE, primary_key=True, related_name="difficulty")
    model_difficulty = models.FloatField(null=True, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    model_updated_at = models.DateTimeField(null=True, blank=True)
    game_difficulty = models.FloatField(null=True, blank=True)
    game_answers = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "game_roi_difficulty"
        verbose_name = "ROI Difficulty"
        verbose_name_plural = "ROI Difficulties"

    @property
    def value(self):
        if self.model_difficulty is not None:
            return self.model_difficulty
        return self.game_difficulty


class GameReport(models.Model):
    """
    A player's report that an ROI they saw in the game looks wrong.

    While a report is open the ROI is not used for scoring, and the reporter's own scored
    answers on it are held out of their score. Staff resolve it on the annotation page:
    "corrected" re-scores every answer on the ROI against its fixed label (or voids them
    if it is no longer validated); "confirmed" puts the held answers back.
    """

    class Reason(models.TextChoices):
        WRONG_LABEL = "wrong_label", "The name looks wrong"
        BAD_BOX = "bad_box", "The box doesn't fit the beetle"
        BAD_IMAGE = "bad_image", "Photo problem (blurry, not a beetle, ...)"
        OTHER = "other", "Something else"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CORRECTED = "corrected", "Corrected"
        CONFIRMED = "confirmed", "Label confirmed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    roi = models.ForeignKey(Beetles, on_delete=models.CASCADE, related_name="game_reports")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_reports"
    )
    answer = models.ForeignKey(GameAnswer, on_delete=models.SET_NULL, null=True, blank=True, related_name="reports")
    reason = models.CharField(max_length=12, choices=Reason.choices)
    note = models.TextField(blank=True, max_length=1000)
    was_validated = models.BooleanField(default=False)
    label_at_report = models.CharField(max_length=255, blank=True, help_text="valid_species_id when reported")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="resolved_game_reports"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    staff_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "game_report"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["roi", "reporter"], condition=models.Q(status="open"), name="game_report_one_open_per_player"
            ),
        ]


# -----------------------------
# Classifier suggestions
# -----------------------------
class ModelPrediction(models.Model):
    """
    A species suggested for an ROI by a classifier model. Uploaded by a superuser
    (Data Management -> Model predictions, or ``manage.py import_model_predictions``).

    A prediction is a suggestion only: it never sets the ROI's label. Its confidence sets the
    ROI's ``RoiDifficulty.model_difficulty`` for the game, and it is meant to be offered to
    curators (and shown, marked as unverified, to viewers) while the ROI has no species.

    One row per ROI, model name and model version: uploading the same model version again
    replaces its predictions instead of adding to them.

    ``rank_confidence`` keeps what the model said at each rank above the species, when it says so (a hierarchical
    classifier, or one that is surer of the genus than the species). Where it is empty, the ranks can be worked out
    from the species candidates (see predictions.rank_tips), for tips such as "AI: genus Xyleborus, 92%".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    roi = models.ForeignKey(Beetles, on_delete=models.CASCADE, related_name="predictions")
    valid_species_id = models.CharField(max_length=255, db_index=True)
    taxon = models.ForeignKey(
        "Taxon", on_delete=models.SET_NULL, null=True, blank=True, related_name="predictions",
        help_text="The taxon for valid_species_id when it was uploaded.",
    )
    confidence = models.FloatField(help_text="Model confidence in this species, 0 to 1.")
    top_k = models.JSONField(
        default=list, blank=True,
        help_text='Further candidates, best first: [{"valid_species_id": "1733", "confidence": 0.08}, ...]',
    )
    rank_confidence = models.JSONField(
        default=dict, blank=True,
        help_text='What the model said per rank, when it says so: {"genus": {"value": "Xyleborus", "confidence": 0.92}, ...}',
    )
    model_name = models.CharField(max_length=100)
    model_version = models.CharField(max_length=50, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="model_predictions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "model_prediction"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["roi", "model_name", "model_version"], name="model_prediction_roi_model_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0, confidence__lte=1), name="model_prediction_confidence_0_1"
            ),
        ]

    def __str__(self):
        return f"{self.model_name}: {self.valid_species_id} ({self.confidence:.0%})"


# -----------------------------
# Access requests
# -----------------------------
class AccessRequest(models.Model):
    """
    Someone asking for an account (or more access) through the public form.

    Approvers (superusers) decide on Data Management -> Access requests. Nothing is created
    until a request is approved; see beetles_app/access.py.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DENIED = "denied", "Denied"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    email = models.EmailField()
    affiliation = models.CharField(max_length=200, blank=True)
    reason = models.TextField(blank=True)
    areas = models.JSONField(default=list, blank=True, help_text="Keys of the parts of the site they asked for.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    granted_role = models.CharField(max_length=10, blank=True, help_text="basic, or areas (see granted_areas), once approved. Older requests: member or curator.")
    granted_areas = models.JSONField(default=list, blank=True, help_text="The areas granted when it was decided (areas.py keys).")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="access_requests",
        help_text="The account created or updated when approved.",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    email_verified_at = models.DateTimeField(
        null=True, blank=True, help_text="When the applicant confirmed their email address. Approvers only see confirmed requests."
    )
    notified_at = models.DateTimeField(null=True, blank=True, help_text="When the approvers were emailed.")
    notify_error = models.CharField(max_length=255, blank=True, help_text="Why the approvers could not be emailed.")

    class Meta:
        db_table = "access_request"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"), condition=models.Q(status="pending"), name="access_request_one_pending_per_email"
            ),
        ]

    def __str__(self):
        return f"{self.name} <{self.email}> ({self.status})"


class AreaGrant(models.Model):
    """An extra area (see areas.py) given to one person on top of their role. Set on the My Account page."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="area_grants")
    area = models.CharField(max_length=30)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "area_grant"
        constraints = [models.UniqueConstraint(fields=["user", "area"], name="area_grant_one_per_user_area")]

    def __str__(self):
        return f"{self.user} - {self.area}"


class SiteNotice(models.Model):
    """
    A one-line notice shown at the top of every page while it is switched on, e.g. during a stress test (issue #383).
    There is only ever one row; a superuser edits it on Tools -> Site notice.
    """

    text = models.CharField(max_length=300, blank=True)
    active = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        db_table = "site_notice"

    def __str__(self):
        return f"{'on' if self.active else 'off'}: {self.text}"


class PredictionUpload(models.Model):
    """
    A model predictions CSV uploaded on Data Management, checked and saved in the background (predictions.py), so a
    big file never times out the page. The dialog polls ``percent`` / ``phase`` and then shows ``result``.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Waiting"
        RUNNING = "running", "Working"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to="prediction_uploads/")
    original_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    model_name = models.CharField(max_length=100, blank=True)
    model_version = models.CharField(max_length=50, blank=True)
    dry_run = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    phase = models.CharField(max_length=100, blank=True)
    percent = models.PositiveSmallIntegerField(default=0)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "prediction_upload"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_filename} ({self.status})"


class RoiName(models.Model):
    """
    Every name an ROI has been given, with its identification tier (Beetles.LabelSource) and where it came from.
    The ROI itself shows the most reliable one (identification.py); the others stay here for the record.
    """

    id = models.BigAutoField(primary_key=True)
    roi = models.ForeignKey(Beetles, on_delete=models.CASCADE, related_name="names")
    valid_species_id = models.CharField(max_length=255)
    taxon = models.ForeignKey("Taxon", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tier = models.CharField(max_length=20, choices=Beetles.LabelSource.choices, blank=True, default="")
    detail = models.CharField(max_length=255, blank=True, default="", help_text="Who, or which database / website.")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "roi_name"
        ordering = ["roi", "-created_at", "-id"]   # -id: names saved in the same instant still list newest first
        indexes = [models.Index(fields=["roi", "-created_at"], name="roi_name_roi_recent")]

    def __str__(self):
        return f"{self.valid_species_id} ({self.get_tier_display() or 'No ID'})"


class GameTuning(models.Model):
    """
    A superuser's override of one scoring setting (game_tuning.TUNABLES), from the Scoring page. It wins over
    settings.py; deleting it puts the default back. The game picks a change up within half a minute.
    """

    key = models.CharField(max_length=64, unique=True)
    value = models.JSONField()
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        db_table = "game_tuning"
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} = {self.value}"



def _forget_tuning(**kwargs):
    from beetlesgallery.beetles_app.game_tuning import forget
    forget()


models.signals.post_save.connect(_forget_tuning, sender=GameTuning, dispatch_uid="game_tuning_saved")
models.signals.post_delete.connect(_forget_tuning, sender=GameTuning, dispatch_uid="game_tuning_deleted")
