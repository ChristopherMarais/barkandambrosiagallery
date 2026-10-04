"""Kept for old notes and scripts: expired downloads are now removed by `cleanup_storage` (nightly)."""
from django.core.management.base import BaseCommand

from beetlesgallery.beetles_app import storage_cleanup


class Command(BaseCommand):
    help = "Delete the files of expired downloads (part of cleanup_storage)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        freed = storage_cleanup.expire_downloads(dry_run=opts["dry_run"])
        self.stdout.write(f"{freed.files} file(s), {freed.bytes} bytes")
