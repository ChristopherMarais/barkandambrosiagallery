import os
import sys
import fcntl
from contextlib import contextmanager

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command

from beetles_app.models import UploadBatch


# Global lock file
LOCK_DEFAULT_PATH = os.path.join(settings.BASE_DIR, "upload_pipeline.lock")


@contextmanager
def upload_pipeline_lock(lock_path: str = LOCK_DEFAULT_PATH):
    """
    Global, cross-process lock for the upload pipeline.

    Any process that wants to run "validate + import" must acquire this lock.
    Only one process can hold the lock at a time; others will block until it's free.
    """
    # Make sure the directory exists
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    # Open the lock file. "a+" means create if missing, append/read.
    # We don't care about the contents; we only use the OS-level lock.
    with open(lock_path, "a+") as f:
        # Ensure there's at least some content (purely cosmetic)
        try:
            f.write("upload pipeline lock\n")
            f.flush()
        except Exception:
            # If write fails for some reason, we still try to lock.
            pass

        # Acquire an exclusive lock. This will block until the lock is available.
        fcntl.flock(f, fcntl.LOCK_EX)

        try:
            yield
        finally:
            # Always release the lock when we're done (or on error).
            fcntl.flock(f, fcntl.LOCK_UN)


class Command(BaseCommand):
    help = "Validate + import a single UploadBatch, with a global lock to prevent overlap."

    def add_arguments(self, parser):
        parser.add_argument(
            "--id",
            required=True,
            help="UploadBatch UUID to process.",
        )

    def handle(self, *args, **opts):
        batch_id = opts["id"]

        # Make sure the batch exists before we grab the lock.
        try:
            batch = UploadBatch.objects.get(id=batch_id)
        except UploadBatch.DoesNotExist:
            raise CommandError(f"UploadBatch {batch_id} does not exist")

        self.stdout.write(f"Processing UploadBatch {batch_id}...")

        # Global lock: only one process can be inside this block at a time.
        with upload_pipeline_lock():
            self.stdout.write("Acquired global upload pipeline lock.")

            # Re-fetch inside the lock in case something changed.
            batch.refresh_from_db()

            # 1) Run validation for this batch only.
            #    This uses your existing management command.
            self.stdout.write("Running validate_uploads...")
            call_command("validate_uploads", id=batch_id)

            # Reload to see new status after validation
            batch.refresh_from_db()
            self.stdout.write(f"Batch {batch_id} status after validation: {batch.status}")

            # 2) Only run import if the batch ended up VALIDATED
            if batch.status == UploadBatch.Status.VALIDATED:
                self.stdout.write("Running import_validated...")
                call_command("import_validated", id=batch_id)
            else:
                self.stdout.write(
                    f"Batch {batch_id} is in status {batch.status}; skipping import."
                )

        self.stdout.write(self.style.SUCCESS(f"Done processing UploadBatch {batch_id}."))

