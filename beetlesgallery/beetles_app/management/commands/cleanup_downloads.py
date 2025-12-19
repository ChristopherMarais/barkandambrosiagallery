from datetime import timedelta
from pathlib import Path
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings

from beetlesgallery.beetles_app.models import DownloadJob

class Command(BaseCommand):
    help = "Delete expired TSV/ZIP artifacts for DownloadJobs and mark them as EXPIRED."

    def add_arguments(self, parser):
        parser.add_argument(
            "--job",
            type=str,
            help="Expire a single job by UUID (ignores time filters).",
        )
        parser.add_argument(
            "--days",
            type=int,
            help=(
                "Fallback retention window for jobs without expires_at. "
                "If provided, jobs finished more than N days ago will be expired."
            ),
        )
        parser.add_argument(
            "--include-failed",
            action="store_true",
            help="Also expire FAILED jobs (if they have files).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted, but do not modify anything.",
        )

    def handle(self, *args, **opts):
        job_id = opts.get("job")
        days = opts.get("days")
        include_failed = opts.get("include_failed", False)
        dry = opts.get("dry_run", False)

        now = timezone.now()

        # --- Build base queryset ---
        if job_id:
            try:
                uuid.UUID(str(job_id))
            except Exception:
                raise CommandError("Invalid --job UUID.")
            qs = DownloadJob.objects.filter(id=job_id)
        else:
            # Expire by expires_at when present
            qs = DownloadJob.objects.filter(expires_at__isnull=False, expires_at__lte=now)

            # Optional fallback window for rows without expires_at
            if days is not None:
                cutoff = now - timedelta(days=int(days))
                qs = qs.union(
                    DownloadJob.objects.filter(expires_at__isnull=True, finished_at__lte=cutoff)
                )

        # Only touch states that might legitimately hold files.
        valid_statuses = [DownloadJob.Status.READY]
        if include_failed:
            valid_statuses.append(DownloadJob.Status.FAILED)

        qs = qs.filter(status__in=valid_statuses)

        jobs = list(qs)
        if not jobs:
            self.stdout.write(self.style.WARNING("No jobs to expire."))
            return

        self.stdout.write(f"Found {len(jobs)} job(s) to expire. dry_run={dry}")

        expired_count = 0
        bytes_freed = 0

        for j in jobs:
            # Collect file names before deletion for logging
            tsv_name = j.tsv_file.name or ""
            zip_name = j.zip_file.name or ""

            # Estimate sizes via storage if available (best-effort)
            tsv_size = self._safe_size(j.tsv_file)
            zip_size = self._safe_size(j.zip_file)

            if dry:
                self.stdout.write(
                    f"[{j.id}] would delete: "
                    f"{'TSV='+tsv_name if tsv_name else 'TSV=∅'}, "
                    f"{'ZIP='+zip_name if zip_name else 'ZIP=∅'}"
                )
                continue

            # Delete from storage via storage API
            self._safe_delete(j.tsv_file)
            self._safe_delete(j.zip_file)

            # Clear fields & mark EXPIRED
            j.tsv_file = ""  # clear FileField
            j.zip_file = ""
            j.status = DownloadJob.Status.EXPIRED
            note = "Expired by cleanup_downloads."
            if j.error_message:
                j.error_message = f"{j.error_message} | {note}"
            else:
                j.error_message = note
            j.save(update_fields=["tsv_file", "zip_file", "status", "error_message"])

            expired_count += 1
            bytes_freed += (tsv_size or 0) + (zip_size or 0)
            self.stdout.write(self.style.SUCCESS(f"[{j.id}] expired."))

        if dry:
            self.stdout.write(self.style.WARNING("Dry-run complete. No files were removed."))
        else:
            human = self._human_bytes(bytes_freed)
            self.stdout.write(self.style.SUCCESS(f"Expired {expired_count} job(s), freed ~{human}."))

    # --- helpers ---

    def _safe_delete(self, filefield):
        """
        Delete a FileField via its storage backend. Ignore errors.
        """
        try:
            if filefield and filefield.name:
                storage = filefield.storage
                storage.delete(filefield.name)
        except Exception:
            # swallow; we still clear the DB field above
            pass

    def _safe_size(self, filefield):
        """
        Best-effort size lookup via storage backend.
        Returns None if unavailable.
        """
        try:
            if filefield and filefield.name:
                return filefield.storage.size(filefield.name)
        except Exception:
            return None
        return None

    def _human_bytes(self, n):
        if n is None:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        x = float(n)
        while x >= 1024 and i < len(units) - 1:
            x /= 1024.0
            i += 1
        return f"{x:.1f} {units[i]}"
