import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command

from beetlesgallery.beetles_app.models import UpdateBatch


# IMPORTANT: use the *same* lockfile as the upload pipeline so that
# uploads and updates never run at the same time.
LOCKFILE_PATH = Path(settings.BASE_DIR) / "upload_pipeline.lock"


class Command(BaseCommand):
    help = (
        "Run validate_updates and apply_updates for a single UpdateBatch, "
        "using the same lockfile as the upload pipeline."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--id",
            required=True,
            help="UUID of the UpdateBatch to process.",
        )

    def handle(self, *args, **options):
        batch_id = options["id"]

        try:
            batch = UpdateBatch.objects.get(id=batch_id)
        except UpdateBatch.DoesNotExist:
            raise CommandError(f"UpdateBatch {batch_id} does not exist")

        self.stdout.write(
            f"[process_single_update] starting pipeline for batch {batch.id} "
            f"(status={batch.status})"
        )

        self._acquire_lock()
        try:
            # --- 1) validate_updates (real run; no --dry-run) ---
            self.stdout.write(
                f"[process_single_update] validate_updates --id {batch.id}"
            )
            call_command("validate_updates", id=str(batch.id))

            # Refresh state after validation
            batch.refresh_from_db()
            self.stdout.write(
                f"[process_single_update] after validate: status={batch.status}"
            )

            if batch.status != UpdateBatch.Status.VALIDATED:
                # The validator already stamped REJECTED + error_message
                self.stdout.write(
                    self.style.WARNING(
                        f"[process_single_update] batch {batch.id} did not "
                        f"validate (status={batch.status}); skipping apply_updates."
                    )
                )
                return

            # --- 2) apply_updates (real run; no --dry-run) ---
            self.stdout.write(
                f"[process_single_update] apply_updates --id {batch.id}"
            )
            call_command("apply_updates", id=str(batch.id))

            batch.refresh_from_db()
            self.stdout.write(
                f"[process_single_update] finished; final status={batch.status}"
            )

        finally:
            self._release_lock()

    # -----------------
    # lock helpers
    # -----------------
    def _acquire_lock(self, timeout=300, poll_interval=1.0):
        """
        Simple file-based lock shared with the upload pipeline.

        Tries to create LOCKFILE_PATH exclusively. If it already exists,
        waits until it's removed or timeout expires.
        """
        start = time.time()
        while True:
            try:
                fd = os.open(
                    LOCKFILE_PATH,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.close(fd)
                self.stdout.write(
                    f"[process_single_update] acquired lock {LOCKFILE_PATH}"
                )
                return
            except FileExistsError:
                if time.time() - start > timeout:
                    raise CommandError(
                        f"Could not acquire pipeline lock {LOCKFILE_PATH} "
                        f"within {timeout} seconds."
                    )
                time.sleep(poll_interval)

    def _release_lock(self):
        try:
            os.unlink(LOCKFILE_PATH)
            self.stdout.write(
                f"[process_single_update] released lock {LOCKFILE_PATH}"
            )
        except FileNotFoundError:
            # Someone already cleaned it up; that's fine.
            pass

