import os
import csv
import uuid
import shutil
import zipfile
import contextlib
import json
from pathlib import Path
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.core.files import File

from beetlesgallery.beetles_app import species_ref
from beetlesgallery.beetles_app.models import Beetles, DownloadJob

class Command(BaseCommand):
    help = "Build CSV + ZIP for pending DownloadJobs."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=str, help="Build only the given job UUID.")
        parser.add_argument("--limit", type=int, default=10, help="Max jobs to process this run.")
        parser.add_argument("--dry-run", action="store_true", help="Resolve selection and count only; do not write files.")

    def _peek_next_job(self, specific_id=None):
        qs = DownloadJob.objects.filter(status=DownloadJob.Status.PENDING).order_by("created_at")
        if specific_id:
            qs = qs.filter(id=specific_id)
        return qs.first()

    def handle(self, *args, **opts):
        job_id = opts.get("job")
        limit = opts.get("limit", 10)
        dry = opts.get("dry_run", False)

        # prevents a crashed/interrupted run from leaving jobs stuck
        STALE_MIN = int(getattr(settings, "DOWNLOAD_STALE_BUILDING_MIN", 15))
        cutoff = timezone.now() - timedelta(minutes=STALE_MIN)
        stale = DownloadJob.objects.filter(
            status=DownloadJob.Status.BUILDING,
            started_at__lt=cutoff
        )
        count = stale.update(
            status=DownloadJob.Status.PENDING,
            error_message="Auto-reset from stale BUILDING; will retry on next run."
        )
        if count:
            self.stdout.write(self.style.WARNING(f"Reset {count} stale BUILDING job(s) to PENDING."))

        # ------------------------------------------------------------------
        # Cleanup: expire old READY jobs whose expires_at is in the past.
        # This runs opportunistically each time build_downloads is invoked.
        # ------------------------------------------------------------------
        
        now = timezone.now()
        expired_qs = DownloadJob.objects.filter(
            status=DownloadJob.Status.READY,
            expires_at__isnull=False,
            expires_at__lt=now,
        )

        expired_count = expired_qs.count()
        for job in expired_qs:
            # Best-effort file deletion; ignore if already missing
            try:
                if job.csv_file:
                    job.csv_file.delete(save=False)
            except Exception:
                pass
            try:
                if job.zip_file:
                    job.zip_file.delete(save=False)
            except Exception:
                pass

            job.status = DownloadJob.Status.EXPIRED
            # Keep any existing error_message if present; otherwise set a default note.
            if not job.error_message:
                job.error_message = "Files expired and removed after retention period."
            # Keep existing finished_at if set; otherwise stamp now.
            if not job.finished_at:
                job.finished_at = now

            job.save(update_fields=["status", "error_message", "csv_file", "zip_file", "finished_at"])

        if expired_count:
            self.stdout.write(
                self.style.WARNING(f"Expired {expired_count} READY download job(s) with past expires_at.")
            )      

        processed = 0
        while processed < limit:
            job = self._peek_next_job(job_id) if dry else self._claim_next_job(job_id)
            if not job:
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No pending jobs to process."))
                break

            try:
                self._process_job(job, dry=dry)

            except Exception as e:
                job.status = DownloadJob.Status.FAILED
                job.error_message = f"Builder error: {e}"
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error_message", "finished_at"])
                self.stderr.write(self.style.ERROR(f"[{job.id}] FAILED: {e}"))

            processed += 1

            if dry:
                break

            # If a specific job was requested, stop after handling it
            if job_id:
                break

    def _resolve_queryset(self, job: DownloadJob):
        from beetlesgallery.beetles_app.utils import build_query_q, filter_beetles_queryset
        """
        Return a queryset of Beetles for this job based on selection_mode.
        """
        if job.selection_mode == "ids":
            ids = job.get_ids()
            return Beetles.objects.filter(id__in=ids)
            
        elif job.selection_mode == "query":
            # Check if query_string is JSON (new style) or plain text (legacy)
            raw_q = job.query_string or ""
            
            try:
                data = json.loads(raw_q)
                # It's JSON: {"q": "...", "filters": {...}, "ranges": {...}}
                qs = Beetles.objects.all()
                
                # 1. Apply Text Search
                text_q = data.get("q", "").strip()
                if text_q:
                    q_obj, _ = build_query_q(text_q)
                    qs = qs.filter(q_obj)
                
                # 2. Apply Filters & Ranges
                filters = data.get("filters", {})
                ranges = data.get("ranges", {})
                
                qs = filter_beetles_queryset(
                    qs, 
                    filters, 
                    size_min=ranges.get("size_min"),
                    size_max=ranges.get("size_max"),
                    res_min=ranges.get("res_min"),
                    res_max=ranges.get("res_max")
                )
                return qs

            except (json.JSONDecodeError, TypeError):
                # Fallback: Treat as plain text query (Legacy)
                q_obj, _ = build_query_q(raw_q)
                return Beetles.objects.filter(q_obj)

        else:
            return Beetles.objects.none()

    def _claim_next_job(self, specific_id=None):
        """
        Atomically pick one PENDING job, mark it BUILDING, and return it.
        Uses row-level locks with skip_locked to avoid races.
        """
        with transaction.atomic():
            qs = DownloadJob.objects.select_for_update(skip_locked=True).filter(
                status=DownloadJob.Status.PENDING
            ).order_by("created_at")

            if specific_id:
                qs = qs.filter(id=specific_id)

            job = qs.first()
            if not job:
                return None

            job.status = DownloadJob.Status.BUILDING
            job.started_at = timezone.now()
            job.error_message = ""
            job.save(update_fields=["status", "started_at", "error_message"])
            return job

    def _storage_size(self, filefield):
        """Best-effort size lookup via storage API (works for S3/local)."""
        try:
            if filefield and filefield.name:
                return filefield.storage.size(filefield.name)
        except Exception:
            return None
        return None

    def _estimate_total_bytes(self, qs, sample=50):
        """
        Estimate total bytes by sampling up to `sample` images via storage.size().
        Returns (estimated_bytes, sampled_count, sample_avg).
        """
        sampled = 0
        total_bytes = 0
        for b in qs.select_related("image_asset").iterator(chunk_size=500):
            if not (b.image_asset and b.image_asset.image_file):
                continue
            sz = self._storage_size(b.image_asset.image_file)
            if sz:
                total_bytes += sz
                sampled += 1
            if sampled >= sample:
                break

        total_rows = qs.count()
        if sampled == 0:
            return 0, 0, 0

        avg = total_bytes / sampled
        est = int(avg * total_rows)
        return est, sampled, avg

    def _free_bytes_at(self, path):
        """Free bytes on the filesystem hosting `path` (for temp/zip staging)."""
        try:
            usage = shutil.disk_usage(str(path))
            return int(usage.free)
        except Exception:
            return None

    def _process_job(self, job: DownloadJob, dry: bool = False):
        self.stdout.write(f"[{job.id}] Starting build (mode={job.selection_mode})")

        qs = (
            self._resolve_queryset(job)
            .select_related("image_asset")
            .order_by("id")
        )

        total = qs.count()
        self.stdout.write(f"[{job.id}] Resolved selection: {total} rows")
        
        # --- reference join (valid_species.csv): build once ---
        try:
            ref_ids = set(
                (str(v).strip() if v is not None else "")
                for v in qs.values_list("depicts_valid_name_id", flat=True)
            )
            ref_map = species_ref.bulk_lookup(ref_ids)  # dict[id] -> dict of ref fields
            ref_label = species_ref.get_label() or ""   # human-friendly label for users
        except Exception as e:
            # Proceed without enrichment if reference is unavailable
            ref_map = {}
            ref_label = ""
            self.stderr.write(self.style.WARNING(
                f"[{job.id}] Reference CSV not available; proceeding without enrichment: {e}"
            ))

        # --- Preflight: count limit ---
        max_records = getattr(settings, "DOWNLOAD_MAX_RECORDS", 50_000)
        
        # Metadata-only downloads are allowed to exceed this limit.
        if job.include_images and total > max_records:
            if dry:
                self.stdout.write(self.style.WARNING(
                    f"[{job.id}] DRY-RUN: would fail preflight: {total} records > limit {max_records}."
                ))
                return
            job.status = DownloadJob.Status.FAILED
            job.error_message = f"Request too large: {total} records > limit {max_records}."
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at", "total_requested"])
            self.stderr.write(self.style.ERROR(f"[{job.id}] FAILED preflight: too many records"))
            return

        # --- Preflight: size estimate + free space check ---
        if job.include_images:
            # Only check disk space/size limits if we are actually building a ZIP
            est_bytes, sampled, avg = self._estimate_total_bytes(qs)
            headroom = 1.2
            max_bytes = getattr(settings, "DOWNLOAD_MAX_BYTES", 20 * 1024**3)  # 20 GB
            need_bytes = int(est_bytes * headroom)

            if need_bytes > max_bytes:
                if dry:
                    self.stdout.write(self.style.WARNING(
                        f"[{job.id}] DRY-RUN: would fail preflight: estimated ~{need_bytes/1024**3:.1f} GB "
                        f"> max {max_bytes/1024**3:.1f} GB (sampled {sampled}, avg ~{avg/1024**2:.2f} MB)."
                    ))
                    return
                
                job.status = DownloadJob.Status.FAILED
                job.error_message = (
                    f"Estimated dataset too large (~{need_bytes/1024**3:.1f} GB) > max "
                    f"{max_bytes/1024**3:.1f} GB. Sampled {sampled} images, avg size ~{avg/1024**2:.2f} MB."
                )
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error_message", "finished_at"])
                self.stderr.write(self.style.ERROR(f"[{job.id}] FAILED preflight: estimated size too big"))
                return

            # Ensure local temp area has enough free space
            media_root = Path(settings.MEDIA_ROOT or ".")
            tmp_parent = media_root / "downloads" / "tmp"
            tmp_parent.mkdir(parents=True, exist_ok=True)
            free = self._free_bytes_at(tmp_parent)
            min_free = 512 * 1024**2  # 512 MB minimum breathing room
            if free is not None and free < max(min_free, need_bytes):
                if dry:
                    self.stdout.write(self.style.WARNING(
                        f"[{job.id}] DRY-RUN: would fail preflight: insufficient disk space. "
                        f"Need ~{need_bytes/1024**3:.1f} GB, free {free/1024**3:.1f} GB."
                    ))
                    return
                job.status = DownloadJob.Status.FAILED
                job.error_message = (
                    f"Insufficient disk space for temp files. Need ~{need_bytes/1024**3:.1f} GB, "
                    f"free {free/1024**3:.1f} GB."
                )
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error_message", "finished_at"])
                self.stderr.write(self.style.ERROR(f"[{job.id}] FAILED preflight: not enough disk"))
                return
            
        else:
            self.stdout.write(f"[{job.id}] Metadata only request; skipping image size checks.")

        # keep total_requested up-to-date (read-only in dry-run)
        if not dry and job.total_requested != total:
            job.total_requested = total
            job.save(update_fields=["total_requested"])

        if dry:
            self.stdout.write(self.style.SUCCESS(
                f"[{job.id}] DRY-RUN: would build CSV+ZIP for {total} rows"
            ))
            return

        # Additional informative error messages
        media_root = Path(settings.MEDIA_ROOT or ".")
        try:
            media_root.mkdir(parents=True, exist_ok=True)
            test = media_root / ".write_test"
            test.write_text("ok")
            test.unlink()
        except Exception as e:
            # fail gracefully and inform user
            job.status = DownloadJob.Status.FAILED
            job.error_message = f"Cannot write to MEDIA_ROOT ({media_root}): {e}"
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            self.stderr.write(self.style.ERROR(f"[{job.id}] FAILED: {job.error_message}"))
            return

        # --- Output paths (tmp -> final) ---
        media_root = Path(settings.MEDIA_ROOT or ".")
        tmp_dir = media_root / "downloads" / "tmp" / str(job.id)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        csv_filename = f"beetles_{job.id}.csv"
        csv_tmp_path = tmp_dir / csv_filename
        zip_tmp_path = tmp_dir / zip_filename

        # --- Write CSV (comma-separated) ---
        # Headers: use visible column names + the stable filename
        headers = [
            "record_id",
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
            "update_notes"
        ]

        rows_iter = qs.iterator(chunk_size=1000)

        with csv_tmp_path.open("w", encoding="utf-8-sig", newline="") as fh_csv, \
             zipfile.ZipFile(zip_tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:

        # Only setup ZIP path if needed
        zip_filename = f"beetles_{job.id}.zip"
        zip_tmp_path = tmp_dir / zip_filename if job.include_images else None

        # --- Write CSV (and optionally ZIP) ---
        # Open CSV normally. Open ZIP only if needed, otherwise use a nullcontext
        ctx_zip = zipfile.ZipFile(zip_tmp_path, "w", compression=zipfile.ZIP_DEFLATED) if job.include_images else contextlib.nullcontext()

        with csv_tmp_path.open("w", encoding="utf-8", newline="") as fh_csv, ctx_zip as zf:

            # --- Write CSV (comma-separated) ---
            writer = csv.writer(fh_csv, dialect="excel")
            # Headers: use visible column names + the stable filename
            headers = [
                "record_id",
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
                "update_notes"
            ]
            writer.writerow(headers)

            rows_iter = qs.iterator(chunk_size=1000)

            for b in rows_iter:
                img = b.image_asset 

                if job.include_images and zf and img and img.image_file:
                    f_obj = img.image_file
                    ext = os.path.splitext(f_obj.name)[1].lstrip(".").lower() or "bin"
                    image_filename = f"{b.id}.{ext}"
                    
                    try:
                        src_path = f_obj.path
                        if os.path.exists(src_path):
                            zf.write(src_path, arcname=image_filename)
                        else:
                            raise FileNotFoundError
                    except Exception:
                        try:
                            with f_obj.storage.open(f_obj.name, "rb") as sf, zf.open(image_filename, "w") as dest:
                                shutil.copyfileobj(sf, dest, length=1024 * 1024)
                        except Exception:
                            pass

                writer.writerow([
                    str(b.id),                                      # record_id
                    b.alternative_id or "",
                    img.image_institution if img else "",
                    img.photographer if img else "",
                    img.image_email if img else "",
                    img.photo_usage_statement if img else "",
                    b.aspect or "",
                    img.resolution_in_ppmm if img else "",
                    img.image_notes if img else "",
                    img.image_date_taken if img else "",
                    img.image_has_multiple_individuals if img else "",
                    b.depicts_specimen or "",
                    b.depicts_valid_name_id or "",
                    b.depicts_described_name_id or "",
                    b.depicts_name_verbatim or "",
                    b.collection_country or "",
                    b.collection_stateProvince or "",
                    b.specimen_sex or "",
                    b.specimen_type_status or "",
                    b.specimen_notes or "",
                    ""                                              # update_notes (blank)
                ])

        self.stdout.write(f"[{job.id}] Wrote CSV -> {csv_tmp_path.name}")
        if job.include_images:
             self.stdout.write(f"[{job.id}] Wrote ZIP -> {zip_tmp_path.name}")

        # --- Attach to FileFields (moves to final storage) ---
        # 1. Always save CSV
        with csv_tmp_path.open("rb") as fh1:
            job.csv_file.save(csv_filename, File(fh1), save=False)
        
        # 2. Conditionally save ZIP
        if job.include_images and zip_tmp_path:
            with zip_tmp_path.open("rb") as fh2:
                job.zip_file.save(zip_filename, File(fh2), save=False)

        job.status = DownloadJob.Status.READY
        job.finished_at = timezone.now()
        # retention window (14 days)
        try:
            retention_days = int(getattr(settings, "DOWNLOAD_RETENTION_DAYS", 14))
        except Exception:
            retention_days = 14
        job.expires_at = job.finished_at + timedelta(days=retention_days)

        job.save(update_fields=["csv_file", "zip_file", "status", "finished_at", "expires_at"])

        # Cleanup tmp files
        try:
            csv_tmp_path.unlink(missing_ok=True)
            if zip_tmp_path:
                zip_tmp_path.unlink(missing_ok=True)
            # remove the empty directory
            tmp_dir.rmdir()
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS(f"[{job.id}] READY."))