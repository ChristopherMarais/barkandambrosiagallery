"""
Free disk space held by expired downloads, leftover temporary files, and upload ZIPs whose images are already in
the gallery (see beetles_app/storage_cleanup.py for exactly what goes when). Run nightly by
.github/workflows/storage-cleanup.yml. Use --dry-run to see what would be removed without removing anything.
"""
from django.core.management.base import BaseCommand

from beetlesgallery.beetles_app import storage_cleanup


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


class Command(BaseCommand):
    help = "Delete expired downloads, leftover temp files, and upload ZIPs whose images are already in the gallery."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only report what would be removed.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        total = storage_cleanup.Freed()
        for label, step in storage_cleanup.STEPS:
            freed = step(dry_run=dry)
            total.add(freed)
            self.stdout.write(f"{label}: {freed.files} file(s), {human(freed.bytes)}")
            for what, why in freed.kept:
                self.stdout.write(f"  kept {what}: {why}")
        verb = "Would free" if dry else "Freed"
        self.stdout.write(self.style.SUCCESS(f"{verb} {human(total.bytes)} in {total.files} file(s)."))
